import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from zipfile import ZipFile
import requests

from app.catalog_import import approved_gc_code, import_catalog_rows, plan_catalog_import
from app.domain import CostingFile, Product, ProductModel, ProductModelCode
from app.filesystem_catalog import FilesystemCatalog, PathSafetyError
from app.exceptions import GristValidationError
from app.grist_admin import GristAdminClient, GristConnectivityError, GristDocument, GristDuplicateNameError, GristPermissionError, ensure_safari_document
from app.grist_admin import GristWorkspace
from app.grist import GristClient
from app.repository import AssociationConflict, AssociationProposal, InMemorySafariRepository
from app.schema import FOUNDATION_TABLES, apply_schema, plan_schema, validate_schema_target


class Phase0Tests(unittest.TestCase):
    def test_export_workbook_with_costing_sheets_is_a_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "S1KHF-Export 1.0.ods"
            with ZipFile(path, "w") as package:
                package.writestr(
                    "content.xml",
                    '<office:document-content><table:table table:name="Cost Log"/>'
                    '<table:table table:name="Total Summary"/></office:document-content>',
                )

            node = FilesystemCatalog(root).inspect(path.name)

        self.assertTrue(node.readable)
        self.assertEqual(node.candidate_classification, "costing_candidate")

    def test_root_confinement_rejects_traversal_and_absolute_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "inside").mkdir()
            catalog = FilesystemCatalog(root)
            for value in ("../outside.ods", "%2e%2e/outside.ods", "C:/outside.ods", "\\\\server\\share"):
                with self.assertRaises(PathSafetyError):
                    catalog.resolve(value)

    def test_symlink_escape_is_rejected_when_supported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root, outside = Path(directory) / "root", Path(directory) / "outside"
            root.mkdir(); outside.mkdir(); (outside / "secret.ods").write_bytes(b"secret")
            link = root / "link"
            try:
                link.symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks are not available")
            catalog = FilesystemCatalog(root)
            with self.assertRaises(PathSafetyError):
                catalog.resolve("link/secret.ods")

    def test_windows_reparse_attribute_is_checked_before_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            junction = root / "junction"
            junction.mkdir()
            catalog = FilesystemCatalog(root)
            original_lstat = Path.lstat

            def fake_lstat(candidate: Path):
                if candidate == junction:
                    return SimpleNamespace(st_mode=0, st_file_attributes=0x400)
                return original_lstat(candidate)

            with patch.object(Path, "lstat", fake_lstat), patch.object(
                Path, "resolve", side_effect=AssertionError("resolve followed a reparse point")
            ):
                with self.assertRaises(PathSafetyError):
                    catalog.resolve("junction/missing.ods", require_exists=False)

    def test_windows_junction_escape_is_rejected_when_supported(self) -> None:
        if os.name != "nt":
            self.skipTest("Windows junctions are only available on Windows")
        powershell = shutil.which("pwsh.exe") or shutil.which("powershell.exe")
        if not powershell:
            self.skipTest("PowerShell is unavailable for temporary junction setup")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "root"
            outside = Path(directory) / "outside"
            root.mkdir()
            outside.mkdir()
            junction = root / "junction"
            env = os.environ.copy()
            env["SAFARI_TEST_JUNCTION_PATH"] = str(junction)
            env["SAFARI_TEST_JUNCTION_TARGET"] = str(outside)
            command = (
                "$ErrorActionPreference = 'Stop'; "
                "New-Item -ItemType Junction -Path $env:SAFARI_TEST_JUNCTION_PATH "
                "-Target $env:SAFARI_TEST_JUNCTION_TARGET | Out-Null"
            )
            try:
                result = subprocess.run(
                    [powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=20,
                    env=env,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                self.skipTest(f"Temporary junction creation could not run: {exc}")
            if result.returncode != 0:
                self.skipTest("The host does not permit temporary junction creation")

            try:
                catalog = FilesystemCatalog(root)
                with self.assertRaises(PathSafetyError):
                    catalog.resolve("junction/missing.ods", require_exists=False)
            finally:
                if os.path.lexists(junction):
                    os.rmdir(junction)

    def test_catalog_hierarchy_gc_alias_and_bush_issue(self) -> None:
        result = import_catalog_rows([
            ["Product", "Product Model Number", "Product Model Code", "Description"],
            ["Mini Crane", "GC", "GCMC", "7.5 ton"],
            ["Mini Crane", "GC", "GCMC", "10 ton"],
            ["Mini Crane", "GC18", "GCMC", "7.5 ton"],
            ["Mini Crane", "GC18", "GCMC", "10 ton"],
            ["Bush", "Bush 100", "BUSH-1", "legacy"],
            ["Mini Crane", "S1KHF", "S1KHFLEP", ""],
        ], source_file="catalog.ods")
        self.assertEqual(len(result.products), 2)
        self.assertEqual({item.code for item in result.codes}, {"GCMC-7.5", "GCMC-10", "GCMC18-7.5", "GCMC18-10", "BUSH-1", "S1KHFLEP"})
        approved_gc = [item for item in result.codes if item.code.startswith("GCMC")]
        self.assertEqual(len(approved_gc), 4)
        self.assertTrue(all(item.active for item in approved_gc))
        self.assertEqual(sum(alias.value == "GCMC" for alias in result.aliases), 4)
        self.assertTrue(any(item.legacy_spares_only for item in result.codes))
        self.assertTrue(any(item.issue_type == "blank_description" for item in result.issues))
        self.assertEqual(approved_gc_code("GCMC", "GC18", "10 ton"), "GCMC18-10")
        self.assertEqual(result.models[0].name, "")
        self.assertEqual(result.codes[0].description, "7.5 ton")

    def test_duplicate_catalog_code_is_inactive_and_cannot_be_associated(self) -> None:
        result = import_catalog_rows([
            ["Product", "Product Model Number", "Product Model Code", "Description"],
            ["Mini Crane", "JK Mini 150", "JK150M", "Local electric"],
            ["Mini Crane", "JK Mini 150", "JK150M", "Duplicate row"],
        ], source_file="catalog.ods")
        code = result.codes[0]

        self.assertFalse(code.active)
        self.assertEqual(sum(issue.issue_type == "duplicate_catalog_code" for issue in result.issues), 1)
        repo = InMemorySafariRepository()
        for product in result.products:
            repo.add_product(product)
        for model in result.models:
            repo.add_model(model)
        for item in result.codes:
            repo.add_code(item)
        repo.issues.update({issue.id: issue for issue in result.issues})
        repo.add_file(CostingFile("file-1", "pilot.ods", "pilot.ods", "pilot.ods", ".ods", 10, "now", "hash"))

        validation = repo.validate_association(
            AssociationProposal("file-1", result.products[0].id, result.models[0].id, (code.id,), "tester"),
            current_file_hash="hash",
        )
        self.assertFalse(validation.valid)
        self.assertIn("UNRESOLVED_CATALOG_CODE", {item.code for item in validation.errors})

    def test_real_catalog_title_row_and_product_model_header_are_supported(self) -> None:
        result = import_catalog_rows([
            ["Product → Product Model Number → Product Model Code"],
            ["Product", "Product Model", "Product Model Code", "Description"],
            ["JK Mini Crane", "JK Mini 150", "JK150M", ""],
        ], source_file="Product-ProductModelNo-ModelCode.ods")
        self.assertEqual(len(result.products), 1)
        self.assertEqual(result.models[0].model_number, "JK Mini 150")
        self.assertEqual(result.codes[0].code, "JK150M")

    def test_catalog_import_plan_is_idempotent_and_dry_run_by_default(self) -> None:
        result = import_catalog_rows([["Product", "Model", "Code", "Description"], ["P", "M", "C", "D"]], source_file="catalog.ods")
        repo = InMemorySafariRepository()
        plan = plan_catalog_import(repo, result)
        self.assertEqual(plan.import_batch.status, "planned"); self.assertFalse(repo.products)
        applied = plan_catalog_import(repo, result, apply=True)
        self.assertEqual(applied.import_batch.status, "applied"); self.assertEqual(len(repo.products), 1)
        retry = plan_catalog_import(repo, result, apply=True)
        self.assertTrue(retry.idempotent); self.assertEqual(len(repo.products), 1)

    def test_file_model_and_code_cardinality_and_idempotency(self) -> None:
        repo = InMemorySafariRepository()
        repo.add_product(Product("p1", "Mini Crane")); repo.add_model(ProductModel("m1", "p1", "S1KHF"))
        repo.add_code(ProductModelCode("c1", "m1", "S1KHF-A")); repo.add_code(ProductModelCode("c2", "m1", "S1KHF-B"))
        repo.add_file(CostingFile("f1", "one.ods", "one.ods", "one.ods", ".ods", 1, "now", "hash"))
        repo.add_file(CostingFile("f2", "two.ods", "two.ods", "two.ods", ".ods", 1, "now", "hash2"))
        first = AssociationProposal("f1", "p1", "m1", ("c1",), "tester", idempotency_key="same")
        saved = repo.save_association(first, current_file_hash="hash")
        self.assertTrue(repo.save_association(first, current_file_hash="hash").idempotent)
        with self.assertRaises(AssociationConflict) as reused_key:
            repo.save_association(AssociationProposal("f1", "p1", "m1", ("c2",), "tester", idempotency_key="same"), current_file_hash="hash")
        self.assertEqual(reused_key.exception.validation.errors[0].code, "IDEMPOTENCY_KEY_REUSED")
        with self.assertRaises(AssociationConflict) as conflict:
            repo.save_association(AssociationProposal("f2", "p1", "m1", ("c1",), "tester"), current_file_hash="hash2")
        self.assertEqual(conflict.exception.validation.errors[0].code, "CODE_ALREADY_ASSOCIATED")
        change = AssociationProposal("f1", "p1", "m1", ("c2",), "tester", reason="refresh", supersede=True)
        repo.save_association(change, current_file_hash="hash")
        self.assertEqual(len(repo.association_history("f1")), 2)
        self.assertEqual(repo.association_history("f1")[0]["codes"][0]["code"], "S1KHF-A")
        self.assertFalse(repo.current_code_owner("c1"))
        self.assertEqual(repo.current_code_owner("c2").file_id, "f1")

    def test_changing_active_codes_requires_explicit_supersede_reason(self) -> None:
        repo = InMemorySafariRepository()
        repo.add_product(Product("p1", "Product"))
        repo.add_model(ProductModel("m1", "p1", "Model"))
        repo.add_code(ProductModelCode("c1", "m1", "Code 1"))
        repo.add_code(ProductModelCode("c2", "m1", "Code 2"))
        repo.add_file(CostingFile("f1", "one.ods", "one.ods", "one.ods", ".ods", 1, "now", "hash"))
        repo.save_association(AssociationProposal("f1", "p1", "m1", ("c1",), "tester", idempotency_key="first"), current_file_hash="hash")

        unapproved = repo.validate_association(AssociationProposal("f1", "p1", "m1", ("c2",), "tester"), current_file_hash="hash")
        no_reason = repo.validate_association(AssociationProposal("f1", "p1", "m1", ("c2",), "tester", supersede=True), current_file_hash="hash")

        self.assertFalse(unapproved.valid)
        self.assertIn("ASSOCIATION_CHANGE_REQUIRES_SUPERSEDE", {item.code for item in unapproved.errors})
        self.assertFalse(no_reason.valid)
        self.assertIn("REASON_REQUIRED", {item.code for item in no_reason.errors})

    def test_cross_product_legacy_and_changed_file_are_structured_errors(self) -> None:
        repo = InMemorySafariRepository()
        repo.add_product(Product("p1", "One")); repo.add_product(Product("p2", "Two"))
        repo.add_model(ProductModel("m1", "p1", "M1")); repo.add_model(ProductModel("m2", "p2", "M2"))
        repo.add_code(ProductModelCode("c1", "m1", "C1")); repo.add_code(ProductModelCode("legacy", "m1", "BUSH", legacy_spares_only=True))
        repo.add_file(CostingFile("f1", "one.ods", "one.ods", "one.ods", ".ods", 1, "now", "old"))
        result = repo.validate_association(AssociationProposal("f1", "p2", "m2", ("c1",), "tester"), current_file_hash="new")
        legacy = repo.validate_association(AssociationProposal("f1", "p1", "m1", ("legacy",), "tester"), current_file_hash="new")
        self.assertFalse(result.valid); self.assertFalse(legacy.valid)
        self.assertEqual({item.code for item in result.errors}, {"CODE_NOT_IN_MODEL", "FILE_CHANGED_SINCE_PREVIEW"})
        self.assertEqual({item.code for item in legacy.errors}, {"LEGACY_SPARES_ONLY", "FILE_CHANGED_SINCE_PREVIEW"})

    def test_schema_guard_rejects_legacy_target(self) -> None:
        with self.assertRaises(GristValidationError):
            validate_schema_target("legacy", document_name="Safari Manufacturing", legacy_doc_id="legacy")

    def test_safari_client_revalidates_explicit_remote_target(self) -> None:
        env = {
            "GRIST_API_KEY": "test-key",
            "GRIST_BASE_URL": "https://grist.test",
            "GRIST_DOC_ID": "costing-new-id",
            "SAFARI_MANUFACTURING_GRIST_DOC_ID": "safari-doc",
            "SAFARI_MANUFACTURING_GRIST_WORKSPACE_ID": "ws1",
        }
        with patch.dict(os.environ, env, clear=False):
            with patch("app.grist.GristAdminClient") as admin_type:
                admin = admin_type.return_value
                admin.discover_writable_workspaces.return_value = [GristWorkspace("ws1", "ERP", "org1", True)]
                admin.get_document.return_value = GristDocument("safari-doc", "Safari Manufacturing", "ws1")
                client = GristClient.from_safari_environment()
                client.validate_safari_write_target()

        self.assertEqual(admin.get_document.call_count, 2)
        self.assertEqual(admin.discover_writable_workspaces.call_count, 2)

    def test_safari_client_rejects_remote_identity_mismatch(self) -> None:
        env = {
            "GRIST_API_KEY": "test-key",
            "GRIST_BASE_URL": "https://grist.test",
            "GRIST_DOC_ID": "costing-new-id",
            "SAFARI_MANUFACTURING_GRIST_DOC_ID": "safari-doc",
            "SAFARI_MANUFACTURING_GRIST_WORKSPACE_ID": "ws1",
        }
        with patch.dict(os.environ, env, clear=False):
            with patch("app.grist.GristAdminClient") as admin_type:
                admin = admin_type.return_value
                admin.discover_writable_workspaces.return_value = [GristWorkspace("ws1", "ERP", "org1", True)]
                admin.get_document.return_value = GristDocument("safari-doc", "Costing-New", "ws1")
                with self.assertRaises(GristValidationError):
                    GristClient.from_safari_environment()

    def test_schema_plan_is_read_only_and_carries_full_target_identity(self) -> None:
        client = _SchemaClient()
        plan = plan_schema(client, "safari-doc", workspace_id="ws1")
        self.assertEqual(len(plan.create_tables), len(FOUNDATION_TABLES))
        self.assertEqual(plan.to_dict()["documentId"], "safari-doc")
        self.assertEqual(plan.to_dict()["workspaceId"], "ws1")
        self.assertEqual(client.writes, [])
        self.assertEqual(client.calls[:2], [("get_document", "safari-doc"), ("list_tables", "safari-doc")])

    def test_schema_apply_revalidates_remote_identity_before_writes(self) -> None:
        client = _SchemaClient()
        plan = plan_schema(client, "safari-doc", workspace_id="ws1")
        client.document = GristDocument("different-doc", "Safari Manufacturing", "ws1")
        with self.assertRaises(GristValidationError):
            apply_schema(client, plan)
        self.assertEqual(client.writes, [])

    def test_schema_apply_writes_only_to_the_validated_document(self) -> None:
        client = _SchemaClient()
        plan = plan_schema(client, "safari-doc", workspace_id="ws1")
        apply_schema(client, plan)
        self.assertEqual(len(client.writes), len(FOUNDATION_TABLES))
        self.assertEqual({write[1] for write in client.writes}, {"safari-doc"})


class _Response:
    def __init__(self, payload, status_code=200): self.payload, self.status_code = payload, status_code
    def json(self): return self.payload


class _Session:
    def __init__(self, responses): self.responses = list(responses); self.calls = []
    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)


class _UncertainSession(_Session):
    def request(self, method, url, **kwargs):
        if method == "POST":
            self.calls.append((method, url, kwargs))
            raise requests.ConnectionError("connection dropped after request")
        return super().request(method, url, **kwargs)


class _SchemaClient:
    def __init__(self):
        self.document = GristDocument("safari-doc", "Safari Manufacturing", "ws1")
        self.calls = []
        self.writes = []

    def get_document(self, document_id):
        self.calls.append(("get_document", document_id))
        return self.document

    def list_tables(self, document_id):
        self.calls.append(("list_tables", document_id))
        return []

    def create_table(self, document_id, table):
        self.writes.append(("create_table", document_id, table["id"]))

    def add_columns(self, document_id, table_id, columns):
        self.writes.append(("add_columns", document_id, table_id))

    def update_columns(self, document_id, table_id, columns):
        self.writes.append(("update_columns", document_id, table_id))


class GristAdminTests(unittest.TestCase):
    def test_workspace_discovery_and_exact_name_duplicate_refusal(self) -> None:
        session = _Session([
            _Response({"orgs": [{"id": "org1", "name": "Safari"}]}),
            _Response({"workspaces": [{"id": "ws1", "name": "ERP", "isWritable": True}]}),
            _Response({"docs": [{"id": "d1", "name": "Safari Manufacturing"}, {"id": "d2", "name": "Safari Manufacturing"}]}),
        ])
        client = GristAdminClient("secret", "https://grist.test", session=session)
        with self.assertRaises(GristDuplicateNameError):
            ensure_safari_document(client, workspace_id="ws1")

    def test_permission_error_and_uncertain_create_retry_are_safe(self) -> None:
        responses = [_Response({"orgs": [{"id": "org1"}]}), _Response({"workspaces": [{"id": "ws1", "isWritable": True}]}), _Response({"docs": []}), _Response({}, 403)]
        with self.assertRaises(GristPermissionError):
            ensure_safari_document(GristAdminClient("secret", "https://grist.test", session=_Session(responses)), workspace_id="ws1", apply=True)
        session = _UncertainSession([_Response({"orgs": [{"id": "org1"}]}), _Response({"workspaces": [{"id": "ws1", "isWritable": True}]}), _Response({"docs": []})])
        result = ensure_safari_document(GristAdminClient("secret", "https://grist.test", session=session), workspace_id="ws1", apply=True, on_uncertain_create=lambda: [GristDocument("new-doc", "Safari Manufacturing", "ws1", "org1")])
        self.assertEqual(result.action, "reused-after-uncertain-create")

        duplicate_session = _UncertainSession([_Response({"orgs": [{"id": "org1"}]}), _Response({"workspaces": [{"id": "ws1", "isWritable": True}]}), _Response({"docs": []})])
        duplicates = lambda: [GristDocument("new-1", "Safari Manufacturing", "ws1"), GristDocument("new-2", "Safari Manufacturing", "ws1")]
        with self.assertRaises(GristDuplicateNameError):
            ensure_safari_document(GristAdminClient("secret", "https://grist.test", session=duplicate_session), workspace_id="ws1", apply=True, on_uncertain_create=duplicates)

    def test_create_document_accepts_grist_string_id_response(self) -> None:
        class CreateSession:
            def request(self, method, url, **kwargs):
                return _Response("new-doc-id")

        document = GristAdminClient("secret", "https://grist.test", session=CreateSession()).create_document("ws1")
        self.assertEqual(document.id, "new-doc-id")
        self.assertEqual(document.name, "Safari Manufacturing")
        self.assertEqual(document.workspace_id, "ws1")

    def test_created_document_is_fetched_and_validated_from_remote_metadata(self) -> None:
        session = _Session([
            _Response({"orgs": [{"id": "org1", "name": "Safari"}]}),
            _Response({"workspaces": [{"id": "ws1", "name": "ERP", "isWritable": True}]}),
            _Response({"docs": []}),
            _Response("created-doc"),
            _Response({"id": "created-doc", "name": "Safari Manufacturing", "workspace": {"id": "ws1"}}),
        ])
        result = ensure_safari_document(GristAdminClient("secret", "https://grist.test", session=session), workspace_id="ws1", apply=True)
        self.assertEqual(result.action, "created")
        self.assertEqual(result.document, GristDocument("created-doc", "Safari Manufacturing", "ws1"))
        self.assertEqual(session.calls[-1][1], "https://grist.test/api/docs/created-doc")

    def test_created_document_with_wrong_workspace_is_rejected(self) -> None:
        session = _Session([
            _Response({"orgs": [{"id": "org1", "name": "Safari"}]}),
            _Response({"workspaces": [{"id": "ws1", "name": "ERP", "isWritable": True}]}),
            _Response({"docs": []}),
            _Response("created-doc"),
            _Response({"id": "created-doc", "name": "Safari Manufacturing", "workspace": {"id": "other-workspace"}}),
        ])
        with self.assertRaises(GristValidationError):
            ensure_safari_document(GristAdminClient("secret", "https://grist.test", session=session), workspace_id="ws1", apply=True)

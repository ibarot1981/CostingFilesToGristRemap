import os
import tempfile
import unittest
from collections import OrderedDict
from pathlib import Path
from unittest import mock
from zipfile import ZipFile

from fastapi import HTTPException
from pyexcel_ods3 import save_data
from starlette.requests import Request

from app.web import _is_external_cache_sheet


class WebTests(unittest.TestCase):
    def test_association_save_http_contract_declares_idempotency_header(self) -> None:
        import app.web as web

        contract = web.app.openapi()["paths"]["/api/associations"]["post"]
        headers = {
            parameter["name"]: parameter
            for parameter in contract.get("parameters", [])
            if parameter.get("in") == "header"
        }

        self.assertIn("Idempotency-Key", headers)
        self.assertFalse(headers["Idempotency-Key"].get("required", False))
        self.assertIn("requestBody", contract)

    def test_memory_adapter_does_not_claim_live_grist_writes(self) -> None:
        import app.web as web
        from app.repository import InMemorySafariRepository
        from starlette.requests import Request

        with mock.patch.object(web, "_repository", InMemorySafariRepository()):
            health = web.health(Request({"type": "http", "headers": []}))
            associations = web.associations()

        self.assertFalse(health["writeEnabled"])
        self.assertFalse(associations["writeEnabled"])
        self.assertFalse(associations["schemaAvailable"])
        self.assertEqual(associations["adapter"], "in-memory")

    def test_model_codes_separate_available_conflict_and_legacy_records(self) -> None:
        import app.web as web
        from app.domain import CostingFile, FileCodeAssociation, Product, ProductModel, ProductModelCode
        from app.repository import InMemorySafariRepository

        repository = InMemorySafariRepository()
        repository.add_product(Product("p1", "Product"))
        repository.add_model(ProductModel("m1", "p1", "MODEL"))
        repository.add_code(ProductModelCode("available", "m1", "AVAILABLE"))
        repository.add_code(ProductModelCode("occupied", "m1", "OCCUPIED"))
        repository.add_code(ProductModelCode("legacy", "m1", "LEGACY", legacy_spares_only=True))
        repository.add_file(CostingFile("file:owner.ods", "owner.ods", "owner.ods", "owner.ods", ".ods", 1, "2026-09-01T00:00:00+00:00"))
        repository.code_associations["owner-link"] = FileCodeAssociation(
            "owner-link", "owner-association", "file:owner.ods", "occupied", "owner", "2026-09-01T00:00:00+00:00"
        )

        with mock.patch.object(web, "_repository", repository):
            result = web.model_codes("m1")

        self.assertEqual({item["id"] for item in result["active"]}, {"available", "occupied"})
        self.assertEqual([item["id"] for item in result["available"]], ["available"])
        self.assertEqual([item["id"] for item in result["legacy"]], ["legacy"])
        self.assertEqual([item["id"] for item in result["conflicts"]], ["occupied"])
        self.assertEqual(result["conflicts"][0]["owner"]["relativePath"], "owner.ods")

    def test_grist_adapter_does_not_seed_duplicate_identities_from_local_ods(self) -> None:
        import app.web as web
        from app.domain import Product
        from app.repository import InMemorySafariRepository

        class FakeGristRepository(InMemorySafariRepository):
            adapter_name = "grist-safari"

        repository = FakeGristRepository()
        repository.add_product(Product("1", "Remote Product"))
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "catalog.ods"
            source.write_bytes(b"read-only catalog sentinel")
            with mock.patch.dict(os.environ, {"SAFARI_REPOSITORY": "grist", "CATALOG_ODS_PATH": str(source), "COSTING_ROOT": directory}, clear=False):
                with mock.patch.object(web, "_repository", None):
                    with mock.patch("app.grist_repository.GristSafariRepository", return_value=repository):
                        with mock.patch.object(web, "import_catalog") as import_local_catalog:
                            selected = web._get_repository()

        self.assertIs(selected, repository)
        self.assertEqual([item.id for item in selected.list_products()], ["1"])
        import_local_catalog.assert_not_called()

    def test_association_audit_actor_comes_from_authenticated_request_headers(self) -> None:
        import app.web as web
        from app.domain import Product, ProductModel, ProductModelCode
        from app.repository import InMemorySafariRepository
        from starlette.requests import Request

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            save_data(str(root / "model.ods"), OrderedDict([("Summary", [["A"]])]))
            repository = InMemorySafariRepository()
            repository.add_product(Product("p1", "Product"))
            repository.add_model(ProductModel("m1", "p1", "MODEL"))
            repository.add_code(ProductModelCode("c1", "m1", "CODE"))
            request = Request({"type": "http", "headers": [(b"x-authentik-uid", b"user-123"), (b"x-authentik-username", b"irshad")]})
            with mock.patch.dict(os.environ, {"COSTING_ROOT": str(root), "SAFARI_REPOSITORY": "memory"}, clear=False):
                with mock.patch.object(web, "_repository", repository):
                    file_id = web._register_file("model.ods")
                    result = web.save_association(request, {"fileId": file_id, "productId": "p1", "modelId": "m1", "codeIds": ["c1"], "actor": "spoofed-user"}, idempotency_key="actor-request")

        self.assertEqual(result["auditEvent"]["actor"], "irshad")

    def test_external_cache_sheets_are_hidden(self) -> None:
        self.assertTrue(
            _is_external_cache_sheet(
                "'file:///C:/Irshad/Products%20Costing/Template.ods'#Total Summary"
            )
        )
        self.assertTrue(_is_external_cache_sheet("https://example.test/rates.ods#Sheet1"))
        self.assertFalse(_is_external_cache_sheet("Total Summary"))

    def test_preview_is_ods_only_and_bounded(self) -> None:
        import app.web as web

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            save_data(str(root / "bounded.ods"), OrderedDict([("Summary", [["A", "B", "C"]] + [[row, row + 1, row + 2] for row in range(20)])]))
            (root / "notes.txt").write_text("not a workbook", encoding="utf-8")
            with mock.patch.dict(os.environ, {"COSTING_ROOT": str(root)}, clear=False):
                result = web.preview("bounded.ods", row_count=2, column_count=1)
                with self.assertRaises(HTTPException) as unsupported:
                    web.preview("notes.txt")
            self.assertEqual(len(result["rows"]), 2)
            self.assertEqual(result["totalRows"], 21)
            self.assertTrue(result["truncatedRows"])
            self.assertTrue(result["truncatedColumns"])
            self.assertEqual(unsupported.exception.status_code, 415)
            self.assertEqual(unsupported.exception.detail["code"], "UNSUPPORTED_EXTENSION")

    def test_preview_distinguishes_formula_and_value_and_warns_on_external_link(self) -> None:
        import app.web as web

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workbook_path = root / "formula.ods"
            save_data(str(workbook_path), OrderedDict([("Summary", [["Formula", "Value"], [3, 7]])]))
            with ZipFile(workbook_path) as source:
                entries = [(item.filename, source.read(item.filename)) for item in source.infolist()]

            formula_cell = b'<table:table-cell office:value="3" office:value-type="float"/>'
            formula_value = (
                b'<table:table-cell table:formula="of:=SUM([file:///C:/rates.ods#Sheet1.A1])" '
                b'office:value="3" office:value-type="float"/>'
            )
            self.assertTrue(any(name == "content.xml" and formula_cell in data for name, data in entries))
            with ZipFile(workbook_path, "w") as target:
                for name, data in entries:
                    if name == "content.xml":
                        data = data.replace(formula_cell, formula_value, 1)
                    target.writestr(name, data)

            with mock.patch.dict(os.environ, {"COSTING_ROOT": str(root)}, clear=False):
                result = web.preview("formula.ods")

        self.assertEqual(result["sheets"], ["Summary"])
        self.assertEqual((result["totalRows"], result["totalColumns"]), (2, 2))
        self.assertTrue(result["readOnly"])
        self.assertEqual(result["formulaCount"], 1)
        self.assertEqual(result["externalReferenceCount"], 1)
        self.assertTrue(result["externalLinkWarning"])
        formula, value = result["cells"][1]
        self.assertEqual((formula["value"], formula["kind"]), (3, "formula"))
        self.assertEqual(formula["formula"], "of:=SUM([file:///C:/rates.ods#Sheet1.A1])")
        self.assertEqual((value["value"], value["kind"], value["formula"]), (7, "value", None))

    def test_corrupt_and_encrypted_ods_return_typed_errors(self) -> None:
        import app.web as web

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "corrupt.ods").write_text("not an ods", encoding="utf-8")
            with ZipFile(root / "encrypted.ods", "w") as package:
                package.writestr("mimetype", "application/vnd.oasis.opendocument.spreadsheet")
                package.writestr("encrypted-package", b"encrypted")
            with mock.patch.dict(os.environ, {"COSTING_ROOT": str(root)}, clear=False):
                with self.assertRaises(HTTPException) as corrupt:
                    web.preview("corrupt.ods")
                with self.assertRaises(HTTPException) as encrypted:
                    web.preview("encrypted.ods")
            self.assertEqual(corrupt.exception.detail["code"], "ODS_PARSE_ERROR")
            self.assertEqual(encrypted.exception.detail["code"], "ODS_ENCRYPTED")

    def test_preview_rejects_traversal_with_machine_code(self) -> None:
        import app.web as web

        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.dict(os.environ, {"COSTING_ROOT": directory}, clear=False):
                with self.assertRaises(HTTPException) as error:
                    web.preview("%2e%2e/secret.ods")
            self.assertEqual(error.exception.status_code, 400)
            self.assertEqual(error.exception.detail["code"], "PATH_OUTSIDE_ROOT")

    def test_current_hash_is_used_for_web_association_concurrency(self) -> None:
        import app.web as web
        from app.domain import CostingFile, Product, ProductModel, ProductModelCode
        from app.repository import InMemorySafariRepository

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workbook = root / "model.ods"
            save_data(str(workbook), OrderedDict([("Summary", [["A"]])]))
            repository = InMemorySafariRepository()
            repository.add_product(Product("p1", "Product"))
            repository.add_model(ProductModel("m1", "p1", "MODEL"))
            repository.add_code(ProductModelCode("c1", "m1", "CODE"))
            with mock.patch.dict(os.environ, {"COSTING_ROOT": str(root), "SAFARI_REPOSITORY": "memory"}, clear=False):
                with mock.patch.object(web, "_repository", repository):
                    file_id = web._register_file("model.ods")
                    expected_hash = repository.get_file(file_id).file_hash
                    validation = web.validate_association({"fileId": file_id, "productId": "p1", "modelId": "m1", "codeIds": ["c1"], "expectedHash": expected_hash})
                    self.assertTrue(validation["valid"])
                    self.assertEqual(validation["proposal"]["expectedVersion"], 0)
                    self.assertEqual(validation["proposal"]["expectedHash"], expected_hash)
                    workbook.write_bytes(workbook.read_bytes() + b"changed")
                    with self.assertRaises(HTTPException) as error:
                        web.save_association(Request({"type": "http", "headers": []}), {"fileId": file_id, "productId": "p1", "modelId": "m1", "codeIds": ["c1"], "expectedHash": expected_hash}, idempotency_key="changed-file")
            self.assertEqual(error.exception.status_code, 409)
            self.assertEqual(error.exception.detail["code"], "ASSOCIATION_CONFLICT")
            self.assertEqual(error.exception.detail["validation"]["errors"][0]["code"], "FILE_CHANGED_SINCE_PREVIEW")

    def test_register_file_refreshes_metadata_and_preserves_mapping_state(self) -> None:
        import app.web as web
        from app.domain import CostingFile
        from app.repository import InMemorySafariRepository

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workbook = root / "model.ods"
            save_data(str(workbook), OrderedDict([("Summary", [["A"]])]))
            repository = InMemorySafariRepository()
            repository.add_file(CostingFile("file:model.ods", "model.ods", "model.ods", "model.ods", ".ods", 1, "2020-01-01T00:00:00+00:00", "stale-hash", product_id="product-1", mapping_status="mapped"))
            with mock.patch.dict(os.environ, {"COSTING_ROOT": str(root), "SAFARI_REPOSITORY": "memory"}, clear=False):
                with mock.patch.object(web, "_repository", repository):
                    file_id = web._register_file("model.ods")
                    current = repository.get_file(file_id)
                    expected_hash = web.sha256_file(workbook)
                    expected_size = workbook.stat().st_size

        self.assertEqual(current.file_hash, expected_hash)
        self.assertEqual(current.size_bytes, expected_size)
        self.assertEqual(current.mapping_status, "mapped")
        self.assertEqual(current.product_id, "product-1")

    def test_mapped_files_includes_unregistered_ods_nodes(self) -> None:
        import app.web as web
        from app.repository import InMemorySafariRepository

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            save_data(str(root / "unmapped.ods"), OrderedDict([("Summary", [["A"]])]))
            with mock.patch.dict(os.environ, {"COSTING_ROOT": str(root), "SAFARI_REPOSITORY": "memory"}, clear=False):
                with mock.patch.object(web, "_repository", InMemorySafariRepository()):
                    result = web.mapped_files(status="unmapped")
            self.assertEqual(result["total"], 1)
            self.assertEqual(result["items"][0]["file"]["relative_path"], "unmapped.ods")

    def test_mapped_files_and_processing_queue_project_persisted_association_batch(self) -> None:
        import app.web as web
        from app.domain import CostingFile, FileModelAssociation, ImportBatch, Product, ProductModel
        from app.repository import InMemorySafariRepository

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            save_data(str(root / "model.ods"), OrderedDict([("Summary", [["A"]])]))
            repository = InMemorySafariRepository()
            repository.add_product(Product("p1", "Product"))
            repository.add_model(ProductModel("m1", "p1", "MODEL"))
            repository.add_file(CostingFile("file:model.ods", "model.ods", "model.ods", "model.ods", ".ods", 1, "2026-09-01T00:00:00+00:00"))
            repository.associations["a1"] = FileModelAssociation("a1", "file:model.ods", "p1", "m1", "tester", "", "2026-09-01T00:00:00+00:00")
            repository.import_batches["b1"] = ImportBatch("b1", "model.ods", "hash", "association-0.1", "2026-09-01T00:01:00+00:00", "2026-09-01T00:01:00+00:00", "queued", "read-only processing queued")
            repository.import_batches["b2"] = ImportBatch("b2", "catalog.ods", "hash2", "catalog-0.2", "2026-09-01T00:02:00+00:00", "2026-09-01T00:02:00+00:00", "applied", "catalog imported")

            with mock.patch.dict(os.environ, {"COSTING_ROOT": str(root), "SAFARI_REPOSITORY": "memory"}, clear=False):
                with mock.patch.object(web, "_repository", repository):
                    mapped = web.mapped_files(status="mapped")
                    queue = web.processing_queue(status="queued", limit=100)

        self.assertEqual(mapped["items"][0]["processingBatch"]["id"], "b1")
        self.assertEqual(mapped["items"][0]["processingBatch"]["status"], "queued")
        self.assertEqual(queue["total"], 1)
        self.assertEqual(queue["items"][0]["source_file"], "model.ods")

    def test_recursive_catalog_search_returns_nested_metadata_without_inspection(self) -> None:
        import app.web as web
        from app.filesystem_catalog import FilesystemCatalog

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            nested = root / "S1KHF" / "Local"
            nested.mkdir(parents=True)
            save_data(str(nested / "deep-only.ods"), OrderedDict([("Summary", [["A"]])]))
            with mock.patch.dict(os.environ, {"COSTING_ROOT": str(root)}, clear=False):
                with mock.patch.object(FilesystemCatalog, "inspect", side_effect=AssertionError("search must not inspect workbooks")):
                    result = web.catalog_files(query="deep-only", offset=0, limit=20)

        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["relative_path"], "S1KHF/Local/deep-only.ods")
        self.assertIsNone(result["items"][0]["content_hash"])

    def test_catalog_tree_and_status_filter_surface_duplicate_code_owners_without_inspection(self) -> None:
        import app.web as web
        from app.domain import CostingFile, FileCodeAssociation
        from app.filesystem_catalog import FilesystemCatalog
        from app.repository import InMemorySafariRepository

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("one.ods", "two.ods"):
                save_data(str(root / name), OrderedDict([("Summary", [[name]])]))
            repository = InMemorySafariRepository()
            for name in ("one.ods", "two.ods"):
                file_id = f"file:{name}"
                repository.add_file(CostingFile(file_id, name, name, name, ".ods", 1, "2000-01-01T00:00:00+00:00", mapping_status="mapped"))
            repository.code_associations["link-one"] = FileCodeAssociation("link-one", "association-one", "file:one.ods", "code-shared", "tester", "2026-09-01T00:00:00+00:00")
            repository.code_associations["link-two"] = FileCodeAssociation("link-two", "association-two", "file:two.ods", "code-shared", "tester", "2026-09-01T00:00:00+00:00")

            with mock.patch.dict(os.environ, {"COSTING_ROOT": str(root), "SAFARI_REPOSITORY": "memory"}, clear=False):
                with mock.patch.object(web, "_repository", repository):
                    with mock.patch.object(FilesystemCatalog, "inspect", side_effect=AssertionError("status projection must not inspect workbooks")):
                        tree = web.catalog_tree()
                        conflicts = web.catalog_files(status="conflict", extension=".ods", offset=0, limit=20)

        self.assertEqual({item["mapping_status"] for item in tree["items"]}, {"conflict"})
        self.assertEqual({item["mapping_status"] for item in conflicts["items"]}, {"conflict"})
        self.assertEqual(conflicts["total"], 2)

    def test_mapped_files_surfaces_duplicate_owners_changes_and_observations(self) -> None:
        import app.web as web
        from app.domain import FileCodeAssociation, FileModelAssociation, FileObservation, Product, ProductModel, ProductModelCode, CostingFile
        from app.repository import InMemorySafariRepository

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            save_data(str(root / "one.ods"), OrderedDict([("Summary", [["A"]])]))
            save_data(str(root / "two.ods"), OrderedDict([("Summary", [["B"]])]))
            repository = InMemorySafariRepository()
            repository.add_product(Product("p1", "Product One"))
            repository.add_model(ProductModel("m1", "p1", "MODEL-1"))
            repository.add_code(ProductModelCode("c1", "m1", "CODE-1"))
            for file_id, relative_path in (("file:one.ods", "one.ods"), ("file:two.ods", "two.ods")):
                repository.add_file(CostingFile(file_id, relative_path, relative_path, relative_path, ".ods", 1, "2000-01-01T00:00:00+00:00"))
            repository.associations["a1"] = FileModelAssociation("a1", "file:one.ods", "p1", "m1", "tester", "", "2026-09-01T00:00:00+00:00")
            repository.associations["a2"] = FileModelAssociation("a2", "file:two.ods", "missing-product", "m1", "tester", "", "2026-09-01T00:00:00+00:00")
            repository.code_associations["c-a1"] = FileCodeAssociation("c-a1", "a1", "file:one.ods", "c1", "tester", "2026-09-01T00:00:00+00:00")
            repository.code_associations["c-a2"] = FileCodeAssociation("c-a2", "a2", "file:two.ods", "c1", "tester", "2026-09-01T00:00:00+00:00")
            repository.add_observation(FileObservation("o1", "file:one.ods", "2026-09-10T12:00:00+00:00", "one.ods", "one.ods", 1, "2000-01-01T00:00:00+00:00", readable=True, external_reference_count=3))

            with mock.patch.dict(os.environ, {"COSTING_ROOT": str(root), "SAFARI_REPOSITORY": "memory"}, clear=False):
                with mock.patch.object(web, "_repository", repository):
                    result = web.mapped_files()
                    conflicts = web.mapped_files(status="conflict")
                    needs_review = web.mapped_files(status="needs-review")

        rows = {item["file"]["relative_path"]: item for item in result["items"]}
        first = rows["one.ods"]
        self.assertEqual(first["reviewStatus"], "conflict")
        self.assertIn("duplicate_code_ownership", first["issues"])
        self.assertIn("changed_file", first["issues"])
        self.assertIn("external_links", first["issues"])
        self.assertEqual(first["lastObservedAt"], "2026-09-10T12:00:00+00:00")
        self.assertEqual(first["lastObservedChange"]["source"], "observation")
        self.assertIn("catalog_mismatch", rows["two.ods"]["issues"])
        self.assertEqual(conflicts["total"], 2)
        self.assertEqual(needs_review["total"], 2)

    def test_mapped_files_detects_a_verified_move(self) -> None:
        import app.web as web
        from app.domain import CostingFile, FileModelAssociation, FileObservation, Product, ProductModel
        from app.filesystem_catalog import FilesystemCatalog
        from app.repository import InMemorySafariRepository, sha256_file

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workbook = root / "renamed.ods"
            save_data(str(workbook), OrderedDict([("Summary", [["A"]])]))
            node = FilesystemCatalog(root).inspect("renamed.ods")
            repository = InMemorySafariRepository()
            repository.add_product(Product("p1", "Product One"))
            repository.add_model(ProductModel("m1", "p1", "MODEL-1"))
            digest = sha256_file(workbook)
            repository.add_file(CostingFile(
                "file:old.ods", "old.ods", "old.ods", "old.ods", ".ods",
                node.size_bytes or 0, node.modified_at or "", file_hash=digest,
            ))
            repository.associations["a1"] = FileModelAssociation(
                "a1", "file:old.ods", "p1", "m1", "tester", "", "2026-09-01T00:00:00+00:00",
            )
            repository.add_observation(FileObservation(
                "o1", "file:old.ods", "2026-09-10T12:00:00+00:00", "old.ods", "old.ods",
                node.size_bytes or 0, node.modified_at or "", file_hash=digest, readable=True,
            ))

            with mock.patch.dict(os.environ, {"COSTING_ROOT": str(root), "SAFARI_REPOSITORY": "memory"}, clear=False):
                with mock.patch.object(web, "_repository", repository):
                    result = web.mapped_files()

        moved = next(item for item in result["items"] if item["file"]["relative_path"] == "old.ods")
        self.assertIn("moved_file", moved["issues"])
        self.assertEqual(moved["movedTo"], "renamed.ods")


if __name__ == "__main__":
    unittest.main()

import csv
import io
import json
import os
import unittest
import tempfile
from collections import OrderedDict
from pathlib import Path
from unittest import mock

from fastapi import HTTPException
from pyexcel_ods3 import save_data
from starlette.requests import Request

from app.catalog_import import import_catalog_rows
from app.domain import CostingFile, FileObservation, IdentityAlias, Product, ProductModel, ProductModelCode, ReconciliationIssue
from app.repository import AssociationProposal, GovernanceConflict, InMemorySafariRepository, normalize_relative_directory


class ReconciliationTests(unittest.TestCase):
    def make_identity_repository(self):
        repository = InMemorySafariRepository()
        repository.add_product(Product("p1", "Safari"))
        repository.add_model(ProductModel("m1", "p1", "Model A"))
        repository.add_code(ProductModelCode("c1", "m1", "CODE-A"))
        return repository

    def test_issue_fingerprint_is_idempotent_updates_last_seen_and_does_not_disappear_silently(self):
        repository = self.make_identity_repository()
        first = repository.upsert_issue(ReconciliationIssue("scan-1", "missing_file", "error", "File is missing", entity_type="CostingFile", entity_id="f1", costing_file_id="f1", source_path="A.ods", fingerprint="same"))
        second = repository.upsert_issue(ReconciliationIssue("scan-2", "missing_file", "error", "File is missing", entity_type="CostingFile", entity_id="f1", costing_file_id="f1", source_path="A.ods", fingerprint="same"))
        self.assertEqual(first.id, second.id)
        self.assertEqual(len(repository.issues), 1)
        self.assertGreaterEqual(second.last_seen_at, first.last_seen_at)
        self.assertEqual(second.status, "open")

    def test_issue_lifecycle_requires_reasons_audits_actor_and_rejects_stale_versions(self):
        repository = self.make_identity_repository()
        issue = repository.upsert_issue(ReconciliationIssue("i1", "changed_file", "high", "Changed", fingerprint="issue-1"))
        with self.assertRaises(GovernanceConflict) as missing_reason:
            repository.mutate_issue(issue.id, "defer", actor="owner", expected_version=issue.version)
        self.assertEqual(missing_reason.exception.code, "REASON_REQUIRED")
        deferred = repository.mutate_issue(issue.id, "defer", actor="owner", reason="Waiting for source owner", expected_version=issue.version)
        self.assertEqual((deferred.status, deferred.deferred_actor), ("deferred", "owner"))
        self.assertEqual(next(iter(repository.audit_events.values())).reason, "Waiting for source owner")
        with self.assertRaises(GovernanceConflict) as stale:
            repository.mutate_issue(issue.id, "reopen", actor="owner", reason="Retry review", expected_version=issue.version)
        self.assertEqual(stale.exception.code, "STALE_ISSUE_VERSION")
        reopened = repository.mutate_issue(issue.id, "reopen", actor="owner", reason="Source changed again", expected_version=deferred.version)
        self.assertEqual(reopened.status, "reopened")
        self.assertEqual(len(repository.audit_events), 2)

    def test_invalid_identity_keeps_en_dash_and_rejects_replacement_character(self):
        imported = import_catalog_rows([
            ["Product", "Product Model Number", "Model Code", "Description"],
            ["Safari", "Safari – SSV", "SSV-1", "canonical"],
            ["Safari", "Safari � SSV", "SSV-2", "corrupt"],
        ], source_file="catalog.ods")
        self.assertTrue(any(model.model_number == "Safari – SSV" for model in imported.models))
        self.assertFalse(any("�" in model.model_number for model in imported.models))
        issue = next(item for item in imported.issues if item.issue_type == "invalid_identity_encoding")
        self.assertEqual(issue.severity, "error")
        report = json.dumps(issue.to_dict(), ensure_ascii=False).encode("utf-8").decode("utf-8")
        self.assertIn("�", report)
        self.assertIn("–", "Safari – SSV".encode("utf-8").decode("utf-8"))

    def test_identity_cleanup_proves_no_references_and_supersedes_without_deleting(self):
        repository = InMemorySafariRepository()
        repository.add_product(Product("p1", "Safari"))
        repository.add_model(ProductModel("bad", "p1", "Safari � SSV", source_file="catalog.ods", source_row=172))
        repository.add_model(ProductModel("good", "p1", "Safari – SSV", source_file="catalog.ods", source_row=172))
        issue = repository.upsert_issue(ReconciliationIssue("encoding", "invalid_identity_encoding", "error", "Invalid identity text", entity_type="ProductModel", entity_id="bad", source_file="catalog.ods", source_row=172, fingerprint="encoding:bad"))
        plan = repository.plan_identity_cleanup(["bad"])[0]
        self.assertTrue(plan["canApply"])
        result = repository.apply_identity_cleanup("bad", "good", issue_id=issue.id, actor="Irshad", reason="Remove import corruption", expected_issue_version=issue.version)
        self.assertFalse(repository.models["bad"].active)
        self.assertEqual(repository.models["bad"].superseded_by_id, "good")
        self.assertIn("bad", repository.models)
        self.assertEqual(result["issue"]["status"], "resolved")
        self.assertEqual(result["auditEvent"]["payload"]["canonicalReplacementId"], "good")

    def test_identity_cleanup_refuses_any_model_code_reference_and_actor_policy(self):
        repository = InMemorySafariRepository()
        repository.add_product(Product("p1", "Safari"))
        repository.add_model(ProductModel("bad", "p1", "Safari � SSM"))
        repository.add_model(ProductModel("good", "p1", "Safari – SSM"))
        repository.add_code(ProductModelCode("c1", "bad", "CODE"))
        issue = repository.upsert_issue(ReconciliationIssue("encoding", "invalid_identity_encoding", "error", "Invalid", entity_type="ProductModel", entity_id="bad", fingerprint="encoding:bad"))
        self.assertFalse(repository.plan_identity_cleanup(["bad"])[0]["canApply"])
        with self.assertRaises(GovernanceConflict) as refused:
            repository.apply_identity_cleanup("bad", "good", issue_id=issue.id, actor="someone-else", reason="cleanup", expected_issue_version=issue.version)
        self.assertEqual(refused.exception.code, "APPROVER_NOT_AUTHORIZED")

    def test_identity_cleanup_blocks_alias_references_and_reports_mapping_reference_check(self):
        repository = InMemorySafariRepository()
        repository.add_product(Product("p1", "Safari"))
        repository.add_model(ProductModel("bad", "p1", "Safari � SSV"))
        repository.add_model(ProductModel("good", "p1", "Safari – SSV"))
        repository.aliases["alias:model"] = IdentityAlias("alias:model", "ProductModel", "bad", "old model", "old model")
        repository.upsert_issue(ReconciliationIssue("encoding", "invalid_identity_encoding", "error", "Invalid", entity_type="ProductModel", entity_id="bad", fingerprint="encoding:bad"))
        plan = repository.plan_identity_cleanup(["bad"])[0]
        self.assertFalse(plan["canApply"])
        self.assertEqual(plan["references"]["aliases"], ["alias:model"])
        self.assertEqual(plan["references"]["directoryMappings"], [])

    def test_source_revision_keeps_old_observation_and_rechecks_hash_and_ownership(self):
        repository = self.make_identity_repository()
        file = CostingFile("f1", "A.ods", "a.ods", "A.ods", ".ods", 10, "2026-09-20T00:00:00+00:00", "old", readable=True)
        repository.add_file(file)
        old_observation = FileObservation("obs-old", "f1", "2026-09-20T00:00:00+00:00", "A.ods", "a.ods", 10, "2026-09-20T00:00:00+00:00", "old", True, 4, 0)
        repository.add_observation(old_observation)
        saved = repository.save_association(AssociationProposal("f1", "p1", "m1", ("c1",), "owner"), current_file_hash="old")
        issue = repository.upsert_issue(ReconciliationIssue("changed", "changed_file", "high", "File changed", entity_type="CostingFile", entity_id="f1", costing_file_id="f1", source_path="A.ods", fingerprint="changed:f1"))
        new_observation = FileObservation("obs-new", "f1", "2026-09-23T00:00:00+00:00", "A.ods", "a.ods", 20, "2026-09-23T00:00:00+00:00", "new", True, 5, 2)
        with self.assertRaises(GovernanceConflict) as changed_after_preview:
            repository.accept_file_revision("f1", new_observation, issue_id=issue.id, actor="Irshad", reason="Reviewed source", expected_issue_version=issue.version, expected_stored_hash="old", expected_current_hash="different")
        self.assertEqual(changed_after_preview.exception.code, "STALE_REVISION_FACTS")
        result = repository.accept_file_revision("f1", new_observation, issue_id=issue.id, actor="Irshad", reason="Reviewed source", expected_issue_version=issue.version, expected_stored_hash="old", expected_current_hash="new")
        self.assertEqual(len([item for item in repository.observations if item.file_id == "f1"]), 2)
        self.assertEqual(repository.files["f1"].file_hash, "new")
        self.assertEqual(repository.current_association("f1").id, saved.association.id)
        self.assertEqual(result["auditEvent"]["payload"]["oldHash"], "old")

    def test_directory_mapping_path_safety_inheritance_precedence_and_irshad_approval(self):
        repository = InMemorySafariRepository()
        repository.add_product(Product("p1", "Safari"))
        repository.add_product(Product("p2", "Mini Crane"))
        with self.assertRaises(GovernanceConflict):
            normalize_relative_directory("%2e%2e/private")
        parent = repository.propose_directory_mapping("S1KHF", "p1", inherit=True, actor="proposer")
        with self.assertRaises(GovernanceConflict) as blocked:
            repository.transition_directory_mapping(parent.id, "approve", actor="delegate", reason="approval", expected_version=parent.version)
        self.assertEqual(blocked.exception.code, "APPROVER_NOT_AUTHORIZED")
        parent = repository.transition_directory_mapping(parent.id, "approve", actor="Irshad", reason="pilot family", expected_version=parent.version)
        inherited = repository.effective_directory_mapping("S1KHF/Local/file.ods")
        self.assertTrue(inherited["inherited"])
        child = repository.propose_directory_mapping("S1KHF/Local", "p2", inherit=True, actor="proposer")
        child = repository.transition_directory_mapping(child.id, "approve", actor="Irshad", reason="local subset", expected_version=child.version)
        selected = repository.effective_directory_mapping("S1KHF/Local/HF-MS/file.ods")
        self.assertEqual(selected["product"]["id"], "p2")
        self.assertTrue(selected["inherited"])
        self.assertEqual(parent.status, "approved")
        self.assertEqual(child.status, "approved")

    def test_filtered_csv_json_export_preserves_utf8_and_escapes_fields(self):
        import app.web as web

        repository = InMemorySafariRepository()
        issue = ReconciliationIssue("utf8", "invalid_identity_encoding", "error", 'Bad, "Safari – SSV"\nsecond line �', source_file="catalog.ods", source_row=172, entity_type="ProductModel", entity_id="m1", fingerprint="utf8", detected_facts={"model": "Safari � SSV"})
        repository.upsert_issue(issue)
        with mock.patch.object(web, "_repository", repository):
            csv_response = web.reconciliation_export("csv", query="Safari")
            json_response = web.reconciliation_export("json", query="Safari")
        csv_text = csv_response.body.decode("utf-8")
        rows = list(csv.reader(io.StringIO(csv_text, newline="")))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][11], issue.message)
        report = json_response.body.decode("utf-8")
        self.assertIn("Safari – SSV", report)
        self.assertIn("Safari � SSV", report)
        self.assertIn("\\nsecond line", report)

    def test_source_revision_preview_and_apply_reject_changed_hash_then_preserve_history(self):
        import app.web as web

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workbook_path = root / "model.ods"
            save_data(str(workbook_path), OrderedDict([("Summary", [["first"]])]))
            repository = self.make_identity_repository()
            file = CostingFile("f1", "model.ods", "model.ods", "model.ods", ".ods", 1, "2026-09-20T00:00:00+00:00", "stored-old", readable=True)
            repository.add_file(file)
            repository.add_observation(FileObservation("old-observation", "f1", "2026-09-20T00:00:00+00:00", "model.ods", "model.ods", 1, file.modified_at, "stored-old", True, 1, 0))
            repository.save_association(AssociationProposal("f1", "p1", "m1", ("c1",), "owner"), current_file_hash="stored-old")
            issue = repository.upsert_issue(ReconciliationIssue("changed", "changed_file", "high", "Workbook changed", entity_type="CostingFile", entity_id="f1", costing_file_id="f1", source_path="model.ods", fingerprint="changed:f1"))
            request = Request({"type": "http", "headers": [(b"x-authentik-username", b"Irshad"), (b"x-authentik-uid", b"irshad")]})
            with mock.patch.dict(os.environ, {"COSTING_ROOT": str(root)}, clear=False), mock.patch.object(web, "_repository", repository):
                plan = web.source_revision_preview(issue.id)
                first_hash = plan["current"]["sha256"]
                save_data(str(workbook_path), OrderedDict([("Summary", [["second revision"]])]))
                with self.assertRaises(HTTPException) as stale:
                    web.source_revision_apply(issue.id, request, {"expectedIssueVersion": issue.version, "expectedStoredHash": "stored-old", "expectedCurrentHash": first_hash, "reason": "reviewed"}, "apply-1")
                self.assertEqual(stale.exception.detail["code"], "STALE_REVISION_FACTS")
                plan = web.source_revision_preview(issue.id)
                apply_payload = {"expectedIssueVersion": issue.version, "expectedStoredHash": "stored-old", "expectedCurrentHash": plan["current"]["sha256"], "reason": "Owner approved the new source"}
                result = web.source_revision_apply(issue.id, request, apply_payload, "apply-2")
                save_data(str(workbook_path), OrderedDict([("Summary", [["third revision"]])]))
                replay = web.source_revision_apply(issue.id, request, apply_payload, "apply-2")
            self.assertEqual(repository.files["f1"].file_hash, plan["current"]["sha256"])
            self.assertEqual([item.file_hash for item in repository.observations if item.file_id == "f1"], ["stored-old", plan["current"]["sha256"]])
            self.assertEqual(repository.current_association("f1").model_id, "m1")
            self.assertEqual(result["auditEvent"]["payload"]["oldHash"], "stored-old")
            self.assertEqual(replay["observation"]["file_hash"], plan["current"]["sha256"])


if __name__ == "__main__":
    unittest.main()

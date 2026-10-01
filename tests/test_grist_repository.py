import json
import json
import unittest
from unittest import mock

from app.domain import CostingFile, FileObservation, ReconciliationIssue
from app.exceptions import GristValidationError
from app.grist import GristClient
from app.grist_repository import GristSafariRepository, _datetime_text, _grist_datetime
from app.repository import AssociationConflict, AssociationProposal, GovernanceConflict
from app.schema import SchemaPlan


class FakeGristClient:
    def __init__(self) -> None:
        self.tables = {
            "Product": [{"id": 1, "fields": {"Name": "Product"}}],
            "ProductModel": [{"id": 2, "fields": {"Product": 1, "ModelNumber": "MODEL", "Active": True}}],
            "ProductModelCode": [
                {"id": 3, "fields": {"ProductModel": 2, "Code": "CODE-A", "Active": True}},
                {"id": 4, "fields": {"ProductModel": 2, "Code": "CODE-B", "Active": True}},
            ],
            "CostingFile": [],
            "FileModelAssociation": [],
            "FileCodeAssociation": [],
        }
        self.next_id = 100
        self.created = []
        self.updated = []
        self.target_valid = True
        self.target_checks = 0
        self.fail_after_create_once = None

    def validate_safari_write_target(self):
        self.target_checks += 1
        if not self.target_valid:
            raise GristValidationError("Remote write target identity does not match Safari Manufacturing.")

    def fetch_table_records_with_ids(self, table_id):
        return list(self.tables.get(table_id, []))

    def create_table_records(self, table_id, records):
        created = []
        for record in records:
            item = {"id": self.next_id, "fields": record["fields"]}
            self.next_id += 1
            self.tables.setdefault(table_id, []).append(item)
            self.created.append((table_id, item))
            created.append(item)
        if table_id == self.fail_after_create_once:
            self.fail_after_create_once = None
            raise RuntimeError("simulated connection drop after Grist accepted the write")
        return created

    def update_table_records(self, table_id, updates):
        self.updated.extend((table_id, update) for update in updates)
        records = self.tables.setdefault(table_id, [])
        for update in updates:
            record = next((item for item in records if item.get("id") == update["id"]), None)
            if record is not None:
                record.setdefault("fields", {}).update(update["fields"])


class GristRepositoryTests(unittest.TestCase):
    def test_costing_acceptance_persists_gated_snapshot_change_audit_and_reloads(self) -> None:
        client = FakeGristClient()
        repository = GristSafariRepository(client=client)
        file = CostingFile("file:s1khf/model.ods", "S1KHF/model.ods", "s1khf/model.ods", "model.ods", ".ods", 125, "2026-09-29T00:00:00+00:00", "prior-hash", readable=True)
        repository.add_file(file)
        semantic = {
            "semantic_hash": "semantic-sha",
            "observed_at": "2026-09-29T10:00:00+05:30",
            "source_hashes": {"selected_workbook_saved": "ods-sha", "raw_steel": "raw-sha", "rate_log_dump": "rates-sha"},
            "source_evidence": {"selected_workbook_saved": {"sha256": "ods-sha", "size_bytes": 125, "modified_at": "2026-09-29T00:00:00+00:00", "sheet_count": 6}},
            "content": {"process_lists": {"5. Material Cut List Price": []}},
        }
        change = {
            "change_key": "mcl-qty-1", "change_type": "line_quantity_changed",
            "classification": "design_structure_change", "reason": "CR-12 changed quantity.",
            "previous_state": {"fields": {"qty": 2}, "source_evidence": {"source_row": 7}},
            "current_state": {"fields": {"qty": 3}, "source_evidence": {"source_row": 9}},
            "cost_impact": "12.75", "cr_reference": {"current": "CR-12"},
        }

        result = repository.accept_costing_snapshot(
            costing_file_id=file.id, semantic_snapshot=semantic, changes=[change], actor="Irshad",
            reason="Reconcile S1KHF ODS", expected_previous_snapshot_key=None,
            expected_semantic_hash="semantic-sha", expected_source_hashes=semantic["source_hashes"],
            idempotency_key="s1khf-accept-1",
        )

        self.assertEqual(len(client.tables["CostingSnapshot"]), 1)
        self.assertEqual(client.tables["CostingSnapshot"][0]["fields"]["Status"], "accepted")
        self.assertEqual(client.tables["CostingChangeSetItem"][0]["fields"]["Status"], "accepted")
        self.assertEqual(client.tables["CostingChangeSetItem"][0]["fields"]["CostImpact"], 12.75)
        self.assertEqual(client.tables["CostingChangeSetItem"][0]["fields"]["CRReference"], '{"current":"CR-12"}')
        snapshot_fields = client.tables["CostingSnapshot"][0]["fields"]
        change_fields = client.tables["CostingChangeSetItem"][0]["fields"]
        self.assertEqual(json.loads(snapshot_fields["SemanticContent"]), semantic["content"])
        self.assertEqual(json.loads(snapshot_fields["SourceHashes"]), semantic["source_hashes"])
        self.assertEqual(json.loads(change_fields["ChangeData"]), change)
        self.assertEqual(client.tables["CostingFile"][0]["fields"]["FileHash"], "ods-sha")
        self.assertEqual(len(client.tables["FileObservation"]), 1)
        self.assertEqual(len(client.tables["AuditEvent"]), 2)
        self.assertEqual(repository.latest_accepted_costing_snapshot(file.id).snapshot_key, result["snapshot"]["snapshot_key"])
        self.assertEqual(len(repository.costing_change_items), 1)
        reloaded = GristSafariRepository(client=client)
        saved = reloaded.latest_accepted_costing_snapshot(file.id)
        self.assertEqual(saved.semantic_content, semantic["content"])
        self.assertEqual(saved.source_hashes, semantic["source_hashes"])
        self.assertEqual(len(reloaded.costing_change_items), 1)
        write_count = (len(client.created), len(client.updated))
        unchanged = reloaded.accept_costing_snapshot(
            costing_file_id=file.id,
            semantic_snapshot=semantic,
            changes=[],
            actor="Irshad",
            reason="Confirm unchanged source state does not create another baseline.",
            expected_previous_snapshot_key=result["snapshot"]["snapshot_key"],
            expected_semantic_hash=semantic["semantic_hash"],
            expected_source_hashes=semantic["source_hashes"],
            idempotency_key="s1khf-unchanged-acceptance",
        )
        self.assertTrue(unchanged["unchanged"])
        self.assertEqual((len(client.created), len(client.updated)), write_count)

    def test_partial_costing_write_stays_staged_and_retry_completes_baseline(self) -> None:
        client = FakeGristClient()
        client.fail_after_create_once = "CostingChangeSetItem"
        repository = GristSafariRepository(client=client)
        file = CostingFile("file:s1khf/model.ods", "S1KHF/model.ods", "s1khf/model.ods", "model.ods", ".ods", 125, "2026-09-29T00:00:00+00:00", "prior-hash", readable=True)
        repository.add_file(file)
        semantic = {
            "semantic_hash": "semantic-sha",
            "observed_at": "2026-09-29T10:00:00+05:30",
            "source_hashes": {"selected_workbook_saved": "ods-sha", "raw_steel": "raw-sha", "rate_log_dump": "rates-sha"},
            "source_evidence": {"selected_workbook_saved": {"sha256": "ods-sha", "size_bytes": 125, "modified_at": "2026-09-29T00:00:00+00:00", "sheet_count": 6}},
            "content": {"process_lists": {"5. Material Cut List Price": []}},
        }
        changes = [{"change_key": "change-retry-1", "change_type": "line_quantity_changed", "classification": "design_structure_change", "previous_state": {"fields": {"qty": 2}}, "current_state": {"fields": {"qty": 3}}, "cost_impact": "10"}]
        kwargs = dict(
            costing_file_id=file.id, semantic_snapshot=semantic, changes=changes, actor="Irshad",
            reason="Retry test", expected_previous_snapshot_key=None, expected_semantic_hash="semantic-sha",
            expected_source_hashes=semantic["source_hashes"], idempotency_key="retry-costing-1",
        )

        with self.assertRaisesRegex(RuntimeError, "simulated connection drop"):
            repository.accept_costing_snapshot(**kwargs)
        self.assertEqual(client.tables["CostingSnapshot"][0]["fields"]["Status"], "staging")
        self.assertEqual(client.tables["CostingChangeSetItem"][0]["fields"]["Status"], "staging")
        self.assertIsNone(repository.latest_accepted_costing_snapshot(file.id))

        result = repository.accept_costing_snapshot(**kwargs)

        self.assertFalse(result.get("unchanged", False))
        self.assertEqual(client.tables["CostingSnapshot"][0]["fields"]["Status"], "accepted")
        self.assertEqual(client.tables["CostingChangeSetItem"][0]["fields"]["Status"], "accepted")
        self.assertEqual(len(client.tables["CostingSnapshot"]), 1)
        self.assertEqual(len(client.tables["CostingChangeSetItem"]), 1)

    def test_governance_writes_stop_before_mutation_when_schema_diff_is_pending(self) -> None:
        class ConcreteGristFake(FakeGristClient, GristClient):
            def __init__(self) -> None:
                FakeGristClient.__init__(self)
                self.api_key = "test-key"
                self.doc_id = "safari-doc"
                self.base_url = "https://example.invalid"
                self.safari_workspace_id = "work"

        client = ConcreteGristFake()
        repository = GristSafariRepository(client=client)
        pending = SchemaPlan("safari-doc", "Safari Manufacturing", "work", "v3", ({"id": "DirectoryProductMapping"},), (), ())
        with mock.patch("app.grist_repository.plan_schema", return_value=pending):
            with self.assertRaises(GovernanceConflict) as raised:
                repository.upsert_issue(ReconciliationIssue("i1", "changed_file", "high", "Changed", fingerprint="changed:file"))
        self.assertEqual(raised.exception.code, "SCHEMA_MIGRATION_REQUIRED")
        self.assertEqual(client.created, [])
        self.assertEqual(client.updated, [])

    def test_identity_cleanup_idempotency_survives_repository_restart(self) -> None:
        client = FakeGristClient()
        client.tables["ProductModel"] = [
            {"id": 2, "fields": {"Product": 1, "ModelNumber": "Safari � SSV", "Name": "", "Active": True}},
            {"id": 5, "fields": {"Product": 1, "ModelNumber": "Safari – SSV", "Name": "", "Active": True}},
        ]
        client.tables["ProductModelCode"] = []
        first_repository = GristSafariRepository(client=client)
        issue = first_repository.upsert_issue(ReconciliationIssue("detected", "invalid_identity_encoding", "error", "Invalid identity", entity_type="ProductModel", entity_id="2", fingerprint="encoding:2"))
        payload = {"issue_id": issue.id, "actor": "Irshad", "reason": "Approved cleanup", "expected_issue_version": issue.version, "idempotency_key": "cleanup-request-1"}
        first_result = first_repository.apply_identity_cleanup("2", "5", **payload)

        restarted_repository = GristSafariRepository(client=client)
        replay = restarted_repository.apply_identity_cleanup("2", "5", **payload)

        self.assertFalse(replay["model"]["active"])
        self.assertEqual(replay["model"]["superseded_by_id"], "5")
        self.assertEqual(replay["auditEvent"]["id"], first_result["auditEvent"]["id"])
        self.assertTrue(replay["idempotent"])

    def test_reconciliation_repository_contract_persists_issue_lifecycle_and_directory_mapping(self) -> None:
        from app.repository import InMemorySafariRepository, SafariRepository, SafariRepositoryBase

        client = FakeGristClient()
        repository = GristSafariRepository(client=client)
        self.assertFalse(isinstance(repository, InMemorySafariRepository))
        self.assertIsInstance(repository, SafariRepositoryBase)
        self.assertIsInstance(repository, SafariRepository)
        self.assertIsInstance(InMemorySafariRepository(), SafariRepository)
        self.assertTrue(hasattr(repository, "mutate_issue"))
        self.assertTrue(hasattr(repository, "accept_file_revision"))

        issue = repository.upsert_issue(ReconciliationIssue("detected", "missing_file", "error", "File is missing", entity_type="CostingFile", entity_id="file:model.ods", fingerprint="missing:model"))
        self.assertTrue(issue.id.startswith("issue:"))
        self.assertEqual(client.tables["ReconciliationIssue"][0]["fields"]["Fingerprint"], "missing:model")
        deferred = repository.mutate_issue(issue.id, "defer", actor="owner", reason="Waiting on owner", expected_version=1, idempotency_key="issue-request-1")
        self.assertEqual(deferred.status, "deferred")
        self.assertEqual(client.tables["ReconciliationIssue"][0]["fields"]["Version"], 2)
        self.assertEqual(client.tables["AuditEvent"][0]["fields"]["RequestKey"], "issue-request-1")

        mapping = repository.propose_directory_mapping("S1KHF/Local", "1", inherit=True, actor="owner", reason="proposal")
        approved = repository.transition_directory_mapping(mapping.id, "approve", actor="Irshad", reason="reviewed", expected_version=mapping.version)
        self.assertEqual(approved.status, "approved")
        self.assertEqual(client.tables["DirectoryProductMapping"][0]["fields"]["Status"], "approved")
        self.assertTrue(client.target_checks >= 4)

    def test_refresh_loads_catalog_governance_and_processing_records(self) -> None:
        client = FakeGristClient()
        client.tables["ProductModelCode"][0]["fields"]["SourceValues"] = ["L", "SOURCE-CODE", "OLD-CODE"]
        client.tables["IdentityAlias"] = [{"id": 20, "fields": {"EntityType": "ProductModelCode", "EntityId": "3", "Value": "OLD-CODE", "NormalizedValue": "old-code", "Source": "catalog.ods", "SourceRow": 7}}]
        client.tables["ReconciliationIssue"] = [{"id": 21, "fields": {"IssueType": "blank_description", "Severity": "warning", "Message": "Description is blank", "SourceFile": "catalog.ods", "SourceRow": 8, "EntityId": "3", "Status": "open", "CreatedAt": 1789812000.0}}]
        client.tables["ImportBatch"] = [{"id": 22, "fields": {"SourceFile": "S1KHF/model.ods", "SourceHash": "hash", "ParserVersion": "association-0.1", "StartedAt": 1789812000.0, "CompletedAt": 1789812000.0, "Status": "queued", "Outcome": "read-only processing queued", "RequestKey": "request-1", "RequestFingerprint": "fingerprint"}}]
        client.tables["AuditEvent"] = [{"id": 23, "fields": {"EventType": "file_association_saved", "Actor": "tester", "OccurredAt": 1789812000.0, "EntityType": "FileModelAssociation", "EntityId": "fma:91", "Reason": "pilot", "Payload": '{"codeIds":["3"]}'}}]

        repository = GristSafariRepository(client=client)

        self.assertEqual(repository.codes["3"].source_values, ("SOURCE-CODE", "OLD-CODE"))
        self.assertEqual(repository.aliases["alias:20"].value, "OLD-CODE")
        self.assertEqual(repository.issues["issue:21"].issue_type, "blank_description")
        self.assertEqual(repository.import_batches["batch:22"].status, "queued")
        self.assertEqual(repository.audit_events["audit:23"].payload, {"codeIds": ["3"]})

    def test_refreshes_identity_and_persists_supersede_projection(self) -> None:
        client = FakeGristClient()
        repository = GristSafariRepository(client=client)
        repository.add_file(CostingFile("file:model.ods", "model.ods", "model.ods", "model.ods", ".ods", 10, "2026-09-19T00:00:00+00:00", "hash", readable=True))
        repository.add_observation(FileObservation("obs-1", "file:model.ods", "2026-09-10T00:00:00+00:00", "model.ods", "model.ods", 10, "2026-09-09T00:00:00+00:00", readable=True, sheet_count=3, external_reference_count=7))

        first = repository.save_association(AssociationProposal("file:model.ods", "1", "2", ("3",), "tester"), current_file_hash="hash")
        second = repository.save_association(AssociationProposal("file:model.ods", "1", "2", ("4",), "tester", reason="refresh", supersede=True), current_file_hash="hash")

        self.assertEqual(repository.list_products()[0].name, "Product")
        self.assertEqual(repository.current_association("file:model.ods").id, second.association.id)
        self.assertEqual(len(repository.association_history("file:model.ods")), 2)
        self.assertEqual(len(repository.association_history("file:model.ods")[0]["auditEvents"]), 1)
        self.assertTrue(any(table == "FileModelAssociation" for table, _ in client.updated))
        self.assertTrue(any(table == "FileCodeAssociation" for table, _ in client.updated))
        self.assertTrue(any(table == "AuditEvent" for table, _ in client.created))
        grist_observation = next(record for table, record in client.created if table == "FileObservation")
        self.assertEqual(grist_observation["fields"]["SheetCount"], 3)
        self.assertEqual(grist_observation["fields"]["ExternalReferenceCount"], 7)
        self.assertIsInstance(grist_observation["fields"]["CostingFile"], int)
        self.assertIsInstance(grist_observation["fields"]["ObservedAt"], float)
        self.assertNotEqual(first.association.id, second.association.id)
        self.assertEqual(client.target_checks, 2)

    def test_grist_datetime_cells_round_trip_epoch_seconds_and_iso(self) -> None:
        value = "2026-09-19T10:11:12+00:00"
        encoded = _grist_datetime(value)

        self.assertIsInstance(encoded, float)
        self.assertEqual(_datetime_text(encoded), value)
        self.assertEqual(_datetime_text(["D", encoded, "UTC"]), value)

    def test_refuses_association_write_when_remote_target_is_not_validated(self) -> None:
        client = FakeGristClient()
        client.target_valid = False
        repository = GristSafariRepository(client=client)
        repository.add_file(CostingFile("file:model.ods", "model.ods", "model.ods", "model.ods", ".ods", 10, "2026-09-19T00:00:00+00:00", "hash"))

        with self.assertRaises(GristValidationError):
            repository.save_association(AssociationProposal("file:model.ods", "1", "2", ("3",), "tester"), current_file_hash="hash")

        self.assertEqual(client.created, [])
        self.assertEqual(client.updated, [])
        self.assertEqual(repository.associations, {})

    def test_refreshes_remote_code_ownership_before_saving(self) -> None:
        client = FakeGristClient()
        repository = GristSafariRepository(client=client)
        repository.add_file(CostingFile("file:model.ods", "model.ods", "model.ods", "model.ods", ".ods", 10, "2026-09-19T00:00:00+00:00", "hash", readable=True))
        client.tables["CostingFile"].append({"id": 90, "fields": {"RelativePath": "other.ods", "NormalizedPath": "other.ods", "Name": "other.ods", "Extension": ".ods", "SizeBytes": 10, "FileHash": "other-hash"}})
        client.tables["FileModelAssociation"].append({"id": 91, "fields": {"CostingFile": 90, "Product": 1, "ProductModel": 2, "Actor": "owner", "CreatedAt": 1789812000.0, "Active": True, "Version": 1}})
        client.tables["FileCodeAssociation"].append({"id": 92, "fields": {"Association": 91, "CostingFile": 90, "ProductModelCode": 3, "Actor": "owner", "CreatedAt": 1789812000.0, "Active": True}})
        proposal = AssociationProposal("file:model.ods", "1", "2", ("3",), "tester", idempotency_key="live-owner-conflict")

        validation = repository.validate_association(proposal, current_file_hash="hash")
        self.assertFalse(validation.valid)
        self.assertEqual(validation.errors[0].code, "CODE_ALREADY_ASSOCIATED")

        with self.assertRaises(AssociationConflict) as raised:
            repository.save_association(proposal, current_file_hash="hash")

        self.assertEqual(raised.exception.validation.errors[0].code, "CODE_ALREADY_ASSOCIATED")
        self.assertEqual(raised.exception.validation.errors[0].details["ownerFileId"], "file:other.ods")
        self.assertEqual(client.created, [])
        self.assertEqual(client.updated, [])

    def test_retry_after_pre_association_failure_revalidates_remote_ownership(self) -> None:
        client = FakeGristClient()
        client.fail_after_create_once = "CostingFile"
        repository = GristSafariRepository(client=client)
        repository.add_file(CostingFile("file:model.ods", "model.ods", "model.ods", "model.ods", ".ods", 10, "2026-09-19T00:00:00+00:00", "hash", readable=True))
        proposal = AssociationProposal("file:model.ods", "1", "2", ("3",), "tester", idempotency_key="owner-before-retry")

        with self.assertRaisesRegex(RuntimeError, "connection drop"):
            repository.save_association(proposal, current_file_hash="hash")

        client.tables["CostingFile"].append({"id": 90, "fields": {"RelativePath": "other.ods", "NormalizedPath": "other.ods", "Name": "other.ods", "Extension": ".ods", "SizeBytes": 10, "FileHash": "other-hash"}})
        client.tables["FileModelAssociation"].append({"id": 91, "fields": {"CostingFile": 90, "Product": 1, "ProductModel": 2, "Actor": "owner", "CreatedAt": 1789812000.0, "Active": True, "Version": 1}})
        client.tables["FileCodeAssociation"].append({"id": 92, "fields": {"Association": 91, "CostingFile": 90, "ProductModelCode": 3, "Actor": "owner", "CreatedAt": 1789812000.0, "Active": True}})

        with self.assertRaises(AssociationConflict) as raised:
            repository.save_association(proposal, current_file_hash="hash")

        self.assertEqual(raised.exception.validation.errors[0].code, "CODE_ALREADY_ASSOCIATED")
        self.assertEqual(len(client.tables["FileModelAssociation"]), 1)
        self.assertEqual(len(client.tables["FileCodeAssociation"]), 1)

    def test_idempotent_retry_does_not_duplicate_grist_records(self) -> None:
        client = FakeGristClient()
        repository = GristSafariRepository(client=client)
        repository.add_file(CostingFile("file:model.ods", "model.ods", "model.ods", "model.ods", ".ods", 10, "2026-09-19T00:00:00+00:00", "hash"))
        proposal = AssociationProposal("file:model.ods", "1", "2", ("3",), "tester", idempotency_key="request-1")

        first = repository.save_association(proposal, current_file_hash="hash")
        writes_after_first = (len(client.created), len(client.updated))
        retry = repository.save_association(proposal, current_file_hash="hash")

        self.assertFalse(first.idempotent)
        self.assertTrue(retry.idempotent)
        self.assertEqual((len(client.created), len(client.updated)), writes_after_first)

    def test_retry_resumes_after_uncertain_partial_grist_write(self) -> None:
        client = FakeGristClient()
        client.fail_after_create_once = "FileCodeAssociation"
        repository = GristSafariRepository(client=client)
        repository.add_file(CostingFile("file:model.ods", "model.ods", "model.ods", "model.ods", ".ods", 10, "2026-09-19T00:00:00+00:00", "hash"))
        proposal = AssociationProposal("file:model.ods", "1", "2", ("3",), "tester", idempotency_key="request-partial")

        with self.assertRaisesRegex(RuntimeError, "connection drop"):
            repository.save_association(proposal, current_file_hash="hash")
        self.assertEqual(len(client.tables["FileCodeAssociation"]), 1)

        retry = repository.save_association(proposal, current_file_hash="hash")

        self.assertTrue(retry.idempotent)
        self.assertEqual(len(client.tables["CostingFile"]), 1)
        self.assertEqual(len(client.tables["FileModelAssociation"]), 1)
        self.assertEqual(len(client.tables["FileCodeAssociation"]), 1)
        self.assertEqual(len(client.tables["FileObservation"]), 1)
        self.assertEqual(len(client.tables["AuditEvent"]), 1)
        self.assertEqual(len(client.tables["ImportBatch"]), 1)
        self.assertEqual(retry.codes[0].code_id, "3")

    def test_retry_resumes_after_audit_payload_is_accepted(self) -> None:
        client = FakeGristClient()
        client.fail_after_create_once = "AuditEvent"
        repository = GristSafariRepository(client=client)
        repository.add_file(CostingFile("file:model.ods", "model.ods", "model.ods", "model.ods", ".ods", 10, "2026-09-19T00:00:00+00:00", "hash"))
        proposal = AssociationProposal("file:model.ods", "1", "2", ("3",), "tester", idempotency_key="request-audit-partial")

        with self.assertRaisesRegex(RuntimeError, "connection drop"):
            repository.save_association(proposal, current_file_hash="hash")

        retry = repository.save_association(proposal, current_file_hash="hash")

        self.assertTrue(retry.idempotent)
        self.assertEqual(len(client.tables["FileModelAssociation"]), 1)
        self.assertEqual(len(client.tables["FileCodeAssociation"]), 1)
        self.assertEqual(len(client.tables["AuditEvent"]), 1)
        self.assertEqual(len(client.tables["ImportBatch"]), 1)
        self.assertEqual(
            json.loads(client.tables["AuditEvent"][0]["fields"]["Payload"]),
            {"codeIds": ["3"], "supersededAssociationId": None, "supersededCodeAssociationIds": []},
        )

    def test_retry_after_repository_restart_resumes_persisted_request(self) -> None:
        client = FakeGristClient()
        client.fail_after_create_once = "FileCodeAssociation"
        first_repository = GristSafariRepository(client=client)
        first_repository.add_file(CostingFile("file:model.ods", "model.ods", "model.ods", "model.ods", ".ods", 10, "2026-09-19T00:00:00+00:00", "hash"))
        proposal = AssociationProposal("file:model.ods", "1", "2", ("3",), "tester", idempotency_key="request-restart")

        with self.assertRaisesRegex(RuntimeError, "connection drop"):
            first_repository.save_association(proposal, current_file_hash="hash")

        restarted_repository = GristSafariRepository(client=client)
        retry = restarted_repository.save_association(proposal, current_file_hash="hash")

        self.assertTrue(retry.idempotent)
        for table in ("CostingFile", "FileModelAssociation", "FileCodeAssociation", "FileObservation", "AuditEvent", "ImportBatch"):
            self.assertEqual(len(client.tables[table]), 1, table)

    def test_grist_idempotency_key_cannot_be_reused_for_another_payload(self) -> None:
        from app.repository import AssociationConflict

        client = FakeGristClient()
        repository = GristSafariRepository(client=client)
        repository.add_file(CostingFile("file:model.ods", "model.ods", "model.ods", "model.ods", ".ods", 10, "2026-09-19T00:00:00+00:00", "hash"))
        repository.save_association(AssociationProposal("file:model.ods", "1", "2", ("3",), "tester", idempotency_key="request-fixed"), current_file_hash="hash")

        with self.assertRaises(AssociationConflict):
            repository.save_association(AssociationProposal("file:model.ods", "1", "2", ("4",), "tester", idempotency_key="request-fixed"), current_file_hash="hash")


if __name__ == "__main__":
    unittest.main()

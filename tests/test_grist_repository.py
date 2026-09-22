import json
import unittest

from app.domain import CostingFile, FileObservation
from app.exceptions import GristValidationError
from app.grist_repository import GristSafariRepository, _datetime_text, _grist_datetime
from app.repository import AssociationConflict, AssociationProposal


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

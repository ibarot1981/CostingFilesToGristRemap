from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import threading
import unittest

from app.grist_parts import GristPartFeatures, GristPartRegistry
from app.part_definition import GristPartDefinitionService
from app.part_identity import PartIdentityError


class DefinitionMemoryGrist:
    def __init__(self):
        self.tables: dict[str, list[dict]] = {
            "ProductPart": [{"id": 10, "fields": {"StablePartId": "p-1", "PartNumber": "SM-P-000001", "DisplayName": "Bracket", "Status": "active", "IdentityKind": "part", "CurrentMetadataVersion": 31, "CurrentPartRevision": 20, "EngineeringRevision": "A"}}],
            "PartMetadataVersion": [{"id": 31, "fields": {"ProductPart": 10, "Version": 1, "DisplayName": "Bracket", "Status": "current"}}],
            "PartRevision": [{"id": 20, "fields": {"RevisionKey": "initial:p-1:A", "ProductPart": 10, "Revision": 1, "RevisionLabel": "A", "Status": "accepted", "BaselineStatus": "draft", "ManufacturingBaseline": 1}}],
            "PartBaselineProcessing": [{"id": 1, "fields": {"BaselineKey": "baseline:p-1", "ProductPart": 10, "Status": "established", "WorkbookName": "Original.ods", "WorkbookPath": "Original.ods", "SourceHash": "source-hash"}}],
            "PartBaselineFamily": [
                {"id": 1, "fields": {"BaselineProcessing": 1, "Family": "mcl", "ApplicabilityStatus": "applicable", "SourceEvidenceStatus": "ok", "CompletenessConfirmed": True, "RequirementCount": 1}},
                {"id": 2, "fields": {"BaselineProcessing": 1, "Family": "toolshop", "ApplicabilityStatus": "not_applicable", "SourceEvidenceStatus": "ok", "CompletenessConfirmed": True, "RequirementCount": 0}},
                {"id": 3, "fields": {"BaselineProcessing": 1, "Family": "cnc", "ApplicabilityStatus": "not_applicable", "SourceEvidenceStatus": "ok", "CompletenessConfirmed": True, "RequirementCount": 0}},
            ],
            "LineMaster": [{"id": 40, "fields": {"LineKey": "line:1", "ProductPart": 10, "ProcessType": "material_cut", "Status": "active", "SourcePartName": "Bracket"}}],
            "LineRevision": [{"id": 50, "fields": {"RevisionKey": "line-rev:1", "LineMaster": 40, "Status": "accepted", "PhysicalSignature": "physical-v1"}}],
            "LineDetail": [{"id": 60, "fields": {"DetailKey": "detail:1", "LineRevision": 50, "ProcessType": "material_cut", "Material": 5, "Quantity": 1, "QuantityUOM": "kg", "Length": 100, "Width": 50, "Thickness": 3, "DimensionUOM": "mm", "ItemName": "Bracket blank", "EngineeringAttributes": {"fieldStatus": {"ToolShopRemarks": "missing"}}}}],
            "PartRevisionLine": [{"id": 70, "fields": {"RevisionLineKey": "membership:1", "RequirementKey": "requirement:p-1:1", "PartRevision": 20, "LineMaster": 40, "LineRevision": 50, "ProcessType": "material_cut", "QuantityPerPart": 1, "QuantityExact": "1", "QuantityUOM": "Nos", "Status": "active", "ProvenanceType": "ods_baseline", "ProvenanceEvidence": {"sourceRow": 18}}}],
            "PartComponentRevision": [], "PartPurchaseSpecification": [], "Material": [{"id": 5, "fields": {"CanonicalName": "Steel"}}],
            "PurchaseItem": [], "ProcessOperation": [], "WorkCenter": [], "SourceLineObservation": [],
            "ProductModelCode": [{"id": 90, "fields": {"Code": "M-1", "Active": True}}],
            "CostingConfiguration": [{"id": 100, "fields": {"ProductModelCode": 90, "Status": "active", "CurrentRevision": 101, "Version": 1}}],
            "CostingConfigurationRevision": [{"id": 101, "fields": {"Configuration": 100, "Revision": 1, "Status": "published", "Currency": "INR"}}],
            "ConfigurationPartSelection": [{"id": 102, "fields": {"SelectionKey": "sel:1", "SelectionIdentity": "selection:p-1", "ConfigurationRevision": 101, "ProductPart": 10, "StablePartId": "p-1", "PartRevision": 20, "Quantity": 2, "QuantityUOM": "each", "SourcingRoute": "make", "OccurrenceLabel": "main", "OptionGroup": "", "Status": "active"}}],
            "PartDefinitionDraft": [], "PartEngineeringChangeRequest": [], "PartDefinitionVersion": [], "PartDefinitionPublication": [],
            "PartRegistryRequest": [], "AuditEvent": [], "PartNameAlias": [], "PartDrawing": [],
        }
        self.next_id = 200
        self.lock = threading.Lock()
        self.lose_after_update_table = ""
        self.lose_after_create_table = ""

    def validate_safari_write_target(self):
        return None

    def fetch_table_records_with_ids(self, table):
        with self.lock:
            return deepcopy(self.tables.get(table, []))

    def create_table_records(self, table, records):
        with self.lock:
            rows = self.tables.setdefault(table, [])
            created = []
            for item in records:
                row = {"id": self.next_id, "fields": deepcopy(item["fields"])}
                self.next_id += 1
                rows.append(row)
                created.append(deepcopy(row))
            lose = table == self.lose_after_create_table
            if lose:
                self.lose_after_create_table = ""
        if lose:
            raise TimeoutError(f"simulated lost response after Grist created {table}")
        return {"records": created}

    def update_table_records(self, table, updates):
        lose = False
        with self.lock:
            rows = self.tables.setdefault(table, [])
            for update in updates:
                row = next(item for item in rows if int(item["id"]) == int(update["id"]))
                row["fields"].update(deepcopy(update["fields"]))
            lose = table == self.lose_after_update_table
            if lose:
                self.lose_after_update_table = ""
        if lose:
            raise TimeoutError(f"simulated lost response after Grist committed {table}")


class PartDefinitionMaintenanceTests(unittest.TestCase):
    def make_service(self, client, path):
        registry = GristPartRegistry(client, path=path)
        registry._verify_writer = lambda: None
        return GristPartDefinitionService(registry)

    def draft_and_submit(self, service, *, request_suffix="normal"):
        definition = service.get_definition("p-1")
        self.assertEqual(definition["revisionLabel"], "A")
        self.assertEqual(definition["requirements"][0]["requirementKey"], "requirement:p-1:1")
        content = {"baseRevisionId": definition["revisionId"], "applicability": definition["applicability"],
            "requirements": deepcopy(definition["requirements"]), "components": [], "purchases": [], "finalizeOnPublication": False}
        content["requirements"][0]["quantity"] = 2
        content["requirements"][0]["detail"]["EngineeringAttributes"] = {"fieldStatus": {"ToolShopRemarks": "missing"}, "grain": "along_length"}
        content["expectedSaveVersion"] = definition.get("draft", {}).get("saveVersion", 0)
        saved = service.save_draft("p-1", content, actor="operator", reason="Increase blank allowance", request_key=f"draft-{request_suffix}")
        reloaded = service.get_definition("p-1")
        self.assertEqual(reloaded["draft"]["reason"], "Increase blank allowance")
        self.assertEqual(reloaded["draft"]["content"]["requirements"][0]["quantity"], 2)
        request = service.submit_change_request("p-1", {"draftKey": saved["draftKey"]}, actor="operator",
            reason="Increase blank allowance", request_key=f"cr-{request_suffix}")
        return definition, request

    def test_physical_identity_claims_are_checked_against_base_before_any_write(self):
        cases = ("missing-master", "foreign-master", "missing-revision", "mismatched-revision", "new-forged", "fractional-master", "boolean-master")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temp:
                client = DefinitionMemoryGrist()
                client.tables["LineMaster"].append({"id": 41, "fields": {"LineKey": "foreign", "ProductPart": 10, "ProcessType": "cnc"}})
                client.tables["LineRevision"].append({"id": 51, "fields": {"RevisionKey": "foreign-rev", "LineMaster": 41, "Status": "accepted"}})
                service = self.make_service(client, Path(temp) / "coordinator.sqlite3")
                definition = service.get_definition("p-1")
                content = {"baseRevisionId": 20, "expectedSaveVersion": 0, "applicability": definition["applicability"],
                    "requirements": deepcopy(definition["requirements"]), "components": [], "purchases": [], "finalizeOnPublication": False}
                item = content["requirements"][0]
                if case == "missing-master": item["lineMasterId"] = 99999
                elif case == "foreign-master": item["lineMasterId"] = 41
                elif case == "missing-revision": item["lineRevisionId"] = 99999
                elif case == "mismatched-revision": item["lineRevisionId"] = 51
                elif case == "fractional-master": item["lineMasterId"] = 40.5
                elif case == "boolean-master": item["lineMasterId"] = True
                else: item["requirementKey"] = "authored-forged"
                before = deepcopy(client.tables)
                with self.assertRaises(PartIdentityError) as error:
                    service.save_draft("p-1", content, actor="operator", reason="identity validation", request_key="invalid-" + case)
                self.assertEqual(error.exception.code, "PART_REQUIREMENT_LINE_IDENTITY_INVALID")
                self.assertEqual(client.tables, before)

    def test_omitted_references_derive_saved_pair_and_shared_line_keeps_lineage(self):
        with tempfile.TemporaryDirectory() as temp:
            client = DefinitionMemoryGrist()
            client.tables["LineMaster"][0]["fields"]["ProductPart"] = 999  # Shared physical line, owned elsewhere.
            service = self.make_service(client, Path(temp) / "coordinator.sqlite3")
            definition = service.get_definition("p-1")
            content = {"baseRevisionId": 20, "expectedSaveVersion": 0, "applicability": definition["applicability"],
                "requirements": deepcopy(definition["requirements"]), "components": [], "purchases": [], "finalizeOnPublication": False}
            item = content["requirements"][0]
            item.pop("lineMasterId"); item.pop("lineRevisionId")
            item["detail"]["ItemName"] = "Edited shared line"
            saved = service.save_draft("p-1", content, actor="operator", reason="shared edit", request_key="shared-draft")
            self.assertEqual((saved["content"]["requirements"][0]["lineMasterId"], saved["content"]["requirements"][0]["lineRevisionId"]), (40, 50))
            cr = service.submit_change_request("p-1", {"draftKey": saved["draftKey"]}, actor="operator", reason="shared edit", request_key="shared-cr")
            published = service.approve_and_publish(cr["changeRequestKey"], actor="Irshad", request_key="shared-publish")
            link = next(row["fields"] for row in client.tables["PartRevisionLine"] if row["fields"]["PartRevision"] == published["publishedRevisionId"])
            line = next(row["fields"] for row in client.tables["LineRevision"] if row["id"] == link["LineRevision"])
            self.assertEqual((link["RequirementKey"], link["LineMaster"], line["PreviousRevision"]), ("requirement:p-1:1", 40, 50))
            self.assertEqual(client.tables["LineDetail"][0]["fields"]["ItemName"], "Bracket blank")
            self.assertEqual(client.tables["PartRevisionLine"][0]["fields"]["LineRevision"], 50)

    def test_malformed_pending_cr_is_rejected_before_approval_journal(self):
        with tempfile.TemporaryDirectory() as temp:
            client = DefinitionMemoryGrist()
            service = self.make_service(client, Path(temp) / "coordinator.sqlite3")
            _, cr = self.draft_and_submit(service, request_suffix="forged-pending")
            fields = client.tables["PartEngineeringChangeRequest"][0]["fields"]
            after = json.loads(fields["AfterDefinition"])
            after["requirements"][0]["lineMasterId"] = 99999
            fields["AfterDefinition"] = json.dumps(after)
            before = deepcopy(client.tables)
            with self.assertRaises(PartIdentityError) as error:
                service.approve_and_publish(cr["changeRequestKey"], actor="Irshad", request_key="forged-approval")
            self.assertEqual(error.exception.code, "PART_REQUIREMENT_LINE_IDENTITY_INVALID")
            self.assertEqual(client.tables, before)

    def test_unresolved_legacy_line_remains_readable_but_cannot_be_published(self):
        with tempfile.TemporaryDirectory() as temp:
            client = DefinitionMemoryGrist()
            client.tables["PartRevisionLine"][0]["fields"]["LineRevision"] = 99999
            service = self.make_service(client, Path(temp) / "coordinator.sqlite3")
            definition = service.get_definition("p-1")
            self.assertEqual(definition["requirements"][0]["lineIdentityStatus"], "unresolved")
            content = {"baseRevisionId": 20, "expectedSaveVersion": 0, "applicability": definition["applicability"],
                "requirements": deepcopy(definition["requirements"]), "components": [], "purchases": [], "finalizeOnPublication": False}
            before = deepcopy(client.tables)
            with self.assertRaises(PartIdentityError) as error:
                service.save_draft("p-1", content, actor="operator", reason="review", request_key="unresolved")
            self.assertEqual(error.exception.code, "PART_REQUIREMENT_LINE_IDENTITY_UNRESOLVED")
            self.assertEqual(before, client.tables)

    def test_new_requirement_materialization_recovers_without_duplicate_line_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            client = DefinitionMemoryGrist()
            path = Path(temp) / "coordinator.sqlite3"
            service = self.make_service(client, path)
            definition = service.get_definition("p-1")
            new = deepcopy(definition["requirements"][0])
            new.update({"requirementKey": "authored-new-line", "identityStatus": "safari_authored", "lineMasterId": None, "lineRevisionId": None})
            new["detail"]["ItemName"] = "New independent blank"
            content = {"baseRevisionId": 20, "expectedSaveVersion": 0, "applicability": definition["applicability"],
                "requirements": [deepcopy(definition["requirements"][0]), new], "components": [], "purchases": [], "finalizeOnPublication": False}
            saved = service.save_draft("p-1", content, actor="operator", reason="new line", request_key="new-line-draft")
            cr = service.submit_change_request("p-1", {"draftKey": saved["draftKey"]}, actor="operator", reason="new line", request_key="new-line-cr")
            client.lose_after_create_table = "LineRevision"
            with self.assertRaises(TimeoutError):
                service.approve_and_publish(cr["changeRequestKey"], actor="Irshad", request_key="new-line-publish")
            restarted = self.make_service(client, path)
            result = restarted.approve_and_publish(cr["changeRequestKey"], actor="Irshad", request_key="new-line-publish")
            accepted = restarted.get_definition("p-1")
            new_item = next(item for item in accepted["requirements"] if item["requirementKey"] == "authored-new-line")
            self.assertNotEqual(new_item["lineMasterId"], 40)
            masters = [row for row in client.tables["LineMaster"] if row["id"] == new_item["lineMasterId"]]
            line_revisions = [row for row in client.tables["LineRevision"] if row["fields"].get("LineMaster") == new_item["lineMasterId"]]
            self.assertEqual((len(masters), len(line_revisions)), (1, 1))
            self.assertEqual(result["publishedRevisionId"], accepted["revisionId"])

    def test_legacy_saved_draft_with_omitted_line_references_remains_approvable(self):
        with tempfile.TemporaryDirectory() as temp:
            from app.part_identity import request_fingerprint
            client = DefinitionMemoryGrist()
            service = self.make_service(client, Path(temp) / "coordinator.sqlite3")
            definition = service.get_definition("p-1")
            content = {"baseRevisionId": 20, "expectedSaveVersion": 0, "applicability": definition["applicability"],
                "requirements": deepcopy(definition["requirements"]), "components": [], "purchases": [], "finalizeOnPublication": False}
            saved = service.save_draft("p-1", content, actor="operator", reason="legacy draft", request_key="legacy-draft")
            fields = client.tables["PartDefinitionDraft"][0]["fields"]
            old_content = json.loads(fields["Content"])
            old_content["requirements"][0].pop("lineMasterId"); old_content["requirements"][0].pop("lineRevisionId")
            fields["Content"] = json.dumps(old_content); fields["ContentFingerprint"] = request_fingerprint(old_content)
            cr = service.submit_change_request("p-1", {"draftKey": saved["draftKey"]}, actor="operator", reason="legacy draft", request_key="legacy-cr")
            result = service.approve_and_publish(cr["changeRequestKey"], actor="Irshad", request_key="legacy-approval")
            link = next(row["fields"] for row in client.tables["PartRevisionLine"] if row["fields"]["PartRevision"] == result["publishedRevisionId"])
            self.assertEqual(link["LineMaster"], 40)
            if link["LineRevision"] != 50:
                line = next(row["fields"] for row in client.tables["LineRevision"] if row["id"] == link["LineRevision"])
                self.assertEqual(line["PreviousRevision"], 50)

    def test_reviewed_publication_preserves_history_and_versions_current_configuration(self):
        with tempfile.TemporaryDirectory() as temp:
            client = DefinitionMemoryGrist()
            service = self.make_service(client, Path(temp) / "coordinator.sqlite3")
            original_line = deepcopy(client.tables["LineDetail"][0])
            original_membership = deepcopy(client.tables["PartRevisionLine"][0])
            original_selection = deepcopy(client.tables["ConfigurationPartSelection"][0])
            _definition, request = self.draft_and_submit(service)
            self.assertEqual(request["impact"]["modelCodeConfigurations"][0]["modelCode"], "M-1")
            approver_view = service.get_definition("p-1", actor="Irshad")
            pending = approver_view["pendingChangeRequests"][0]
            self.assertEqual(str(pending["before"]["requirements"][0]["quantity"]), "1")
            self.assertEqual(pending["after"]["requirements"][0]["quantity"], 2)
            self.assertNotEqual(pending["before"], pending["after"])
            self.assertEqual(client.tables["ProductPart"][0]["fields"]["CurrentPartRevision"], 20)
            with self.assertRaises(PartIdentityError):
                service.approve_and_publish(request["changeRequestKey"], actor="not Irshad", request_key="approval-normal")

            published = service.approve_and_publish(request["changeRequestKey"], actor="Irshad", request_key="approval-normal")

            current_part = client.tables["ProductPart"][0]["fields"]
            self.assertEqual(current_part["EngineeringRevision"], "B")
            self.assertEqual(current_part["CurrentPartRevision"], published["publishedRevisionId"])
            published_part_revision = next(row for row in client.tables["PartRevision"] if row["id"] == published["publishedRevisionId"])
            self.assertEqual(published_part_revision["fields"]["ManufacturingBaseline"], 1)
            self.assertEqual(published_part_revision["fields"]["ManufacturingBaselineStatus"], "established")
            self.assertEqual(published_part_revision["fields"]["ManufacturingBaselineSourceHash"], "source-hash")
            self.assertEqual(service.get_definition("p-1")["baselineStatus"], "established")
            self.assertEqual(client.tables["LineDetail"][0], original_line)
            self.assertEqual(client.tables["PartRevisionLine"][0], original_membership)
            self.assertEqual(client.tables["ConfigurationPartSelection"][0], original_selection)
            new_membership = next(row for row in client.tables["PartRevisionLine"] if row["fields"].get("PartRevision") == published["publishedRevisionId"])
            self.assertEqual(new_membership["fields"]["RequirementKey"], "requirement:p-1:1")
            self.assertNotEqual(new_membership["fields"]["LineRevision"], 50)
            new_revision = client.tables["CostingConfiguration"][0]["fields"]["CurrentRevision"]
            self.assertNotEqual(new_revision, 101)
            new_selection = next(row for row in client.tables["ConfigurationPartSelection"] if row["fields"].get("ConfigurationRevision") == new_revision)
            self.assertEqual(new_selection["fields"]["PartRevision"], published["publishedRevisionId"])
            self.assertEqual(service.approve_and_publish(request["changeRequestKey"], actor="Irshad", request_key="approval-normal")["idempotent"], False)

    def test_partial_pointer_commit_recovers_from_durable_plan_without_duplicate_successors(self):
        with tempfile.TemporaryDirectory() as temp:
            client = DefinitionMemoryGrist()
            service = self.make_service(client, Path(temp) / "coordinator.sqlite3")
            _definition, request = self.draft_and_submit(service, request_suffix="retry")
            client.lose_after_update_table = "ProductPart"
            with self.assertRaisesRegex(TimeoutError, "committed ProductPart"):
                service.approve_and_publish(request["changeRequestKey"], actor="Irshad", request_key="approval-retry")

            recovered = service.approve_and_publish(request["changeRequestKey"], actor="Irshad", request_key="approval-retry")

            self.assertEqual(recovered["status"], "published")
            self.assertEqual(len([row for row in client.tables["PartRevision"] if row["fields"].get("PublicationCause") == request["changeRequestKey"]]), 1)
            self.assertEqual(len([row for row in client.tables["CostingConfigurationRevision"] if row["fields"].get("PublicationCause") == request["changeRequestKey"]]), 1)
            self.assertEqual(len([row for row in client.tables["PartDefinitionPublication"] if row["fields"].get("Status") == "published"]), 1)

    def test_started_publication_exposes_verified_original_and_rejects_replacement_before_write(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "coordinator.sqlite3"
            client = DefinitionMemoryGrist()
            service = self.make_service(client, path)
            _, request = self.draft_and_submit(service, request_suffix="original-owner")
            client.lose_after_create_table = "LineRevision"
            with self.assertRaises(TimeoutError):
                service.approve_and_publish(request["changeRequestKey"], actor="Irshad", request_key="original-approval")
            before = deepcopy(client.tables)
            with self.assertRaises(PartIdentityError) as conflict:
                service.approve_and_publish(request["changeRequestKey"], actor="Irshad", request_key="replacement-approval")
            self.assertEqual(conflict.exception.code, "PART_PUBLICATION_RECOVER_ORIGINAL")
            self.assertEqual(client.tables, before)
            recovery = self.make_service(client, path).publication_recovery("p-1", actor="Irshad")
            self.assertEqual(recovery["items"][0]["requestKey"], "original-approval")
            self.assertTrue(recovery["items"][0]["canRetry"])
            other = service.publication_recovery("p-1", actor="other")
            self.assertFalse(other["items"][0]["canRetry"])
            self.assertEqual(other["items"][0]["requestKey"], "")
            with self.assertRaises(PartIdentityError):
                service.approve_and_publish(request["changeRequestKey"], actor="other", request_key="original-approval")
            result = self.make_service(client, path).approve_and_publish(request["changeRequestKey"], actor="Irshad", request_key="original-approval")
            self.assertEqual(result["status"], "published")
            self.assertEqual(len([row for row in client.tables["LineRevision"] if row["fields"].get("RequestKey") == "original-approval"]), 1)

    def test_recovery_rejects_a_tampered_plan_before_disclosing_or_writing(self):
        with tempfile.TemporaryDirectory() as temp:
            client = DefinitionMemoryGrist()
            service = self.make_service(client, Path(temp) / "coordinator.sqlite3")
            _, request = self.draft_and_submit(service, request_suffix="tampered-owner")
            client.lose_after_create_table = "LineRevision"
            with self.assertRaises(TimeoutError):
                service.approve_and_publish(request["changeRequestKey"], actor="Irshad", request_key="original-approval")
            journal = client.tables["PartDefinitionPublication"][0]["fields"]
            plan = json.loads(journal["Plan"])
            plan["operationKey"] = "replacement-approval"
            journal["Plan"] = json.dumps(plan)
            before = deepcopy(client.tables)
            with self.assertRaises(PartIdentityError) as error:
                service.publication_recovery("p-1", actor="Irshad")
            self.assertEqual(error.exception.code, "PART_PUBLICATION_PLAN_CONFLICT")
            self.assertEqual(client.tables, before)

    def test_approval_written_before_journal_keeps_its_original_key(self):
        with tempfile.TemporaryDirectory() as temp:
            client = DefinitionMemoryGrist()
            service = self.make_service(client, Path(temp) / "coordinator.sqlite3")
            _, request = self.draft_and_submit(service, request_suffix="approved-before-journal")
            client.lose_after_update_table = "PartEngineeringChangeRequest"
            with self.assertRaises(TimeoutError):
                service.approve_and_publish(request["changeRequestKey"], actor="Irshad", request_key="approval-first")
            projected = service.get_definition("p-1", actor="Irshad")["pendingChangeRequests"][0]
            self.assertEqual(projected["approvalRecoveryKey"], "approval-first")
            self.assertNotIn("approvalRecoveryKey", service.get_definition("p-1", actor="other")["pendingChangeRequests"][0])
            before = deepcopy(client.tables)
            with self.assertRaises(PartIdentityError) as error:
                service.approve_and_publish(request["changeRequestKey"], actor="Irshad", request_key="approval-second")
            self.assertEqual(error.exception.code, "PART_PUBLICATION_RECOVER_ORIGINAL")
            self.assertEqual(client.tables, before)
            self.assertEqual(service.approve_and_publish(request["changeRequestKey"], actor="Irshad", request_key="approval-first")["status"], "published")

    def test_lost_configuration_pointer_response_recovers_its_own_successor_across_configs(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "coordinator.sqlite3"
            client = DefinitionMemoryGrist()
            client.tables["ProductModelCode"].append({"id": 91, "fields": {"Code": "M-2", "Active": True}})
            client.tables["CostingConfiguration"].append({"id": 110, "fields": {"ProductModelCode": 91, "Status": "active", "CurrentRevision": 111, "Version": 1}})
            client.tables["CostingConfigurationRevision"].append({"id": 111, "fields": {"Configuration": 110, "Revision": 1, "Status": "published", "Currency": "INR"}})
            client.tables["ConfigurationPartSelection"].append({"id": 112, "fields": {"SelectionKey": "sel:2", "SelectionIdentity": "selection:p-1:m2", "ConfigurationRevision": 111, "ProductPart": 10, "StablePartId": "p-1", "PartRevision": 20, "Quantity": 5, "QuantityUOM": "each", "SourcingRoute": "make", "OccurrenceLabel": "second code", "OptionGroup": "", "Status": "active"}})
            service = self.make_service(client, path)
            _definition, request = self.draft_and_submit(service, request_suffix="config-pointer-retry")
            client.lose_after_update_table = "CostingConfiguration"
            with self.assertRaisesRegex(TimeoutError, "committed CostingConfiguration"):
                service.approve_and_publish(request["changeRequestKey"], actor="Irshad", request_key="approval-config-pointer-retry")
            with self.assertRaises(PartIdentityError) as pending:
                service.registry.assert_definition_generation_ready(code_record_id=90)
            self.assertEqual(pending.exception.code, "PART_CONFIGURATION_PUBLICATION_PENDING")

            recovery = service.publication_recovery("p-1", actor="Irshad")
            self.assertEqual(recovery["status"], "recovering")
            self.assertEqual(recovery["items"][0]["changeRequestKey"], request["changeRequestKey"])
            self.assertEqual(recovery["items"][0]["requestKey"], "approval-config-pointer-retry")
            self.assertTrue(recovery["items"][0]["canRetry"])

            restarted = self.make_service(client, path)
            result = restarted.approve_and_publish(request["changeRequestKey"], actor="Irshad", request_key="approval-config-pointer-retry")

            self.assertEqual((result["status"], result["configurationCount"]), ("published", 2))
            self.assertEqual([row["fields"]["Version"] for row in client.tables["CostingConfiguration"]], [2, 2])
            self.assertEqual(len([row for row in client.tables["CostingConfigurationRevision"] if row["fields"].get("PublicationCause") == request["changeRequestKey"]]), 2)
            self.assertEqual(len([row for row in client.tables["PartDefinitionPublication"] if row["fields"].get("Status") == "published"]), 1)

    def test_publication_retry_rejects_a_foreign_configuration_generation(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "coordinator.sqlite3"
            client = DefinitionMemoryGrist()
            service = self.make_service(client, path)
            _definition, request = self.draft_and_submit(service, request_suffix="foreign-config-generation")
            client.lose_after_update_table = "CostingConfiguration"
            with self.assertRaises(TimeoutError):
                service.approve_and_publish(request["changeRequestKey"], actor="Irshad", request_key="approval-foreign-config")
            foreign = client.create_table_records("CostingConfigurationRevision", [{"fields": {"ConfigurationRevisionKey": "foreign-generation", "Configuration": 100,
                "Revision": 99, "Status": "published", "Currency": "INR", "PublicationCause": "some-other-request"}}])["records"][0]
            client.update_table_records("CostingConfiguration", [{"id": 100, "fields": {"CurrentRevision": foreign["id"], "Version": 99}}])

            with self.assertRaises(PartIdentityError) as error:
                self.make_service(client, path).approve_and_publish(request["changeRequestKey"], actor="Irshad", request_key="approval-foreign-config")

            self.assertEqual(error.exception.code, "PART_CONFIGURATION_STALE_BASE")

    def test_stale_saved_draft_cannot_be_submitted_against_a_new_current_revision(self):
        with tempfile.TemporaryDirectory() as temp:
            client = DefinitionMemoryGrist()
            service = self.make_service(client, Path(temp) / "coordinator.sqlite3")
            definition = service.get_definition("p-1")
            content = {"baseRevisionId": definition["revisionId"], "applicability": definition["applicability"],
                "requirements": definition["requirements"], "components": [], "purchases": [], "finalizeOnPublication": False, "expectedSaveVersion": 0}
            saved = service.save_draft("p-1", content, actor="operator", reason="hold", request_key="draft-stale")
            created = client.create_table_records("PartRevision", [{"fields": {"RevisionKey": "external:B", "ProductPart": 10,
                "Revision": 2, "RevisionLabel": "B", "Status": "accepted", "BaselineStatus": "draft", "ManufacturingBaseline": 1}}])
            client.update_table_records("ProductPart", [{"id": 10, "fields": {"CurrentPartRevision": created["records"][0]["id"], "EngineeringRevision": "B"}}])

            with self.assertRaises(PartIdentityError) as conflict:
                service.submit_change_request("p-1", {"draftKey": saved["draftKey"]}, actor="operator", reason="hold", request_key="cr-stale")

            self.assertEqual(conflict.exception.code, "PART_DEFINITION_STALE_BASE")

    def test_stale_change_request_reopens_as_new_draft_and_requires_fresh_approval(self):
        with tempfile.TemporaryDirectory() as temp:
            client = DefinitionMemoryGrist()
            service = self.make_service(client, Path(temp) / "coordinator.sqlite3")
            _definition, stale_request = self.draft_and_submit(service, request_suffix="reopen")
            original_after = deepcopy(stale_request["after"])
            client.update_table_records("ConfigurationPartSelection", [{"id": 102, "fields": {"Quantity": 3}}])

            with self.assertRaises(PartIdentityError) as stale:
                service.approve_and_publish(stale_request["changeRequestKey"], actor="Irshad", request_key="approval-stale-reopen")

            self.assertEqual(stale.exception.code, "PART_DEPENDENCY_CLOSURE_STALE")
            old_cr = next(row["fields"] for row in client.tables["PartEngineeringChangeRequest"]
                if row["fields"]["ChangeRequestKey"] == stale_request["changeRequestKey"])
            self.assertEqual(old_cr["Status"], "stale")
            self.assertNotEqual(old_cr.get("ApprovedBy"), "Irshad")
            stale_view = service.get_definition("p-1", actor="Irshad")["staleChangeRequests"][0]
            self.assertEqual(stale_view["after"], original_after)
            self.assertEqual(str(stale_view["before"]["requirements"][0]["quantity"]), "1")

            reopened = service.reopen_stale_change_request("p-1", stale_request["changeRequestKey"], actor="operator",
                reason="Review current configuration closure", request_key="reopen-stale")
            self.assertEqual(reopened["content"]["requirements"][0]["quantity"], 2)
            reopened_retry = service.reopen_stale_change_request("p-1", stale_request["changeRequestKey"], actor="operator",
                reason="Review current configuration closure", request_key="reopen-stale")
            self.assertTrue(reopened_retry["idempotent"])
            self.assertNotEqual(reopened["draftKey"], next(row["fields"]["DraftKey"] for row in client.tables["PartDefinitionDraft"]
                if row["fields"].get("Status") == "submitted"))
            corrected = deepcopy(reopened["content"])
            corrected["draftKey"] = reopened["draftKey"]
            corrected["expectedSaveVersion"] = reopened["saveVersion"]
            corrected["requirements"][0]["quantity"] = 3
            saved = service.save_draft("p-1", corrected, actor="operator", reason="Corrected after closure review", request_key="save-reopened")
            fresh_request = service.submit_change_request("p-1", {"draftKey": saved["draftKey"]}, actor="operator",
                reason="Corrected definition with refreshed impact", request_key="cr-reopened")
            self.assertEqual(fresh_request["after"]["requirements"][0]["quantity"], 3)
            self.assertEqual(fresh_request["impact"]["modelCodeConfigurations"][0]["quantity"], 3)
            published = service.approve_and_publish(fresh_request["changeRequestKey"], actor="Irshad", request_key="approval-reopened")

            self.assertEqual(published["status"], "published")
            self.assertEqual(old_cr["AfterDefinition"], next(row["fields"]["AfterDefinition"] for row in client.tables["PartEngineeringChangeRequest"]
                if row["fields"]["ChangeRequestKey"] == stale_request["changeRequestKey"]))
            with self.assertRaises(PartIdentityError) as second_reopen:
                service.reopen_stale_change_request("p-1", stale_request["changeRequestKey"], actor="operator",
                    reason="Second reopen", request_key="reopen-stale-again")
            self.assertEqual(second_reopen.exception.code, "PART_CHANGE_REQUEST_ALREADY_REOPENED")

    def test_publication_parts_are_ordered_child_first_for_shared_diamond(self):
        parts = [
            {"partId": "top", "content": {"components": [{"childPartId": "target"}, {"childPartId": "sub"}]}},
            {"partId": "target", "content": {"components": []}},
            {"partId": "sub", "content": {"components": [{"childPartId": "target"}]}},
        ]

        ordered = GristPartDefinitionService._child_first_part_order(parts)

        self.assertEqual([item["partId"] for item in ordered], ["target", "sub", "top"])

    def test_impact_preview_exposes_stable_component_paths_and_proposed_revisions(self):
        with tempfile.TemporaryDirectory() as temp:
            client = DefinitionMemoryGrist()
            client.tables["ProductPart"].extend([
                {"id": 11, "fields": {"StablePartId": "assembly-1", "PartNumber": "SM-A-000001", "Status": "active", "IdentityKind": "assembly", "CurrentPartRevision": 21}},
                {"id": 12, "fields": {"StablePartId": "assembly-2", "PartNumber": "SM-A-000002", "Status": "active", "IdentityKind": "assembly", "CurrentPartRevision": 22}},
            ])
            client.tables["PartRevision"].extend([
                {"id": 21, "fields": {"ProductPart": 11, "Revision": 1, "RevisionLabel": "A", "BaselineStatus": "finalized"}},
                {"id": 22, "fields": {"ProductPart": 12, "Revision": 2, "RevisionLabel": "B", "BaselineStatus": "draft"}},
            ])
            client.tables["PartComponentRevision"].extend([
                {"id": 111, "fields": {"ComponentIdentity": "assembly-1:bracket", "ParentRevision": 21, "ChildRevision": 20, "Quantity": 2, "QuantityUOM": "Nos", "SourcingRoute": "make", "Status": "active"}},
                {"id": 112, "fields": {"ComponentIdentity": "assembly-2:subassembly", "ParentRevision": 22, "ChildRevision": 21, "Quantity": 1, "QuantityUOM": "set", "SourcingRoute": "make", "Status": "active"}},
            ])
            service = self.make_service(client, Path(temp) / "coordinator.sqlite3")

            impact = service.impact_preview("p-1")

            self.assertEqual(impact["target"]["proposedRevisionLabel"], "B")
            direct = next(item for item in impact["assemblies"] if item["partId"] == "assembly-1")
            top = next(item for item in impact["assemblies"] if item["partId"] == "assembly-2")
            self.assertEqual((direct["currentRevisionLabel"], direct["proposedRevisionLabel"]), ("A", "B"))
            self.assertEqual(top["paths"], [["assembly-2", "assembly-1", "p-1"]])
            self.assertEqual(top["pathDetails"][0]["edges"][0]["componentIdentity"], "assembly-2:subassembly")
            self.assertEqual(top["pathDetails"][0]["edges"][0]["proposedChildRevisionLabel"], "B")

    def test_impact_preview_deduplicates_a_shared_assembly_but_retains_diamond_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            client = DefinitionMemoryGrist()
            client.tables["ProductPart"].extend([
                {"id": 11, "fields": {"StablePartId": "branch-a", "PartNumber": "SM-A-000001", "Status": "active", "IdentityKind": "assembly", "CurrentPartRevision": 21}},
                {"id": 12, "fields": {"StablePartId": "branch-b", "PartNumber": "SM-A-000002", "Status": "active", "IdentityKind": "assembly", "CurrentPartRevision": 22}},
                {"id": 13, "fields": {"StablePartId": "shared-top", "PartNumber": "SM-A-000003", "Status": "active", "IdentityKind": "assembly", "CurrentPartRevision": 23}},
            ])
            client.tables["PartRevision"].extend([
                {"id": 21, "fields": {"ProductPart": 11, "Revision": 1, "RevisionLabel": "A", "BaselineStatus": "draft"}},
                {"id": 22, "fields": {"ProductPart": 12, "Revision": 1, "RevisionLabel": "A", "BaselineStatus": "draft"}},
                {"id": 23, "fields": {"ProductPart": 13, "Revision": 1, "RevisionLabel": "A", "BaselineStatus": "draft"}},
            ])
            client.tables["PartComponentRevision"].extend([
                {"id": 111, "fields": {"ComponentIdentity": "branch-a:target", "ParentRevision": 21, "ChildRevision": 20, "Quantity": 2, "QuantityUOM": "Nos", "Status": "active"}},
                {"id": 112, "fields": {"ComponentIdentity": "branch-b:target", "ParentRevision": 22, "ChildRevision": 20, "Quantity": 3, "QuantityUOM": "Nos", "Status": "active"}},
                {"id": 113, "fields": {"ComponentIdentity": "shared-top:a", "ParentRevision": 23, "ChildRevision": 21, "Quantity": 1, "QuantityUOM": "set", "Status": "active"}},
                {"id": 114, "fields": {"ComponentIdentity": "shared-top:b", "ParentRevision": 23, "ChildRevision": 22, "Quantity": 1, "QuantityUOM": "set", "Status": "active"}},
            ])
            service = self.make_service(client, Path(temp) / "coordinator.sqlite3")

            impact = service.impact_preview("p-1")

            self.assertEqual(len(impact["assemblies"]), 3)
            top = next(item for item in impact["assemblies"] if item["partId"] == "shared-top")
            self.assertEqual({tuple(path) for path in top["paths"]}, {
                ("shared-top", "branch-a", "p-1"), ("shared-top", "branch-b", "p-1")})
            self.assertEqual({item["partId"] for item in impact["assemblies"]}, {"branch-a", "branch-b", "shared-top"})

    def test_impact_preview_propagates_through_current_part_into_assembly_and_config(self):
        with tempfile.TemporaryDirectory() as temp:
            client = DefinitionMemoryGrist()
            client.tables["ProductPart"].extend([
                {"id": 11, "fields": {"StablePartId": "nested-part", "PartNumber": "SM-P-000002", "Status": "active", "IdentityKind": "part", "CurrentPartRevision": 21}},
                {"id": 12, "fields": {"StablePartId": "nested-assembly", "PartNumber": "SM-A-000001", "Status": "active", "IdentityKind": "assembly", "CurrentPartRevision": 22}},
            ])
            client.tables["PartRevision"].extend([
                {"id": 21, "fields": {"ProductPart": 11, "Revision": 1, "RevisionLabel": "A", "BaselineStatus": "finalized"}},
                {"id": 22, "fields": {"ProductPart": 12, "Revision": 1, "RevisionLabel": "A", "BaselineStatus": "draft"}},
            ])
            client.tables["PartComponentRevision"].extend([
                {"id": 111, "fields": {"ComponentIdentity": "nested-part:target", "ParentRevision": 21, "ChildRevision": 20, "Quantity": 2, "QuantityUOM": "Nos", "SourcingRoute": "make", "Status": "active"}},
                {"id": 112, "fields": {"ComponentIdentity": "nested-assembly:part", "ParentRevision": 22, "ChildRevision": 21, "Quantity": 3, "QuantityUOM": "set", "SourcingRoute": "make", "Status": "active"}},
            ])
            config_selection = client.tables["ConfigurationPartSelection"][0]["fields"]
            config_selection.update({"ProductPart": 11, "StablePartId": "nested-part", "PartRevision": 21, "SelectionIdentity": "selection:nested-part"})
            service = self.make_service(client, Path(temp) / "coordinator.sqlite3")

            impact = service.impact_preview("p-1")

            self.assertEqual(impact["status"], "complete")
            self.assertEqual({item["partId"] for item in impact["assemblies"]}, {"nested-assembly"})
            self.assertEqual({item["partId"] for item in impact["dependents"]}, {"nested-part", "nested-assembly"})
            nested_part = next(item for item in impact["dependents"] if item["partId"] == "nested-part")
            nested_assembly = next(item for item in impact["dependents"] if item["partId"] == "nested-assembly")
            self.assertEqual(nested_part["paths"], [["nested-part", "p-1"]])
            self.assertEqual(nested_assembly["paths"], [["nested-assembly", "nested-part", "p-1"]])
            code_use = impact["modelCodeConfigurations"][0]
            self.assertEqual(code_use["partId"], "nested-part")
            self.assertEqual(code_use["dependencyPaths"], [["nested-part", "p-1"]])

    def test_line_materialization_serializes_engineering_attributes_as_a_grist_cell(self):
        with tempfile.TemporaryDirectory() as temp:
            client = DefinitionMemoryGrist()
            service = self.make_service(client, Path(temp) / "coordinator.sqlite3")
            item = {"requirementKey": "requirement:p-1:1", "lineMasterId": 40, "lineRevisionId": 50,
                "detail": {"ItemName": "Updated bracket blank", "EngineeringAttributes": {"fieldStatus": {"ToolShopRemarks": "missing"}}}}

            service._materialize_line("p-1", 30, "cr:grist-cell", item, "Irshad", "QA", "request:grist-cell")

            saved = next(row for row in client.tables["LineDetail"] if row["fields"].get("DetailKey") == "part-definition-line-detail:cr:grist-cell:requirement:p-1:1")
            self.assertIsInstance(saved["fields"]["EngineeringAttributes"], str)
            self.assertEqual(json.loads(saved["fields"]["EngineeringAttributes"]), {"fieldStatus": {"ToolShopRemarks": "missing"}})

    def test_duplicate_legacy_requirement_keys_are_reviewed_without_rewriting_history(self):
        with tempfile.TemporaryDirectory() as temp:
            client = DefinitionMemoryGrist()
            duplicate = deepcopy(client.tables["PartRevisionLine"][0])
            duplicate["id"] = 71
            duplicate["fields"]["RevisionLineKey"] = "membership:duplicate"
            client.tables["PartRevisionLine"].append(duplicate)
            original_links = deepcopy(client.tables["PartRevisionLine"])
            service = self.make_service(client, Path(temp) / "coordinator.sqlite3")

            definition = service.get_definition("p-1")
            self.assertEqual({item["identityStatus"] for item in definition["requirements"]}, {"legacy_duplicate_unclassified"})
            self.assertEqual(len({item["requirementKey"] for item in definition["requirements"]}), 2)
            content = {"baseRevisionId": definition["revisionId"], "applicability": definition["applicability"],
                "requirements": deepcopy(definition["requirements"]), "components": [], "purchases": [], "finalizeOnPublication": False, "expectedSaveVersion": 0}
            content["requirements"][0]["requirementKey"] = "safari-authored-requirement:reviewed-1"
            content["requirements"][0]["identityStatus"] = "safari_authored"

            saved = service.save_draft("p-1", content, actor="operator", reason="Classify duplicate legacy occurrence", request_key="draft-legacy-duplicate")

            self.assertTrue(saved["draftKey"])
            self.assertEqual(client.tables["PartRevisionLine"], original_links)

            content["requirements"][1]["requirementKey"] = "changed-without-review"
            content["expectedSaveVersion"] = 1
            with self.assertRaises(PartIdentityError) as conflict:
                service.save_draft("p-1", content, actor="operator", reason="Unreviewed identity change", request_key="draft-legacy-duplicate-invalid")
            self.assertEqual(conflict.exception.code, "PART_DEFINITION_LEGACY_IDENTITY_REVIEW_REQUIRED")

    def test_established_rev_a_is_locked_in_all_legacy_physical_writers(self):
        with tempfile.TemporaryDirectory() as temp:
            client = DefinitionMemoryGrist()
            registry = GristPartRegistry(client, path=Path(temp) / "coordinator.sqlite3")
            registry._verify_writer = lambda: None
            features = GristPartFeatures(registry)
            client.tables["PartMetadataVersion"].append({"id": 32, "fields": {"ProductPart": 12, "Version": 1, "DisplayName": "Other", "Status": "current"}})
            client.tables["ProductPart"].append({"id": 12, "fields": {"StablePartId": "p-2", "PartNumber": "SM-P-000002", "DisplayName": "Other", "Status": "active", "IdentityKind": "part", "CurrentMetadataVersion": 32, "CurrentPartRevision": 22, "EngineeringRevision": "A"}})
            client.tables["PartRevision"].append({"id": 22, "fields": {"RevisionKey": "initial:p-2:A", "ProductPart": 12, "Revision": 1, "RevisionLabel": "A", "Status": "accepted", "BaselineStatus": "draft"}})
            before = deepcopy(client.tables)
            operations = [
                lambda: features.add_component(parent_part_id="p-1", child_part_id="p-2", quantity=1, uom="each",
                    actor="operator", reason="legacy mutation", request_key="legacy-component"),
                lambda: features.link_process_line(part_id="p-1", line_master_id=40, line_revision_id=50,
                    actor="operator", reason="legacy mutation", request_key="legacy-line"),
                lambda: features.add_drawing(part_id="p-1", identity="drawing-1", link_type="external_url",
                    external_url="https://example.test/drawing", actor="operator", reason="legacy mutation", request_key="legacy-drawing"),
                lambda: features.create_purchase_specification(part_id="p-1", code="SPEC-1", manufacturer="Maker",
                    manufacturer_part_number="M-1", description="spec", costing_uom="each", currency="INR",
                    purchase_item_id=None, actor="operator", reason="legacy mutation", request_key="legacy-spec"),
            ]
            for operation in operations:
                with self.subTest(operation=operation), self.assertRaises(PartIdentityError) as error:
                    operation()
                self.assertEqual(error.exception.code, "PART_REVISION_LOCKED")
            self.assertEqual(client.tables, before)

    def test_unestablished_draft_rev_a_keeps_initial_line_authoring_available(self):
        with tempfile.TemporaryDirectory() as temp:
            client = DefinitionMemoryGrist()
            client.tables["PartBaselineProcessing"][0]["fields"]["Status"] = "draft"
            registry = GristPartRegistry(client, path=Path(temp) / "coordinator.sqlite3")
            registry._verify_writer = lambda: None
            features = GristPartFeatures(registry)

            result = features.link_process_line(part_id="p-1", line_master_id=40, line_revision_id=50,
                actor="operator", reason="initial line authoring", request_key="initial-line")

            self.assertEqual(result["line"]["PartRevision"], 20)
            self.assertEqual(len(client.tables["PartRevisionLine"]), 2)

    def test_changed_legacy_line_fields_validate_before_any_draft_write(self):
        invalid_edits = [
            ("quantity", lambda row: row.update({"quantity": "NaN"})),
            ("dimension", lambda row: row["detail"].update({"Length": "-25"})),
            ("consumption", lambda row: row["detail"].update({"Quantity": "NaN"})),
            ("material_ref", lambda row: row["detail"].update({"Material": {"bad": "reference"}})),
        ]
        for label, edit in invalid_edits:
            with self.subTest(field=label), tempfile.TemporaryDirectory() as temp:
                client = DefinitionMemoryGrist()
                service = self.make_service(client, Path(temp) / "coordinator.sqlite3")
                definition = service.get_definition("p-1")
                content = {"baseRevisionId": definition["revisionId"], "expectedSaveVersion": 0,
                    "applicability": definition["applicability"], "requirements": deepcopy(definition["requirements"]),
                    "components": [], "purchases": [], "finalizeOnPublication": False}
                edit(content["requirements"][0])
                before = deepcopy(client.tables)
                with self.assertRaises(PartIdentityError):
                    service.save_draft("p-1", content, actor="operator", reason="invalid physical edit", request_key=f"invalid-{label}")
                self.assertEqual(client.tables, before)

    def test_existing_grist_zero_dimensions_can_be_preserved_but_not_introduced(self):
        with tempfile.TemporaryDirectory() as temp:
            client = DefinitionMemoryGrist()
            client.tables["LineDetail"][0]["fields"].update({"Length": 0, "Width": 0, "Thickness": 0})
            service = self.make_service(client, Path(temp) / "coordinator.sqlite3")
            definition = service.get_definition("p-1")
            content = {"baseRevisionId": definition["revisionId"], "expectedSaveVersion": 0,
                "applicability": definition["applicability"], "requirements": deepcopy(definition["requirements"]),
                "components": [], "purchases": [], "finalizeOnPublication": False}
            content["requirements"][0]["detail"].update({"ItemName": "Changed item", "Quantity": 1, "QuantityUOM": "kg"})
            saved = service.save_draft("p-1", content, actor="operator", reason="edit", request_key="preserve-zero")
            self.assertEqual(saved["content"]["requirements"][0]["detail"]["Length"], 0)
            content["expectedSaveVersion"] = 1
            content["requirements"][0]["detail"]["Length"] = -1
            with self.assertRaises(PartIdentityError):
                service.save_draft("p-1", content, actor="operator", reason="edit", request_key="negative-dimension")

    def test_new_and_changed_process_requirements_need_real_rate_basis(self):
        with tempfile.TemporaryDirectory() as temp:
            client = DefinitionMemoryGrist()
            service = self.make_service(client, Path(temp) / "coordinator.sqlite3")
            definition = service.get_definition("p-1")
            requirement = {"requirementKey": "safari-authored-requirement:new-mcl", "identityStatus": "safari_authored",
                "family": "mcl", "quantity": "1", "uom": "Nos", "processType": "material_cut",
                "detail": {"Material": 5, "Quantity": "2", "QuantityUOM": "kg", "ItemName": "Steel blank"},
                "provenance": {"type": "safari_authored", "evidence": {}}}
            content = {"baseRevisionId": definition["revisionId"], "expectedSaveVersion": 0,
                "applicability": definition["applicability"], "requirements": [requirement],
                "components": [], "purchases": [], "finalizeOnPublication": False}
            saved = service.save_draft("p-1", content, actor="operator", reason="add measured MCL", request_key="draft-new-mcl")
            self.assertEqual((saved["content"]["requirements"][0]["detail"]["Quantity"],
                saved["content"]["requirements"][0]["detail"]["QuantityUOM"]), (2.0, "kg"))

            content["expectedSaveVersion"] = 1
            content["requirements"][0]["detail"]["Quantity"] = ""
            with self.assertRaises(PartIdentityError) as error:
                service.save_draft("p-1", content, actor="operator", reason="remove MCL basis", request_key="draft-new-mcl-invalid")
            self.assertEqual(error.exception.code, "PART_DEFINITION_RATE_BASIS_INVALID")

    def test_new_toolshop_and_cnc_requirements_keep_explicit_rate_units(self):
        for family, process_type, uom in (("toolshop", "tool_shop", "hour"), ("cnc", "cnc", "minute")):
            with self.subTest(family=family), tempfile.TemporaryDirectory() as temp:
                client = DefinitionMemoryGrist()
                service = self.make_service(client, Path(temp) / "coordinator.sqlite3")
                definition = service.get_definition("p-1")
                applicability = deepcopy(definition["applicability"])
                applicability[family] = {"applicable": True, "status": "applicable"}
                requirement = {"requirementKey": f"safari-authored-requirement:new-{family}", "identityStatus": "safari_authored",
                    "family": family, "quantity": "1", "uom": "Nos", "processType": process_type,
                    "detail": {"Quantity": "2.5", "QuantityUOM": uom, "ItemName": f"New {family} operation"},
                    "provenance": {"type": "safari_authored", "evidence": {}}}
                payload = {"baseRevisionId": definition["revisionId"], "expectedSaveVersion": 0,
                    "applicability": applicability, "requirements": [requirement], "components": [], "purchases": [], "finalizeOnPublication": False}

                saved = service.save_draft("p-1", payload, actor="operator", reason=f"add {family}", request_key=f"draft-{family}")

                normalized = saved["content"]["requirements"][0]
                self.assertEqual((normalized["processType"], normalized["detail"]["Quantity"], normalized["detail"]["QuantityUOM"]), (process_type, 2.5, uom))

    def test_draft_save_versions_reject_stale_edit_and_superseded_replay(self):
        with tempfile.TemporaryDirectory() as temp:
            client = DefinitionMemoryGrist()
            service = self.make_service(client, Path(temp) / "coordinator.sqlite3")
            definition = service.get_definition("p-1")
            content = {"baseRevisionId": definition["revisionId"], "expectedSaveVersion": 0,
                "applicability": definition["applicability"], "requirements": deepcopy(definition["requirements"]),
                "components": [], "purchases": [], "finalizeOnPublication": False}
            content["requirements"][0]["quantity"] = 2
            version_one = service.save_draft("p-1", content, actor="editor one", reason="first edit", request_key="draft-v1")
            content_v2 = deepcopy(content)
            content_v2["expectedSaveVersion"] = 1
            content_v2["requirements"][0]["quantity"] = 3
            version_two = service.save_draft("p-1", content_v2, actor="editor two", reason="second edit", request_key="draft-v2")
            stale = {**content, "expectedSaveVersion": 1}
            stale["requirements"] = deepcopy(content["requirements"])
            stale["requirements"][0]["quantity"] = 4
            with self.assertRaises(PartIdentityError) as error:
                service.save_draft("p-1", stale, actor="editor one", reason="out of order retry", request_key="draft-v1-stale")
            self.assertEqual(error.exception.code, "PART_DEFINITION_DRAFT_STALE")
            self.assertEqual(service.get_definition("p-1")["draft"]["saveVersion"], 2)
            with self.assertRaises(PartIdentityError) as replay_error:
                service.save_draft("p-1", content, actor="editor one", reason="first edit", request_key="draft-v1")
            self.assertEqual(replay_error.exception.code, "PART_DEFINITION_DRAFT_REPLAY_SUPERSEDED")
            conflicting = deepcopy(content)
            conflicting["requirements"][0]["quantity"] = 99
            with self.assertRaises(PartIdentityError) as key_conflict:
                service.save_draft("p-1", conflicting, actor="editor one", reason="changed payload", request_key="draft-v1")
            self.assertEqual(key_conflict.exception.code, "PART_REQUEST_CONFLICT")
            replay_current = service.save_draft("p-1", content_v2, actor="editor two", reason="second edit", request_key="draft-v2")
            self.assertTrue(replay_current["idempotent"])
            self.assertEqual((version_one["saveVersion"], version_two["saveVersion"], replay_current["saveVersion"]), (1, 2, 2))

    def test_lost_draft_save_response_recovers_with_original_request_and_payload(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "coordinator.sqlite3"
            client = DefinitionMemoryGrist()
            service = self.make_service(client, path)
            definition = service.get_definition("p-1")
            client.tables["PartDefinitionDraft"].append({"id": 150, "fields": {"DraftKey": "part-definition-draft:p-1:20", "ProductPart": 10,
                "BaseRevision": 20, "SaveVersion": 0, "Status": "open", "Content": "{}", "ContentFingerprint": ""}})
            content = {"baseRevisionId": definition["revisionId"], "expectedSaveVersion": 0,
                "applicability": definition["applicability"], "requirements": deepcopy(definition["requirements"]),
                "components": [], "purchases": [], "finalizeOnPublication": False}
            client.lose_after_update_table = "PartDefinitionDraft"
            with self.assertRaisesRegex(TimeoutError, "committed PartDefinitionDraft"):
                service.save_draft("p-1", content, actor="operator", reason="recover save", request_key="draft-recover")

            restarted = self.make_service(client, path)
            recovered = restarted.save_draft("p-1", content, actor="operator", reason="recover save", request_key="draft-recover")

            self.assertEqual((recovered["saveVersion"], recovered["status"], recovered["idempotent"]), (1, "saved", True))
            self.assertEqual(restarted.get_definition("p-1")["draft"]["saveVersion"], 1)

    def test_submitted_draft_is_immutable(self):
        with tempfile.TemporaryDirectory() as temp:
            client = DefinitionMemoryGrist()
            service = self.make_service(client, Path(temp) / "coordinator.sqlite3")
            definition = service.get_definition("p-1")
            content = {"baseRevisionId": definition["revisionId"], "expectedSaveVersion": 0,
                "applicability": definition["applicability"], "requirements": deepcopy(definition["requirements"]),
                "components": [], "purchases": [], "finalizeOnPublication": False}
            saved = service.save_draft("p-1", content, actor="operator", reason="submit", request_key="draft-submit")
            service.submit_change_request("p-1", {"draftKey": saved["draftKey"]}, actor="operator", reason="submit", request_key="cr-submit")
            content["expectedSaveVersion"] = 1
            with self.assertRaises(PartIdentityError) as error:
                service.save_draft("p-1", content, actor="operator", reason="change after submit", request_key="draft-after-submit")
            self.assertEqual(error.exception.code, "PART_DEFINITION_DRAFT_IMMUTABLE")

    def test_submission_replay_and_partial_writes_preserve_original_review(self):
        for interruption in ("completed", "journal-create", "PartEngineeringChangeRequest", "PartDefinitionDraft", "PartRegistryRequest"):
            with self.subTest(interruption=interruption), tempfile.TemporaryDirectory() as temp:
                client = DefinitionMemoryGrist()
                path = Path(temp) / "coordinator.sqlite3"
                service = self.make_service(client, path)
                definition = service.get_definition("p-1")
                content = {"baseRevisionId": 20, "expectedSaveVersion": 0, "applicability": definition["applicability"],
                    "requirements": deepcopy(definition["requirements"]), "components": [], "purchases": [], "finalizeOnPublication": False}
                saved = service.save_draft("p-1", content, actor="operator", reason="review", request_key="draft")
                payload = {"draftKey": saved["draftKey"]}
                if interruption in ("journal-create", "PartEngineeringChangeRequest"):
                    client.lose_after_create_table = "PartRegistryRequest" if interruption == "journal-create" else interruption
                elif interruption != "completed":
                    client.lose_after_update_table = interruption
                if interruption == "completed":
                    original = service.submit_change_request("p-1", payload, actor="operator", reason="review", request_key="submit")
                else:
                    with self.assertRaises(TimeoutError):
                        service.submit_change_request("p-1", payload, actor="operator", reason="review", request_key="submit")
                    if interruption == "PartEngineeringChangeRequest":
                        request = next(row for row in client.tables["PartRegistryRequest"] if row["fields"].get("RequestKey") == "submit")
                        request["fields"].pop("Payload", None)  # Pre-fix pending journal compatibility.
                restarted = self.make_service(client, path)
                # A later configuration change must not alter the already captured review.
                client.tables["ConfigurationPartSelection"][0]["fields"]["Quantity"] = 7
                result = restarted.submit_change_request("p-1", payload, actor="operator", reason="review", request_key="submit")
                self.assertEqual(result["impact"]["modelCodeConfigurations"][0]["quantity"], 2)
                if interruption == "completed":
                    self.assertEqual(original, result)
                self.assertEqual(len(client.tables["PartEngineeringChangeRequest"]), 1)
                self.assertEqual(client.tables["PartDefinitionDraft"][0]["fields"]["Status"], "submitted")
                for actor, reason, draft in (("other", "review", payload["draftKey"]), ("operator", "other", payload["draftKey"]), ("operator", "review", "other")):
                    with self.assertRaises(PartIdentityError) as error:
                        restarted.submit_change_request("p-1", {"draftKey": draft}, actor=actor, reason=reason, request_key="submit")
                    self.assertEqual(error.exception.code, "PART_REQUEST_CONFLICT")
                with self.assertRaises(PartIdentityError) as error:
                    restarted.submit_change_request("other-part", payload, actor="operator", reason="review", request_key="submit")
                self.assertEqual(error.exception.code, "PART_REQUEST_CONFLICT")
                draft_fields = client.tables["PartDefinitionDraft"][0]["fields"]
                old_content = draft_fields["Content"]
                draft_fields["Content"] = json.dumps({"changed": True})
                with self.assertRaises(PartIdentityError) as error:
                    restarted.submit_change_request("p-1", payload, actor="operator", reason="review", request_key="submit")
                self.assertEqual(error.exception.code, "PART_REQUEST_CONFLICT")
                draft_fields["Content"] = old_content
                with self.assertRaises(PartIdentityError) as error:
                    restarted.submit_change_request("p-1", payload, actor="operator", reason="review", request_key="new-key")
                self.assertEqual(error.exception.code, "PART_DEFINITION_DRAFT_IMMUTABLE")

    def test_completed_submission_replays_after_publication_reuses_cr_journal_fields(self):
        with tempfile.TemporaryDirectory() as temp:
            client = DefinitionMemoryGrist()
            service = self.make_service(client, Path(temp) / "coordinator.sqlite3")
            _, original = self.draft_and_submit(service, request_suffix="published-replay")
            draft = client.tables["PartDefinitionDraft"][0]["fields"]["DraftKey"]
            # A request recorded before the journal payload was introduced must remain replayable.
            request = next(row for row in client.tables["PartRegistryRequest"] if row["fields"].get("RequestKey") == "cr-published-replay")
            request["fields"].pop("Payload", None)
            service.approve_and_publish(original["changeRequestKey"], actor="Irshad", request_key="approve-published-replay")
            replay = service.submit_change_request("p-1", {"draftKey": draft}, actor="operator",
                reason="Increase blank allowance", request_key="cr-published-replay")
            self.assertEqual(original, replay)
            self.assertEqual(len(client.tables["PartEngineeringChangeRequest"]), 1)
            cr_fields = client.tables["PartEngineeringChangeRequest"][0]["fields"]
            before = json.loads(cr_fields["BeforeDefinition"])
            before["partName"] = "altered history"
            cr_fields["BeforeDefinition"] = json.dumps(before)
            with self.assertRaises(PartIdentityError) as error:
                service.submit_change_request("p-1", {"draftKey": draft}, actor="operator",
                    reason="Increase blank allowance", request_key="cr-published-replay")
            self.assertEqual(error.exception.code, "PART_REQUEST_CONFLICT")

    def test_configuration_impact_includes_assemblies_selected_indirectly_and_preserves_other_configs(self):
        with tempfile.TemporaryDirectory() as temp:
            client = DefinitionMemoryGrist()
            client.tables["ProductPart"].extend([
                {"id": 11, "fields": {"StablePartId": "assembly-1", "PartNumber": "SM-A-000001", "DisplayName": "Assembly", "Status": "active", "IdentityKind": "assembly", "CurrentMetadataVersion": 32, "CurrentPartRevision": 21, "EngineeringRevision": "A"}},
                {"id": 13, "fields": {"StablePartId": "unrelated", "PartNumber": "SM-P-000002", "DisplayName": "Unrelated", "Status": "active", "IdentityKind": "part", "CurrentMetadataVersion": 33, "CurrentPartRevision": 23, "EngineeringRevision": "A"}},
            ])
            client.tables["PartRevision"].extend([
                {"id": 21, "fields": {"RevisionKey": "assembly-a", "ProductPart": 11, "Revision": 1, "RevisionLabel": "A", "Status": "accepted", "BaselineStatus": "draft"}},
                {"id": 23, "fields": {"RevisionKey": "unrelated-a", "ProductPart": 13, "Revision": 1, "RevisionLabel": "A", "Status": "accepted", "BaselineStatus": "draft"}},
            ])
            client.tables["PartComponentRevision"].append({"id": 111, "fields": {"ComponentIdentity": "assembly-1:bracket", "ParentRevision": 21, "ChildRevision": 20, "Quantity": 2, "QuantityUOM": "Nos", "SourcingRoute": "make", "Status": "active"}})
            client.tables["ConfigurationPartSelection"].extend([
                {"id": 103, "fields": {"SelectionKey": "sel:assembly", "SelectionIdentity": "assembly-use", "ConfigurationRevision": 101, "ProductPart": 11, "StablePartId": "assembly-1", "PartRevision": 21, "Quantity": 4, "QuantityUOM": "set", "SourcingRoute": "make", "OccurrenceLabel": "top assembly", "OptionGroup": "frame", "Status": "active"}},
                {"id": 112, "fields": {"SelectionKey": "sel:unrelated", "SelectionIdentity": "unrelated-use", "ConfigurationRevision": 111, "ProductPart": 13, "StablePartId": "unrelated", "PartRevision": 23, "Quantity": 7, "QuantityUOM": "each", "SourcingRoute": "make", "OccurrenceLabel": "other", "OptionGroup": "", "Status": "active"}},
            ])
            client.tables["ProductModelCode"].append({"id": 91, "fields": {"Code": "UNRELATED", "Active": True}})
            client.tables["CostingConfiguration"].append({"id": 110, "fields": {"ProductModelCode": 91, "Status": "active", "CurrentRevision": 111, "Version": 1}})
            client.tables["CostingConfigurationRevision"].append({"id": 111, "fields": {"Configuration": 110, "Revision": 1, "Status": "published", "Currency": "INR"}})
            service = self.make_service(client, Path(temp) / "coordinator.sqlite3")
            _definition, request = self.draft_and_submit(service, request_suffix="assembly-config")

            uses = request["impact"]["modelCodeConfigurations"]
            self.assertEqual({item["selectionIdentity"] for item in uses}, {"selection:p-1", "assembly-use"})
            self.assertEqual(next(item for item in uses if item["selectionIdentity"] == "assembly-use")["proposedPartRevisionLabel"], "B")
            original_selection = deepcopy(client.tables["ConfigurationPartSelection"])
            result = service.approve_and_publish(request["changeRequestKey"], actor="Irshad", request_key="approval-assembly-config")
            current_config_revision = client.tables["CostingConfiguration"][0]["fields"]["CurrentRevision"]
            new_selections = [row["fields"] for row in client.tables["ConfigurationPartSelection"] if row["fields"].get("ConfigurationRevision") == current_config_revision]
            by_identity = {row["SelectionIdentity"]: row for row in new_selections}
            self.assertEqual(by_identity["assembly-use"]["PartRevision"], next(row["id"] for row in client.tables["PartRevision"] if row["fields"].get("ProductPart") == 11 and row["fields"].get("PublicationCause") == request["changeRequestKey"]))
            self.assertEqual((by_identity["assembly-use"]["Quantity"], by_identity["assembly-use"]["QuantityUOM"], by_identity["assembly-use"]["OptionGroup"]), (4, "set", "frame"))
            self.assertEqual((client.tables["CostingConfiguration"][1]["fields"]["CurrentRevision"], client.tables["CostingConfiguration"][1]["fields"]["Version"]), (111, 1))
            self.assertEqual(client.tables["ConfigurationPartSelection"][:len(original_selection)], original_selection)
            self.assertEqual(result["configurationCount"], 1)


if __name__ == "__main__":
    unittest.main()

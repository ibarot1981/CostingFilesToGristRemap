from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import threading
import unittest

from app.grist_parts import GristPartRegistry
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
        saved = service.save_draft("p-1", content, actor="operator", reason="Increase blank allowance", request_key=f"draft-{request_suffix}")
        reloaded = service.get_definition("p-1")
        self.assertEqual(reloaded["draft"]["reason"], "Increase blank allowance")
        self.assertEqual(reloaded["draft"]["content"]["requirements"][0]["quantity"], 2)
        request = service.submit_change_request("p-1", {"draftKey": saved["draftKey"]}, actor="operator",
            reason="Increase blank allowance", request_key=f"cr-{request_suffix}")
        return definition, request

    def test_reviewed_publication_preserves_history_and_versions_current_configuration(self):
        with tempfile.TemporaryDirectory() as temp:
            client = DefinitionMemoryGrist()
            service = self.make_service(client, Path(temp) / "coordinator.sqlite3")
            original_line = deepcopy(client.tables["LineDetail"][0])
            original_membership = deepcopy(client.tables["PartRevisionLine"][0])
            original_selection = deepcopy(client.tables["ConfigurationPartSelection"][0])
            _definition, request = self.draft_and_submit(service)
            self.assertEqual(request["impact"]["modelCodeConfigurations"][0]["modelCode"], "M-1")
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

    def test_stale_saved_draft_cannot_be_submitted_against_a_new_current_revision(self):
        with tempfile.TemporaryDirectory() as temp:
            client = DefinitionMemoryGrist()
            service = self.make_service(client, Path(temp) / "coordinator.sqlite3")
            definition = service.get_definition("p-1")
            content = {"baseRevisionId": definition["revisionId"], "applicability": definition["applicability"],
                "requirements": definition["requirements"], "components": [], "purchases": [], "finalizeOnPublication": False}
            saved = service.save_draft("p-1", content, actor="operator", reason="hold", request_key="draft-stale")
            created = client.create_table_records("PartRevision", [{"fields": {"RevisionKey": "external:B", "ProductPart": 10,
                "Revision": 2, "RevisionLabel": "B", "Status": "accepted", "BaselineStatus": "draft", "ManufacturingBaseline": 1}}])
            client.update_table_records("ProductPart", [{"id": 10, "fields": {"CurrentPartRevision": created["records"][0]["id"], "EngineeringRevision": "B"}}])

            with self.assertRaises(PartIdentityError) as conflict:
                service.submit_change_request("p-1", {"draftKey": saved["draftKey"]}, actor="operator", reason="hold", request_key="cr-stale")

            self.assertEqual(conflict.exception.code, "PART_DEFINITION_STALE_BASE")

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
                "requirements": deepcopy(definition["requirements"]), "components": [], "purchases": [], "finalizeOnPublication": False}
            content["requirements"][0]["requirementKey"] = "safari-authored-requirement:reviewed-1"
            content["requirements"][0]["identityStatus"] = "safari_authored"

            saved = service.save_draft("p-1", content, actor="operator", reason="Classify duplicate legacy occurrence", request_key="draft-legacy-duplicate")

            self.assertTrue(saved["draftKey"])
            self.assertEqual(client.tables["PartRevisionLine"], original_links)

            content["requirements"][1]["requirementKey"] = "changed-without-review"
            with self.assertRaises(PartIdentityError) as conflict:
                service.save_draft("p-1", content, actor="operator", reason="Unreviewed identity change", request_key="draft-legacy-duplicate-invalid")
            self.assertEqual(conflict.exception.code, "PART_DEFINITION_LEGACY_IDENTITY_REVIEW_REQUIRED")


if __name__ == "__main__":
    unittest.main()

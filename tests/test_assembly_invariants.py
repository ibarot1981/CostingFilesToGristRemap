from __future__ import annotations

from types import SimpleNamespace
import unittest

from app.live_cost import GristLiveCostService
from app.part_mapping import MemoryPartStore, PartConflict, save_mapping, search_parts, source_groups
from app.part_identity import PartIdentityError


class AssemblyAssignmentGuardTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryPartStore()
        self.store.tables["ProductPart"].append({"id": 41, "fields": {
            "PartKey": "assembly-key", "StablePartId": "assembly-uuid", "IdentityKind": "assembly",
            "PartNumber": "SM-A-000001", "DisplayName": "SM — Main Assembly", "Description": "Main Assembly",
            "ScopeType": "global", "ScopeTargetId": "global", "Status": "active", "PublishStatus": "published",
        }})
        self.groups = source_groups({"5. Material Cut List Price": [{
            "status": "active", "source_row": 12,
            "fields": {"product_part_name": "Plate", "material_to_cut": "MS Plate", "dimension_to_cut_mm": "3 x 25", "qty": "2"},
            "available_fields": ["product_part_name", "material_to_cut", "dimension_to_cut_mm", "qty"],
        }]})

    def test_assemblies_are_not_searchable_as_mapping_assignments_or_savable_to_source_lines(self):
        results = search_parts(self.store, query="Main Assembly")
        self.assertEqual((results["total"], results["items"]), (0, []))
        group = self.groups[0]
        with self.assertRaises(PartConflict) as blocked:
            save_mapping(self.store, file_id="file:pilot.ods", source_hash="source-hash",
                association=SimpleNamespace(id="association-1", version=1), groups=self.groups,
                decisions={group["key"]: "assembly-uuid"}, expected_hash="source-hash", expected_version=0,
                expected_association="association-1", expected_association_version=1, actor="reviewer",
                reason="Attempt deferred assembly assignment", request_key="assembly-mapping")
        self.assertEqual(blocked.exception.code, "PART_ASSIGNMENT_DEFERRED")
        self.assertEqual(self.store.tables["PartMappingReview"], [])

    def test_assemblies_are_rejected_by_model_code_configuration_write(self):
        class Registry:
            client = object()
            tables = {
                "ProductModelCode": [{"id": 77, "fields": {"Active": True, "Code": "TEST"}}],
                "ProductPart": [{"id": 41, "fields": {"StablePartId": "assembly-uuid", "IdentityKind": "assembly", "Status": "active"}}],
            }

            def _verify_writer(self):
                return None

            def _rows(self, table):
                return self.tables.setdefault(table, [])

        registry = Registry()
        service = GristLiveCostService(registry)
        payload = {"currency": "INR", "reason": "deferred assembly selection", "selections": [
            {"selectionIdentity": "main", "partId": "assembly-uuid", "quantity": 1, "uom": "each", "sourcingRoute": "make"},
        ]}
        with self.assertRaises(PartIdentityError) as blocked:
            service.save_configuration(77, payload, actor="reviewer", request_key="assembly-config")
        self.assertEqual(blocked.exception.code, "COST_CONFIGURATION_ASSEMBLY_DEFERRED")
        self.assertNotIn("CostingConfiguration", registry.tables)


if __name__ == "__main__":
    unittest.main()

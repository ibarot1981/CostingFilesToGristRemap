import unittest
from types import SimpleNamespace
from unittest import mock
from fastapi import HTTPException
from starlette.requests import Request
from app.part_mapping import MemoryPartStore, GristPartStore, PartConflict, PartRegistryMappingStore, source_groups, list_parts, search_parts, mapping_detail, save_mapping, attach_mapping_evidence, fingerprint, MAPPING_POLICY_VERSION
from test_grist_repository import FakeGristClient


class PartMappingTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryPartStore()
        self.association = SimpleNamespace(id="association-1", version=1)
        self.groups = source_groups({"Tool Shop Items": [
            {"status": "active", "source_row": 10, "fields": {"product_part_name": "Shaft"}},
            {"status": "active", "source_row": 11, "fields": {"product_part_name": "Shaft"}},
            {"status": "active", "source_row": 12, "fields": {}},
            {"status": "active", "source_row": 13, "fields": {}},
            {"status": "historical", "source_row": 14, "fields": {"product_part_name": "Old"}}]})

    def create(self, *, name="Drive Shaft", status="canonical", part_key="fixture-part"):
        return self.store.append("ProductPart", [{"PartKey": part_key, "DisplayName": name, "NameKey": name,
            "Status": status, "CreatedActor": "Irshad", "CreatedReason": "Legacy compatibility fixture"}])[0]

    def save(self, **overrides):
        args = dict(file_id="file:pilot.ods", source_hash="hash1", association=self.association, groups=self.groups,
            decisions={self.groups[0]["key"]: "1"}, expected_hash="hash1", expected_version=0,
            expected_association="association-1", expected_association_version=1,
            actor="Irshad", reason="Reviewed shaft rows", request_key="mapping1")
        args.update(overrides)
        return save_mapping(self.store, **args)

    def detail(self, **overrides):
        args = dict(file_id="file:pilot.ods", source_hash="hash1", association=self.association, groups=self.groups)
        args.update(overrides)
        return mapping_detail(self.store, **args)

    def manufacturing_groups(self, sheet, entries):
        return source_groups({sheet: [{"status": "active", "source_row": row, "fields": fields,
            "available_fields": list(fields), "source_headers": {"qty": "Quantity Nos"},
            "source_cells": {name: {"value": value, "style": "test"} for name, value in fields.items()}}
            for row, fields in entries]})

    def save_groups(self, groups, source_hash="hash1", request_key="semantic-map", decisions=None):
        return save_mapping(self.store, file_id="file:pilot.ods", source_hash=source_hash,
            association=self.association, groups=groups, decisions=decisions or {group["key"]: "1" for group in groups},
            expected_hash=source_hash, expected_version=0, expected_association="association-1",
            expected_association_version=1, actor="Irshad", request_key=request_key)

    def test_groups_preserve_individual_blank_rows(self):
        self.assertEqual(len(self.groups), 3)
        self.assertEqual(len(self.groups[0]["rows"]), 2)
        self.assertNotEqual(self.groups[1]["key"], self.groups[2]["key"])
        self.assertEqual(self.detail()["unresolvedGroups"], 3)
        self.assertEqual(self.store.tables["PartMappingReview"], [])

    def test_group_identity_is_sheet_and_mapping_label_with_source_details(self):
        groups = source_groups({
            "5. Material Cut List Price": [{"status": "active", "source_row": 10,
                "fields": {"product_part_name": "Shaft", "material_to_cut": "MS", "qty": "2"},
                "source_headers": {"product_part_name": "Machine Piece Description"}, "source_header_cells": {"product_part_name": "A8"}, "available_fields": ["product_part_name", "material_to_cut", "qty"]}],
            "Tool Shop Items": [{"status": "active", "source_row": 10, "fields": {"product_part_name": "Shaft", "material_to_cut": "Tool Steel"}}],
            "CNC Cut List": [
                {"status": "active", "source_row": 10, "fields": {"product_part_name": "Plate 1", "part_category": "Shaft", "length": "120", "width": "8", "thickness": "3", "qty": "2"}},
                {"status": "active", "source_row": 11, "fields": {"product_part_name": "Plate 2", "part_category": "", "length": "80"}},
            ]
        }, sheet_diagnostics={"CNC Cut List": {"status": "ok", "labelHeader": "Part Category"}})
        self.assertEqual(len(groups), 4)
        self.assertEqual([group["sheet"] for group in groups], ["5. Material Cut List Price", "Tool Shop Items", "CNC Cut List", "CNC Cut List"])
        self.assertEqual([group["description"] for group in groups], ["Shaft", "Shaft", "Shaft", ""])
        self.assertEqual(groups[2]["labelField"], "part_category")
        self.assertEqual(groups[2]["rows"][0]["fields"]["product_part_name"], "Plate 1")
        self.assertEqual(groups[2]["rows"][0]["fields"]["length"], "120")
        self.assertEqual(groups[0]["rows"][0]["sourceHeaders"]["product_part_name"], "Machine Piece Description")
        self.assertEqual(groups[0]["mappingPolicyVersion"], MAPPING_POLICY_VERSION)
        self.assertNotEqual(groups[0]["key"], groups[1]["key"])

    def test_old_cross_sheet_and_cnc_identity_rows_are_reviewed_not_carried_forward(self):
        self.create(name="Frame Part")
        groups = source_groups({"CNC Cut List": [{"status": "active", "source_row": 44,
            "fields": {"product_part_name": "Plate Flange", "part_category": "Bracket", "material_to_cut": "MS"}}]})
        legacy_key = "description:" + fingerprint("Plate Flange")
        fields = {"ReviewKey": "old-review", "FileKey": "file:pilot.ods", "SourceHash": "hash1",
            "AssociationKey": "association-1", "AssociationVersion": 1, "GroupKey": legacy_key,
            "SheetName": "CNC Cut List", "SourceRow": 44, "SourceDescription": "Plate Flange", "ProductPart": 1,
            "Version": 1, "Actor": "Irshad", "Reason": "Previous review", "OccurredAt": "2026-10-01T00:00:00Z",
            "RequestKey": "old-request", "RequestFingerprint": "old-fingerprint", "RequestRowCount": 1,
            "PartNumberUsed": "SM-P-000009", "NameUsed": "Frame Part"}
        self.store.append("PartMappingReview", [fields])
        result = mapping_detail(self.store, file_id="file:pilot.ods", source_hash="hash1", association=self.association, groups=groups)
        self.assertFalse(result["groups"][0]["reviewed"])
        self.assertTrue(result["groups"][0]["needsCompatibilityReview"])
        self.assertEqual(result["groups"][0]["previousAssignment"]["name"], "Frame Part")
        self.assertIsNone(result["groups"][0]["part"])

    def test_split_legacy_group_uses_each_source_rows_latest_prior_assignment(self):
        self.create(name="Bracket Part", part_key="bracket-part")
        self.create(name="Frame Part", part_key="frame-part")
        groups = source_groups({"CNC Cut List": [
            {"status": "active", "source_row": 44, "fields": {"product_part_name": "Shared legacy label", "part_category": "Bracket"}},
            {"status": "active", "source_row": 45, "fields": {"product_part_name": "Shared legacy label", "part_category": "Frame"}},
        ]})
        request_fields = []
        for row, part_id, part_number, name in ((44, 1, "SM-P-000001", "Bracket Part"), (45, 2, "SM-P-000002", "Frame Part")):
            request_fields.append({"ReviewKey": f"old-{row}", "FileKey": "file:pilot.ods", "SourceHash": "hash1",
                "AssociationKey": "association-1", "AssociationVersion": 1,
                "GroupKey": "description:" + fingerprint("Shared legacy label"), "SheetName": "CNC Cut List", "SourceRow": row,
                "SourceDescription": "Shared legacy label", "ProductPart": part_id, "Version": 1,
                "Actor": "Irshad", "Reason": "Previous review", "OccurredAt": "2026-10-01T00:00:00Z",
                "RequestKey": "old-request", "RequestFingerprint": "old-fingerprint", "RequestRowCount": 2,
                "PartNumberUsed": part_number, "NameUsed": name})
        self.store.append("PartMappingReview", request_fields)
        result = mapping_detail(self.store, file_id="file:pilot.ods", source_hash="hash1", association=self.association, groups=groups)
        self.assertEqual(result["groups"][0]["previousAssignment"]["name"], "Bracket Part")
        self.assertEqual(result["groups"][1]["previousAssignment"]["name"], "Frame Part")
        self.assertTrue(all(group["needsCompatibilityReview"] for group in result["groups"]))

    def test_merged_current_group_marks_disagreeing_legacy_row_assignments(self):
        self.create(name="Plate A Part", part_key="plate-a")
        self.create(name="Plate B Part", part_key="plate-b")
        groups = source_groups({"CNC Cut List": [
            {"status": "active", "source_row": 46, "fields": {"product_part_name": "Plate A", "part_category": "Shared category"}},
            {"status": "active", "source_row": 47, "fields": {"product_part_name": "Plate B", "part_category": "Shared category"}},
        ]})
        old_rows = []
        for row, part_id, name in ((46, 1, "Plate A Part"), (47, 2, "Plate B Part")):
            old_rows.append({"ReviewKey": f"old-merge-{row}", "FileKey": "file:pilot.ods", "SourceHash": "hash1",
                "AssociationKey": "association-1", "AssociationVersion": 1,
                "GroupKey": "description:" + fingerprint("Plate " + ("A" if row == 46 else "B")),
                "SheetName": "CNC Cut List", "SourceRow": row, "SourceDescription": "Plate " + ("A" if row == 46 else "B"),
                "ProductPart": part_id, "Version": row - 45, "Actor": "Irshad", "Reason": "Previous review",
                "OccurredAt": f"2026-10-0{row - 45}T00:00:00Z", "RequestKey": f"old-merge-request-{row}",
                "RequestFingerprint": f"old-fingerprint-{row}", "RequestRowCount": 1,
                "PartNumberUsed": f"SM-P-00000{part_id}", "NameUsed": name})
        self.store.append("PartMappingReview", old_rows)
        result = mapping_detail(self.store, file_id="file:pilot.ods", source_hash="hash1", association=self.association, groups=groups)
        previous = result["groups"][0]["previousAssignment"]
        self.assertFalse(previous["previousRowsAgree"])
        self.assertIsNone(previous["partNumber"])
        self.assertIsNone(result["groups"][0]["part"])

    def test_explicit_clear_is_append_only_and_audited(self):
        self.create(); self.save()
        first = self.detail()
        self.assertTrue(first["groups"][0]["reviewed"])
        cleared = self.save(decisions={self.groups[0]["key"]: ""}, expected_version=1, request_key="clear-shaft")
        result = self.detail()
        self.assertEqual(cleared["savedRows"], 2)
        self.assertTrue(result["groups"][0]["explicitlyUnassigned"])
        self.assertFalse(result["groups"][0]["reviewed"])
        self.assertEqual(len(result["history"]), 4)
        self.assertTrue(all(row["fields"].get("Reason") for row in self.store.tables["PartMappingReview"]))

    def test_bounded_part_search_matches_current_identity_and_alias(self):
        self.store.append("ProductPart", [
            {"PartKey": "part-a", "DisplayName": "S1KHF — Chassis", "PartNumber": "SM-P-000001", "Description": "Main frame", "DesignVariant": "Standard", "Aliases": ["Old Frame Name"], "Status": "canonical"},
            {"PartKey": "part-b", "DisplayName": "S1KHF — Bracket", "PartNumber": "SM-P-000002", "Description": "Support", "Status": "canonical"},
        ])
        page = search_parts(self.store, query="Old Frame", offset=0, limit=1)
        self.assertEqual(page["total"], 1)
        self.assertEqual(page["items"][0]["partNumber"], "SM-P-000001")
        number_page = search_parts(self.store, query="SM-P-000002", offset=0, limit=1)
        self.assertEqual(number_page["items"][0]["name"], "S1KHF — Bracket")

    def test_legacy_duplicates_and_unallocated_parts_are_not_selectable(self):
        self.create()
        self.create(name="  ＤＲＩＶＥ   shaft  ", part_key="duplicate-part")
        self.create(name="Legacy Part", status="temporary", part_key="unallocated")
        parts = list_parts(self.store)
        self.assertTrue(all(part["duplicateName"] for part in parts if "shaft" in part["name"].casefold()))
        unallocated = next(part for part in parts if part["name"] == "Legacy Part")
        self.assertFalse(unallocated["selectable"])
        with self.assertRaises(PartConflict) as error:
            self.save(decisions={self.groups[0]["key"]: "1"})
        self.assertEqual(error.exception.code, "PART_SELECTION_INVALID")

    def test_typed_assignments_partial_review_and_stale_changes(self):
        self.create(); self.save()
        rows = self.store.tables["PartMappingReview"]
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["fields"]["ProductPart"], 1)
        self.assertEqual(rows[0]["fields"]["Actor"], "Irshad")
        self.assertEqual(self.detail()["unresolvedGroups"], 2)
        changed_source = self.detail(source_hash="hash2")
        self.assertEqual(changed_source["unresolvedGroups"], 2)
        self.assertTrue(changed_source["groups"][0]["reviewed"])
        self.assertEqual(changed_source["groups"][0]["semanticComparison"]["status"], "unchanged")
        self.assertTrue(changed_source["groups"][0]["previousAssignment"]["sourceChanged"])
        changed_association = self.detail(association=SimpleNamespace(id="association-1", version=2))
        self.assertTrue(changed_association["groups"][0]["previousAssignment"]["associationChanged"])
        self.assertFalse(changed_association["groups"][0]["reviewed"])
        self.assertEqual(changed_association["groups"][0]["semanticComparison"]["status"], "association_changed")
        self.assertTrue(self.save()["idempotent"])
        for overrides in ({"expected_hash": "old"}, {"expected_version": 0}, {"expected_version": 1, "expected_association_version": 2}):
            with self.assertRaises(PartConflict):
                self.save(request_key="next", **overrides)
        self.assertEqual(len(rows), 2)

    def test_grouping_policy_change_keeps_prior_part_as_review_proposal(self):
        self.create(name="Mapped Shaft")
        groups = self.manufacturing_groups("5. Material Cut List Price", [
            (10, {"product_part_name": "Shaft", "material_to_cut": "MS", "qty": 2}),
        ])
        self.save_groups(groups)
        changed_policy = [dict(groups[0], key="future-grouping-policy:group-1")]
        detail = mapping_detail(self.store, file_id="file:pilot.ods", source_hash="hash-policy",
            association=self.association, groups=changed_policy)
        group = detail["groups"][0]
        self.assertFalse(group["reviewed"])
        self.assertIsNone(group["part"])
        self.assertEqual(group["previousAssignment"]["name"], "Mapped Shaft")
        self.assertTrue(group["semanticComparison"]["groupingPolicyChanged"])
        self.assertEqual(group["semanticComparison"]["status"], "grouping_policy_changed")

    def test_price_only_resave_and_reordered_rows_preserve_assignment_without_write(self):
        part = self.create(name="Mapped Bracket")
        before = self.manufacturing_groups("5. Material Cut List Price", [
            (10, {"product_part_name": "Bracket", "material_to_cut": "MS", "dimension_to_cut_mm": "10.0", "qty": "2.00", "optional_item_group_1": "", "in_use": "Yes", "rate": 5, "amount": 10}),
            (11, {"product_part_name": "Bracket", "material_to_cut": "MS", "dimension_to_cut_mm": "20", "qty": 1, "optional_item_group_1": "", "in_use": "Yes", "rate": 7, "amount": 7}),
        ])
        self.save_groups(before)
        mapping_count = len(self.store.tables["PartMappingReview"])
        after = self.manufacturing_groups("5. Material Cut List Price", [
            (90, {"product_part_name": "Bracket", "material_to_cut": "MS", "dimension_to_cut_mm": 20.0, "qty": "1.0", "optional_item_group_1": None, "in_use": "active", "rate": 99, "amount": 99}),
            (77, {"product_part_name": "Bracket", "material_to_cut": "MS", "dimension_to_cut_mm": 10, "qty": 2, "optional_item_group_1": "", "in_use": "true", "rate": 150, "amount": 300}),
        ])
        self.assertEqual(before[0]["semanticFingerprint"], after[0]["semanticFingerprint"])
        current = mapping_detail(self.store, file_id="file:pilot.ods", source_hash="hash-price-resave",
            association=self.association, groups=after)
        self.assertTrue(current["groups"][0]["reviewed"])
        self.assertEqual(current["groups"][0]["part"]["id"], str(part["id"]))
        self.assertEqual(current["groups"][0]["semanticComparison"]["status"], "unchanged")
        self.assertEqual(len(self.store.tables["PartMappingReview"]), mapping_count)

    def test_relevant_quantity_change_flags_only_affected_group_and_proposes_prior_part(self):
        self.create(name="Part A")
        self.create(name="Part B", part_key="part-b")
        before = self.manufacturing_groups("5. Material Cut List Price", [
            (10, {"product_part_name": "A", "material_to_cut": "MS", "dimension_to_cut_mm": "10", "qty": 1}),
            (20, {"product_part_name": "B", "material_to_cut": "Aluminium", "dimension_to_cut_mm": "8", "qty": 2}),
        ])
        self.save_groups(before, decisions={group["key"]: "1" if group["description"] == "A" else "2" for group in before})
        after = self.manufacturing_groups("5. Material Cut List Price", [
            (10, {"product_part_name": "A", "material_to_cut": "MS", "dimension_to_cut_mm": "10", "qty": 3}),
            (20, {"product_part_name": "B", "material_to_cut": "Aluminium", "dimension_to_cut_mm": "8", "qty": 2, "rate": 500, "amount": 1000}),
        ])
        current = mapping_detail(self.store, file_id="file:pilot.ods", source_hash="hash-relevant-change",
            association=self.association, groups=after)
        group_a = next(group for group in current["groups"] if group["description"] == "A")
        group_b = next(group for group in current["groups"] if group["description"] == "B")
        self.assertFalse(group_a["reviewed"])
        self.assertIsNone(group_a["part"])
        self.assertEqual(group_a["previousAssignment"]["name"], "Part A")
        self.assertEqual(group_a["semanticComparison"]["status"], "changed")
        self.assertEqual(group_a["semanticComparison"]["differences"][0]["type"], "modified")
        self.assertIn("qty", [item["field"] for item in group_a["semanticComparison"]["differences"][0]["fieldDifferences"]])
        self.assertTrue(group_b["reviewed"])
        self.assertEqual(group_b["part"]["name"], "Part B")

    def test_add_delete_and_ambiguous_duplicate_line_differences(self):
        self.create(name="Bracket")
        baseline = self.manufacturing_groups("Tool Shop Items", [
            (10, {"product_part_name": "Bracket", "item_name": "Pin", "material_to_cut": "Steel", "qty": 1}),
            (11, {"product_part_name": "Bracket", "item_name": "Pin", "material_to_cut": "Steel", "qty": 1}),
        ])
        self.save_groups(baseline)
        changed = self.manufacturing_groups("Tool Shop Items", [
            (20, {"product_part_name": "Bracket", "item_name": "Pin", "material_to_cut": "Steel", "qty": 2}),
        ])
        detail = mapping_detail(self.store, file_id="file:pilot.ods", source_hash="hash-ambiguous",
            association=self.association, groups=changed)
        comparison = detail["groups"][0]["semanticComparison"]
        self.assertEqual(comparison["status"], "ambiguous_correspondence")
        self.assertTrue(all(item["type"] == "ambiguous" for item in comparison["differences"]))

        added = self.manufacturing_groups("Tool Shop Items", [
            (20, {"product_part_name": "Bracket", "item_name": "Pin", "material_to_cut": "Steel", "qty": 1}),
            (21, {"product_part_name": "Bracket", "item_name": "Bush", "material_to_cut": "Bronze", "qty": 1}),
        ])
        addition_detail = mapping_detail(self.store, file_id="file:pilot.ods", source_hash="hash-addition",
            association=self.association, groups=added)
        additions = addition_detail["groups"][0]["semanticComparison"]["differences"]
        self.assertEqual({item["type"] for item in additions}, {"added", "deleted"})

    def test_price_totals_formatting_and_document_metadata_do_not_change_semantics(self):
        first = self.manufacturing_groups("CNC Cut List", [
            (10, {"part_category": "Frame", "product_part_name": "Frame side", "material_to_cut": "MS",
                "length": "120.00", "width": "20", "thickness": "2", "qty": "2", "rate": 12, "amount": 24}),
        ])
        second = self.manufacturing_groups("CNC Cut List", [
            (44, {"part_category": " frame ", "product_part_name": "FRAME SIDE", "material_to_cut": "ms",
                "length": 120, "width": "20.0", "thickness": "2.000", "qty": 2.0, "rate": 99, "amount": 198,
                "workbook_title": "new metadata", "cell_format": "currency-red"}),
        ])
        self.assertEqual(first[0]["semanticFingerprint"], second[0]["semanticFingerprint"])
        self.assertNotEqual(first[0]["evidenceFingerprint"], second[0]["evidenceFingerprint"])

    def test_v14_schema_introspection_is_read_only_cached_and_fails_closed(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from app.grist_parts import GristPartRegistry

        class SchemaClient:
            api_key = "isolated-schema-fixture"
            base_url = "http://isolated.invalid"

        with TemporaryDirectory() as temp:
            registry = GristPartRegistry(SchemaClient(), path=Path(temp) / "schema.sqlite3")
            with mock.patch("app.grist_parts.GristAdminClientShim") as admin:
                admin.return_value.list_columns.return_value = [{"id": name} for name in (
                    "SemanticPolicyVersion", "SemanticFingerprint", "SemanticEvidence")]
                self.assertTrue(registry.mapping_evidence_schema_available())
                self.assertTrue(registry.mapping_evidence_schema_available())
                admin.assert_called_once_with(registry.client)
                admin.return_value.list_columns.assert_called_once_with("PartMappingReview")

            older_registry = GristPartRegistry(SchemaClient(), path=Path(temp) / "schema.sqlite3")
            with mock.patch("app.grist_parts.GristAdminClientShim") as admin:
                admin.return_value.list_columns.return_value = [{"id": "SemanticFingerprint"}]
                self.assertFalse(older_registry.mapping_evidence_schema_available())

            unknown_registry = GristPartRegistry(SchemaClient(), path=Path(temp) / "schema.sqlite3")
            with mock.patch("app.grist_parts.GristAdminClientShim", side_effect=RuntimeError("offline")):
                self.assertFalse(unknown_registry.mapping_evidence_schema_available())

    def test_legacy_mapping_requires_one_time_confirmation_and_blank_label_is_not_reused(self):
        self.create(name="Legacy mapping")
        group = self.manufacturing_groups("5. Material Cut List Price", [
            (10, {"product_part_name": "Bracket", "material_to_cut": "MS", "qty": 1}),
        ])[0]
        self.store.append("PartMappingReview", [{"ReviewKey": "legacy-mapping", "FileKey": "file:pilot.ods",
            "SourceHash": "hash1", "AssociationKey": "association-1", "AssociationVersion": 1,
            "GroupKey": group["key"], "SheetName": group["sheet"], "SourceRow": 10,
            "SourceDescription": group["description"], "ProductPart": 1, "Version": 1, "Actor": "Irshad",
            "Reason": "Legacy assignment", "OccurredAt": "2026-10-01T00:00:00Z", "RequestKey": "legacy-request",
            "RequestFingerprint": "legacy-fingerprint", "RequestRowCount": 1}])
        legacy = mapping_detail(self.store, file_id="file:pilot.ods", source_hash="hash1",
            association=self.association, groups=[group])["groups"][0]
        self.assertFalse(legacy["reviewed"])
        self.assertEqual(legacy["semanticComparison"]["status"], "legacy_evidence_missing")
        self.assertEqual(legacy["previousAssignment"]["name"], "Legacy mapping")

        blank = self.manufacturing_groups("5. Material Cut List Price", [
            (10, {"material_to_cut": "MS", "qty": 1}),
        ])[0]
        blank_result = mapping_detail(self.store, file_id="file:pilot.ods", source_hash="hash1",
            association=self.association, groups=[blank])["groups"][0]
        self.assertFalse(blank_result["reviewed"])

    def test_grist_mapping_evidence_is_durable_and_recovers_after_fresh_registry_read(self):
        from copy import deepcopy
        from pathlib import Path
        from app.grist_parts import GristPartRegistry

        class IsolatedGrist:
            doc_id = "isolated-mapping-fixture"
            def __init__(self):
                self.tables = {"ProductPart": [{"id": 1, "fields": {"StablePartId": "stable-bracket",
                    "PartKey": "stable-bracket", "DisplayName": "Bracket", "PartNumber": "SM-P-000001",
                    "EngineeringRevision": "A", "ScopeType": "product", "ScopeTargetLabel": "Safari 1000",
                    "Description": "Bracket", "Status": "canonical", "PublishStatus": "published"}}],
                    "PartMappingReview": []}
            def validate_safari_write_target(self):
                return None
            def fetch_table_records_with_ids(self, table):
                return deepcopy(self.tables.get(table, []))
            def create_table_records(self, table, records):
                rows = self.tables.setdefault(table, [])
                next_id = len(rows) + 1
                for item in records:
                    rows.append({"id": next_id, "fields": deepcopy(item["fields"])})
                    next_id += 1
                return {"records": deepcopy(rows[-len(records):])}

        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as temp:
            client = IsolatedGrist()
            registry = GristPartRegistry(client, path=Path(temp) / "journal.sqlite3")
            registry._verify_writer = lambda: None
            registry.mapping_evidence_schema_available = lambda: True
            store = PartRegistryMappingStore(GristPartStore(client), registry)
            groups = self.manufacturing_groups("CNC Cut List", [
                (42, {"part_category": "Bracket", "product_part_name": "Plate", "material_to_cut": "MS",
                    "length": "12.0", "width": "8", "thickness": "3", "qty": 1}),
            ])
            save_mapping(store, file_id="file:pilot.ods", source_hash="hash-grist", association=self.association,
                groups=groups, decisions={groups[0]["key"]: "stable-bracket"}, expected_hash="hash-grist",
                expected_version=0, expected_association="association-1", expected_association_version=1,
                actor="Irshad", request_key="grist-semantic-save")
            persisted = client.tables["PartMappingReview"][0]["fields"]
            self.assertEqual(persisted["SemanticPolicyVersion"], "part-mapping-semantic-v1")
            self.assertTrue(persisted["SemanticFingerprint"])
            self.assertIn("fields", persisted["SemanticEvidence"])
            fresh_registry = GristPartRegistry(client, path=Path(temp) / "journal.sqlite3")
            fresh_registry._verify_writer = lambda: None
            fresh_registry.mapping_evidence_schema_available = lambda: True
            fresh_store = PartRegistryMappingStore(GristPartStore(client), fresh_registry)
            recovered = mapping_detail(fresh_store, file_id="file:pilot.ods", source_hash="hash-resaved",
                association=self.association, groups=groups)
            self.assertTrue(recovered["groups"][0]["reviewed"])
            self.assertEqual(recovered["groups"][0]["part"]["id"], "stable-bracket")
            replay = save_mapping(store, file_id="file:pilot.ods", source_hash="hash-grist", association=self.association,
                groups=groups, decisions={groups[0]["key"]: "stable-bracket"}, expected_hash="hash-grist",
                expected_version=0, expected_association="association-1", expected_association_version=1,
                actor="Irshad", request_key="grist-semantic-save")
            self.assertTrue(replay["idempotent"])
            old_schema_registry = GristPartRegistry(client, path=Path(temp) / "journal.sqlite3")
            old_schema_registry._verify_writer = lambda: None
            old_schema_registry.mapping_evidence_schema_available = lambda: False
            old_schema_store = PartRegistryMappingStore(GristPartStore(client), old_schema_registry)
            blocked_detail = mapping_detail(old_schema_store, file_id="file:pilot.ods", source_hash="hash-resaved",
                association=self.association, groups=groups)
            self.assertFalse(blocked_detail["schemaAvailable"])
            self.assertIn("schema v14", blocked_detail["schemaMessage"])
            with self.assertRaises(PartConflict) as blocked_save:
                save_mapping(old_schema_store, file_id="file:pilot.ods", source_hash="hash-resaved",
                    association=self.association, groups=groups, decisions={groups[0]["key"]: "stable-bracket"},
                    expected_hash="hash-resaved", expected_version=1, expected_association="association-1",
                    expected_association_version=1, actor="Irshad", request_key="old-schema-blocked-save")
            self.assertEqual(blocked_save.exception.code, "PART_SCHEMA_UNAVAILABLE")
            self.assertEqual(len(client.tables["PartMappingReview"]), 1)

    def test_legacy_grist_part_store_is_read_only(self):
        client = FakeGristClient(); self.store = GristPartStore(client)
        with self.assertRaises(PartConflict) as error:
            self.store.append("ProductPart", [{"DisplayName": "Must not be written"}])
        self.assertEqual(error.exception.code, "PART_LEGACY_READ_ONLY")
        self.assertEqual(client.created, [])

    def test_wrong_target_duplicate_names_and_prewrite_changes_block(self):
        client = FakeGristClient(); self.store = GristPartStore(client); client.target_valid = False
        with self.assertRaises(PartConflict): self.store.append("ProductPart", [{"DisplayName": "Must not be written"}])
        self.assertEqual(client.created, [])
        self.store = MemoryPartStore(); self.create()
        guard = mock.Mock(side_effect=PartConflict("PART_REVIEW_STALE", "Changed source"))
        with self.assertRaises(PartConflict): self.save(before_write=guard)
        self.assertEqual(self.store.tables["PartMappingReview"], [])
        self.store.tables["ProductPart"].append({"id": 2, "fields": {"DisplayName": "Drive Shaft", "PartKey": "duplicate", "Status": "canonical"}})
        with self.assertRaises(PartConflict): self.save()

    def test_current_assignment_evidence_and_incomplete_batch_fail_closed(self):
        self.create(); self.save()
        rows = [{"sheet": "Tool Shop Items", "row": number} for number in (10, 11, 12, 13)]
        evidence = {"partRowsRequiringReview": rows, "pendingPolicies": ["Reviewed Part assignments", "Model Code Summary configuration"]}
        attach_mapping_evidence(evidence, self.detail())
        self.assertEqual(evidence["partMapping"]["reviewedRows"], 2)
        self.assertEqual([row["row"] for row in evidence["partRowsRequiringReview"]], [12, 13])
        self.store.tables["PartMappingReview"].pop()
        with self.assertRaises(PartConflict): self.detail()

    def test_http_actor_and_final_source_recheck(self):
        from app import web
        from app.repository import InMemorySafariRepository
        repository = InMemorySafariRepository(); repository.part_store = self.store; self.create()
        request = Request({"type": "http", "headers": [(b"x-authentik-username", b"Irshad")]})
        payload = {"path": "pilot.ods", "expectedHash": "hash1", "expectedVersion": 0,
            "expectedAssociationKey": "association-1", "expectedAssociationVersion": 1,
            "decisions": {self.groups[0]["key"]: "1"}, "reason": "Reviewed", "actor": "Spoofed"}
        with mock.patch.object(web, "_part_context", return_value=(repository, "file:pilot.ods", self.association, "hash1", self.groups)), mock.patch.object(web, "_processing_context", return_value=(repository, "file:pilot.ods", self.association, "hash1")):
            web.save_part_mappings(request, payload, "http-key")
        self.assertEqual(self.store.tables["PartMappingReview"][0]["fields"]["Actor"], "Irshad")

    def test_http_final_source_change_and_processed_file_block_new_writes(self):
        from app import web
        from app.repository import InMemorySafariRepository
        repository = InMemorySafariRepository(); repository.part_store = self.store; self.create()
        request = Request({"type": "http", "headers": []})
        payload = {"path": "pilot.ods", "expectedHash": "hash1", "expectedVersion": 0,
            "expectedAssociationKey": "association-1", "expectedAssociationVersion": 1,
            "decisions": {self.groups[0]["key"]: "1"}, "reason": "Reviewed"}
        with mock.patch.object(web, "_part_context", return_value=(repository, "file:pilot.ods", self.association, "hash1", self.groups)), mock.patch.object(web, "_processing_context", return_value=(repository, "file:pilot.ods", self.association, "hash2")):
            with self.assertRaises(HTTPException) as error:
                web.save_part_mappings(request, payload, "http-key")
            self.assertEqual(error.exception.detail["code"], "PART_REVIEW_STALE")
        with mock.patch.object(web, "_part_context", return_value=(repository, "file:pilot.ods", self.association, "hash1", self.groups)), mock.patch.object(web, "_processing_context", return_value=(repository, "file:pilot.ods", self.association, "hash1")), mock.patch("app.processing.processing_detail", return_value={"state": "processed"}):
            with self.assertRaises(HTTPException) as error:
                web.save_part_mappings(request, payload, "http-key")
            self.assertEqual(error.exception.detail["code"], "PART_REOPEN_REQUIRED")
        self.assertEqual(self.store.tables["PartMappingReview"], [])


if __name__ == "__main__": unittest.main()

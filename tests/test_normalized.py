import copy
import unittest

from app.normalized import project_snapshot
from app.normalized_import import assess_cost_drift
from app.normalized_store import GristNormalizedStore, NormalizedStore, TABLES


def line(row=9, quantity="2", material="Steel"):
    return {"source_row": row, "status": "active", "identity_values": {
        "product_part_name": "Chassis", "material_to_cut": material,
        "dimension_to_cut_mm": "2042", "qty": quantity, "optional_item_group_1": "",
    }, "fields": {"product_part_name": "Chassis", "material_to_cut": material,
                   "dimension_to_cut_mm": "2042", "qty": quantity, "amount": "100"},
            "source_cells": {"qty": {"cell": f"E{row}"}, "amount": {"cell": f"O{row}", "formula": f"of:=[.E{row}]*50", "cached_value": "100"}},
            "cost_value": "100"}


class NormalizedTests(unittest.TestCase):
    def semantic(self, *lines):
        return {"process_lists": {"5. Material Cut List Price": list(lines)},
                "material_master_state": {"Steel": {}}}

    def test_repeat_is_idempotent_and_reorder_retains_revision(self):
        first = project_snapshot(self.semantic(line()), snapshot_key="s1", source_hash="h1")
        store = NormalizedStore()
        self.assertEqual(store.apply(first)["LineRevision"], 1)
        self.assertEqual(store.apply(first)["LineRevision"], 0)
        moved = project_snapshot(self.semantic(line(row=25)), snapshot_key="s2", source_hash="h2", previous=first)
        self.assertEqual(moved["line_revisions"], [])
        self.assertEqual(moved["source_mappings"][0]["master_key"], first["line_masters"][0]["key"])
        self.assertEqual(store.apply(moved)["SourceLineObservation"], 1)

    def test_quantity_change_creates_one_revision_under_master(self):
        first = project_snapshot(self.semantic(line()), snapshot_key="s1", source_hash="h1")
        changed = project_snapshot(self.semantic(line(quantity="3")), snapshot_key="s2", source_hash="h2", previous=first)
        self.assertEqual(changed["line_masters"], [])
        self.assertEqual(len(changed["line_revisions"]), 1)
        self.assertEqual(changed["line_revisions"][0]["previous_key"], first["line_revisions"][0]["key"])

    def test_rate_only_change_adds_observation_without_physical_revision(self):
        first = project_snapshot(self.semantic(line()), snapshot_key="s1", source_hash="h1")
        later = line()
        later["fields"]["amount"] = "120"
        later["source_cells"]["amount"]["cached_value"] = "120"
        later["cost_value"] = "120"
        changed = project_snapshot(self.semantic(later), snapshot_key="s2", source_hash="h2", previous=first)
        self.assertEqual(changed["line_revisions"], [])
        self.assertEqual(len(changed["source_observations"]), 1)
        self.assertEqual(changed["reconciliation"]["value_differences"], [])

    def test_two_changed_rows_cannot_claim_one_old_master(self):
        first = project_snapshot(self.semantic(line()), snapshot_key="s1", source_hash="h1")
        next_rows = project_snapshot(self.semantic(line(9, quantity="3"), line(10, quantity="4")),
                                     snapshot_key="s2", source_hash="h2", previous=first)
        self.assertEqual(next_rows["reconciliation"]["unresolved_mappings"], 2)
        self.assertEqual(len([x for x in next_rows["exceptions"] if x["code"] == "CHANGED_KEY_AMBIGUOUS"]), 2)

    def test_removed_line_is_retired_with_predecessor(self):
        first = project_snapshot(self.semantic(line()), snapshot_key="s1", source_hash="h1")
        removed = project_snapshot(self.semantic(), snapshot_key="s2", source_hash="h2", previous=first)
        self.assertEqual(removed["line_revisions"][0]["status"], "retired")
        self.assertEqual(removed["line_revisions"][0]["previous_key"], first["line_revisions"][0]["key"])
        self.assertEqual(len(removed["line_details"]), 1)

    def test_duplicate_composite_remains_unresolved_but_has_child_rows(self):
        result = project_snapshot(self.semantic(line(9), line(10)), snapshot_key="s", source_hash="h")
        self.assertEqual(len(result["line_details"]), 2)
        self.assertEqual(result["reconciliation"]["unresolved_mappings"], 2)
        self.assertTrue(all(row["status"] == "unresolved" for row in result["line_masters"]))

    def test_material_sharing_does_not_merge_lines(self):
        other = line(10)
        other["identity_values"]["product_part_name"] = "Support"
        other["fields"]["product_part_name"] = "Support"
        result = project_snapshot(self.semantic(line(), other), snapshot_key="s", source_hash="h")
        self.assertEqual(len(result["materials"]), 1)
        self.assertEqual(len(result["line_masters"]), 2)
        self.assertEqual(result["line_masters"][0]["part_key"], "part:s1khf-unallocated")

    def test_observation_pins_formula_and_cached_value(self):
        result = project_snapshot(self.semantic(line()), snapshot_key="s", source_hash="h")
        observed = result["source_observations"][0]
        self.assertEqual(observed["cells"]["amount"]["cached_value"], "100")
        self.assertEqual(observed["source_hash"], "h")
        self.assertEqual(result["reconciliation"]["differences"], [])

    def test_toolshop_remarks_and_cnc_plate_text_serialize_to_grist_detail_and_reread(self):
        tool = line(21)
        tool.update({"identity_values": {"product_part_name": "Tool fixture", "item_name": "Jig", "material_to_cut": "Steel",
            "dimension_to_cut_mm": "80", "qty": "1", "optional_item_group_1": ""},
            "fields": {"product_part_name": "Tool fixture", "item_name": "Jig", "material_to_cut": "Steel",
                "dimension_to_cut_mm": "80", "qty": "1", "toolshop_remarks": "  keep\nexact text  ", "job_remarks": "separate note", "amount": "100"},
            "source_cells": {"toolshop_remarks": {"cell": "G21"}, "job_remarks": {"cell": "H21"}}})
        cnc = line(31)
        cnc.update({"identity_values": {"product_part_name": "Plate source label", "material_to_cut": "MS Plate", "length": "120",
            "width": "60", "thickness": "3", "qty": "2", "optional_item_group_1": ""},
            "fields": {"product_part_name": "Plate source label", "part_category": "Bracket", "material_to_cut": "MS Plate",
                "length": "120", "width": "60", "thickness": "3", "qty": "2", "amount": "200"},
            "source_cells": {"product_part_name": {"cell": "A31"}, "part_category": {"cell": "B31"}}})
        semantic = {"process_lists": {"Tool Shop Items": [tool], "CNC Cut List": [cnc]},
            "material_master_state": {"Steel": {}, "MS Plate": {}}}
        projection = project_snapshot(semantic, snapshot_key="fields-snapshot", source_hash="fields-hash")
        detail_by_type = {row["process_type"]: row for row in projection["line_details"]}
        self.assertEqual(detail_by_type["tool_shop"]["toolshop_remarks"], "  keep\nexact text  ")
        self.assertEqual(detail_by_type["cnc"]["plate_part"], "Plate source label")
        observed_tool = next(row for row in projection["source_observations"] if row["sheet"] == "Tool Shop Items")
        observed_cnc = next(row for row in projection["source_observations"] if row["sheet"] == "CNC Cut List")
        self.assertEqual(observed_tool["observed_fields"]["job_remarks"], "separate note")
        self.assertEqual(observed_cnc["observed_fields"]["part_category"], "Bracket")
        self.assertEqual(observed_cnc["observed_fields"]["product_part_name"], "Plate source label")

        class Client:
            def __init__(self):
                self.allowed = True
                self.tables = {table: [] for _, table, _, _ in TABLES}
                self.tables["CostingSnapshot"] = [{"id": 1, "fields": {"SnapshotKey": "fields-snapshot", "Status": "accepted",
                    "SourceHashes": {"selected_workbook_saved": "fields-hash"}}}]
                self.next_id = 2

            def validate_safari_write_target(self):
                if not self.allowed:
                    raise ValueError("wrong document")

            def fetch_table_records_with_ids(self, table):
                return self.tables[table]

            def create_table_records(self, table, records):
                saved = []
                for record in records:
                    item = {"id": self.next_id, "fields": record["fields"]}
                    self.next_id += 1
                    self.tables[table].append(item)
                    saved.append(item)
                return saved

        client = Client()
        grist = GristNormalizedStore(client)
        plan = grist.plan(projection, snapshot_key="fields-snapshot", source_hash="fields-hash")
        grist.apply(projection, plan)
        detail_rows = client.tables["LineDetail"]
        by_fields = {row["fields"]["ProcessType"]: row["fields"] for row in detail_rows}
        self.assertEqual(by_fields["tool_shop"]["ToolShopRemarks"], "  keep\nexact text  ")
        self.assertEqual(by_fields["cnc"]["PlatePart"], "Plate source label")
        readback = grist.query(snapshot_key="fields-snapshot", filters={}, offset=0, limit=10)
        reread_tool = next(row for row in readback["items"] if row["observation"]["sheet"] == "Tool Shop Items")
        reread_cnc = next(row for row in readback["items"] if row["observation"]["sheet"] == "CNC Cut List")
        self.assertEqual(reread_tool["detail"]["toolshop_remarks"], "  keep\nexact text  ")
        self.assertEqual(reread_tool["observation"]["observed_fields"]["job_remarks"], "separate note")
        self.assertEqual(reread_cnc["detail"]["plate_part"], "Plate source label")
        self.assertEqual(reread_cnc["observation"]["observed_fields"]["part_category"], "Bracket")

        updated_tool = copy.deepcopy(tool)
        updated_tool["fields"]["toolshop_remarks"] = "Changed source note"
        updated_tool["source_cells"]["toolshop_remarks"] = {"cell": "G21", "value": "Changed source note"}
        next_semantic = {"process_lists": {"Tool Shop Items": [updated_tool], "CNC Cut List": [copy.deepcopy(cnc)]},
            "material_master_state": {"Steel": {}, "MS Plate": {}}}
        next_projection = project_snapshot(next_semantic, snapshot_key="fields-snapshot-2", source_hash="fields-hash-2", previous=projection)
        self.assertEqual(next_projection["line_revisions"], [])
        self.assertEqual(next_projection["line_details"], [])
        client.tables["CostingSnapshot"].append({"id": 90, "fields": {"SnapshotKey": "fields-snapshot-2", "Status": "accepted",
            "PreviousSnapshotKey": "fields-snapshot", "SourceHashes": {"selected_workbook_saved": "fields-hash-2"}}})
        next_plan = grist.plan(next_projection, snapshot_key="fields-snapshot-2", source_hash="fields-hash-2")
        grist.apply(next_projection, next_plan)
        latest = grist.query(snapshot_key="fields-snapshot-2", filters={}, offset=0, limit=10)
        latest_tool = next(row for row in latest["items"] if row["observation"]["sheet"] == "Tool Shop Items")
        self.assertEqual(latest_tool["detail"]["toolshop_remarks"], "Changed source note")
        self.assertEqual(latest_tool["revision"]["key"], reread_tool["revision"]["key"])
        historical = grist.query(snapshot_key="fields-snapshot", filters={}, offset=0, limit=10)
        historical_tool = next(row for row in historical["items"] if row["observation"]["sheet"] == "Tool Shop Items")
        self.assertEqual(historical_tool["detail"]["toolshop_remarks"], "  keep\nexact text  ")

    def test_cost_drift_reconciles_workbook_total_without_replacing_physical_baseline(self):
        old = line()
        old["cost_value"] = "100"
        new = copy.deepcopy(old)
        new["cost_value"] = "120"
        new["fields"]["amount"] = "120"
        new["fields"]["newly_configured_weight"] = "4"
        assessment = assess_cost_drift(self.semantic(old), self.semantic(new), "120")
        self.assertTrue(assessment["cost_only_confirmed"])
        self.assertEqual(assessment["newly_covered_fields"]["5. Material Cut List Price"], ["newly_configured_weight"])
        changed = copy.deepcopy(new)
        changed["fields"]["qty"] = "3"
        self.assertFalse(assess_cost_drift(self.semantic(old), self.semantic(changed), "120")["cost_only_confirmed"])

    def test_immutable_store_rejects_key_rewrite(self):
        result = project_snapshot(self.semantic(line()), snapshot_key="s", source_hash="h")
        store = NormalizedStore()
        store.apply(result)
        tampered = copy.deepcopy(result)
        tampered["line_revisions"][0]["reason"] = "silent rewrite"
        with self.assertRaises(ValueError):
            store.apply(tampered)

    def test_grist_refs_retry_and_wrong_document_guard(self):
        class FakeClient:
            def __init__(self):
                self.allowed = True
                self.tables = {table: [] for _, table, _, _ in TABLES}
                self.tables["CostingSnapshot"] = [{"id": 1, "fields": {"SnapshotKey": "s", "Status": "accepted", "SourceHashes": {"selected_workbook_saved": "h"}}}]
                self.next_id = 2
                self.fail_once_table = None

            def validate_safari_write_target(self):
                if not self.allowed:
                    raise ValueError("wrong document")

            def fetch_table_records_with_ids(self, table):
                return self.tables[table]

            def create_table_records(self, table, records):
                if self.fail_once_table == table:
                    self.fail_once_table = None
                    raise RuntimeError("interrupted write")
                saved = []
                for record in records:
                    item = {"id": self.next_id, "fields": record["fields"]}
                    self.next_id += 1
                    self.tables[table].append(item)
                    saved.append(item)
                return saved

        client = FakeClient()
        store = GristNormalizedStore(client)
        result = project_snapshot(self.semantic(line()), snapshot_key="s", source_hash="h")
        plan = store.plan(result, snapshot_key="s", source_hash="h")
        self.assertEqual(plan["create_counts"]["LineDetail"], 1)
        drift_plan = store.plan(result, snapshot_key="s", source_hash="new-cost-hash", baseline_source_hash="h")
        self.assertEqual(drift_plan["baseline_source_hash"], "h")
        with self.assertRaisesRegex(ValueError, "baseline changed"):
            store.plan(result, snapshot_key="s", source_hash="new-cost-hash", baseline_source_hash="wrong")
        client.fail_once_table = "LineDetail"
        with self.assertRaisesRegex(RuntimeError, "interrupted write"):
            store.apply(result, plan)
        self.assertEqual(len(client.tables["LineMaster"]), 1)
        self.assertEqual(store.apply(result, plan)["LineDetail"], 1)
        self.assertEqual(store.apply(result, plan)["LineDetail"], 0)
        master_id = client.tables["LineMaster"][0]["id"]
        self.assertEqual(client.tables["LineRevision"][0]["fields"]["LineMaster"], master_id)
        queried = store.query(snapshot_key="s", filters={"status": "active"}, offset=0, limit=10)
        self.assertEqual(queried["total"], 1)
        self.assertEqual(queried["items"][0]["master"]["key"], result["line_masters"][0]["key"])
        # Another source revision must not leak into this accepted baseline.
        client.tables["LineRevision"].append({"id": 99999, "fields": {
            "LineMaster": master_id, "Snapshot": 999, "RevisionKey": "future-revision"}})
        pinned = store.query(snapshot_key="s", filters={}, offset=0, limit=10)
        self.assertNotEqual(pinned["items"][0]["revision"]["key"], "future-revision")
        client.tables["CostingSnapshot"].append({"id": 999, "fields": {"SnapshotKey": "s2", "Status": "accepted", "PreviousSnapshotKey": "s"}})
        # A new observation may reuse the prior immutable revision.
        observation = client.tables["SourceLineObservation"][0]
        observation["fields"]["Snapshot"] = 999
        # Remove the unimported future revision: only inherited evidence remains.
        client.tables["LineRevision"].pop()
        inherited = store.query(snapshot_key="s2", filters={}, offset=0, limit=10)
        self.assertEqual(inherited["items"][0]["revision"]["key"], result["line_revisions"][0]["key"])
        client.allowed = False
        with self.assertRaisesRegex(ValueError, "wrong document"):
            store.plan(result, snapshot_key="s", source_hash="h")


if __name__ == "__main__":
    unittest.main()

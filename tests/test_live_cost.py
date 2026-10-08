from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from fastapi import HTTPException

from app.grist_parts import GristPartFeatures, GristPartRegistry
from app.grist_types import grist_datetime
from app.live_cost import GristLiveCostService
from app.part_identity import PartIdentityError


class MemoryCostGrist:
    def __init__(self):
        self.tables = {
            "Product": [{"id": 5, "fields": {"Name": "Safari 1000"}}],
            "ProductModel": [{"id": 50, "fields": {"Product": 5, "ModelNumber": "S1K", "Name": "Safari 1000"}}],
            "ProductModelCode": [{"id": 100, "fields": {"ProductModel": 50, "Code": "S1KHF", "Description": "High frame", "Active": True}}],
            "PartScopeShortcode": [{"id": 1, "fields": {"ScopeKey": "product:5", "ScopeType": "product", "ScopeProduct": 5,
                "ScopeTargetId": "5", "ScopeTargetLabel": "Safari 1000", "Shortcode": "S1K", "Version": 1}}],
        }
        self.next_id = 101
        self.lock = threading.Lock()
        self.fail_after_table = None
        self.fail_once = False
        self.doc_id = "isolated-cost-doc"
        self.reads = []

    def validate_safari_write_target(self):
        return None

    def fetch_table_records_with_ids(self, table):
        with self.lock:
            self.reads.append(table)
            return deepcopy(self.tables.get(table, []))

    def create_table_records(self, table, records):
        with self.lock:
            rows = self.tables.setdefault(table, [])
            added = []
            for item in records:
                row = {"id": self.next_id, "fields": deepcopy(item["fields"])}
                self.next_id += 1
                rows.append(row)
                added.append(deepcopy(row))
            should_fail = self.fail_once and table == self.fail_after_table
            if should_fail:
                self.fail_once = False
        if should_fail:
            raise TimeoutError(f"simulated response loss after {table} committed")
        return {"records": added}

    def update_table_records(self, table, updates):
        with self.lock:
            rows = self.tables.setdefault(table, [])
            for update in updates:
                row = next(item for item in rows if item["id"] == update["id"])
                row["fields"].update(deepcopy(update["fields"]))


class LiveCostTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "parts.sqlite3"
        self.client = MemoryCostGrist()
        self.registry = GristPartRegistry(self.client, path=self.path)
        self.registry._verify_writer = lambda: None
        self.features = GristPartFeatures(self.registry)
        self.rate = {"Bolt": "5"}
        self.service = GristLiveCostService(self.registry, material_rate_loader=self.load_rates)
        self._make_model()

    def tearDown(self):
        self.temp.cleanup()

    def load_rates(self, names):
        return {"status": "available", "source": "Costing-New", "sourceDocumentId": "costing-new-test",
            "rows": [{"material": name, "status": "available", "costingNewRate": self.rate.get(name),
                "basis": "Default_MaterialRate", "masterRecordId": 700, "rateLogRecordIds": []} for name in names]}

    def create_part(self, key, name, variant=""):
        return self.registry.create_part(scope_type="product", target_id="5", target_label="Safari 1000",
            description=name, variant=variant, expected_name=f"S1K — {name}" + (f" — {variant}" if variant else ""),
            actor="operator", reason="isolated live costing fixture", request_key=key, revision_assertion="A")["part"]

    def _make_model(self):
        self.parent = self.create_part("parent", "Actuator", "Assembly")
        self.purchased = self.create_part("purchased", "Bearing")
        spec = self.features.create_purchase_specification(part_id=self.purchased["id"], code="BRG-6201", manufacturer="Maker",
            manufacturer_part_number="6201", description="Deep groove bearing", costing_uom="each", currency="INR",
            purchase_item_id=None, actor="buyer", reason="physical purchase specification", request_key="spec")
        vendor = self.features.create_vendor(name="Vendor A", actor="buyer", reason="approved vendor", request_key="vendor")
        mapping = self.features.create_vendor_mapping(specification_id=spec["id"], vendor_id=vendor["id"], sku="A-6201", description="6201 bearing",
            actor="reviewer", reason="manufacturer match", request_key="vendor-map")
        self.spec_id, self.vendor_id, self.mapping_id = spec["id"], vendor["id"], mapping["id"]
        self.purchase("INV-1", "purchase-1", "2026-10-01T00:00:00Z", 100)
        self.features.finalize_revision(part_id=self.purchased["id"], actor="reviewer", reason="frozen physical specification", request_key="finish-bearing")

        material = self.client.create_table_records("Material", [{"fields": {"MaterialKey": "mat-bolt", "CanonicalName": "Bolt", "MappingStatus": "reviewed"}}])["records"][0]
        mcl_master = self.client.create_table_records("LineMaster", [{"fields": {"LineKey": "line:bolt", "ProcessType": "MCL", "SourcePartName": "Bolt"}}])["records"][0]
        mcl_revision = self.client.create_table_records("LineRevision", [{"fields": {"RevisionKey": "revision:bolt", "LineMaster": mcl_master["id"],
            "ProcessType": "MCL", "PhysicalSignature": "bolt-physical-v1", "Status": "active"}}])["records"][0]
        self.client.create_table_records("LineDetail", [{"fields": {"DetailKey": "detail:bolt", "LineRevision": mcl_revision["id"],
            "ProcessType": "MCL", "Material": material["id"], "MaterialDisplayName": "Bolt", "ItemName": "Hex bolt",
            "Quantity": 3, "QuantityUOM": "each"}}])
        self.features.link_process_line(part_id=self.parent["id"], line_master_id=mcl_master["id"], line_revision_id=mcl_revision["id"],
            quantity=1, uom="each", actor="reviewer", reason="reviewed assembly material requirement", request_key="parent-mcl")
        cnc_master = self.client.create_table_records("LineMaster", [{"fields": {"LineKey": "line:cnc", "ProcessType": "CNC", "SourcePartName": "CNC finish"}}])["records"][0]
        cnc_revision = self.client.create_table_records("LineRevision", [{"fields": {"RevisionKey": "revision:cnc", "LineMaster": cnc_master["id"],
            "ProcessType": "CNC", "PhysicalSignature": "cnc-physical-v1", "Status": "active"}}])["records"][0]
        self.client.create_table_records("LineDetail", [{"fields": {"DetailKey": "detail:cnc", "LineRevision": cnc_revision["id"],
            "ProcessType": "CNC", "ItemName": "CNC finish", "Quantity": 1, "QuantityUOM": "each"}}])
        self.features.link_process_line(part_id=self.parent["id"], line_master_id=cnc_master["id"], line_revision_id=cnc_revision["id"],
            quantity=1, uom="each", actor="reviewer", reason="reviewed CNC process line", request_key="parent-cnc")
        self.cnc_master_id = cnc_master["id"]
        self.features.add_component(parent_part_id=self.parent["id"], child_part_id=self.purchased["id"], quantity=2, uom="each",
            actor="reviewer", reason="assembly buys this bearing", request_key="parent-bearing", sourcing_route="buy")
        self.features.add_component(parent_part_id=self.parent["id"], child_part_id=self.purchased["id"], quantity=1, uom="each",
            actor="reviewer", reason="second bearing occurrence in a different assembly location", request_key="parent-bearing-second", sourcing_route="buy")
        self.features.finalize_revision(part_id=self.parent["id"], actor="reviewer", reason="closed child and process baseline", request_key="finish-parent")
        self.service.record_process_rate(self.cnc_master_id, {"rate": 20, "uom": "each", "currency": "INR", "effectiveAt": "2026-01-01T00:00:00Z",
            "sourceReference": "approved CNC shop rate card", "reason": "reviewed source"}, actor="reviewer", request_key="cnc-rate")
        self.initial_configuration_payload = {"currency": "INR", "reason": "current released Model Code selection", "selections": [
            {"selectionIdentity": "main-actuator", "partId": self.parent["id"], "quantity": 2, "uom": "each", "sourcingRoute": "make"}]}
        configured = self.service.save_configuration(100, self.initial_configuration_payload,
            actor="configurator", request_key="configuration-v1")
        self.assertEqual(configured["status"], "configured")

    def purchase(self, tx, key, when, amount):
        return self.features.capture_purchase(part_id=self.purchased["id"], specification_id=self.spec_id, mapping_id=self.mapping_id,
            transaction_key=tx, transaction_line_key="1", record_type="invoice", status="posted", transaction_at=when,
            document_reference=tx, quantity=1, quantity_uom="each", currency="INR", extended_amount=amount,
            discount_amount=0, tax_amount=0, freight_amount=0, other_charges=0, actor="buyer", reason="received actual purchase", request_key=key)

    def rename_part(self, part_id, description, key):
        part = self.registry.get_part(part_id)
        preview = self.registry.preview(scope_type=part["scope"], target_id=part["scopeTargetId"], description=description, variant=part["variant"], exclude_part_id=part_id)
        return self.registry.update_metadata(part_id=part_id, scope_type=part["scope"], target_id=part["scopeTargetId"],
            target_label=part["scopeTarget"], description=description, variant=part["variant"], expected_version=part["metadataVersion"],
            expected_name=preview["name"], expected_usage_fingerprint=self.registry.usage_evidence(part_id)["fingerprint"],
            actor="reviewer", reason="metadata-only rename", request_key=key)["part"]

    def test_live_is_read_only_explicit_snapshot_freezes_mixed_configuration_and_all_rates(self):
        before = {table: len(rows) for table, rows in self.client.tables.items() if table.startswith("CostSnapshot") or table == "PurchasedPartCostEvidence"}
        live = self.service.live_cost(100)
        self.assertEqual(live["status"], "complete")
        self.assertEqual(live["totalCost"], 670)
        self.assertEqual({line["sourceType"] for line in live["lines"]}, {"material", "process", "purchased_part"})
        self.assertEqual(len(live["parts"]), 3)
        self.assertEqual(len({item["occurrencePath"] for item in live["parts"]}), 3)
        for _ in range(2):
            again = self.service.live_cost(100)
            self.assertEqual(again["inputFingerprint"], live["inputFingerprint"])
        after = {table: len(rows) for table, rows in self.client.tables.items() if table.startswith("CostSnapshot") or table == "PurchasedPartCostEvidence"}
        self.assertEqual(before, after)
        saved = self.service.save_snapshot(100, {"liveResult": live, "label": "Reviewed cost", "reason": "baseline capture"},
            actor="cost engineer", request_key="snapshot-one")
        self.assertEqual(saved["snapshot"]["PublicationStatus"], "complete")
        self.assertEqual(saved["snapshot"]["TotalCost"], 670)
        self.assertEqual((len(saved["parts"]), len(saved["lines"]), len(saved["rateEvidence"])), (3, 4, 4))
        self.assertEqual(len(self.client.tables["CostSnapshot"]), 1)
        self.assertEqual(len(self.client.tables.get("PurchasedPartCostEvidence", [])), 0)

        self.rename_part(self.purchased["id"], "Bearing Updated", "metadata-bearing")
        metadata_live = self.service.live_cost(100)
        self.assertEqual(metadata_live["totalCost"], 670)
        self.assertEqual(sum("Bearing Updated" in item["name"] for item in metadata_live["parts"]), 2)
        self.assertEqual(self.registry.get_part(self.parent["id"])["engineeringRevision"], "A")
        self.assertTrue(all("Bearing" in item["FrozenName"] and "Updated" not in item["FrozenName"]
                            for item in saved["parts"] if item["ProductPart"] == self.registry.get_part(self.purchased["id"])["gristRecordId"]))

        self.purchase("INV-2", "purchase-2", "2026-10-07T00:00:00Z", 120)
        self.rate["Bolt"] = "7"
        self.service.record_process_rate(self.cnc_master_id, {"rate": 25, "uom": "each", "currency": "INR", "effectiveAt": "2026-10-07T00:00:00Z",
            "sourceReference": "updated approved CNC shop rate card", "reason": "reviewed current source"}, actor="reviewer", request_key="cnc-rate-v2")
        live_now = self.service.live_cost(100)
        self.assertEqual(live_now["totalCost"], 812)
        historical = self.service.get_snapshot(saved["snapshot"]["SnapshotKey"])
        self.assertEqual(historical["snapshot"]["TotalCost"], 670)
        self.assertEqual(sorted(line["Rate"] for line in historical["lines"]), [5.0, 20.0, 100.0, 100.0])
        self.client.reads.clear()
        self.service.get_snapshot(saved["snapshot"]["SnapshotKey"])
        self.assertFalse({"PartPurchaseRecord", "CostingProcessRate"} & set(self.client.reads))
        self.assertEqual(self.service.compare(100, {"mode": "live_snapshot", "snapshotKey": saved["snapshot"]["SnapshotKey"], "liveResult": live_now})["difference"], 142)
        metadata_and_rates = self.service.compare(100, {"mode": "live_snapshot", "snapshotKey": saved["snapshot"]["SnapshotKey"], "liveResult": live_now})
        self.assertTrue(any("metadata rename (no cost identity change)" in row["classification"] for row in metadata_and_rates["lines"]))
        second = self.service.save_snapshot(100, {"liveResult": live_now, "label": "Updated rates", "reason": "rate refresh"},
            actor="cost engineer", request_key="snapshot-two")
        comparison = self.service.compare(100, {"mode": "snapshot_snapshot", "leftSnapshotKey": saved["snapshot"]["SnapshotKey"],
            "rightSnapshotKey": second["snapshot"]["SnapshotKey"]})
        self.assertEqual((comparison["quantityImpact"], comparison["rateImpact"], comparison["reconciledDelta"]), (0, 142, 142))
        self.assertEqual(self.service.snapshot_history(100)["total"], 2)

    def test_partial_snapshot_publication_recovers_exact_frozen_payload_after_rate_changes_and_restart(self):
        live = self.service.live_cost(100)
        self.assertEqual(live["totalCost"], 670)
        payload = {"liveResult": live, "label": "Recovery capture", "reason": "resume exact staged facts"}
        self.client.fail_after_table = "CostSnapshotLine"
        self.client.fail_once = True
        with self.assertRaises(TimeoutError):
            self.service.save_snapshot(100, payload, actor="cost engineer", request_key="snapshot-recover")
        self.assertEqual(self.service.snapshot_history(100)["total"], 0)
        self.purchase("INV-new", "purchase-new", "2026-10-08T00:00:00Z", 150)
        self.rate["Bolt"] = "8"
        restarted_registry = GristPartRegistry(self.client, path=self.path)
        restarted_registry._verify_writer = lambda: None
        restarted = GristLiveCostService(restarted_registry, material_rate_loader=self.load_rates)
        resumed = restarted.save_snapshot(100, payload, actor="cost engineer", request_key="snapshot-recover")
        self.assertEqual(resumed["snapshot"]["PublicationStatus"], "complete")
        self.assertEqual(resumed["snapshot"]["TotalCost"], 670)
        self.assertEqual(sorted(line["Rate"] for line in resumed["lines"]), [5.0, 20.0, 100.0, 100.0])
        self.assertEqual(restarted.snapshot_history(100)["total"], 1)
        retry = restarted.save_snapshot(100, payload, actor="cost engineer", request_key="snapshot-recover")
        self.assertEqual(retry["snapshot"]["SnapshotKey"], resumed["snapshot"]["SnapshotKey"])
        self.assertEqual(len(self.client.tables["CostSnapshot"]), 1)

    def test_live_without_explicit_configuration_is_incomplete_and_saves_no_history(self):
        self.client.tables["CostingConfiguration"] = []
        result = self.service.live_cost(100)
        self.assertEqual(result["status"], "incomplete")
        self.assertIsNone(result["totalCost"])
        self.assertEqual(self.service.snapshot_history(100)["total"], 0)

    def test_legacy_purchase_rate_evidence_endpoint_requires_snapshot(self):
        from app.web import save_part_purchase_rate_evidence

        with self.assertRaises(HTTPException) as error:
            save_part_purchase_rate_evidence(self.purchased["id"], {})
        self.assertEqual(error.exception.status_code, 410)
        self.assertEqual(error.exception.detail["code"], "SNAPSHOT_REQUIRED")

    def test_save_rejects_live_result_when_inputs_changed_after_display(self):
        live = self.service.live_cost(100)
        self.purchase("INV-later", "purchase-later", "2026-10-08T00:00:00Z", 150)
        with self.assertRaises(PartIdentityError) as stale:
            self.service.save_snapshot(100, {"liveResult": live, "reason": "stale save"}, actor="cost engineer", request_key="stale-snapshot")
        self.assertEqual(stale.exception.code, "COST_LIVE_STALE")
        self.assertEqual(self.service.snapshot_history(100)["total"], 0)

    def test_frozen_snapshot_read_detects_direct_grist_edit(self):
        live = self.service.live_cost(100)
        saved = self.service.save_snapshot(100, {"liveResult": live, "reason": "integrity check"}, actor="cost engineer", request_key="snapshot-integrity")
        line = self.client.tables["CostSnapshotLine"][0]
        self.client.update_table_records("CostSnapshotLine", [{"id": line["id"], "fields": {"NetCost": 999}}])
        with self.assertRaises(PartIdentityError) as error:
            self.service.get_snapshot(saved["snapshot"]["SnapshotKey"])
        self.assertEqual(error.exception.code, "COST_SNAPSHOT_INTEGRITY_FAILED")

    def test_configuration_publication_resumes_and_old_retry_does_not_roll_back_current_revision(self):
        next_payload = {"currency": "INR", "reason": "reviewed quantity correction", "selections": [
            {"selectionIdentity": "main-actuator", "partId": self.parent["id"], "quantity": 3, "uom": "each", "sourcingRoute": "make"}]}
        self.client.fail_after_table = "ConfigurationPartSelection"
        self.client.fail_once = True
        with self.assertRaises(TimeoutError):
            self.service.save_configuration(100, next_payload, actor="configurator", request_key="configuration-v2")
        self.assertEqual(self.service.get_configuration(100)["configuration"]["version"], 1)
        recovered = self.service.save_configuration(100, next_payload, actor="configurator", request_key="configuration-v2")
        self.assertEqual(recovered["configuration"]["version"], 2)
        self.assertEqual(self.service.live_cost(100)["totalCost"], 1005)
        replay = self.service.save_configuration(100, self.initial_configuration_payload, actor="configurator", request_key="configuration-v1")
        self.assertEqual(replay["configuration"]["version"], 2)
        self.assertEqual(self.service.live_cost(100)["configuration"]["revisionKey"], "code:100:configuration:2")
        self.assertEqual(len(self.client.tables["CostingConfiguration"]), 1)
        self.assertEqual(len(self.client.tables["CostingConfigurationRevision"]), 2)

    def test_quantity_rate_attribution_zero_baseline_and_currency_guard(self):
        compare = self.service._comparison_result(
            {"total": 1200, "currency": "INR", "costBasis": "same", "lines": [{"matchKey": "bearing", "quantity": 2, "rate": 600, "netCost": 1200, "linear": True}]},
            {"total": 1950, "currency": "INR", "costBasis": "same", "lines": [{"matchKey": "bearing", "quantity": 3, "rate": 650, "netCost": 1950, "linear": True}]},
            {"left": "Old", "right": "New"})
        self.assertEqual((compare["quantityImpact"], compare["rateImpact"], compare["difference"]), (600, 150, 750))
        zero = self.service._comparison_result({"total": 0, "currency": "INR", "costBasis": "same", "lines": []},
            {"total": 10, "currency": "INR", "costBasis": "same", "lines": []}, {"left": "Zero", "right": "New"})
        self.assertTrue(zero["zeroBaselinePercentUndefined"])
        self.assertIsNone(zero["percentDifference"])
        with self.assertRaises(PartIdentityError):
            self.service.compare(100, {"mode": "snapshot_snapshot", "leftSnapshotKey": "missing", "rightSnapshotKey": "missing"})
        live = self.service.live_cost(100)
        with patch.object(self.service, "get_snapshot", return_value={"snapshot": {"ProductModelCode": 100, "TotalCost": 1, "Currency": "USD", "CostBasis": live["costBasis"]}, "parts": [], "lines": [], "rateEvidence": []}):
            incompatible = self.service.compare(100, {"mode": "live_snapshot", "snapshotKey": "foreign-currency", "liveResult": live})
        self.assertEqual(incompatible["status"], "incomparable")

    def test_comparisons_reject_snapshot_from_another_model_code(self):
        live = self.service.live_cost(100)
        foreign = {"snapshot": {"ProductModelCode": 101, "Currency": "INR", "CostBasis": live["costBasis"]}, "parts": [], "lines": [], "rateEvidence": []}
        with patch.object(self.service, "get_snapshot", return_value=foreign):
            with self.assertRaises(PartIdentityError) as error:
                self.service.compare(100, {"mode": "live_snapshot", "snapshotKey": "foreign-code", "liveResult": live})
        self.assertEqual(error.exception.code, "COST_SNAPSHOT_CODE_MISMATCH")

    def test_policy_inheritance_manual_and_calendar_month_edge(self):
        self.service.set_policy("system", None, {"policy": "monthly", "timeZone": "Asia/Kolkata", "scheduleAnchorAt": "2026-01-31T09:00:00+05:30", "anchorDay": 31, "reason": "monthly reminder"},
            actor="owner", request_key="policy-system")
        live = self.service.live_cost(100)
        saved = self.service.save_snapshot(100, {"liveResult": live, "reason": "monthly test snapshot"}, actor="cost engineer", request_key="policy-snapshot")
        header = next(row for row in self.client.tables["CostSnapshot"] if row["fields"].get("SnapshotKey") == saved["snapshot"]["SnapshotKey"])
        self.client.update_table_records("CostSnapshot", [{"id": header["id"], "fields": {"CapturedAt": grist_datetime("2026-01-31T09:00:00+05:30")}}])
        before_due = self.service.policy_state(100, now=datetime(2026, 2, 27, 4, 0, tzinfo=timezone.utc))
        state = self.service.policy_state(100, now=datetime(2026, 2, 28, 4, 0, tzinfo=timezone.utc))
        self.assertEqual(state["policy"], "monthly")
        self.assertFalse(before_due["due"])
        self.assertTrue(state["due"])
        self.assertTrue(state["nextDueAt"].startswith("2026-02-28T03:30:00"))
        self.assertFalse(state["firstSnapshotDueImmediately"])
        self.service.set_policy("model_code", 100, {"policy": "manual", "reason": "manual reviews", "timeZone": "Asia/Kolkata"},
            actor="owner", request_key="policy-manual")
        manual = self.service.policy_state(100)
        self.assertEqual((manual["policy"], manual["source"], manual["due"], manual["snapshotNowAvailable"]), ("manual", "model_code", False, True))
        self.service.set_policy("model_code", 100, {"policy": "inherit", "reason": "restore inherited schedule", "timeZone": "Asia/Kolkata"},
            actor="owner", request_key="policy-inherit")
        inherited = self.service.policy_state(100)
        self.assertEqual((inherited["policy"], inherited["source"]), ("monthly", "system"))


if __name__ == "__main__":
    unittest.main()

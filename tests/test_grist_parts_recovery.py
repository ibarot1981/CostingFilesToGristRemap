from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
import tempfile
import threading
import unittest

from app.grist_parts import GristPartFeatures, GristPartRegistry
from app.part_identity import PartIdentityError


class MemoryGrist:
    """Small Grist API double with durable rows and an after-commit timeout hook."""

    def __init__(self):
        self.tables: dict[str, list[dict]] = {"PartScopeShortcode": [
            {"id": 1, "fields": {"ScopeKey": "product:17", "ScopeType": "product", "ScopeProduct": 17,
                               "ScopeTargetId": "17", "ScopeTargetLabel": "Safari 1000", "Shortcode": "S1K", "Version": 1}}
        ]}
        self.next_id = 2
        self.lock = threading.Lock()
        self.lose_next_metadata_response = False

    def validate_safari_write_target(self):
        return None

    def fetch_table_records_with_ids(self, table):
        with self.lock:
            return deepcopy(self.tables.get(table, []))

    def create_table_records(self, table, records):
        with self.lock:
            rows = self.tables.setdefault(table, [])
            created = []
            for value in records:
                item = {"id": self.next_id, "fields": deepcopy(value["fields"])}
                self.next_id += 1
                rows.append(item)
                created.append(deepcopy(item))
            if table == "PartMetadataVersion" and self.lose_next_metadata_response:
                self.lose_next_metadata_response = False
                raise TimeoutError("simulated lost response after Grist committed the row")
            return {"records": created}

    def update_table_records(self, table, updates):
        with self.lock:
            rows = self.tables.setdefault(table, [])
            for update in updates:
                target = next(row for row in rows if row["id"] == update["id"])
                target["fields"].update(deepcopy(update["fields"]))


class GristPartRecoveryTests(unittest.TestCase):
    def make_registry(self, client, path):
        registry = GristPartRegistry(client, path=path)
        # These tests focus on Grist publication/recovery and allocator behavior.
        # Production writer binding and exact-document checks have separate guards.
        registry._verify_writer = lambda: None
        return registry

    def create(self, registry, *, request_key="create-one", description="Chassis", variant="Standard"):
        return registry.create_part(scope_type="product", target_id="17", target_label="Safari 1000",
            description=description, variant=variant, expected_name=f"S1K — {description}" + (f" — {variant}" if variant else ""),
            actor="test operator", reason="isolated recovery test", request_key=request_key, revision_assertion="A")

    def test_grist_partial_publication_recovers_without_duplicates_and_survives_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "parts-journal.sqlite3"
            client = MemoryGrist()
            client.lose_next_metadata_response = True
            first_registry = self.make_registry(client, path)
            with self.assertRaises(TimeoutError):
                self.create(first_registry)
            part_before_retry = client.tables["ProductPart"][0]["fields"]
            self.assertEqual(part_before_retry["PublishStatus"], "publishing")
            self.assertEqual(part_before_retry["ScopeProduct"], 17)

            resumed = self.create(first_registry)
            part = resumed["part"]
            self.assertEqual((part["partNumber"], part["engineeringRevision"], part["publishStatus"]),
                             ("SM-P-000001", "A", "published"))
            self.assertEqual(client.tables["PartMetadataVersion"][0]["fields"]["ScopeProduct"], 17)
            self.assertTrue(client.tables["PartNameAlias"][0]["fields"]["IsCurrent"])
            self.assertEqual(client.tables["PartRevision"][0]["fields"]["RevisionLabel"], "A")
            self.assertEqual(len(client.tables["ProductPart"]), 1)
            self.assertEqual(len(client.tables["PartMetadataVersion"]), 1)
            self.assertEqual(len(client.tables["PartNameAlias"]), 1)
            self.assertEqual(len(client.tables["PartRevision"]), 1)
            self.assertEqual(client.tables["PartRegistryRequest"][0]["fields"]["Status"], "published")

            after_restart = self.make_registry(client, path)
            reread = after_restart.get_part(part["id"])
            self.assertEqual((reread["partNumber"], reread["name"], reread["engineeringRevision"]),
                             ("SM-P-000001", "S1K — Chassis — Standard", "A"))
            self.assertEqual(self.create(after_restart)["part"]["id"], part["id"])
            self.assertEqual(len(client.tables["ProductPart"]), 1)

    def test_reused_request_key_with_changed_payload_conflicts(self):
        with tempfile.TemporaryDirectory() as temp:
            client = MemoryGrist()
            registry = self.make_registry(client, Path(temp) / "parts.sqlite3")
            self.create(registry)
            with self.assertRaises(PartIdentityError) as error:
                self.create(registry, description="Frame")
            self.assertEqual(error.exception.code, "PART_REQUEST_CONFLICT")
            self.assertEqual(len(client.tables["ProductPart"]), 1)

    def test_two_registry_instances_allocate_different_numbers_under_concurrency(self):
        with tempfile.TemporaryDirectory() as temp:
            client = MemoryGrist()
            path = Path(temp) / "shared-parts.sqlite3"
            registries = [self.make_registry(client, path), self.make_registry(client, path)]
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda args: self.create(args[0], request_key=args[1], description=args[2], variant=""),
                                        [(registries[0], "create-a", "Engine Mount"), (registries[1], "create-b", "Gear Cover")]))
            numbers = {item["part"]["partNumber"] for item in results}
            self.assertEqual(numbers, {"SM-P-000001", "SM-P-000002"})
            self.assertEqual(len(client.tables["ProductPart"]), 2)

    def test_reusable_child_mixed_process_lines_purchase_history_and_finalization_lock(self):
        with tempfile.TemporaryDirectory() as temp:
            client = MemoryGrist()
            registry = self.make_registry(client, Path(temp) / "parts.sqlite3")
            parent = self.create(registry, request_key="parent", description="Chassis", variant="Standard")["part"]
            second_parent = self.create(registry, request_key="second-parent", description="Chassis", variant="Reinforced")["part"]
            purchased_child = self.create(registry, request_key="child", description="Bearing", variant="")["part"]
            features = GristPartFeatures(registry)

            for parent_part, key in ((parent, "child-a"), (second_parent, "child-b")):
                result = features.add_component(parent_part_id=parent_part["id"], child_part_id=purchased_child["id"],
                    quantity=2, uom="each", actor="test operator", reason="isolated composition test", request_key=key)
                self.assertEqual(result["components"][0]["quantity"], 2)
                self.assertEqual(result["components"][0]["childPartId"], purchased_child["id"])

            line_ids = []
            for process in ("MCL", "Toolshop", "CNC"):
                master = client.create_table_records("LineMaster", [{"fields": {"LineKey": f"line:{process}", "ProcessType": process, "SourcePartName": process}}])["records"][0]
                revision = client.create_table_records("LineRevision", [{"fields": {"RevisionKey": f"line-rev:{process}", "LineMaster": master["id"], "ProcessType": process, "Status": "active"}}])["records"][0]
                linked = features.link_process_line(part_id=parent["id"], line_master_id=master["id"], line_revision_id=revision["id"],
                    quantity=1.25, uom="each", actor="test operator", reason="reviewed initial requirement", request_key=f"line-{process}")
                self.assertEqual(linked["line"]["ProcessType"], process)
                line_ids.append(linked["id"])
            self.assertEqual(len({client.tables["PartRevisionLine"][i]["fields"]["ProcessType"] for i in range(3)}), 3)

            with self.assertRaises(PartIdentityError) as cycle:
                features.add_component(parent_part_id=purchased_child["id"], child_part_id=parent["id"], quantity=1,
                    uom="each", actor="test operator", reason="cycle attempt", request_key="cycle")
            self.assertEqual(cycle.exception.code, "PART_COMPONENT_CYCLE")

            spec = features.create_purchase_specification(part_id=purchased_child["id"], code="BRG-6201", manufacturer="Maker",
                manufacturer_part_number="6201", description="Equivalent bearing", costing_uom="each", currency="INR",
                purchase_item_id=None, actor="test operator", reason="physical specification", request_key="spec")["id"]
            vendor_a = features.create_vendor(name="Vendor A", actor="test operator", reason="supplier", request_key="vendor-a")["id"]
            vendor_b = features.create_vendor(name="Vendor B", actor="test operator", reason="supplier", request_key="vendor-b")["id"]
            mapping_a = features.create_vendor_mapping(specification_id=spec, vendor_id=vendor_a, sku="A-6201", description="6201 bearing",
                actor="reviewer", reason="matched manufacturer number", request_key="map-a")["id"]
            mapping_b = features.create_vendor_mapping(specification_id=spec, vendor_id=vendor_b, sku="B-6201", description="6201 bearing",
                actor="reviewer", reason="matched manufacturer number", request_key="map-b")["id"]

            def record(mapping_id, tx, when, amount, key):
                return features.capture_purchase(part_id=purchased_child["id"], specification_id=spec, mapping_id=mapping_id,
                    transaction_key=tx, transaction_line_key="1", record_type="invoice", status="posted", transaction_at=when,
                    document_reference=tx, quantity=1, quantity_uom="each", currency="INR", extended_amount=amount,
                    discount_amount=0, tax_amount=0, freight_amount=0, other_charges=0, actor="buyer", reason="actual purchase", request_key=key)

            record(mapping_a, "A-100", "2026-01-01T00:00:00Z", 10, "purchase-a")
            record(mapping_b, "B-200", "2026-02-01T00:00:00Z", 12, "purchase-b")
            historical = features.purchase_detail(purchased_child["id"], as_of="2026-02-15T00:00:00Z",
                cost_run_key="run-historical", configuration_selection_key="selection-child")
            self.assertEqual(historical["specifications"][0]["rate"]["rate"], 12)
            record(mapping_a, "A-300", "2026-03-01T00:00:00Z", 14, "purchase-a-later")
            current = features.purchase_detail(purchased_child["id"])["specifications"][0]["rate"]
            self.assertEqual((current["vendorId"], current["rate"]), (str(vendor_a), 14))
            self.assertEqual(registry.get_part(purchased_child["id"])["partNumber"], purchased_child["partNumber"])
            self.assertEqual(registry.get_part(purchased_child["id"])["engineeringRevision"], "A")
            self.assertTrue(client.tables["PurchasedPartCostEvidence"])

            features.finalize_revision(part_id=parent["id"], actor="reviewer", reason="baseline reviewed", request_key="finalize-parent")
            with self.assertRaises(PartIdentityError) as locked:
                features.add_component(parent_part_id=parent["id"], child_part_id=purchased_child["id"], quantity=1,
                    uom="each", actor="test operator", reason="post-finalization edit", request_key="late-component")
            self.assertEqual(locked.exception.code, "PART_REVISION_LOCKED")


if __name__ == "__main__":
    unittest.main()

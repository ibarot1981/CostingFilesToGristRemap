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
        self.lose_next_purchase_response = False
        self.lose_after_create_table = ""
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
            for value in records:
                item = {"id": self.next_id, "fields": deepcopy(value["fields"])}
                self.next_id += 1
                rows.append(item)
                created.append(deepcopy(item))
            if table == "PartMetadataVersion" and self.lose_next_metadata_response:
                self.lose_next_metadata_response = False
                raise TimeoutError("simulated lost response after Grist committed the row")
            if table == "PartPurchaseRecord" and self.lose_next_purchase_response:
                self.lose_next_purchase_response = False
                raise TimeoutError("simulated lost response after Grist committed the purchase")
            if table == self.lose_after_create_table:
                self.lose_after_create_table = ""
                raise TimeoutError(f"simulated lost response after Grist committed {table}")
            return {"records": created}

    def update_table_records(self, table, updates):
        should_fail = False
        with self.lock:
            rows = self.tables.setdefault(table, [])
            for update in updates:
                target = next(row for row in rows if row["id"] == update["id"])
                target["fields"].update(deepcopy(update["fields"]))
            should_fail = table == self.lose_after_update_table
            if should_fail:
                self.lose_after_update_table = ""
        if should_fail:
            raise TimeoutError(f"simulated lost response after Grist updated {table}")


class GristPartRecoveryTests(unittest.TestCase):
    def make_registry(self, client, path):
        registry = GristPartRegistry(client, path=path)
        # These tests focus on Grist publication/recovery and allocator behavior.
        # Production writer binding and exact-document checks have separate guards.
        registry._verify_writer = lambda: None
        return registry

    def test_grist_scope_shortcodes_use_the_shared_target_lookup_shape(self):
        with tempfile.TemporaryDirectory() as temp:
            client = MemoryGrist()
            client.tables["PartScopeShortcode"].append({"id": 2, "fields": {
                "ScopeKey": "model_code:203", "ScopeTargetLabel": "S500 EMS", "Shortcode": "S500E",
                "Version": 2,
            }})
            registry = self.make_registry(client, Path(temp) / "parts-journal.sqlite3")
            self.assertEqual(registry.shortcodes(), [
                {"scope_type": "model_code", "target_id": "203", "target_label": "S500 EMS", "shortcode": "S500E", "version": 2},
                {"scope_type": "product", "target_id": "17", "target_label": "Safari 1000", "shortcode": "S1K", "version": 1},
            ])

    def create(self, registry, *, request_key="create-one", description="Chassis", variant="Standard"):
        return registry.create_part(scope_type="product", target_id="17", target_label="Safari 1000",
            description=description, variant=variant, expected_name=f"S1K — {description}" + (f" — {variant}" if variant else ""),
            actor="test operator", reason="isolated recovery test", request_key=request_key, revision_assertion="A")

    def rename(self, registry, part, *, request_key, description):
        target_id = part["scopeTargetId"]
        preview = registry.preview(scope_type=part["scope"], target_id=target_id, description=description, variant=part["variant"])
        return registry.update_metadata(part_id=part["id"], scope_type=part["scope"], target_id=target_id,
            target_label=part["scopeTarget"], description=description, variant=part["variant"],
            expected_version=part["metadataVersion"], expected_name=preview["name"],
            expected_usage_fingerprint=registry.usage_evidence(part["id"])["fingerprint"], actor="reviewer",
            reason="metadata history test", request_key=request_key)

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
            self.assertFalse(client.tables.get("PurchasedPartCostEvidence"))

            with self.assertRaises(PartIdentityError) as open_child:
                features.finalize_revision(part_id=parent["id"], actor="reviewer", reason="baseline reviewed", request_key="finalize-parent-too-early")
            self.assertEqual(open_child.exception.code, "PART_CHILD_REVISION_DRAFT")
            features.finalize_revision(part_id=purchased_child["id"], actor="reviewer", reason="purchased Part baseline", request_key="finalize-child")
            features.finalize_revision(part_id=parent["id"], actor="reviewer", reason="baseline reviewed", request_key="finalize-parent")
            original_definition = registry.current_revision(parent["id"])["fields"]["DefinitionHash"]
            renamed_child = registry.get_part(purchased_child["id"])
            self.rename(registry, renamed_child, request_key="rename-purchased-child", description="Bearing Set")
            self.assertIn("Bearing Set", features.components(parent["id"])[0]["childName"])
            self.assertEqual(registry.current_revision(parent["id"])["fields"]["DefinitionHash"], original_definition)
            with self.assertRaises(PartIdentityError) as child_locked:
                features.add_component(parent_part_id=purchased_child["id"], child_part_id=parent["id"], quantity=1,
                    uom="each", actor="test operator", reason="post-finalization indirect change", request_key="late-child-component")
            self.assertEqual(child_locked.exception.code, "PART_REVISION_LOCKED")
            with self.assertRaises(PartIdentityError) as spec_locked:
                features.create_purchase_specification(part_id=purchased_child["id"], code="SECOND", manufacturer="Maker",
                    manufacturer_part_number="new", description="new physical identity", costing_uom="each", currency="INR",
                    purchase_item_id=None, actor="buyer", reason="late physical spec", request_key="late-spec")
            self.assertEqual(spec_locked.exception.code, "PART_REVISION_LOCKED")
            with self.assertRaises(PartIdentityError) as locked:
                features.add_component(parent_part_id=parent["id"], child_part_id=purchased_child["id"], quantity=1,
                    uom="each", actor="test operator", reason="post-finalization edit", request_key="late-component")
            self.assertEqual(locked.exception.code, "PART_REVISION_LOCKED")

    def test_different_rename_cannot_adopt_pending_metadata_and_originating_retry_recovers(self):
        with tempfile.TemporaryDirectory() as temp:
            client = MemoryGrist()
            registry = self.make_registry(client, Path(temp) / "parts.sqlite3")
            part = self.create(registry)["part"]
            client.lose_next_metadata_response = True
            with self.assertRaises(TimeoutError):
                self.rename(registry, part, request_key="rename-a", description="Frame A")
            with self.assertRaises(PartIdentityError) as conflict:
                self.rename(registry, part, request_key="rename-b", description="Frame B")
            self.assertEqual(conflict.exception.code, "PART_METADATA_PUBLICATION_CONFLICT")
            recovered = self.rename(registry, part, request_key="rename-a", description="Frame A")["part"]
            self.assertEqual(recovered["name"], "S1K — Frame A — Standard")
            self.assertEqual(client.tables["ProductPart"][0]["fields"]["CurrentMetadataVersion"], client.tables["PartMetadataVersion"][1]["id"])
            self.assertEqual(client.tables["PartNameAlias"][-1]["fields"]["DisplayName"], recovered["name"])
            originating = next(row for row in client.tables["PartRegistryRequest"] if row["fields"].get("RequestKey") == "rename-a")
            self.assertEqual(originating["fields"]["Status"], "published")
            self.rename(registry, recovered, request_key="rename-c", description="Frame C")
            old_retry = self.rename(registry, part, request_key="rename-a", description="Frame A")
            self.assertTrue(old_retry["idempotent"])
            self.assertEqual(old_retry["part"]["name"], "S1K — Frame C — Standard")
            self.assertEqual(len([row for row in client.tables["PartMetadataVersion"] if row["fields"].get("ProductPart") == client.tables["ProductPart"][0]["id"]]), 3)

    def test_metadata_exact_retry_recovers_after_every_grist_publication_boundary(self):
        for phase, table in (("create", "PartNameAlias"), ("create", "AuditEvent"),
                             ("update", "ProductPart"), ("update", "PartRegistryRequest")):
            with self.subTest(phase=phase, table=table), tempfile.TemporaryDirectory() as temp:
                client = MemoryGrist()
                registry = self.make_registry(client, Path(temp) / "parts.sqlite3")
                part = self.create(registry)["part"]
                if phase == "create":
                    client.lose_after_create_table = table
                else:
                    client.lose_after_update_table = table
                with self.assertRaises(TimeoutError):
                    self.rename(registry, part, request_key="rename-recover", description="Frame Updated")
                recovered = self.rename(registry, part, request_key="rename-recover", description="Frame Updated")["part"]
                self.assertEqual(recovered["name"], "S1K — Frame Updated — Standard")
                self.assertEqual(len(client.tables["PartMetadataVersion"]), 2)
                self.assertEqual(len(client.tables["PartNameAlias"]), 2)
                journal = next(row for row in client.tables["PartRegistryRequest"] if row["fields"].get("RequestKey") == "rename-recover")
                self.assertEqual(journal["fields"]["Status"], "published")

    def test_purchase_epoch_retry_conflict_and_cross_request_transaction_deduplication(self):
        with tempfile.TemporaryDirectory() as temp:
            client = MemoryGrist()
            registry = self.make_registry(client, Path(temp) / "parts.sqlite3")
            part = self.create(registry, variant="")["part"]
            features = GristPartFeatures(registry)
            spec = features.create_purchase_specification(part_id=part["id"], code="BRG", manufacturer="Maker", manufacturer_part_number="6201",
                description="Bearing", costing_uom="each", currency="INR", purchase_item_id=None, actor="buyer", reason="spec", request_key="spec")
            vendor = features.create_vendor(name="Vendor A", actor="buyer", reason="approved", request_key="vendor")
            mapping = features.create_vendor_mapping(specification_id=spec["id"], vendor_id=vendor["id"], sku="A-1", description="6201",
                actor="reviewer", reason="matched", request_key="mapping")
            client.lose_next_purchase_response = True
            args = dict(part_id=part["id"], specification_id=spec["id"], mapping_id=mapping["id"], transaction_key="INV-1",
                transaction_line_key="1", record_type="invoice", status="posted", transaction_at="2026-10-01T00:00:00Z",
                document_reference="INV-1", quantity=1, quantity_uom="each", currency="INR", extended_amount=100,
                discount_amount=0, tax_amount=0, freight_amount=0, other_charges=0, actor="buyer", reason="received", request_key="buy1")
            with self.assertRaises(TimeoutError):
                features.capture_purchase(**args)
            self.assertIsInstance(client.tables["PartPurchaseRecord"][0]["fields"]["TransactionAt"], float)
            saved_id = client.tables["PartPurchaseRecord"][0]["id"]
            replay = features.capture_purchase(**args)
            self.assertTrue(replay["idempotent"])
            self.assertEqual(replay["id"], saved_id)
            with self.assertRaises(PartIdentityError) as changed:
                features.capture_purchase(**{**args, "extended_amount": 101})
            self.assertEqual(changed.exception.code, "PART_REQUEST_CONFLICT")
            imported = features.capture_purchase(**{**args, "request_key": "buy2"})
            self.assertTrue(imported["idempotent"])
            self.assertEqual(imported["id"], replay["id"])
            self.assertEqual(len(client.tables["PartPurchaseRecord"]), 1)


if __name__ == "__main__":
    unittest.main()

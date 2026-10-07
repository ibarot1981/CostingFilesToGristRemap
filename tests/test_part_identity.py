from concurrent.futures import ProcessPoolExecutor
import multiprocessing
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from types import SimpleNamespace

from app.part_identity import PartIdentityError, PartIdentityStore
from app.part_mapping import MemoryPartStore, PartRegistryMappingStore, mapping_detail, save_mapping, source_groups


def _allocate_in_process(args):
    path, index = args
    store = PartIdentityStore(path)
    name = store.preview(scope_type="global", target_id="global", description=f"Fixture {index}")["name"]
    return store.create_part(scope_type="global", target_id="global", target_label="Safari Manufacturing",
        description=f"Fixture {index}", expected_name=name, actor="owner", reason="Concurrent allocation",
        request_key=f"concurrent-{index}")["part"]["partNumber"]


class PartIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.path = Path(self.temp.name) / "parts.sqlite3"
        self.store = PartIdentityStore(self.path)
        self.store.set_shortcode(scope_type="global", target_id="global", target_label="Safari Manufacturing",
                                shortcode="GBL", actor="owner", reason="Maintain global name prefix", request_key="global-code")
        self.store.set_shortcode(scope_type="product", target_id="product-1", target_label="Safari 1000",
                                shortcode="S1K", actor="owner", reason="Maintain Product prefix", request_key="product-code")
        self.store.set_shortcode(scope_type="product_model", target_id="model-1", target_label="S1KHF Safari 1000 HF",
                                shortcode="S1KHF", actor="owner", reason="Maintain Model prefix", request_key="model-code")
        self.store.set_shortcode(scope_type="model_code", target_id="code-1", target_label="S1KHFELP",
                                shortcode="S1KHFELP", actor="owner", reason="Maintain Code prefix", request_key="code-code")

    def tearDown(self):
        self.temp.cleanup()

    def create(self, key="create-1", *, scope="product_model", target="model-1", description="Chassis", variant="Standard", expected_name=None, reason="Distinct physical design"):
        name = expected_name or self.store.preview(scope_type=scope, target_id=target, description=description, variant=variant)["name"]
        return self.store.create_part(scope_type=scope, target_id=target, target_label="Scope target", description=description,
            variant=variant, expected_name=name, actor="owner", reason=reason, request_key=key)

    def test_atomic_number_allocation_is_unique_across_instances_and_restart(self):
        with ProcessPoolExecutor(max_workers=6, mp_context=multiprocessing.get_context("spawn")) as pool:
            numbers = list(pool.map(_allocate_in_process, [(str(self.path), index) for index in range(18)]))
        self.assertEqual(len(numbers), len(set(numbers)))
        self.assertEqual(set(numbers), {f"SM-P-{number:06d}" for number in range(1, 19)})
        restarted = PartIdentityStore(self.path)
        number = restarted.preview(scope_type="global", target_id="global", description="After restart")["name"]
        result = restarted.create_part(scope_type="global", target_id="global", target_label="Safari Manufacturing",
            description="After restart", expected_name=number, actor="owner", reason="Restart recovery", request_key="after-restart")
        self.assertEqual(result["part"]["partNumber"], "SM-P-000019")

    def test_lost_response_replay_returns_same_identity_and_changed_payload_conflicts(self):
        first = self.create(key="retry-key")
        replay = self.create(key="retry-key")
        self.assertTrue(replay["idempotent"])
        self.assertEqual(first["part"]["id"], replay["part"]["id"])
        self.assertEqual(first["part"]["partNumber"], replay["part"]["partNumber"])
        with self.assertRaisesRegex(PartIdentityError, "different Part request"):
            self.create(key="retry-key", description="Changed payload")

    def test_generated_names_cover_all_scopes_and_same_model_variants(self):
        cases = [("global", "global", "GBL — Chassis — Standard"),
                 ("product", "product-1", "S1K — Chassis — Standard"),
                 ("product_model", "model-1", "S1KHF — Chassis — Standard"),
                 ("model_code", "code-1", "S1KHFELP — Chassis — Standard")]
        for index, (scope, target, expected) in enumerate(cases):
            preview = self.store.preview(scope_type=scope, target_id=target, description="Chassis", variant="Standard")
            self.assertEqual(preview["name"], expected)
            result = self.create(key=f"scope-{index}", scope=scope, target=target)
            self.assertEqual(result["part"]["name"], expected)
        reinforced = self.create(key="reinforced", variant="Reinforced")
        self.assertEqual(reinforced["part"]["name"], "S1KHF — Chassis — Reinforced")
        self.assertNotEqual(reinforced["part"]["partNumber"], self.store.list_parts()[2]["partNumber"])

    def test_name_and_scope_change_preserves_number_rev_a_and_old_name_alias(self):
        created = self.create()
        original = created["part"]
        preview = self.store.preview(scope_type="product", target_id="product-1", description="Chassis", variant="Standard", exclude_part_id=original["id"])
        usage = self.store.usage_evidence(original["id"])
        changed = self.store.update_metadata(part_id=original["id"], scope_type="product", target_id="product-1", target_label="Safari 1000",
            description="Chassis", variant="Standard", expected_version=1, expected_name=preview["name"], actor="owner",
            expected_usage_fingerprint=usage["fingerprint"], reason="Reviewed broader sharing and design label", request_key="metadata-1")["part"]
        self.assertEqual(changed["partNumber"], original["partNumber"])
        self.assertEqual(changed["engineeringRevision"], "A")
        detail = self.store.get_part(original["id"])
        self.assertEqual(detail["metadataVersion"], 2)
        self.assertEqual(self.store.list_parts(search="S1KHF — Chassis — Standard")[0]["id"], original["id"])
        collision = self.store.preview(scope_type="product_model", target_id="model-1", description="Chassis", variant="Standard")
        self.assertFalse(collision["available"])

    def test_metadata_edit_cannot_change_physical_design_variant(self):
        created = self.create(key="variant-guard")['part']
        preview = self.store.preview(scope_type="product_model", target_id="model-1", description="Chassis", variant="Reinforced", exclude_part_id=created["id"])
        usage = self.store.usage_evidence(created["id"])
        with self.assertRaisesRegex(PartIdentityError, "distinct design variant needs a new Part"):
            self.store.update_metadata(part_id=created["id"], scope_type="product_model", target_id="model-1", target_label="Scope target",
                description="Chassis", variant="Reinforced", expected_version=1, expected_name=preview["name"], actor="owner",
                expected_usage_fingerprint=usage["fingerprint"], reason="Try to edit a design change", request_key="variant-edit")

    def test_name_normalization_and_legacy_names_block_ambiguous_reuse(self):
        first = self.create()
        conflict = self.store.preview(scope_type="product_model", target_id="model-1", description="  CHASSIS  ", variant="standard")
        self.assertFalse(conflict["available"])
        self.store.sync_legacy_names([{"id": 37, "fields": {"DisplayName": "Legacy — Bearing Housing", "Status": "canonical"}}])
        legacy_preview = self.store.preview(scope_type="global", target_id="global", description="Bearing Housing", variant="Legacy")
        self.assertTrue(legacy_preview["available"])
        legacy_name = "GBL — Bearing Housing — Legacy"
        self.store.sync_legacy_names([{"id": 38, "fields": {"DisplayName": legacy_name, "Status": "canonical"}}])
        self.assertFalse(self.store.preview(scope_type="global", target_id="global", description="Bearing Housing", variant="Legacy")["available"])
        with self.assertRaisesRegex(PartIdentityError, "already used"):
            self.create(key="blocked-legacy", scope="global", target="global", description="Bearing Housing", variant="Legacy")
        self.assertEqual(first["part"]["partNumber"], "SM-P-000001")

    def test_shortcodes_cannot_be_ambiguous_and_missing_shortcode_fails_closed(self):
        with self.assertRaisesRegex(PartIdentityError, "already maintained"):
            self.store.set_shortcode(scope_type="product", target_id="product-2", target_label="Safari 2000",
                shortcode="s1k", actor="owner", reason="Maintain duplicate", request_key="product-code-2")
        with self.assertRaisesRegex(PartIdentityError, "shortcode"):
            self.store.preview(scope_type="product", target_id="product-2", description="Drum")

    def test_rev_a_is_fixed_retirement_does_not_recycle_number(self):
        with self.assertRaisesRegex(PartIdentityError, "Rev A"):
            self.store.create_part(scope_type="global", target_id="global", target_label="Safari Manufacturing", description="Engine",
                expected_name="GBL — Engine", actor="owner", reason="Attempt future revision", request_key="rev-b", revision_assertion="B")
        first = self.create(key="to-retire")['part']
        self.store.retire_part(part_id=first["id"], expected_version=1, actor="owner", reason="No longer released", request_key="retire-1")
        next_part = self.create(key="after-retire", description="Chassis", variant="Design 02")["part"]
        self.assertEqual(first["partNumber"], "SM-P-000001")
        self.assertEqual(next_part["partNumber"], "SM-P-000002")
        self.assertEqual(self.store.get_part(first["id"])["status"], "retired")

    def test_stable_part_assignment_is_durable_and_retry_safe(self):
        part = self.create(key="for-mapping")["part"]
        store = PartRegistryMappingStore(MemoryPartStore(), self.store)
        association = SimpleNamespace(id="association-1", version=1)
        groups = source_groups({"Tool Shop Items": [{"status": "active", "source_row": 10,
            "fields": {"product_part_name": "Source shaft"}}]})
        detail = mapping_detail(store, file_id="file:fixture.ods", source_hash="sha256-a", association=association, groups=groups)
        self.assertEqual(detail["unresolvedGroups"], 1)
        result = save_mapping(store, file_id="file:fixture.ods", source_hash="sha256-a", association=association, groups=groups,
            decisions={groups[0]["key"]: part["id"]}, expected_hash="sha256-a", expected_version=0,
            expected_association="association-1", expected_association_version=1, actor="owner", reason="Reviewed source design", request_key="mapping-retry")
        self.assertFalse(result["idempotent"])
        row = self.store.mapping_records()[0]["fields"]
        self.assertEqual(row["PartIdentity"], part["id"])
        self.assertIsNone(row["ProductPart"])

        restarted_store = PartRegistryMappingStore(MemoryPartStore(), PartIdentityStore(self.path))
        after_restart = mapping_detail(restarted_store, file_id="file:fixture.ods", source_hash="sha256-a", association=association, groups=groups)
        self.assertEqual(after_restart["unresolvedGroups"], 0)
        self.assertEqual(after_restart["groups"][0]["part"]["id"], part["id"])
        retry = save_mapping(restarted_store, file_id="file:fixture.ods", source_hash="sha256-a", association=association, groups=groups,
            decisions={groups[0]["key"]: part["id"]}, expected_hash="sha256-a", expected_version=0,
            expected_association="association-1", expected_association_version=1, actor="owner", reason="Reviewed source design", request_key="mapping-retry")
        self.assertTrue(retry["idempotent"])
        self.assertEqual(len(self.store.mapping_records()), 1)

    def test_metadata_scope_change_rejects_stale_affected_use_evidence(self):
        part = self.create(key="stale-usage-part")["part"]
        preview = self.store.preview(scope_type="product", target_id="product-1", description="Chassis", variant="Standard", exclude_part_id=part["id"])
        old_usage = self.store.usage_evidence(part["id"])
        mapping_store = PartRegistryMappingStore(MemoryPartStore(), self.store)
        association = SimpleNamespace(id="association-2", version=1)
        groups = source_groups({"5. Material Cut List Price": [{"status": "active", "source_row": 8,
            "fields": {"product_part_name": "Chassis"}}]})
        save_mapping(mapping_store, file_id="file:usage.ods", source_hash="hash-u", association=association, groups=groups,
            decisions={groups[0]["key"]: part["id"]}, expected_hash="hash-u", expected_version=0,
            expected_association="association-2", expected_association_version=1, actor="owner", reason="Assign source row", request_key="usage-map")
        with self.assertRaisesRegex(PartIdentityError, "source assignments changed"):
            self.store.update_metadata(part_id=part["id"], scope_type="product", target_id="product-1", target_label="Safari 1000",
                description="Chassis", variant="Standard", expected_version=1, expected_name=preview["name"],
                expected_usage_fingerprint=old_usage["fingerprint"], actor="owner", reason="Broaden reviewed scope", request_key="stale-scope")


if __name__ == "__main__":
    unittest.main()

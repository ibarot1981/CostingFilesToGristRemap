from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

from starlette.requests import Request
from fastapi import HTTPException

from app import web
from app.domain import Product, ProductModel, ProductModelCode
from app.part_identity import PartIdentityStore
from app.part_mapping import MemoryPartStore
from app.repository import InMemorySafariRepository


class PartIdentityWebTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.store = PartIdentityStore(Path(self.temp.name) / "parts.sqlite3")
        self.repository = InMemorySafariRepository()
        self.repository.add_product(Product(id="product-1", name="Safari 1000"))
        self.repository.add_model(ProductModel(id="model-1", product_id="product-1", model_number="S1KHF", name="Safari 1000 HF"))
        self.repository.add_code(ProductModelCode(id="code-1", model_id="model-1", code="S1KHFELP"))

    def tearDown(self):
        web._part_identity_registry = None
        self.temp.cleanup()

    def request(self, callback, *args):
        with mock.patch.object(web, "_repository", self.repository), mock.patch.object(web, "_part_identity_registry", self.store):
            try:
                return callback(*args)
            except HTTPException as exc:
                return exc

    @staticmethod
    def request_context():
        return Request({"type": "http", "headers": [(b"x-authentik-uid", b"owner")]})

    def set_shortcode(self, scope, target_id, code, key):
        return self.request(web.maintain_part_shortcode, self.request_context(), {"scope": scope, "targetId": target_id,
            "shortcode": code, "reason": "Reviewed master shortcode"}, key)

    def test_scope_relationships_live_preview_create_detail_and_metadata_change(self):
        self.set_shortcode("product", "product-1", "S1K", "short-product")
        self.set_shortcode("product_model", "model-1", "S1KHF", "short-model")
        self.set_shortcode("model_code", "code-1", "S1KHFELP", "short-code")
        bad_target = self.request(web.part_name_preview, "product_model", "code-1", "Chassis")
        self.assertEqual(bad_target.status_code, 422)
        targets = self.request(web.part_scope_targets)
        self.assertEqual(len(targets["scopes"]), 4)

        preview = self.request(web.part_name_preview, "product_model", "model-1", "Chassis", "Standard")
        self.assertEqual(preview["name"], "S1KHF — Chassis — Standard")
        created = self.request(web.create_canonical_part, self.request_context(), {"scope": "product_model", "targetId": "model-1", "description": "Chassis",
            "variant": "Standard", "expectedName": preview["name"], "reason": "Initial controlled design"}, "create-part")
        part = created["part"]
        self.assertEqual(part["partNumber"], "SM-P-000001")
        self.assertEqual(part["engineeringRevision"], "A")

        legacy_revision = self.request(web.create_canonical_part, self.request_context(), {"scope": "product", "targetId": "product-1", "description": "Wheel",
            "expectedName": "S1K — Wheel", "reason": "Try numeric revision", "revision": 2}, "blocked-revision")
        self.assertEqual(legacy_revision.status_code, 422)
        detail = self.request(web.part_details, part["id"])
        self.assertEqual(detail["part"]["metadataHistory"][0]["version"], 1)
        self.assertEqual(detail["processLines"]["status"], "unavailable")

        updated_preview = self.request(web.part_metadata_preview, part["id"], "product", "product-1", "Chassis", "Standard")
        self.assertTrue(updated_preview["available"])
        updated = self.request(web.update_part_metadata, part["id"], self.request_context(), {"scope": "product", "targetId": "product-1",
            "description": "Chassis", "variant": "Standard", "expectedName": updated_preview["after"]["name"], "expectedVersion": 1,
            "expectedUsageFingerprint": updated_preview["usageFingerprint"],
            "reason": "Reviewed broader sharing"}, "metadata-change")
        self.assertEqual(updated["part"]["partNumber"], part["partNumber"])
        self.assertEqual(updated["part"]["engineeringRevision"], "A")
        old_name = self.request(web.parts_register, "S1KHF — Chassis — Standard")
        self.assertEqual(old_name["items"][0]["id"], part["id"])

    def test_mapping_uses_stable_part_identity_and_saves_separately(self):
        from types import SimpleNamespace
        from app.part_mapping import source_groups

        self.repository.part_store = MemoryPartStore()
        self.repository.add_model(ProductModel(id="model-2", product_id="product-1", model_number="S1KHD", name="Safari 1000 HD"))
        self.repository.add_code(ProductModelCode(id="code-2", model_id="model-2", code="S1KHDSEP"))
        self.store.set_shortcode(scope_type="product_model", target_id="model-1", target_label="S1KHF Safari 1000 HF",
            shortcode="S1KHF", actor="owner", reason="Reviewed Model prefix", request_key="mapping-shortcode")
        preview = self.store.preview(scope_type="product_model", target_id="model-1", description="Shaft", variant="Standard")
        part = self.store.create_part(scope_type="product_model", target_id="model-1", target_label="S1KHF Safari 1000 HF",
            description="Shaft", variant="Standard", expected_name=preview["name"], actor="owner", reason="Create mapping fixture", request_key="mapping-part")["part"]
        groups = source_groups({"Tool Shop Items": [{"status": "active", "source_row": 10,
            "fields": {"product_part_name": "Source shaft"}}]})
        self.repository.code_associations["code-association-1"] = SimpleNamespace(code_id="code-2", association_id="association-1", active=True)
        association = SimpleNamespace(id="association-1", version=1, model_id="model-2")
        request = self.request_context()
        web._part_identity_registry = self.store
        with mock.patch.object(web, "_part_context", return_value=(self.repository, "file:pilot.ods", association, "hash-a", groups)), \
             mock.patch.object(web, "_processing_context", return_value=(self.repository, "file:pilot.ods", association, "hash-a")), \
             mock.patch("app.processing.processing_detail", return_value={"state": "associated"}):
            detail = web.part_mappings("pilot.ods")
            self.assertEqual(detail["parts"], [])
            self.assertTrue(detail["scopeAdvisoryOnly"])
            with mock.patch.object(self.repository, "current_association", return_value=association), \
                 mock.patch.object(web, "_part_scope_warnings", return_value=[{"id": "code-2", "code": "S1KHDSEP"}]):
                page = web.part_search(query="Shaft", offset=0, limit=30, path="pilot.ods")
            self.assertEqual(page["items"][0]["id"], part["id"])
            self.assertEqual(page["items"][0]["outOfScopeCodes"], [{"id": "code-2", "code": "S1KHDSEP"}])
            saved = web.save_part_mappings(request, {"path": "pilot.ods", "expectedHash": "hash-a", "expectedVersion": 0,
                "expectedAssociationKey": "association-1", "expectedAssociationVersion": 1,
                "decisions": {groups[0]["key"]: part["id"]}, "reason": "Assign reviewed source rows"}, "mapping-save")
        self.assertEqual(saved["savedRows"], 1)
        mapping = self.store.mapping_records()[0]["fields"]
        self.assertEqual(mapping["PartIdentity"], part["id"])
        self.assertIsNone(mapping["ProductPart"])


if __name__ == "__main__":
    unittest.main()

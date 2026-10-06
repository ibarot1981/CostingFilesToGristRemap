import unittest
from types import SimpleNamespace
from unittest import mock
from fastapi import HTTPException
from starlette.requests import Request
from app.part_mapping import MemoryPartStore, GristPartStore, PartConflict, source_groups, create_part, mapping_detail, save_mapping, attach_mapping_evidence
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

    def create(self, **overrides):
        args = dict(name="Drive Shaft", actor="Irshad", reason="Reviewed canonical name", request_key="new-part")
        args.update(overrides)
        return create_part(self.store, **args)

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

    def test_groups_preserve_individual_blank_rows(self):
        self.assertEqual(len(self.groups), 3)
        self.assertEqual(len(self.groups[0]["rows"]), 2)
        self.assertNotEqual(self.groups[1]["key"], self.groups[2]["key"])
        self.assertEqual(self.detail()["unresolvedGroups"], 3)
        self.assertEqual(self.store.tables["PartMappingReview"], [])

    def test_unique_names_include_unicode_case_whitespace_and_legacy(self):
        self.create()
        with self.assertRaises(PartConflict) as error:
            self.create(name="  ＤＲＩＶＥ   shaft  ", request_key="other")
        self.assertEqual(error.exception.code, "PART_NAME_EXISTS")
        self.store.tables["ProductPart"].append({"id": 2, "fields": {"DisplayName": "Legacy Part", "Status": "temporary"}})
        with self.assertRaises(PartConflict):
            self.create(name="legacy part", request_key="legacy")
        self.assertTrue(self.create()["idempotent"])
        with self.assertRaises(PartConflict):
            self.create(reason="Different reason")

    def test_typed_assignments_partial_review_and_stale_changes(self):
        self.create(); self.save()
        rows = self.store.tables["PartMappingReview"]
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["fields"]["ProductPart"], 1)
        self.assertEqual(rows[0]["fields"]["Actor"], "Irshad")
        self.assertEqual(self.detail()["unresolvedGroups"], 2)
        self.assertEqual(self.detail(source_hash="hash2")["unresolvedGroups"], 3)
        self.assertTrue(self.save()["idempotent"])
        for overrides in ({"expected_hash": "old"}, {"expected_version": 0}, {"expected_version": 1, "expected_association_version": 2}):
            with self.assertRaises(PartConflict):
                self.save(request_key="next", **overrides)
        self.assertEqual(len(rows), 2)

    def test_durable_creation_and_mapping_recover_after_lost_responses(self):
        client = FakeGristClient(); self.store = GristPartStore(client)
        client.fail_after_create_once = "ProductPart"
        with self.assertRaises(RuntimeError): self.create()
        self.store = GristPartStore(client)
        part = self.create()["part"]
        client.fail_after_create_once = "PartMappingReview"
        decisions = {self.groups[0]["key"]: part["id"]}
        with self.assertRaises(RuntimeError): self.save(decisions=decisions)
        self.store = GristPartStore(client)
        self.assertTrue(self.save(decisions=decisions)["idempotent"])
        self.assertEqual(len(client.tables["ProductPart"]), 1)
        self.assertEqual(len(client.tables["PartMappingReview"]), 2)
        self.assertGreater(client.target_checks, 0)

    def test_wrong_target_duplicate_names_and_prewrite_changes_block(self):
        client = FakeGristClient(); self.store = GristPartStore(client); client.target_valid = False
        with self.assertRaises(Exception): self.create()
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

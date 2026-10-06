import unittest
from types import SimpleNamespace
from unittest import mock
from starlette.requests import Request

from app.processing import MemoryProcessingStore, GristProcessingStore, ProcessingConflict, processing_detail, transition
from test_grist_repository import FakeGristClient


class ProcessingTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryProcessingStore()
        self.association = SimpleNamespace(id="association-1", version=1)

    def change(self, **overrides):
        values = dict(file_id="file:pilot.ods", association=self.association, source_hash="source-1",
            expected_hash="source-1", expected_version=0, state="associated", actor="Irshad",
            reason="Reviewed association", request_key="review-1")
        values.update(expected_association_key="association-1",expected_association_version=1)
        values.update(overrides)
        return transition(self.store, **values)

    def test_immutable_history_and_idempotency(self):
        first = self.change()
        retry = self.change()
        self.assertEqual(first["event"], retry["event"])
        self.assertTrue(retry["idempotent"])
        self.assertEqual(len(self.store.read("file:pilot.ods")), 1)
        with self.assertRaises(ProcessingConflict) as error:
            self.change(reason="Changed payload")
        self.assertEqual(error.exception.code, "IDEMPOTENCY_KEY_REUSED")

    def test_stale_source_and_version_do_not_write(self):
        self.change()
        for overrides in ({"request_key":"next"}, {"request_key":"next", "expected_version":1, "expected_hash":"old"}):
            with self.assertRaises(ProcessingConflict) as error:
                self.change(**overrides)
            self.assertEqual(error.exception.code, "STALE_PROCESSING_REVIEW")
        self.assertEqual(len(self.store.events), 1)

    def test_reassociation_requires_a_new_review(self):
        self.association.version=2
        with self.assertRaises(ProcessingConflict) as error:
            self.change()
        self.assertEqual(error.exception.code,"STALE_PROCESSING_ASSOCIATION")
        self.assertEqual(len(self.store.events),0)

    def test_change_is_projected_without_saving_and_can_be_recorded(self):
        self.change()
        detail = processing_detail(self.store,"file:pilot.ods",self.association,"source-2")
        self.assertEqual(detail["state"], "associated")
        self.assertEqual(detail["effectiveState"], "changes_pending")
        self.assertEqual(len(self.store.events), 1)
        result = self.change(source_hash="source-2",expected_hash="source-2",expected_version=1,
            state="changes_pending",request_key="source-changed")
        self.assertEqual(result["event"]["source_hash"], "source-2")
        self.assertFalse(processing_detail(self.store,"file:pilot.ods",self.association,"source-2")["authorityChanged"])

    def test_extraction_requires_evidence_and_completion_stays_server_gated(self):
        with self.assertRaises(ProcessingConflict) as error:
            self.change(state="extracted")
        self.assertEqual(error.exception.code, "CURRENT_EXTRACTION_REQUIRED")
        self.change(state="extracted",extracted_hash="source-1")
        self.change(state="mapping_review",expected_version=1,request_key="map")
        self.change(state="reconciliation_review",expected_version=2,request_key="reconcile")
        with self.assertRaises(ProcessingConflict) as error:
            self.change(state="ready_to_store",expected_version=3,request_key="ready")
        self.assertEqual(error.exception.code,"PROCESSING_GATES_UNAVAILABLE")
        self.assertEqual(len(self.store.events),3)

    def test_durable_restart_and_response_loss_retry(self):
        client=FakeGristClient()
        self.store=GristProcessingStore(client)
        client.fail_after_create_once="FileProcessingEvent"
        with self.assertRaises(RuntimeError):
            self.change()
        self.store=GristProcessingStore(client)
        self.assertTrue(self.change()["idempotent"])
        history=self.store.read("file:pilot.ods")
        self.assertEqual(len(history),1)
        self.assertEqual(history[0].actor,"Irshad")
        self.assertEqual(history[0].source_hash,"source-1")

    def test_wrong_target_and_duplicate_versions_fail(self):
        client=FakeGristClient()
        self.store=GristProcessingStore(client)
        client.target_valid=False
        with self.assertRaises(Exception):
            self.change()
        self.assertEqual(client.created,[])
        client.target_valid=True
        self.change()
        row=client.tables["FileProcessingEvent"][0]
        client.tables["FileProcessingEvent"].append({"id":999,"fields":dict(row["fields"])})
        with self.assertRaises(ProcessingConflict) as error:
            self.store.read("file:pilot.ods")
        self.assertEqual(error.exception.code,"PROCESSING_VERSION_CONFLICT")

    def test_http_actor_is_attributed_from_headers(self):
        from app import web
        from app.repository import InMemorySafariRepository
        repository=InMemorySafariRepository()
        request=Request({"type":"http","headers":[(b"x-authentik-username",b"Irshad")]})
        payload={"path":"pilot.ods","state":"associated","reason":"Reviewed association",
            "expectedVersion":0,"expectedHash":"source-1","expectedAssociationKey":"association-1",
            "expectedAssociationVersion":1,"actor":"Spoofed actor"}
        with mock.patch.object(web,"_processing_context",return_value=(repository,"file:pilot.ods",self.association,"source-1")):
            result=web.change_processing_state(request,payload,"http-review")
        self.assertEqual(result["event"]["actor"],"Irshad")

"""Association hydration and safe preview entry-point regression coverage."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock
from contextlib import contextmanager

from fastapi import HTTPException
from pyexcel_ods3 import save_data

from app import web
from app.domain import CostingFile
from app.grist_repository import GristSafariRepository
from app.libreoffice_refresh import RefreshEvidence, LibreOfficeRefreshError
from app.repository import AssociationProposal, InMemorySafariRepository, sha256_file
from test_grist_repository import FakeGristClient


class WorkflowTests(unittest.TestCase):
    def test_saved_association_reloads_codes_history_and_queue_without_writes(self):
        client = FakeGristClient()
        repository = GristSafariRepository(client=client)
        repository.add_file(CostingFile("file:nested/pilot.ods", "nested/pilot.ods", "nested/pilot.ods", "pilot.ods", ".ods", 10, "2026-10-03T00:00:00+00:00", "hash", readable=True))
        proposal = AssociationProposal("file:nested/pilot.ods", "1", "2", ("3",), "Irshad", idempotency_key="first")
        self.assertTrue(repository.validate_association(proposal, "hash").valid)
        self.assertEqual(client.created, [])
        self.assertEqual(client.updated, [])
        repository.save_association(proposal, "hash")
        created, updated = len(client.created), len(client.updated)
        # A fresh process must recover durable state, including the audit.
        reloaded = GristSafariRepository(client=client)
        detail = reloaded.association_detail(proposal.file_id)
        self.assertEqual(detail["product"]["id"], "1")
        self.assertEqual(detail["model"]["id"], "2")
        self.assertEqual([c["id"] for c in detail["codes"]], ["3"])
        self.assertEqual(detail["history"][0]["auditEvents"][0]["actor"], "Irshad")
        self.assertEqual(detail["processingBatch"]["status"], "queued")
        self.assertEqual((len(client.created), len(client.updated)), (created, updated))
        # Reads refresh externally updated state instead of serving startup data.
        client.tables["FileModelAssociation"][0]["fields"]["Reason"] = "reviewed outside this process"
        self.assertEqual(reloaded.association_detail(proposal.file_id)["current"]["reason"], "reviewed outside this process")

    def test_path_lookup_is_read_only_and_root_confined(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "nested").mkdir()
            save_data(str(root / "nested" / "pilot.ods"), {"Summary": [["Cost"]]})
            repository = InMemorySafariRepository()
            with mock.patch.object(web, "_costing_root", return_value=root), mock.patch.object(web, "_repository", repository):
                detail = web.file_association("nested/pilot.ods")
                self.assertIsNone(detail["current"])
                self.assertEqual(detail["fileId"], "file:nested/pilot.ods")
                self.assertEqual(repository.files, {})
                with self.assertRaises(HTTPException):
                    web.file_association("../outside.ods")

    def test_unlinked_preview_reports_no_refresh_and_preserves_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "pilot.ods"
            save_data(str(source), {"Summary": [["Cost", 42]]})
            before = sha256_file(source)
            with mock.patch.object(web, "_costing_root", return_value=root), mock.patch.object(web, "refreshed_ods_copy") as refresh:
                result = web.refresh_preview("pilot.ods")
            self.assertEqual(result["refreshMetadata"]["status"], "no_external_links")
            self.assertEqual(result["refreshMetadata"]["originalSha256"], before)
            self.assertEqual(sha256_file(source), before)
            refresh.assert_not_called()

    def test_linked_preview_uses_copy_and_surfaces_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, copy = root / "pilot.ods", root / "copy.ods"
            save_data(str(source), {"Summary": [["Cost", 1]]})
            save_data(str(copy), {"Summary": [["Cost", 2]]})
            before = sha256_file(source)

            @contextmanager
            def refresh(*args):
                yield RefreshEvidence(copy, before, sha256_file(copy), [], "2026-10-03T00:00:00Z")

            with mock.patch.object(web, "_costing_root", return_value=root), mock.patch.object(web, "linked_ods_sources", return_value=[source]):
                with mock.patch.object(web, "refreshed_ods_copy", refresh):
                    result = web.refresh_preview("pilot.ods")
                self.assertEqual(result["rows"][0][1], 2)
                self.assertEqual(result["refreshMetadata"]["originalSha256"], before)
                self.assertEqual(sha256_file(source), before)
                with mock.patch.object(web, "refreshed_ods_copy", side_effect=LibreOfficeRefreshError("missing dependency")):
                    with self.assertRaises(HTTPException) as error:
                        web.refresh_preview("pilot.ods")
                self.assertEqual(error.exception.detail["code"], "EXTERNAL_REFRESH_FAILED")

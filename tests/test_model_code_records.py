"""Stored Model Code inspection must work with unavailable source workbooks."""
import unittest
from dataclasses import replace
from unittest import mock

from fastapi import HTTPException
from app import web
from app.domain import CostingFile, CostingSnapshot, FileCodeAssociation, FileModelAssociation, ProductModelCode
from app.repository import InMemorySafariRepository


class ModelCodeRecordsTests(unittest.TestCase):
    def setUp(self):
        self.repo = InMemorySafariRepository()
        self.repo.codes['c'] = ProductModelCode('c', 'm', 'CODE')
        self.repo.files['f'] = CostingFile('f', 'missing.ods', 'missing.ods', 'missing.ods', '.ods', 1, '2026-10-05', 'hash')
        self.repo.associations['a'] = FileModelAssociation('a', 'f', 'p', 'm', 'Irshad', 'reviewed', '2026-10-05')
        self.repo.code_associations['o'] = FileCodeAssociation('o', 'a', 'f', 'c', 'Irshad', '2026-10-05')

    def test_stored_read_never_opens_source_and_pins_baseline(self):
        self.repo.adapter_name = 'grist-safari'
        self.repo.client = mock.Mock()
        snapshot = CostingSnapshot('s', 'snapshot', 'f', 'observed', 'semantic', {}, {'selected_workbook_saved': 'hash'})
        with mock.patch.object(web, '_get_repository', return_value=self.repo), \
             mock.patch.object(self.repo, 'latest_accepted_costing_snapshot', return_value=snapshot), \
             mock.patch.object(web, '_resolve_preview_workbook', side_effect=AssertionError('source read')), \
             mock.patch.object(web, 'sha256_file', side_effect=AssertionError('source hash')), \
             mock.patch('app.normalized_store.GristNormalizedStore') as store:
            store.return_value.query.return_value = {'total': 1, 'items': [{'stored': True}]}
            result = web.model_code_records('c', offset=0, limit=100)
            self.assertEqual(result['status'], 'stored')
            self.assertEqual(result['baseline']['sourceHash'], 'hash')
            self.assertFalse(result['sourceRead'])
            self.assertFalse(result['authorityChanged'])
            self.assertEqual(result['configurationStatus'], 'review_required')
            self.assertEqual(store.return_value.query.call_args.kwargs['snapshot_key'], 'snapshot')

    def test_no_baseline_is_explicit_and_duplicate_or_stale_owner_fails(self):
        with mock.patch.object(web, '_get_repository', return_value=self.repo):
            self.assertEqual(web.model_code_records('c', offset=0, limit=100)['status'], 'no_accepted_snapshot')
            self.repo.code_associations['o2'] = replace(self.repo.code_associations['o'], id='o2')
            with self.assertRaises(HTTPException) as exc:
                web.model_code_records('c', offset=0, limit=100)
            self.assertEqual(exc.exception.status_code, 409)
            del self.repo.code_associations['o2']
            self.repo.associations['a'] = replace(self.repo.associations['a'], active=False)
            with self.assertRaises(HTTPException):
                web.model_code_records('c', offset=0, limit=100)

    def test_unassociated_and_unknown_codes(self):
        self.repo.code_associations.clear()
        with mock.patch.object(web, '_get_repository', return_value=self.repo):
            self.assertEqual(web.model_code_records('c', offset=0, limit=100)['status'], 'unassociated')
            with self.assertRaises(HTTPException) as exc:
                web.model_code_records('unknown', offset=0, limit=100)
            self.assertEqual(exc.exception.status_code, 404)

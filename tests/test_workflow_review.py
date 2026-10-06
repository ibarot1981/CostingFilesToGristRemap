import unittest
from decimal import Decimal
from app.workflow_review import numeric_match, processing_review_evidence, REQUIRED_SHEETS


class ProcessingReviewTests(unittest.TestCase):
    def test_weight_evidence_is_compared_even_when_legacy_semantic_change_list_is_empty(self):
        def snapshot(grams):
            row = {'process_list': 'CNC Cut List', 'source_row': 4, 'status': 'active',
                   'identity_values': {'product_part_name': 'Plate'},
                   'fields': {'product_part_name': 'Plate', 'qty': '2', 'total_grams': grams}}
            return {'content': {'process_lists': {'CNC Cut List': [row]}}}
        result = processing_review_evidence(snapshot('1005'), {'changes': []}, set(REQUIRED_SHEETS), snapshot('1004'))
        checks = {row['field']: row for row in result['physicalComparisons']}
        self.assertTrue(checks['qty']['matches'])
        self.assertFalse(checks['total_grams']['matches'])
        self.assertEqual(result['missingRequiredSheets'], [])

    def test_quantity_exact_weight_kg_rounding_and_missing_never_pass(self):
        self.assertTrue(numeric_match('2.0', 2))
        self.assertFalse(numeric_match('2.0001', 2))
        self.assertTrue(numeric_match('1001', '1004', kg_factor=Decimal('.001')))
        self.assertFalse(numeric_match('1004', '1005', kg_factor=Decimal('.001')))
        for value in (None, '', 'NaN', 'Infinity', 'invalid'):
            self.assertFalse(numeric_match(value, value))

    def test_all_required_sheets_blank_parts_and_rates_separate_from_completion(self):
        row = {'source_row': 4, 'status': 'active', 'fields': {'product_part_name': ''}}
        snapshot = {'source_hashes': {'selected_workbook_saved': 'hash'}, 'content': {
            'process_lists': {'CNC Cut List': [row], 'Stores and Consumables List': [row]},
            'rate_state': {'Steel': {'effective_rate_per_kg': None}}}}
        result = processing_review_evidence(snapshot, {'changes': [
            {'classification': 'rate_source_change', 'field_changes': {'rate': {}}},
            {'classification': 'rate_source_change', 'field_changes': {'total_grams': {}}},
        ]}, {'CNC Cut List'})
        self.assertEqual(result['requiredSheets'], list(REQUIRED_SHEETS))
        self.assertIn('Spares Summary', result['missingRequiredSheets'])
        self.assertEqual(len(result['partRowsRequiringReview']), 1)
        self.assertTrue(result['partRowsRequiringReview'][0]['blankDescription'])
        self.assertEqual(len(result['structuralDifferences']), 1)
        self.assertEqual(len(result['pricingDifferences']), 1)
        self.assertFalse(result['pricingBlocksProcessing'])
        self.assertFalse(result['rules']['finalCostTotalBlocksProcessing'])
        self.assertFalse(result['completionAvailable'])

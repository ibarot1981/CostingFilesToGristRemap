import unittest
from app.interim_rates import compare_interim_rates


class InterimRatesTests(unittest.TestCase):
    def test_latest_display_default_fallback_and_zero_are_preserved(self):
        masters = [{'id': 1, 'fields': {'MasterMaterial': 'Steel', 'MaterialLatestRate': 55, 'Default_MaterialRate': 40}},
                   {'id': 2, 'fields': {'MasterMaterial': 'Zero', 'MaterialLatestRate': 90, 'Default_MaterialRate': 0}}]
        rows = compare_interim_rates({'Plate': '50', 'Zero': '1', 'Unknown': None}, {'Plate': 'Steel', 'Zero': 'Zero'}, masters,
                                     [{'id': 7, 'fields': {'MasterMaterial': 1, 'RatePerKG': 52}}])
        by_name = {row['material']: row for row in rows}
        self.assertEqual(by_name['Plate']['costingNewRate'], '55')
        self.assertEqual(by_name['Plate']['difference'], '5')
        self.assertEqual(by_name['Zero']['costingNewRate'], '0')
        self.assertEqual(by_name['Zero']['basis'], 'Default_MaterialRate')
        self.assertEqual(by_name['Unknown']['status'], 'unmapped')
        self.assertTrue(all(not row['processingBlocker'] for row in rows))

    def test_duplicate_master_or_error_latest_does_not_silently_fallback(self):
        master = {'id': 1, 'fields': {'MasterMaterial': 'Steel', 'MaterialLatestRate': ['E', 'ValueError'], 'Default_MaterialRate': 40}}
        rates = {'Plate': 50}; mappings = {'Plate': 'Steel'}
        result = compare_interim_rates(rates, mappings, [master], [{'id': 1, 'fields': {'MasterMaterial': 1}}])[0]
        self.assertEqual(result['status'], 'rate_unavailable')
        self.assertIsNone(result['costingNewRate'])
        self.assertEqual(compare_interim_rates(rates, mappings, [master, master], [])[0]['status'], 'ambiguous_master')

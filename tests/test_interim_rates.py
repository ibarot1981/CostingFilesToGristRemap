import unittest
from unittest.mock import patch
from app.interim_rates import compare_interim_rates, load_material_weight_catalog


class InterimRatesTests(unittest.TestCase):
    def test_material_weight_catalog_uses_explicit_costing_new_document_and_governed_factor(self):
        records = [{"id": 55, "fields": {"MasterMaterial": "MS-Round Bar 16 mm", "InUse": True,
            "KGsPerMeter": 1.58, "LatestKGsPerMeterDate": "2026-09-24"}}]
        with patch.dict("os.environ", {"GRIST_API_KEY": "qa-key", "COSTING_NEW_GRIST_DOC_ID": "costing-new-doc",
                "GRIST_BASE_URL": "https://grist.invalid", "GRIST_DOC_ID": ""}, clear=False), \
             patch("app.grist.GristClient.fetch_table_records_with_ids", return_value=records) as fetch, \
             patch("app.grist_admin.GristAdminClient.get_document", return_value=type("Document", (), {"name": "Costing-New"})()):
            catalog = load_material_weight_catalog()

        fetch.assert_called_once_with("MasterMaterial")
        self.assertEqual(catalog["sourceDocumentId"], "costing-new-doc")
        self.assertEqual(catalog["records"][0], {
            "canonicalName": "MS-Round Bar 16 mm", "masterMaterialId": 55, "code": "", "inUse": True,
            "kgPerMeter": "1.58", "factorStatus": "available", "sourceDate": "2026-09-24",
            "source": "Costing-New", "sourceDocumentId": "costing-new-doc", "sourceTable": "MasterMaterial",
            "sourceField": "KGsPerMeter", "factorUnit": "kg/m"})

    def test_material_weight_catalog_rejects_non_costing_new_document(self):
        with patch.dict("os.environ", {"GRIST_API_KEY": "qa-key", "COSTING_NEW_GRIST_DOC_ID": "wrong-doc",
                "GRIST_BASE_URL": "https://grist.invalid", "GRIST_DOC_ID": ""}, clear=False), \
             patch("app.grist_admin.GristAdminClient.get_document", return_value=type("Document", (), {"name": "Safari Manufacturing"})()):
            with self.assertRaisesRegex(ValueError, "exactly Costing-New"):
                load_material_weight_catalog()

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

    def test_selected_rate_log_ages_from_update_and_exposes_distinct_dates(self):
        masters = [{"id": 1, "fields": {"MasterMaterial": "Steel", "MaterialLatestRate": 55, "Default_MaterialRate": 40}}]
        logs = [{"id": 7, "fields": {"MasterMaterial": 1, "RatePerKG": 52, "EffectiveAt": "2026-11-10T00:00:00Z",
            "RecordedAt": "2026-11-09T12:00:00Z", "UpdatedAt": "2026-11-11T09:00:00Z"}}]

        row = compare_interim_rates({"Plate": "50"}, {"Plate": "Steel"}, masters, logs)[0]

        self.assertEqual((row["selectedRateLogId"], row["effectiveAt"], row["rateSourceDate"], row["rateDateBasis"]),
            (7, "2026-11-10T00:00:00+00:00", "2026-11-11T09:00:00+00:00", "update"))
        self.assertEqual((row["sourceRecordedAt"], row["sourceUpdatedAt"]), ("2026-11-09T12:00:00+00:00", "2026-11-11T09:00:00+00:00"))

    def test_master_metadata_update_does_not_become_a_rate_age_event(self):
        masters = [{"id": 1, "fields": {"MasterMaterial": "Steel", "MaterialLatestRate": 55,
            "EffectiveAt": "2026-11-10T00:00:00Z", "UpdatedAt": "2026-11-18T00:00:00Z"}}]

        row = compare_interim_rates({"Plate": "50"}, {"Plate": "Steel"}, masters, [])[0]

        self.assertEqual(row["effectiveAt"], "2026-11-10T00:00:00+00:00")
        self.assertEqual(row["rateSourceDate"], "2026-11-10T00:00:00+00:00")
        self.assertEqual(row["rateDateBasis"], "effective")
        self.assertIsNone(row["sourceUpdatedAt"])

    def test_legacy_date_text_is_retained_as_explicit_entry_and_update_basis(self):
        masters = [{"id": 1, "fields": {"MasterMaterial": "Steel", "MaterialLatestRate": 55}}]
        logs = [{"id": 7, "fields": {"MasterMaterial": 1, "RatePerKG": 52, "EffectiveAt": "2026-10-01T00:00:00Z",
            "RecordedAt": "17/11/2026", "UpdatedAt": "18/11/2026"}}]

        row = compare_interim_rates({"Plate": "50"}, {"Plate": "Steel"}, masters, logs)[0]

        self.assertEqual(row["rateDateBasis"], "update")
        self.assertEqual(row["rateSourceDate"], "2026-11-18")
        self.assertEqual(row["sourceRecordedAt"], "2026-11-17")
        self.assertEqual(row["effectiveAt"], "2026-10-01T00:00:00+00:00")

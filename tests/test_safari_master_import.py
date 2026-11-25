from __future__ import annotations

import unittest

from app.safari_master_import import _source_fingerprint, _source_rows, active_local_import, imported_purchase_rate, load_local_interim_rates, load_local_material_weight_catalog


class FakeClient:
    doc_id = "qa-safari"

    def __init__(self, tables):
        self.tables = tables
        self.reads = []

    def fetch_table_records_with_ids(self, table):
        self.reads.append(table)
        return self.tables.get(table, [])


class SafariMasterImportTests(unittest.TestCase):
    def test_capture_preserves_formula_values_ids_dates_and_exact_matching_logs(self):
        tables = {
            "MasterMaterial": [{"id": 8, "fields": {"MasterMaterial": "MS-Angles 65 x 65 x 6", "Code": "ANG-65",
                "InUse": True, "KGsPerMeter": 5.8, "MaterialLatestRate": 50.68, "Default_MaterialRate": 52.36,
                "TransportLatestRate": 3, "Total_Rate": 53.68, "LatestRateDate": 1794009600,
                "LatestKGsPerMeterDate": 1794009600}}],
            "MaterialRateLog": [{"id": 41, "fields": {"MasterMaterial": ["R", "MasterMaterial", 8],
                "DateOfEntry": 1794009600, "RatePerKG": 50.68, "Supplier": "Vendor A"}}],
            "PurchasePartsMaster": [{"id": 21, "fields": {"SparesPartName": "012 Thrust", "SparesPartCode": "JKMX001",
                "SparesMasterPartName": "012 Thrust", "Unit": "Per Nos", "Nos_Per_KG": 10, "PerNos_FromKg": 0.1,
                "DefaultRate2": 88, "SparesPartCostRs": 90, "SparePartsFreight": 2.25,
                "SparesPartHandling": 0.45, "TotalSparesPartCost": 92.7,
                "SparesPartLastPurchaseDate": 1788998400, "Currency": "INR"}}],
            "PurchasePartsRateLog": [{"id": 91, "fields": {"SparesPartMasterName2": ["R", "PurchasePartsMaster", 21],
                "DateOfEntry": 1788998400, "SparesPurchaseNegoRate": 90, "Supplier": "Vendor B"}}],
        }

        materials, purchases, exceptions = _source_rows(tables, "costing-new")

        self.assertEqual(exceptions, [])
        self.assertEqual((materials[0]["sourceRecordId"], materials[0]["canonicalName"], materials[0]["kgPerMeter"]),
            ("8", "MS-Angles 65 x 65 x 6", 5.8))
        self.assertEqual((materials[0]["materialLatestRate"], materials[0]["totalRate"], materials[0]["latestRateDate"]),
            (50.68, 53.68, 1794009600))
        self.assertEqual((materials[0]["selectedRateLogId"], materials[0]["rateLogEvidence"][0]["id"]), ("41", 41))
        self.assertEqual((purchases[0]["sourceRecordId"], purchases[0]["partCode"], purchases[0]["unit"]), ("21", "JKMX001", "Per Nos"))
        self.assertEqual((purchases[0]["currentUnitRate"], purchases[0]["freight"], purchases[0]["handling"], purchases[0]["totalCurrentRate"]),
            (90, 2.25, 0.45, 92.7))
        self.assertTrue(purchases[0]["freightIncluded"])
        self.assertEqual((purchases[0]["currency"], purchases[0]["currencyEvidence"]),
            ("INR", "PurchasePartsMaster.Currency"))
        self.assertEqual(purchases[0]["selectedRateLogId"], "91")
        self.assertEqual(purchases[0]["vendor"], "Vendor B")
        self.assertEqual(_source_fingerprint(tables), _source_fingerprint(tables))

    def test_rs_named_authoritative_purchase_amount_records_explicit_inr_basis_when_source_has_no_currency_column(self):
        tables = {"MasterMaterial": [], "MaterialRateLog": [], "PurchasePartsRateLog": [],
            "PurchasePartsMaster": [{"id": 8, "fields": {"SparesPartName": "Thrust", "SparesPartCode": "JKMX001",
                "Unit": "Per Nos", "SparesPartCostRs": 90, "SparePartsFreight": 2.25,
                "SparesPartHandling": 0.45, "TotalSparesPartCost": 92.7}}]}
        _, purchases, _ = _source_rows(tables, "costing-new")
        self.assertEqual((purchases[0]["currency"], purchases[0]["currencyEvidence"]),
            ("INR", "PurchasePartsMaster.SparesPartCostRs (source field is denominated in Rs)"))

    def test_rate_log_evidence_preserves_existing_related_row_order_selector(self):
        tables = {"MasterMaterial": [{"id": 8, "fields": {"MasterMaterial": "Steel", "InUse": True,
            "KGsPerMeter": 2, "MaterialLatestRate": 12, "LatestRateDate": "2026-11-20"}}],
            "MaterialRateLog": [
                {"id": 41, "fields": {"MasterMaterial": 8, "DateOfEntry": "2026-11-18", "RatePerKG": 10}},
                {"id": 42, "fields": {"MasterMaterial": 8, "DateOfEntry": "2026-11-20", "RatePerKG": 11}},
            ], "PurchasePartsMaster": [], "PurchasePartsRateLog": []}
        materials, _, _ = _source_rows(tables, "costing-new")
        self.assertEqual(materials[0]["selectedRateLogId"], "42")
        self.assertEqual([item["id"] for item in materials[0]["rateLogEvidence"]], [41, 42])

    def test_purchase_price_normalizes_only_the_captured_nos_per_kg_conversion(self):
        row = {"TotalCurrentRate": 92.7, "Unit": "Per Nos", "Currency": "INR", "FreightIncluded": True,
            "NosPerKG": 10, "PerNosFromKg": 0.1, "SourceRecordId": "21"}
        same_unit = imported_purchase_rate(row, "Nos", "INR")
        self.assertEqual((same_unit["status"], same_unit["rate"], same_unit["conversionFactor"]), ("available", 92.7, 1.0))
        per_kg = imported_purchase_rate(row, "kg", "INR")
        self.assertEqual((per_kg["status"], per_kg["rate"], per_kg["conversionFactor"]), ("available", 927.0, 10.0))
        row["PerNosFromKg"] = 0.2
        self.assertEqual(imported_purchase_rate(row, "kg", "INR")["status"], "review_required")
        row["PerNosFromKg"] = 0.1
        row["FreightIncluded"] = False
        self.assertEqual(imported_purchase_rate(row, "kg", "INR")["status"], "review_required")
        self.assertEqual(imported_purchase_rate(row, "Nos", "USD")["status"], "review_required")

    def test_ordinary_local_catalog_and_rate_loader_use_only_published_safari_rows(self):
        client = FakeClient({
            "SafariMasterImportControl": [{"id": 1, "fields": {"ControlKey": "active", "ActiveImport": ["R", "SafariMasterImport", 5],
                "Version": 3, "Fingerprint": "manifest-3"}}],
            "SafariMasterImport": [{"id": 5, "fields": {"Status": "published", "MasterCount": 1, "PurchaseRateCount": 1,
                "SourceDocumentId": "costing-new", "Fingerprint": "source-sha"}}],
            "SafariMasterImportMaterial": [{"id": 8, "fields": {"Import": ["R", "SafariMasterImport", 5], "Material": ["R", "Material", 77],
                "SourceRecordId": "8", "CanonicalName": "Steel", "InUse": True, "KGsPerMeter": 5.8,
                "MaterialLatestRate": 50.68, "DefaultMaterialRate": 52.36, "RateBasis": "MaterialLatestRate", "LatestRateDate": "2026-11-07"}}],
            "SafariMasterImportPurchase": [{"id": 9, "fields": {"Import": ["R", "SafariMasterImport", 5], "PurchaseItem": ["R", "PurchaseItem", 11],
                "TotalCurrentRate": 92.7}}],
            "Material": [{"id": 77, "fields": {"CanonicalName": "Steel"}}],
        })

        manifest, materials, purchases = active_local_import(client)
        catalog = load_local_material_weight_catalog(client)
        rates = load_local_interim_rates(["Steel"], client)

        self.assertEqual(manifest["version"], 3)
        self.assertEqual(len(materials), 1)
        self.assertEqual(len(purchases), 1)
        self.assertEqual(catalog["records"][0]["kgPerMeter"], "5.8")
        self.assertEqual(catalog["records"][0]["localMaterialId"], 77)
        self.assertEqual(rates["rows"][0]["costingNewRate"], "50.68")
        self.assertEqual(rates["rows"][0]["localImportFingerprint"], "manifest-3")
        self.assertNotIn("MasterMaterial", client.reads)
        self.assertNotIn("MaterialRateLog", client.reads)

    def test_no_import_or_incomplete_manifest_is_never_reported_as_available(self):
        empty = FakeClient({})
        self.assertEqual(load_local_material_weight_catalog(empty)["status"], "unavailable")
        broken = FakeClient({
            "SafariMasterImportControl": [{"id": 1, "fields": {"ControlKey": "active", "ActiveImport": 5}}],
            "SafariMasterImport": [{"id": 5, "fields": {"Status": "staging", "MasterCount": 1, "PurchaseRateCount": 1}}],
            "SafariMasterImportMaterial": [], "SafariMasterImportPurchase": [],
        })
        with self.assertRaises(Exception):
            active_local_import(broken)


if __name__ == "__main__":
    unittest.main()

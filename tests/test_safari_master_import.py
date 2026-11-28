from __future__ import annotations

import unittest
from copy import deepcopy
import threading
from concurrent.futures import ThreadPoolExecutor

from app.safari_master_import import (_capture_fingerprint, SafariMasterImportService, _source_fingerprint,
    _source_rows, active_local_import, imported_purchase_rate, load_local_interim_rates, load_local_material_weight_catalog)


class FakeClient:
    doc_id = "qa-safari"

    def __init__(self, tables):
        self.tables = tables
        self.reads = []

    def fetch_table_records_with_ids(self, table):
        self.reads.append(table)
        return self.tables.get(table, [])


class ImportMemoryClient(FakeClient):
    def __init__(self):
        super().__init__({"Material": [{"id": 7, "fields": {"CanonicalName": "Steel"}}]})
        self.next_id = 1
        self.lose_update_table = ""
        self.lose_create_table = ""
        self.lock = threading.RLock()

    def validate_safari_write_target(self):
        return None

    def create_table_records(self, table, records):
        with self.lock:
            result = []
            rows = self.tables.setdefault(table, [])
            for record in records:
                row = {"id": self.next_id, "fields": deepcopy(record["fields"])}
                self.next_id += 1
                rows.append(row)
                result.append(deepcopy(row))
            fail = self.lose_create_table == table
            if fail:
                self.lose_create_table = ""
        if fail:
            raise TimeoutError(f"simulated lost response after committed {table}")
        return {"records": result}

    def update_table_records(self, table, updates):
        with self.lock:
            rows = self.tables.setdefault(table, [])
            for update in updates:
                target = next(row for row in rows if row["id"] == update["id"])
                target["fields"].update(deepcopy(update["fields"]))
            fail = self.lose_update_table == table
            if fail:
                self.lose_update_table = ""
        if fail:
            raise TimeoutError(f"simulated lost response after committed {table}")


class ImportMemoryRegistry:
    def __init__(self):
        self.client = ImportMemoryClient()
        self.lock = threading.RLock()

    def _rows(self, table):
        return self.client.fetch_table_records_with_ids(table)

    def _verify_writer(self):
        return None

    def _ensure_schema(self):
        return None


def _memory_capture(kgm: float) -> dict:
    capture = {"schema": "safari-master-import-capture-v1", "sourceDocumentId": "cPCjjr9oCzhWC7z8vJbcky",
        "sourceWorkspaceId": "source-workspace", "sourceCounts": {"MasterMaterial": 1, "MaterialRateLog": 0,
            "PurchasePartsMaster": 0, "PurchasePartsRateLog": 0}, "sourceFingerprint": f"source-{kgm}",
        "capturedAt": "2026-11-28T00:00:00+00:00", "materials": [{"sourceRecordId": "8",
            "sourceDocumentId": "cPCjjr9oCzhWC7z8vJbcky", "sourceTable": "MasterMaterial", "canonicalName": "Steel",
            "code": "ST", "inUse": True, "kgPerMeter": kgm, "latestKgPerMeterDate": "2026-11-20",
            "materialLatestRate": 50, "defaultMaterialRate": 52, "transportLatestRate": 3, "totalRate": 53,
            "latestRateDate": "2026-11-20", "rateBasis": "MaterialLatestRate", "selectedRateLogId": None,
            "rateLogEvidence": [], "masterFingerprint": f"master-{kgm}", "materialId": 7, "bindingStatus": "bound"}],
        "purchases": [], "exceptions": []}
    capture["captureFingerprint"] = _capture_fingerprint(capture)
    return capture


def _memory_import_service(capture_ref):
    registry = ImportMemoryRegistry()
    service = SafariMasterImportService(registry, source_client=type("Source", (), {"doc_id": "cPCjjr9oCzhWC7z8vJbcky"})())
    service._guard = lambda **kwargs: None
    service._read_capture = lambda: deepcopy(capture_ref[0])
    service._resolve_materials = lambda capture: None
    service._resolve_purchases = lambda capture: None
    return service, registry


class SafariMasterImportTests(unittest.TestCase):
    def test_concurrent_first_preview_for_same_key_creates_one_capture_and_request(self):
        capture_ref = [_memory_capture(5.8)]
        service, registry = _memory_import_service(capture_ref)
        reads = 0
        read_lock = threading.Lock()
        original = service._read_capture

        def counted_read():
            nonlocal reads
            with read_lock:
                reads += 1
            return original()

        service._read_capture = counted_read
        with ThreadPoolExecutor(max_workers=2) as pool:
            previews = list(pool.map(lambda _: service.preview(request_key="same-preview", actor="qa"), range(2)))
        self.assertEqual(previews[0]["fingerprint"], previews[1]["fingerprint"])
        self.assertEqual(reads, 1)
        self.assertEqual(len(registry._rows("SafariMasterImportRequest")), 1)
        self.assertEqual(len(registry._rows("SafariMasterImportCapture")), 1)

    def test_concurrent_first_apply_and_recovery_publish_one_immutable_outcome(self):
        capture_ref = [_memory_capture(5.8)]
        service, registry = _memory_import_service(capture_ref)
        preview = service.preview(request_key="same-apply", actor="qa")
        def apply_or_recover(index):
            if index:
                return service.recover("same-apply")
            return service.apply(request_key="same-apply", expected_fingerprint=preview["fingerprint"],
                actor="qa", reason="same request")
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(apply_or_recover, range(2)))
        self.assertEqual([item["status"] for item in outcomes], ["published", "published"])
        self.assertEqual(outcomes[0]["importId"], outcomes[1]["importId"])
        self.assertEqual(len(registry._rows("SafariMasterImport")), 1)
        self.assertEqual(service._current_pointer()["version"], 1)

    def test_competing_stale_previews_cannot_both_publish_over_the_same_predecessor(self):
        capture_ref = [_memory_capture(5.8)]
        service, _ = _memory_import_service(capture_ref)
        old_preview = service.preview(request_key="competing-a", actor="qa")
        stale_preview = service.preview(request_key="competing-b", actor="qa")
        service.apply(request_key="competing-a", expected_fingerprint=old_preview["fingerprint"], actor="qa", reason="first")
        stale = service.apply(request_key="competing-b", expected_fingerprint=stale_preview["fingerprint"], actor="qa", reason="stale")
        self.assertEqual(stale["status"], "superseded")
        self.assertEqual(service._current_pointer()["requestKey"], "competing-a")

    def test_lost_pointer_response_then_newer_import_prevents_old_key_rollback(self):
        capture_ref = [_memory_capture(5.8)]
        service, registry = _memory_import_service(capture_ref)
        old_preview = service.preview(request_key="old-a", actor="qa")
        registry.client.lose_create_table = "SafariMasterImportControl"
        with self.assertRaises(TimeoutError):
            service.apply(request_key="old-a", expected_fingerprint=old_preview["fingerprint"], actor="qa", reason="old")
        self.assertEqual(service._current_pointer()["version"], 1)
        capture_ref[0] = _memory_capture(6.0)
        new_preview = service.preview(request_key="new-b", actor="qa")
        new_result = service.apply(request_key="new-b", expected_fingerprint=new_preview["fingerprint"], actor="qa", reason="new")
        self.assertEqual(new_result["version"], 2)
        recovered = service.recover("old-a")
        self.assertEqual(recovered["status"], "superseded")
        self.assertEqual(service._current_pointer()["importId"], new_result["importId"])
        manifest, materials, _ = active_local_import(registry.client)
        self.assertEqual(manifest["version"], 2)
        self.assertEqual(materials[0]["fields"]["KGsPerMeter"], 6.0)

    def test_partial_pre_pointer_old_key_is_superseded_by_newer_import(self):
        capture_ref = [_memory_capture(5.8)]
        service, registry = _memory_import_service(capture_ref)
        old_preview = service.preview(request_key="old-before-pointer", actor="qa")
        registry.client.lose_create_table = "SafariMasterImportMaterial"
        with self.assertRaises(TimeoutError):
            service.apply(request_key="old-before-pointer", expected_fingerprint=old_preview["fingerprint"], actor="qa", reason="old")
        self.assertIsNone(service._current_pointer()["importId"])
        capture_ref[0] = _memory_capture(6.0)
        new_preview = service.preview(request_key="new-after-partial", actor="qa")
        new_result = service.apply(request_key="new-after-partial", expected_fingerprint=new_preview["fingerprint"], actor="qa", reason="new")
        self.assertEqual(new_result["version"], 1)
        self.assertEqual(service.recover("old-before-pointer")["status"], "superseded")
        _, materials, _ = active_local_import(registry.client)
        self.assertEqual(materials[0]["fields"]["KGsPerMeter"], 6.0)

    def test_reverse_material_binding_conflict_is_explicit_and_readers_do_not_choose_a_winner(self):
        capture = _memory_capture(5.8)
        second = deepcopy(capture["materials"][0])
        second.update({"sourceRecordId": "9", "canonicalName": "Steel reviewed alias", "kgPerMeter": 9.0,
            "materialLatestRate": 99.0, "masterFingerprint": "master-9"})
        capture["materials"].append(second)
        capture["captureFingerprint"] = _capture_fingerprint(capture)
        capture_ref = [capture]
        service, registry = _memory_import_service(capture_ref)
        registry.client.tables["Material"][0]["fields"].update({"ODSDisplayName": "Steel reviewed alias", "MappingStatus": "reviewed_alias"})
        service._resolve_materials = SafariMasterImportService._resolve_materials.__get__(service, SafariMasterImportService)
        service._resolve_materials(capture)
        self.assertEqual({row["bindingStatus"] for row in capture["materials"]}, {"identity_conflict"})
        self.assertEqual(capture["exceptions"][0]["code"], "MATERIAL_SOURCE_FACT_CONFLICT")
        preview = service.preview(request_key="conflict-preview", actor="qa")
        self.assertEqual(len(preview["materialConflicts"]), 2)
        result = service.apply(request_key="conflict-preview", expected_fingerprint=preview["fingerprint"], actor="qa", reason="test")
        self.assertEqual(result["status"], "published")
        catalog = load_local_material_weight_catalog(registry.client)
        self.assertTrue(all(row["factorStatus"] == "identity_conflict" and row["kgPerMeter"] is None for row in catalog["records"]))
        rate = load_local_interim_rates(["Steel"], registry.client)["rows"][0]
        self.assertEqual((rate["status"], rate["costingNewRate"]), ("identity_conflict", None))

    def test_capture_preserves_formula_values_ids_dates_and_exact_matching_logs(self):
        tables = {
            "MasterMaterial": [{"id": 8, "fields": {"MasterMaterial": "MS-Angles 65 x 65 x 6", "Code": "ANG-65",
                "InUse": True, "KGsPerMeter": 5.8, "MaterialLatestRate": 50.68, "Default_MaterialRate": 52.36,
                "TransportLatestRate": 3, "Total_Rate": 53.68, "LatestRateDate": 1794009600,
                "LatestKGsPerMeterDate": 1794009600}}],
            "MaterialRateLog": [{"id": 41, "fields": {"MasterMaterial": ["R", "MasterMaterial", 8],
                "DateOfEntry": 1794009600, "RatePerKG": 50.68, "Supplier": "Vendor A"}}],
            "PurchasePartsMaster": [{"id": 21, "fields": {"SparesPartName": "012 Thrust", "SparesPartCode": "JKMX001",
                "SparesMasterPartName": "012 Thrust", "Unit": "Per Nos", "Nos_Per_KG": 10, "PerNos_FromKg": True,
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
        self.assertIs(purchases[0]["perNosFromKgFlag"], True)
        self.assertIs(purchases[0]["perNosFromKgRaw"], True)
        self.assertEqual(purchases[0]["perNosFromKgSourceEvidence"]["sourceFieldType"], "Bool")
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
            "NosPerKG": 10, "PerNosFromKgFlag": True, "PerNosFromKgRaw": True, "SourceRecordId": "21"}
        same_unit = imported_purchase_rate(row, "Nos", "INR")
        self.assertEqual((same_unit["status"], same_unit["rate"], same_unit["conversionFactor"]), ("available", 92.7, 1.0))
        per_kg = imported_purchase_rate(row, "kg", "INR")
        self.assertEqual((per_kg["status"], per_kg["rate"], per_kg["conversionFactor"]), ("available", 927.0, 10.0))
        reverse = {**row, "Unit": "Per Kg", "TotalCurrentRate": 927, "PerNosFromKgFlag": False, "PerNosFromKgRaw": False}
        per_nos = imported_purchase_rate(reverse, "Nos", "INR")
        self.assertEqual((per_nos["status"], per_nos["rate"], per_nos["conversionFactor"]), ("available", 92.7, 0.1))
        invalid = {**row, "PerNosFromKgFlag": False}
        self.assertEqual(imported_purchase_rate(invalid, "kg", "INR")["status"], "review_required")
        self.assertEqual(imported_purchase_rate({**row, "PerNosFromKgFlag": None}, "kg", "INR")["status"], "review_required")
        self.assertEqual(imported_purchase_rate({**row, "NosPerKG": 0}, "kg", "INR")["status"], "review_required")
        # Early R1 captures stored this Bool as a Numeric. Only 0/1 are
        # accepted for compatibility; the mistaken fractional factor is not.
        self.assertEqual(imported_purchase_rate({**row, "PerNosFromKgFlag": None, "PerNosFromKg": 1}, "kg", "INR")["rate"], 927)
        self.assertEqual(imported_purchase_rate({**row, "PerNosFromKgFlag": None, "PerNosFromKg": 0.1}, "kg", "INR")["status"], "review_required")
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

from __future__ import annotations

from datetime import date, timedelta
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.schema import FOUNDATION_TABLES
from app.spare_import import apply_import_plan, build_import_plan, preview_payload
from app.spares import GristSpareRegistry, SpareRegistryError, SPARE_TABLE_IDS, parse_source_date, source_cost_age
from app.milestone2 import OdsCell, OdsDocument, OdsSheet


class _Admin:
    def __init__(self, *_args): pass
    def list_tables(self, _doc_id, include_columns=False):
        return [{"id": table_id} for table_id in SPARE_TABLE_IDS]


class _Grist:
    api_key = "test-key"
    base_url = "https://grist.invalid"
    doc_id = "isolated-spares-test"

    def __init__(self):
        self.tables: dict[str, list[dict]] = {}
        self.next_id = 1
        self.fail_once_on: str | None = None
        self.writes = 0

    def validate_safari_write_target(self):
        self.writes += 1

    def fetch_table_records_with_ids(self, table: str):
        return [{"id": row["id"], "fields": dict(row["fields"])} for row in self.tables.get(table, [])]

    def create_table_records(self, table: str, records: list[dict]):
        if self.fail_once_on == table:
            self.fail_once_on = None
            raise RuntimeError(f"injected partial-write failure at {table}")
        saved = self.tables.setdefault(table, [])
        for record in records:
            saved.append({"id": self.next_id, "fields": dict(record["fields"])})
            self.next_id += 1

    def update_table_records(self, table: str, records: list[dict]):
        saved = self.tables.setdefault(table, [])
        by_id = {row["id"]: row for row in saved}
        for record in records:
            by_id[int(record["id"])]["fields"].update(record["fields"])


def _cell(row: int, column: int, value=None, *, formula=None, value_type=None, display="") -> OdsCell:
    return OdsCell(row, column, formula, value_type, value, display or (str(value) if value is not None else ""), {})


def _registry(directory: str, grist: _Grist) -> GristSpareRegistry:
    return GristSpareRegistry(grist, journal_path=Path(directory) / "coordination.sqlite3")


class SpareRegistryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.grist = _Grist()
        self.admin_patch = patch("app.spares.GristAdminClient", _Admin)
        self.admin_patch.start()
        self.addCleanup(self.admin_patch.stop)
        self.registry = _registry(self.directory.name, self.grist)

    def test_create_retry_update_retire_history_and_number_reservation(self):
        first = self.registry.create_spare(description="Pilot washer", category="Washer", unit="EA", sourcing_type="unclassified", actor="qa", reason="reviewed", request_key="create-1", aliases=[{"value": "W-OLD"}])
        replay = self.registry.create_spare(description="Pilot washer", category="Washer", unit="EA", sourcing_type="unclassified", actor="qa", reason="reviewed", request_key="create-1", aliases=[{"value": "W-OLD"}])
        second = self.registry.create_spare(description="Retainer", actor="qa", reason="reviewed", request_key="create-2")
        self.assertEqual(first["spareCode"], "SM-S-000001")
        self.assertTrue(replay["idempotent"])
        self.assertEqual(second["spareCode"], "SM-S-000002")
        self.assertEqual(len(self.grist.tables["SpareMaster"]), 2)
        self.assertEqual(first["legacyAliases"], ["W-OLD"])

        updated = self.registry.update_spare(spare_key=first["spareKey"], expected_version=1, updates={"description": "Pilot washer rev 2"}, actor="qa", reason="corrected description", request_key="update-1")
        self.assertEqual((updated["version"], updated["description"]), (2, "Pilot washer rev 2"))
        retired = self.registry.update_spare(spare_key=first["spareKey"], expected_version=2, updates={}, actor="qa", reason="replaced", request_key="retire-1", retire=True)
        self.assertEqual(retired["status"], "retired")
        self.assertEqual([row["EventType"] for row in retired["history"]], ["created", "updated", "retired"])
        with self.assertRaises(SpareRegistryError):
            self.registry.update_spare(spare_key=first["spareKey"], expected_version=3, updates={"description": "No"}, actor="qa", reason="stale", request_key="edit-retired")

    def test_source_cost_age_uses_explicit_india_calendar_date(self):
        as_of = date(2026, 11, 17)
        self.assertEqual(source_cost_age("2026-10-18", as_of=as_of)["ageDays"], 30)
        self.assertEqual(source_cost_age(None, as_of=as_of)["status"], "unknown")
        future = source_cost_age("2026-11-18", as_of=as_of)
        self.assertEqual((future["status"], future["daysUntil"]), ("future_date", 1))
        self.assertEqual(parse_source_date("17/10/2026"), ("2026-10-17", "source_text_day_first"))

    def test_interrupted_source_evidence_write_recovers_with_same_request_without_duplicates(self):
        plan = {
            "previewFingerprint": "preview-fingerprint-1", "sourceHashes": {"associatedWorkbook": "workbook-hash", "templateMaster": "template-hash", "associationContext": "association-hash"},
            "sourceLabels": {"associatedWorkbook": "Pilot.ods", "templateMaster": "Template/Spares List - Master.ods"},
            "counts": {"creates": 1, "updates": 0, "noops": 0, "sourceEvidence": 1, "costEvidence": 1, "compositionEvidence": 1, "unresolvedEvidenceRows": 0},
            "identities": [{"action": "create", "normalizedLegacyCode": "washer-1", "legacyCode": "WASHER-1", "description": "Pilot washer", "category": "Washer", "unit": "EA", "sourcingTypeCandidate": None, "sourceRow": 8}],
            "sourceEvidence": [{"key": "source:1", "legacyCode": "WASHER-1", "sourceKind": "SparesDB", "sourceFile": "Pilot.ods", "sourceHash": "workbook-hash", "sheetName": "SparesDB", "sourceRow": 8, "sourceCell": "A8", "resolutionStatus": "resolved_unique_source_identity", "formula": "", "cachedValue": "WASHER-1", "sourceDateRaw": "", "sourceDateISO": "", "sourceDateMeaning": "", "classificationCandidate": "", "cells": {"A8": {"cached_value": "WASHER-1"}}, "fingerprint": "source-fp-1"}],
            "costEvidence": [{"key": "cost:1", "legacyCode": "WASHER-1", "costKind": "source_purchase_price", "value": 12.5, "cachedValue": 12.5, "unit": "EA", "currency": "", "sourceFile": "Pilot.ods", "sourceHash": "workbook-hash", "sheetName": "SparesDB", "sourceRow": 8, "sourceCell": "E8", "formula": "", "sourceDateRaw": "", "sourceDateISO": "", "sourceDateMeaning": "missing", "dateBasis": "missing", "entryISO": "", "fingerprint": "cost-fp-1"}],
            "compositions": [{"key": "composition:1", "sourceKind": "SparesDetail", "sourceFile": "Pilot.ods", "sourceHash": "workbook-hash", "sheetName": "Spares Detail", "sourceRow": 9, "sourceIdentity": "WASHER-1", "sourceLineType": "MCL", "sourceLineCode": "MCL-7", "manufacturingLineCode": "MCL-7", "storesLineCode": "", "nestedLegacySpareCode": "", "nestedReferenceStatus": "not_identified", "quantity": 2, "quantityUnit": "", "quantityOverride": 3, "overrideUnit": "", "resolutionStatus": "resolved_parent", "cells": {"B9": {"cached_value": "WASHER-1"}}, "fingerprint": "composition-fp-1"}],
        }
        self.grist.fail_once_on = "SpareSourceEvidence"
        with self.assertRaisesRegex(RuntimeError, "injected partial-write"):
            apply_import_plan(plan=plan, registry=self.registry, actor="qa", request_key="import-request-1")
        result = apply_import_plan(plan=plan, registry=self.registry, actor="qa", request_key="import-request-1")
        replay = apply_import_plan(plan=plan, registry=self.registry, actor="qa", request_key="import-request-1")
        self.assertEqual(result["status"], "complete")
        self.assertTrue(replay["idempotent"])
        self.assertEqual(len(self.grist.tables["SpareMaster"]), 1)
        self.assertEqual(len(self.grist.tables["SpareHistory"]), 1)
        self.assertEqual(len(self.grist.tables["SpareSourceEvidence"]), 1)
        self.assertEqual(len(self.grist.tables["SpareSourceCost"]), 1)
        self.assertEqual(len(self.grist.tables["SpareCompositionEvidence"]), 1)
        self.assertEqual(self.registry.import_status("import-request-1")["status"], "complete")

    def test_fresh_noop_import_reuses_source_alias_fingerprints_without_duplicates(self):
        identity = {"action": "create", "normalizedLegacyCode": "washer-1", "legacyCode": "WASHER-1", "description": "Pilot washer", "category": "Washer", "unit": "EA", "sourcingTypeCandidate": "purchased", "sourceRow": 8}
        plan = {
            "previewFingerprint": "preview-create-1",
            "sourceHashes": {"associatedWorkbook": "workbook-hash", "templateMaster": "template-hash", "associationContext": "association-hash"},
            "sourceLabels": {"associatedWorkbook": "Pilot.ods", "templateMaster": "Template/Spares List - Master.ods"},
            "counts": {"creates": 1, "updates": 0, "noops": 0, "conflicts": 0, "sourceEvidence": 2, "costEvidence": 1, "compositionEvidence": 1, "unresolvedEvidenceRows": 0},
            "identities": [identity],
            "sourceEvidence": [
                {"key": "source:associated:8", "legacyCode": "WASHER-1", "sourceKind": "SparesDB", "sourceFile": "Pilot.ods", "sourceHash": "workbook-hash", "sheetName": "SparesDB", "sourceRow": 8, "sourceCell": "A8", "resolutionStatus": "resolved_unique_source_identity", "formula": "", "cachedValue": "WASHER-1", "sourceDateRaw": "", "sourceDateISO": "", "sourceDateMeaning": "", "classificationCandidate": "purchased", "cells": {"A8": {"cached_value": "WASHER-1"}}, "fingerprint": "source-fp-associated"},
                {"key": "source:template:2", "legacyCode": "WASHER-1", "sourceKind": "TemplateMaster", "sourceFile": "Template/Spares List - Master.ods", "sourceHash": "template-hash", "sheetName": "Master", "sourceRow": 2, "sourceCell": "A2", "resolutionStatus": "resolved_unique_source_identity", "formula": "", "cachedValue": "WASHER-1", "sourceDateRaw": "", "sourceDateISO": "", "sourceDateMeaning": "", "classificationCandidate": "", "cells": {"A2": {"cached_value": "WASHER-1"}}, "fingerprint": "source-fp-template"},
            ],
            "costEvidence": [{"key": "cost:associated:8:E8", "legacyCode": "WASHER-1", "costKind": "source_purchase_price", "value": 12.5, "cachedValue": 12.5, "unit": "EA", "currency": "", "sourceFile": "Pilot.ods", "sourceHash": "workbook-hash", "sheetName": "SparesDB", "sourceRow": 8, "sourceCell": "E8", "formula": "", "sourceDateRaw": "", "sourceDateISO": "", "sourceDateMeaning": "missing", "dateBasis": "missing", "entryISO": "", "fingerprint": "cost-fp-1"}],
            "compositions": [{"key": "composition:detail:9", "sourceKind": "SparesDetail", "sourceFile": "Pilot.ods", "sourceHash": "workbook-hash", "sheetName": "Spares Detail", "sourceRow": 9, "sourceIdentity": "WASHER-1", "sourceLineType": "MCL", "sourceLineCode": "MCL-7", "manufacturingLineCode": "MCL-7", "storesLineCode": "", "nestedLegacySpareCode": "", "nestedReferenceStatus": "not_identified", "quantity": 2, "quantityUnit": "", "quantityOverride": 3, "overrideUnit": "", "resolutionStatus": "resolved_parent", "cells": {"B9": {"cached_value": "WASHER-1"}}, "fingerprint": "composition-fp-1"}],
        }
        first = apply_import_plan(plan=plan, registry=self.registry, actor="qa", request_key="import-first-preview")
        spare_key = self.registry.list_spares(status="all")["items"][0]["spareKey"]
        spare_record_id = int(self.registry._spare_master_by_key(spare_key)["id"])
        repeated_plan = {**plan, "previewFingerprint": "preview-noop-2", "counts": {**plan["counts"], "creates": 0, "noops": 1}, "identities": [{**identity, "action": "noop", "spareKey": spare_key, "recordId": spare_record_id}]}
        repeated = apply_import_plan(plan=repeated_plan, registry=self.registry, actor="qa", request_key="import-noop-preview")
        replay = apply_import_plan(plan=repeated_plan, registry=self.registry, actor="qa", request_key="import-noop-preview")

        self.assertEqual(first["created"], {"sourceEvidence": 2, "costEvidence": 1, "compositionEvidence": 1})
        self.assertEqual(repeated["created"], {"sourceEvidence": 0, "costEvidence": 0, "compositionEvidence": 0})
        self.assertEqual(repeated["existing"], {"sourceEvidence": 2, "costEvidence": 1, "compositionEvidence": 1})
        self.assertTrue(replay["idempotent"])
        self.assertEqual(self.registry.get_spare(spare_key)["legacyAliases"], ["WASHER-1"])
        self.assertEqual(len(self.grist.tables["SpareMaster"]), 1)
        self.assertEqual(len(self.grist.tables["SpareAlias"]), 2)
        self.assertEqual(len(self.grist.tables["SpareHistory"]), 1)
        self.assertEqual(len(self.grist.tables["SpareSourceEvidence"]), 2)
        self.assertEqual(len(self.grist.tables["SpareSourceCost"]), 1)
        self.assertEqual(len(self.grist.tables["SpareCompositionEvidence"]), 1)

    def test_preview_pages_exact_source_formula_cache_and_composition_lineage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product_path = root / "pilot.ods"; template_path = root / "template.ods"
            product_path.write_bytes(b"synthetic-pilot-ods"); template_path.write_bytes(b"synthetic-template-ods")
            product = OdsDocument(product_path, {
                # Build explicit sheets with sparse exact coordinates, as returned by the ODS reader.
                "SparesDB": OdsSheet("SparesDB", {
                    8: {1: _cell(8, 1, "WASHER-1"), 2: _cell(8, 2, "Washer"), 3: _cell(8, 3, "Pilot washer"), 4: _cell(8, 4, "EA"), 5: _cell(8, 5, 12.5), 7: _cell(8, 7, date(2026, 10, 18))},
                    9: {1: _cell(9, 1, "WASHER-2"), 3: _cell(9, 3, "Retainer"), 4: _cell(9, 4, "EA")},
                }),
                "Spares Detail": OdsSheet("Spares Detail", {9: {2: _cell(9, 2, "WASHER-1"), 4: _cell(9, 4, "MCL"), 5: _cell(9, 5, "MCL-7"), 6: _cell(9, 6, "WASHER-2"), 8: _cell(9, 8, 2), 10: _cell(9, 10, 3)}}),
                "Spares Summary": OdsSheet("Spares Summary", {5: {2: _cell(5, 2, "WASHER-1"), 9: _cell(5, 9, "summary note")}}),
            })
            template = OdsDocument(template_path, {"Master": OdsSheet("Master", {
                2: {1: _cell(2, 1, "WASHER-1"), 4: _cell(2, 4, "EA"), 5: _cell(2, 5, 14.0, formula="of:=7*2", value_type="float"), 8: _cell(2, 8, "Store"), 11: _cell(2, 11, 15.0), 12: _cell(2, 12, "9999-12-31"), 16: _cell(2, 16, 16.0), 22: _cell(2, 22, "")},
            })})
            with patch("app.spare_import.read_ods", side_effect=lambda path, _sheets: product if Path(path) == product_path else template):
                plan = build_import_plan(product_path=product_path, template_path=template_path, registry=self.registry, costing_root=root, association_key="association-v1")
            preview = preview_payload(plan, offset=0, limit=10, evidence_offset=0, evidence_limit=1)
            self.assertEqual(plan["identities"][0]["action"], "create")
            self.assertEqual(plan["identities"][0]["sourcingTypeCandidate"], "purchased")
            self.assertGreaterEqual(plan["counts"]["futureCostDates"], 1)
            self.assertEqual(preview["identityRows"][0]["sourceRow"], 8)
            formula_index = next(i for i, row in enumerate(plan["costEvidence"]) if row["formula"])
            formula_page = preview_payload(plan, evidence_offset=formula_index, evidence_limit=1)
            self.assertEqual(formula_page["costEvidenceRows"][0]["formula"], "of:=7*2")
            composition = plan["compositions"][0]
            self.assertEqual((composition["manufacturingLineCode"], composition["nestedLegacySpareCode"], composition["quantityOverride"]), ("MCL-7", "WASHER-2", 3.0))
            next_evidence = preview_payload(plan, evidence_offset=1, evidence_limit=1)
            self.assertEqual(next_evidence["sourceEvidenceRows"][0]["sourceCell"], "A9")


if __name__ == "__main__":
    unittest.main()

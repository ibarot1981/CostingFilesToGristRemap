from __future__ import annotations

import tempfile
import json
import unittest
import zipfile
from datetime import date
from decimal import Decimal
from pathlib import Path

from app.milestone2 import (
    OdsCell,
    _json_value,
    _round_up_whole_rupee,
    _line_key,
    _parse_log_date,
    apply_rate_display_controls,
    classify_layout_variants,
    compare_dump_to_product_log,
    read_ods,
    resolve_material_rates,
    validate_product_log_occurrence_keys,
)


class Milestone2SemanticTests(unittest.TestCase):
    def setUp(self) -> None:
        self.raw = [
            {
                "source_row": 2,
                "unique_item_list": "Flat 20 x 3",
                "default_rate_per_kg": Decimal("40"),
            },
            {
                "source_row": 3,
                "unique_item_list": "Round 10 mm",
                "default_rate_per_kg": Decimal("0"),
            },
        ]

    def test_nested_snapshot_values_preserve_decimal_precision_and_are_json_safe(self) -> None:
        value = {
            "raw_steel_values": {"default_rate": Decimal("46.1250")},
            "rate_log_values": [{"price": Decimal("44.70"), "recorded": date(2026, 9, 20)}],
        }
        normalized = _json_value(value)
        self.assertEqual(normalized["raw_steel_values"]["default_rate"], "46.1250")
        self.assertEqual(normalized["rate_log_values"][0], {"price": "44.70", "recorded": "2026-09-20"})
        self.assertIn('"44.70"', json.dumps(normalized))

    def test_latest_rate_uses_last_source_occurrence_and_records_date_regression(self) -> None:
        logs = [
            {"source_row": 3, "material": "Flat 20 x 3", "rate_per_kg": Decimal("45"), "date_iso": "2026-09-20"},
            {"source_row": 8, "material": "Flat 20 x 3", "rate_per_kg": Decimal("43"), "date_iso": "2026-09-18"},
        ]
        rates, issues = resolve_material_rates(self.raw, logs, "Latest Rate")
        result = rates["Flat 20 x 3"]
        self.assertEqual(result["latest_rate_per_kg"], Decimal("43"))
        self.assertEqual(result["last_rate_log_row"], 8)
        self.assertEqual(result["maximum_logged_rate_per_kg"], Decimal("45"))
        self.assertEqual(result["maximum_rate_log_row"], 3)
        self.assertEqual(result["selected_rate_source_row"], 8)
        self.assertEqual(result["final_rate_per_kg"], Decimal("45"))
        self.assertEqual(result["last_logged_rate_date"], "2026-09-18")
        self.assertTrue(any(issue["code"] == "RATE_LOG_LAST_ROW_NOT_MAX_DATE" for issue in issues))

    def test_max_rate_records_the_source_row_that_supplied_the_maximum(self) -> None:
        logs = [
            {"source_row": 3, "material": "Flat 20 x 3", "rate_per_kg": Decimal("45"), "date_iso": "2026-09-20"},
            {"source_row": 8, "material": "Flat 20 x 3", "rate_per_kg": Decimal("43"), "date_iso": "2026-09-18"},
        ]
        rates, _ = resolve_material_rates(self.raw, logs, "Max Rate")
        self.assertEqual(rates["Flat 20 x 3"]["selected_rate_source_row"], 3)
        self.assertEqual(rates["Flat 20 x 3"]["selected_rate_date"], "2026-09-20")

    def test_no_log_uses_default_for_latest_and_max_and_leaves_date_null(self) -> None:
        rates, _ = resolve_material_rates(self.raw, [], "Latest Rate")
        result = rates["Flat 20 x 3"]
        self.assertEqual(result["rate_source"], "default_master_rate")
        self.assertEqual(result["latest_rate_per_kg"], Decimal("40"))
        self.assertEqual(result["maximum_logged_rate_per_kg"], Decimal("40"))
        self.assertEqual(result["final_rate_per_kg"], Decimal("42"))
        self.assertIsNone(result["last_logged_rate_date"])

    def test_zero_only_log_maximum_falls_back_to_latest(self) -> None:
        logs = [
            {"source_row": 3, "material": "Round 10 mm", "rate_per_kg": Decimal("0"), "date_iso": "2026-09-20"}
        ]
        rates, _ = resolve_material_rates(self.raw, logs, "Maximum Rate")
        self.assertEqual(rates["Round 10 mm"]["maximum_logged_rate_per_kg"], Decimal("0"))
        self.assertEqual(rates["Round 10 mm"]["selected_rate_before_safety_margin"], Decimal("0"))
        self.assertEqual(rates["Round 10 mm"]["final_rate_per_kg"], Decimal("2"))

    def test_dmy_text_date_is_not_locale_guessed(self) -> None:
        cell = OdsCell(3, 4, None, "string", "17-01-2025", "17-01-2025", {"value-type": "string"})
        parsed, status = _parse_log_date(cell)
        self.assertEqual(parsed, date(2025, 1, 17))
        self.assertEqual(status, "parsed_text_date")

    def test_business_output_rounds_each_line_grand_total_up_to_whole_rupee(self) -> None:
        self.assertEqual(_round_up_whole_rupee(Decimal("23.01")), Decimal("24"))
        self.assertEqual(_round_up_whole_rupee(Decimal("23.00")), Decimal("23"))

    def test_future_date_is_informational_but_invalid_rate_is_withheld(self) -> None:
        resolutions = {
            "Flat 20 x 3": {"final_rate_per_kg": Decimal("47")},
            "Round 10 mm": {"final_rate_per_kg": Decimal("2")},
        }
        blocked = apply_rate_display_controls(
            resolutions,
            [
                {"source_row": 10, "material": "Flat 20 x 3", "date_iso": "2026-10-01"},
                {"source_row": 11, "material": "Round 10 mm", "date_iso": "2026-09-20"},
            ],
            [{"code": "RATE_LOG_INVALID_RATE", "material": "Round 10 mm", "source_row": 11}],
            date(2026, 9, 26),
        )
        self.assertEqual(resolutions["Flat 20 x 3"]["display_rate_per_kg"], Decimal("47"))
        self.assertEqual(resolutions["Flat 20 x 3"]["display_status"], "available")
        self.assertEqual(resolutions["Round 10 mm"]["display_rate_per_kg"], None)
        self.assertEqual(resolutions["Round 10 mm"]["final_rate_per_kg"], Decimal("2"))
        self.assertEqual(len(blocked), 1)

    def test_material_cut_list_key_uses_only_confirmed_composite_fields(self) -> None:
        values = {
            "machine_piece_description": "Frame",
            "material_to_cut": "Flat 20 x 3",
            "dimension_mm": Decimal("250"),
            "quantity": Decimal("2.0"),
            "optional_item_group_1": "",
            "item_line_no": "MCL001",
            "date": "2026-09-01",
            "cr_log": "CR-21",
        }
        self.assertEqual(
            _line_key(values),
            ("Frame", "Flat 20 x 3", "250", "2", ""),
        )
        changed = {**values, "quantity": Decimal("3"), "item_line_no": "MCL999"}
        self.assertNotEqual(_line_key(values), _line_key(changed))

    def test_formula_and_cached_value_are_both_retained_from_ods(self) -> None:
        xml = '''<?xml version="1.0" encoding="UTF-8"?>
        <office:document-content
          xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
          xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0"
          xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">
          <office:body><office:spreadsheet><table:table table:name="RawSteel">
            <table:table-header-rows><table:table-row>
              <table:table-cell office:value-type="string"><text:p>Name</text:p></table:table-cell>
            </table:table-row></table:table-header-rows>
            <table:table-row><table:table-cell table:formula="of:=1+1" office:value-type="float" office:value="2"><text:p>2</text:p></table:table-cell></table:table-row>
          </table:table></office:spreadsheet></office:body>
        </office:document-content>'''
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.ods"
            with zipfile.ZipFile(path, "w") as package:
                package.writestr("content.xml", xml)
            sheet = read_ods(path, {"RawSteel"}).sheets["RawSteel"]
            self.assertEqual(sheet.cell(1, 1).value, "Name")
            self.assertEqual(sheet.cell(2, 1).formula, "of:=1+1")
            self.assertEqual(sheet.cell(2, 1).value, Decimal("2"))

    def test_product_helper_occurrence_keys_are_checked_exactly(self) -> None:
        rows = [
            {"source_row": 3, "material": "Flat 20 x 3", "helper": "Flat 20 x 3-1", "cells": {"helper": {"cell": "A3", "formula": "=..."}}},
            {"source_row": 4, "material": "Flat 20 x 3", "helper": "Flat 20 x 3-4", "cells": {"helper": {"cell": "A4", "formula": "=..."}}},
        ]
        issues = validate_product_log_occurrence_keys(rows)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["expected_helper"], "Flat 20 x 3-2")

    def test_import_row_coverage_difference_is_not_hidden(self) -> None:
        source = [{"source_row": 3, "material": "X", "rate_per_kg": Decimal("10"), "quantity_ordered": "2", "date_iso": "2026-09-01", "description": "x", "remarks": "r"}]
        product = []
        issues = compare_dump_to_product_log(source, product)
        self.assertEqual(issues, [{"code": "PRODUCT_STEEL_LOG_ROW_COVERAGE_MISMATCH", "source_row": 3, "dump_present": True, "product_present": False}])

    def test_only_selected_workbook_variants_are_review_findings(self) -> None:
        variants = [
            {
                "path": "max-range.ods",
                "iron_contract": {
                    "selector": "Max Rate",
                    "sample_source_lookup_end_row": 350,
                    "has_maxifs": True,
                    "has_last_occurrence_lookup": True,
                    "has_two_rupee_safety_margin": False,
                },
            },
            {
                "path": "selected-max.ods",
                "iron_contract": {
                    "selector": "Max Rate",
                    "sample_source_lookup_end_row": 350,
                    "has_maxifs": True,
                    "has_last_occurrence_lookup": True,
                    "has_two_rupee_safety_margin": True,
                },
            },
        ]
        self.assertEqual(classify_layout_variants(variants, "selected-max.ods"), [])
        self.assertEqual(
            classify_layout_variants(variants, "max-range.ods")[0]["code"],
            "SELECTED_PRODUCT_WORKBOOK_FORMULA_VARIANT",
        )


if __name__ == "__main__":
    unittest.main()

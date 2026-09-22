import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from rich.console import Console

import app.verification as verification


class _Result:
    def __init__(self, label):
        self.only_in_ods = [{"row": label}]
        self.label = label
        self.grist_tally_updates = []

    def counts(self):
        return {"only in ODS": 1, "Grist tally updates": len(self.grist_tally_updates)}

    def match_band_summary(self):
        return {}

    def detail_rows(self):
        return []


class VerificationRegressionTests(unittest.TestCase):
    def test_cnc_tally_uses_refreshed_grist_rows_after_dxf_sync(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            grist_config = SimpleNamespace(enabled=True, table_id="ProductPartCNCList", tally_field="TallyWithODS", comparable_material_field="")
            sheet_config = SimpleNamespace(grist=grist_config, header_row=1)
            app_config = SimpleNamespace(supported_sheets={"CNC Cut List": sheet_config}, reports_dir=root)
            sheet = SimpleNamespace(rows=[["head"], ["value"]], last_non_empty_row_number=lambda: 2)
            workbook = SimpleNamespace(path=root / "source.ods", sheet_view=lambda *_: sheet)
            filters = SimpleNamespace(product_part_names={"Part A"}, product_filter_label="Part A")
            scope = SimpleNamespace(metadata=lambda: {})
            first_result, refreshed_result = _Result("before"), _Result("after")

            class Client:
                def __init__(self):
                    self.fetches = []

                def fetch_table_records_with_ids(self, table_id):
                    if table_id == "MasterMaterial":
                        return []
                    self.fetches.append(table_id)
                    return [{"id": len(self.fetches), "fields": {"snapshot": len(self.fetches)}}]

            client = Client()
            writes = {}
            writer = SimpleNamespace(
                write_json=lambda *_: root / "summary.json",
                write_csv=lambda *_: root / "details.csv",
                write_verification_html=lambda *_: root / "report.html",
            )
            patches = [
                mock.patch.object(verification.GristClient, "from_environment", return_value=client),
                mock.patch.object(verification, "ask_verification_filters", return_value=(filters, 0, 0, ["Part A"])),
                mock.patch.object(verification, "ask_ods_option_scope", return_value=(scope, {}, False)),
                mock.patch.object(verification, "load_material_mapping", return_value={}),
                mock.patch.object(verification, "build_master_material_index", return_value={}),
                mock.patch.object(verification, "grist_record_passes_filters", return_value=True),
                mock.patch.object(verification, "Confirm"),
                mock.patch.object(verification, "compare_sheet_to_grist", side_effect=[first_result, refreshed_result]),
                mock.patch.object(verification, "show_count_summary"),
                mock.patch.object(verification, "ask_cnc_dxf_review_filter", return_value=(object(), {"selected": []})),
                mock.patch.object(verification, "filter_cnc_dxf_review_rows", return_value=([], [])),
                mock.patch.object(verification, "review_and_update_cnc_dxf_paths", return_value=(root / "dxf.json", SimpleNamespace(action="updated", updated_rows=1, cleared_rows=0, unchanged_rows=0, skipped_rows=0, created_rows=0))),
                mock.patch.object(verification, "update_grist_tally", side_effect=lambda **kwargs: writes.update(kwargs) or []),
                mock.patch.object(verification, "show_tally_update_summary"),
                mock.patch.object(verification, "ReportWriter", return_value=writer),
                mock.patch.object(verification, "report_dir_for_source", return_value=root),
            ]
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6] as confirm, patches[7], patches[8], patches[9], patches[10], patches[11], patches[12], patches[13], patches[14], patches[15]:
                confirm.ask.return_value = True
                verification.verify_sheet_with_grist(
                    console=Console(), workbook=workbook, app_config=app_config,
                    sheet_name="CNC Cut List", material_mapping_path=root / "mapping.yaml",
                )

        self.assertEqual(client.fetches, ["ProductPartCNCList", "ProductPartCNCList"])
        self.assertEqual(writes["grist_records"][0]["fields"]["snapshot"], 2)
        self.assertIs(writes["result"], refreshed_result)


if __name__ == "__main__":
    unittest.main()

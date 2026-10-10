import unittest
from pathlib import Path

from app.milestone2 import OdsCell, OdsDocument, OdsSheet, _configured_process_lines, part_mapping_source_diagnostics
from app.part_mapping import source_groups


def cell(row, column, value):
    return OdsCell(row, column, None, "string", value, "" if value is None else str(value), {})


def sheet(name, header_row, headers, data_rows):
    rows = {header_row: {index: cell(header_row, index, label) for index, label in enumerate(headers, 1)}}
    for row_number, values in data_rows.items():
        rows[row_number] = {index: cell(row_number, index, value) for index, value in enumerate(values, 1)}
    return OdsSheet(name, rows)


class PartMappingExtractionTests(unittest.TestCase):
    def test_sheet_specific_labels_and_exact_header_cell_provenance(self):
        document = OdsDocument(Path("pilot.ods"), {
            "5. Material Cut List Price": sheet("5. Material Cut List Price", 8,
                ["Machine Piece Description", "Material to Cut", "Dimension to Cut (mm)", "Quantity Nos", "Optional Item Group 1", "In Use", "Remarks", "CR Log"],
                {9: ["", "MS Plate", "3 x 25", "2", "HF", "Yes", "Deburr", "CR-10"]}),
            "Tool Shop Items": sheet("Tool Shop Items", 9,
                ["Machine Piece Description", "Item Name", "Material Used", "Dimension in mm", "Qty", "In Use", "Remarks", "Job Remarks", "CR Log"],
                {10: ["Bracket", "Laser cut", "Steel", "80", "1", "Yes", "Flat", "Urgent", "CR-11"]}),
            "CNC Cut List": sheet("CNC Cut List", 8,
                ["Plate Part to Cut", "Part Category", "Material used", "Length", "Width", "Thickness", "Qty Of Part Used", "In Use"],
                {9: ["Plate Flange", "Bracket", "MS", "120", "60", "3", "4", "Yes"]}),
        })
        lines = _configured_process_lines(document)
        diagnostics = part_mapping_source_diagnostics(document)
        groups = source_groups(lines, sheet_diagnostics=diagnostics)
        by_sheet = {group["sheet"]: group for group in groups}
        mcl = by_sheet["5. Material Cut List Price"]
        tool = by_sheet["Tool Shop Items"]
        cnc = by_sheet["CNC Cut List"]
        self.assertEqual(mcl["description"], "")  # active blank Part label remains individually reviewed
        self.assertTrue(mcl["blankDescription"])
        self.assertEqual(mcl["rows"][0]["fields"]["material_to_cut"], "MS Plate")
        self.assertEqual(mcl["rows"][0]["sourceHeaders"]["product_part_name"], "Machine Piece Description")
        self.assertEqual(mcl["rows"][0]["sourceHeaderCells"]["product_part_name"], "A8")
        self.assertEqual(tool["description"], "Bracket")
        self.assertEqual(tool["rows"][0]["fields"]["job_remarks"], "Urgent")
        self.assertEqual(cnc["description"], "Bracket")
        self.assertEqual(cnc["labelField"], "part_category")
        self.assertEqual(cnc["rows"][0]["fields"]["product_part_name"], "Plate Flange")
        self.assertEqual(cnc["rows"][0]["fields"]["length"], "120")
        self.assertEqual(diagnostics["CNC Cut List"]["labelHeader"], "Part Category")
        self.assertEqual(diagnostics["CNC Cut List"]["labelHeaderCell"], "B8")

    def test_cnc_missing_category_does_not_substitute_plate_identity_and_ambiguous_headers_are_reported(self):
        document = OdsDocument(Path("pilot.ods"), {
            "5. Material Cut List Price": sheet("5. Material Cut List Price", 8,
                ["Machine Piece Description", "Machine Piece Description", "Material to Cut"], {9: ["Shaft", "Shaft alias", "MS"]}),
            "Tool Shop Items": sheet("Tool Shop Items", 9,
                ["Machine Piece Description", "Material Used"], {10: ["", "Steel"]}),
            "CNC Cut List": sheet("CNC Cut List", 8,
                ["Plate Part to Cut", "Material used", "Length", "Width", "Thickness"],
                {9: ["Plate Flange", "MS", "100", "20", "3"]}),
        })
        lines = _configured_process_lines(document)
        diagnostics = part_mapping_source_diagnostics(document)
        groups = source_groups(lines, sheet_diagnostics=diagnostics)
        cnc = next(group for group in groups if group["sheet"] == "CNC Cut List")
        self.assertEqual(cnc["description"], "")
        self.assertTrue(cnc["blankDescription"])
        self.assertEqual(cnc["rows"][0]["fields"]["product_part_name"], "Plate Flange")
        self.assertEqual(diagnostics["CNC Cut List"]["status"], "label_column_missing")
        self.assertEqual(diagnostics["5. Material Cut List Price"]["status"], "label_column_ambiguous")
        self.assertEqual(len(diagnostics["5. Material Cut List Price"]["ambiguousLabelHeaders"]), 2)


if __name__ == "__main__":
    unittest.main()

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import mock
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from app.milestone2 import OdsCell, OdsDocument, OdsSheet, _configured_process_lines, part_mapping_source_diagnostics
from app.part_mapping import source_groups
from app import web
from app.part_mapping import MemoryPartStore
from app.repository import sha256_file


def cell(row, column, value):
    return OdsCell(row, column, None, "string", value, "" if value is None else str(value), {})


def sheet(name, header_row, headers, data_rows):
    rows = {header_row: {index: cell(header_row, index, label) for index, label in enumerate(headers, 1)}}
    for row_number, values in data_rows.items():
        rows[row_number] = {index: cell(row_number, index, value) for index, value in enumerate(values, 1)}
    return OdsSheet(name, rows)


_OFFICE = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
_TABLE = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"
_TEXT = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
for _prefix, _uri in (("office", _OFFICE), ("table", _TABLE), ("text", _TEXT)):
    ET.register_namespace(_prefix, _uri)


def write_ods(path: Path, sheets: dict[str, tuple[list[str], list[list[str | None]]]]) -> None:
    root = ET.Element(f"{{{_OFFICE}}}document-content")
    body = ET.SubElement(root, f"{{{_OFFICE}}}body")
    spreadsheet = ET.SubElement(body, f"{{{_OFFICE}}}spreadsheet")
    for name, (headers, rows) in sheets.items():
        table = ET.SubElement(spreadsheet, f"{{{_TABLE}}}table", {f"{{{_TABLE}}}name": name})
        for values in (headers, *rows):
            row = ET.SubElement(table, f"{{{_TABLE}}}table-row")
            for value in values:
                cell = ET.SubElement(row, f"{{{_TABLE}}}table-cell", {f"{{{_OFFICE}}}value-type": "string"})
                paragraph = ET.SubElement(cell, f"{{{_TEXT}}}p")
                if value is not None:
                    paragraph.text = value
    with ZipFile(path, "w") as package:
        package.writestr("mimetype", "application/vnd.oasis.opendocument.spreadsheet")
        package.writestr("content.xml", ET.tostring(root, encoding="unicode"))


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
        self.assertEqual(tool["rows"][0]["fields"]["toolshop_remarks"], "Flat")
        self.assertEqual(tool["rows"][0]["sourceHeaders"]["toolshop_remarks"], "Remarks")
        self.assertEqual(tool["rows"][0]["sourceHeaderCells"]["toolshop_remarks"], "G9")
        self.assertEqual(tool["rows"][0]["sourceCells"]["toolshop_remarks"]["cell"], "G10")
        self.assertEqual(tool["rows"][0]["fields"]["toolshop_part_name"], "Flat")
        self.assertEqual(cnc["description"], "Bracket")
        self.assertEqual(cnc["labelField"], "part_category")
        self.assertEqual(cnc["rows"][0]["fields"]["product_part_name"], "Plate Flange")
        self.assertEqual(cnc["rows"][0]["sourceHeaders"]["product_part_name"], "Plate Part to Cut")
        self.assertEqual(cnc["rows"][0]["sourceHeaderCells"]["product_part_name"], "A8")
        self.assertEqual(cnc["rows"][0]["fields"]["length"], "120")
        self.assertEqual(diagnostics["CNC Cut List"]["labelHeader"], "Part Category")
        self.assertEqual(diagnostics["CNC Cut List"]["labelHeaderCell"], "B8")

    def test_in_use_filter_and_semantic_source_evidence_are_preserved_for_all_part_sheets(self):
        flags = ["Yes", "No", None, "Maybe"]
        source_rows = {
            "5. Material Cut List Price": {row: [f"MCL part {row}", "MS Plate", "3 x 25", "1", "HF", flag]
                for row, flag in zip(range(9, 13), flags)},
            "Tool Shop Items": {row: [f"Tool part {row}", f"Item {row}", "Steel", "20", "1", flag]
                for row, flag in zip(range(10, 14), flags)},
            "CNC Cut List": {row: [f"Plate {row}", f"CNC part {row}", "MS", "120", "30", "3", "1", flag]
                for row, flag in zip(range(9, 13), flags)},
        }
        document = OdsDocument(Path("in-use-status.ods"), {
            "5. Material Cut List Price": sheet("5. Material Cut List Price", 8,
                ["Machine Piece Description", "Material to Cut", "Dimension to Cut (mm)", "Quantity Nos", "Optional Item Group 1", "In Use"],
                source_rows["5. Material Cut List Price"]),
            "Tool Shop Items": sheet("Tool Shop Items", 9,
                ["Machine Piece Description", "Item Name", "Material Used", "Dimension in mm", "Qty", "In Use"],
                source_rows["Tool Shop Items"]),
            "CNC Cut List": sheet("CNC Cut List", 8,
                ["Plate Part to Cut", "Part Category", "Material used", "Length", "Width", "Thickness", "Qty Of Part Used", "In Use"],
                source_rows["CNC Cut List"]),
        })

        lines = _configured_process_lines(document)
        groups = source_groups(lines)
        expected_statuses = ["active", "historical", "active", "unexpected"]
        for name, first_row in (("5. Material Cut List Price", 9), ("Tool Shop Items", 10), ("CNC Cut List", 9)):
            with self.subTest(sheet=name):
                extracted = lines[name]
                self.assertEqual([row["status"] for row in extracted], expected_statuses)
                for row, expected_flag in zip(extracted, flags):
                    self.assertEqual(row["fields"]["in_use"], expected_flag)
                    self.assertEqual(row["source_headers"]["in_use"], "In Use")
                    self.assertIn("in_use", row["available_fields"])
                    self.assertEqual(row["source_cells"]["in_use"]["cell"], f"F{row['source_row']}" if name != "CNC Cut List" else f"H{row['source_row']}")

                retained = [row for group in groups if group["sheet"] == name for row in group["rows"]]
                self.assertEqual(sorted(row["row"] for row in retained), [first_row, first_row + 2])
                semantic_by_row = {row["row"]: group["semanticEvidence"][row["row"]]
                    for group in groups if group["sheet"] == name for row in group["rows"]}
                for source_row, source_state, display in ((first_row, "value", "Yes"), (first_row + 2, "blank", None)):
                    evidence = next(field for field in semantic_by_row[source_row]["fields"] if field["field"] == "in_use")
                    self.assertEqual(evidence["sourceState"], source_state)
                    self.assertEqual(evidence["value"], "active")
                    self.assertEqual(evidence["display"], display)

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

    def test_real_ods_mapping_api_keeps_line_remarks_plate_text_and_header_status(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "source-fields.ods"
            tool_headers = ["Machine Piece Description", "Item Name", "Material Used", "Dimension in mm", "Qty", "In Use", "Remarks", "Job Remarks"]
            cnc_headers = ["Plate Part to Cut", "Part Category", "Material used", "Length", "Width", "Thickness", "Qty Of Part Used", "In Use"]
            write_ods(path, {
                "5. Material Cut List Price": (["Machine Piece Description", "Material to Cut", "Dimension to Cut (mm)", "Quantity Nos", "Optional Item Group 1"],
                    [["Frame", "MS", "3 x 25", "2", "HF"]]),
                "Tool Shop Items": (tool_headers, [
                    ["Weld fixture", "Fixture", "Steel", "80", "1", "Yes", "  Deburr edges; keep finish  ", "Job note only"],
                    ["Inspection jig", "Gauge", "Steel", "20", "1", "Yes", "", "Separate job note"],
                ]),
                "CNC Cut List": (cnc_headers, [
                    ["Plate Alpha", "Bracket", "MS", "120", "60", "3", "4", "Yes"],
                    ["Plate Beta", "Bracket", "MS", "130", "60", "3", "2", "Yes"],
                ]),
            })
            source_hash = sha256_file(path)
            association = SimpleNamespace(id="association:source-field-api", version=4)
            repository = SimpleNamespace(part_store=MemoryPartStore())
            with mock.patch.object(web, "_part_registry", return_value=object()), \
                 mock.patch.object(web, "_processing_context", return_value=(repository, "file:source-fields.ods", association, source_hash)), \
                 mock.patch.object(web, "_resolve_preview_workbook", return_value=path), \
                 mock.patch.object(web, "_part_mapping_store", return_value=repository.part_store):
                payload = web.part_mappings("source-fields.ods")

            tool_rows = [row for group in payload["groups"] if group["sheet"] == "Tool Shop Items" for row in group["rows"]]
            tool_by_row = {row["row"]: row for row in tool_rows}
            self.assertEqual(tool_by_row[2]["fields"]["toolshop_remarks"], "  Deburr edges; keep finish  ")
            self.assertEqual(tool_by_row[2]["fields"]["job_remarks"], "Job note only")
            self.assertEqual(tool_by_row[2]["sourceHeaders"]["toolshop_remarks"], "Remarks")
            self.assertEqual(tool_by_row[2]["sourceHeaderCells"]["toolshop_remarks"], "G1")
            self.assertEqual(tool_by_row[2]["sourceCells"]["toolshop_remarks"]["cell"], "G2")
            self.assertEqual(tool_by_row[3]["fields"]["toolshop_remarks"], "")
            self.assertIn("toolshop_remarks", tool_by_row[3]["availableFields"])

            cnc = next(group for group in payload["groups"] if group["sheet"] == "CNC Cut List")
            self.assertEqual(cnc["labelField"], "part_category")
            self.assertEqual(cnc["description"], "Bracket")
            self.assertEqual([row["fields"]["product_part_name"] for row in cnc["rows"]], ["Plate Alpha", "Plate Beta"])
            self.assertEqual([row["sourceCells"]["product_part_name"]["cell"] for row in cnc["rows"]], ["A2", "A3"])
            self.assertEqual(cnc["rows"][1]["sourceHeaderCells"]["product_part_name"], "A1")

            missing_path = Path(directory) / "missing-remarks.ods"
            write_ods(missing_path, {"Tool Shop Items": (
                ["Machine Piece Description", "Item Name", "Material Used", "Dimension in mm", "Qty", "In Use", "Job Remarks"],
                [["No remarks column", "Tool", "Steel", "10", "1", "Yes", "Still separate"]],
            )})
            missing_document = __import__("app.milestone2", fromlist=["read_ods"]).read_ods(missing_path)
            missing_lines = _configured_process_lines(missing_document)
            missing_group = source_groups(missing_lines)[0]
            missing_row = missing_group["rows"][0]
            self.assertNotIn("toolshop_remarks", missing_row["availableFields"])
            self.assertNotIn("toolshop_remarks", missing_row["fields"])
            self.assertEqual(missing_row["fields"]["job_remarks"], "Still separate")


if __name__ == "__main__":
    unittest.main()

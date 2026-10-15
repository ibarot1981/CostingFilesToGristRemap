"""Create a synthetic three-family ODS workbook for isolated browser QA."""
from __future__ import annotations

import argparse
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZIP_STORED, ZIP_DEFLATED, ZipFile


OFFICE = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
TABLE = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"
TEXT = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
MANIFEST = "urn:oasis:names:tc:opendocument:xmlns:manifest:1.0"
MEDIA_TYPE = "application/vnd.oasis.opendocument.spreadsheet"

for prefix, uri in (("office", OFFICE), ("table", TABLE), ("text", TEXT), ("manifest", MANIFEST)):
    ET.register_namespace(prefix, uri)


SHEETS = {
    "5. Material Cut List Price": (
        ["Machine Piece Description", "Material to Cut", "Dimension to Cut (mm)", "Quantity Nos", "In Use", "Remarks"],
        [["Browser QA Plate", "MS Plate", "3 x 25", "2", "Yes", "Synthetic fixture"]],
    ),
    "Tool Shop Items": (
        ["Machine Piece Description", "Item Name", "Material Used", "Dimension in mm", "Qty", "In Use", "Remarks", "Job Remarks", "CR Log"],
        [["Browser QA Fixture", "Fixture bracket", "Steel", "80", "1", "Yes", "", "Synthetic fixture", "QA-ONLY"]],
    ),
    "CNC Cut List": (
        ["Plate Part to Cut", "Part Category", "Material used", "Length", "Width", "Thickness", "Qty Of Part Used", "In Use"],
        [["Browser QA Plate", "Browser QA Bracket", "MS", "120", "60", "3", "4", "Yes"]],
    ),
}


def _content_xml() -> bytes:
    root = ET.Element(f"{{{OFFICE}}}document-content", {f"{{{OFFICE}}}version": "1.2"})
    body = ET.SubElement(root, f"{{{OFFICE}}}body")
    spreadsheet = ET.SubElement(body, f"{{{OFFICE}}}spreadsheet")
    for name, (headers, rows) in SHEETS.items():
        table = ET.SubElement(spreadsheet, f"{{{TABLE}}}table", {f"{{{TABLE}}}name": name})
        for values in (headers, *rows):
            row = ET.SubElement(table, f"{{{TABLE}}}table-row")
            for value in values:
                cell = ET.SubElement(row, f"{{{TABLE}}}table-cell", {f"{{{OFFICE}}}value-type": "string"})
                paragraph = ET.SubElement(cell, f"{{{TEXT}}}p")
                if value:
                    paragraph.text = value
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _manifest_xml() -> bytes:
    root = ET.Element(f"{{{MANIFEST}}}manifest", {f"{{{MANIFEST}}}version": "1.2"})
    ET.SubElement(root, f"{{{MANIFEST}}}file-entry", {
        f"{{{MANIFEST}}}full-path": "/", f"{{{MANIFEST}}}version": "1.2", f"{{{MANIFEST}}}media-type": MEDIA_TYPE,
    })
    ET.SubElement(root, f"{{{MANIFEST}}}file-entry", {
        f"{{{MANIFEST}}}full-path": "content.xml", f"{{{MANIFEST}}}media-type": "text/xml",
    })
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path, help="Temporary COSTING_ROOT for an isolated browser run.")
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    output = root / "Browser-QA.ods"
    if output.exists():
        raise SystemExit(f"Refusing to overwrite an existing fixture: {output}")
    with ZipFile(output, "w") as package:
        package.writestr("mimetype", MEDIA_TYPE, compress_type=ZIP_STORED)
        package.writestr("content.xml", _content_xml(), compress_type=ZIP_DEFLATED)
        package.writestr("META-INF/manifest.xml", _manifest_xml(), compress_type=ZIP_DEFLATED)
    print(output)


if __name__ == "__main__":
    main()

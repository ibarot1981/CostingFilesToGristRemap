import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from app.catalog import scan_costing_file, scan_costing_root


def _ods(path: Path, content: str) -> None:
    with ZipFile(path, "w") as package:
        package.writestr("mimetype", "application/vnd.oasis.opendocument.spreadsheet")
        package.writestr("content.xml", content)


class CatalogTests(unittest.TestCase):
    def test_scan_costing_root_finds_sheets_and_external_formulas(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _ods(
                root / "model.ods",
                '<table:table table:name="Summary">'
                '<table:table-cell table:formula="of:=SUM([.A1:.A2])"/>'
                '<table:table-cell table:formula="of:=\'file:///C:/rates.ods\'#$Sheet1.A1"/>'
                "</table:table>",
            )

            summary, files = scan_costing_root(root, workers=1)

            self.assertEqual(summary.file_count, 1)
            self.assertEqual(summary.formula_count, 2)
            self.assertEqual(summary.files_with_external_references, 1)
            self.assertEqual(files[0].sheets, ("Summary",))
            self.assertEqual(files[0].external_reference_count, 1)

    def test_unreadable_ods_is_reported_not_raised(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            broken = root / "broken.ods"
            broken.write_text("not a zip file", encoding="utf-8")

            result = scan_costing_file(broken, root)

            self.assertFalse(result.readable)
            self.assertIn("BadZipFile", result.error or "")


if __name__ == "__main__":
    unittest.main()

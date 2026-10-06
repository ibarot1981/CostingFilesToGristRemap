import unittest

from app.material_resolution import resolve_material


class MaterialResolutionGoldenTests(unittest.TestCase):
    def test_cli_exact_mapping_cases(self):
        # Existing CLI uses a case-sensitive direct YAML lookup. These are
        # frozen examples of that behavior for the extracted pilot seam.
        mapping = {"ODS Steel": "Master Steel", "Bar 12": "Bar 12"}
        cases = [
            ("ODS Steel", "Master Steel", "reviewed_alias"),
            ("Bar 12", "Bar 12", "reviewed_alias"),
            ("ods steel", None, "unmatched"),
            ("Unknown", None, "unmatched"),
        ]
        for source, expected, status in cases:
            with self.subTest(source=source):
                match = resolve_material(source, mapping)
                self.assertEqual((match.canonical_name, match.status), (expected, status))

    def test_alternate_is_proposal_not_automatic_approval(self):
        match = resolve_material("Alt 12", {}, alternates={"alt 12": "Bar 12"})
        self.assertEqual(match.status, "alternate_size_proposal")
        self.assertEqual(match.canonical_name, "Bar 12")


if __name__ == "__main__":
    unittest.main()

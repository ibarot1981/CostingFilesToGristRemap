import tempfile
import unittest
from pathlib import Path

from app.catalog_grist import sync_catalog_to_grist
from app.catalog_import import import_catalog_rows
from app.exceptions import GristDuplicateNameError, GristValidationError


class FakeCatalogGristClient:
    doc_id = "safari-doc"

    def __init__(self):
        self.tables = {name: [] for name in ("Product", "ProductModel", "ProductModelCode", "IdentityAlias", "ReconciliationIssue", "ImportBatch")}
        self.next_id = 1
        self.writes = []
        self.target_checks = 0

    def validate_safari_write_target(self):
        self.target_checks += 1

    def fetch_table_records_with_ids(self, table_id):
        return list(self.tables[table_id])

    def create_table_records(self, table_id, records):
        created = []
        for record in records:
            saved = {"id": self.next_id, "fields": dict(record["fields"])}
            self.next_id += 1
            self.tables[table_id].append(saved)
            self.writes.append(("create", table_id, saved["id"]))
            created.append(saved)
        return created

    def update_table_records(self, table_id, updates):
        for update in updates:
            record = next(item for item in self.tables[table_id] if item["id"] == update["id"])
            record["fields"].update(update["fields"])
            self.writes.append(("update", table_id, update["id"]))


class CatalogGristTests(unittest.TestCase):
    def test_dry_run_apply_retry_and_changed_source_are_upserted(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "catalog.ods"
            source.write_bytes(b"catalog version 1")
            result = import_catalog_rows([
                ["Product", "Model", "Code", "Description"],
                ["Product One", "GC", "GCMC", "7.5 ton"],
                ["Product One", "GC", "GCMC", "10 ton"],
            ], source_file=str(source))
            client = FakeCatalogGristClient()

            plan = sync_catalog_to_grist(client, result)
            self.assertEqual(plan.creates, {"Product": 1, "ProductModel": 1, "ProductModelCode": 2, "IdentityAlias": 2, "ImportBatch": 1})
            self.assertEqual(client.writes, [])

            applied = sync_catalog_to_grist(client, result, apply=True, expected_plan=plan)
            self.assertFalse(applied.idempotent)
            self.assertEqual(len(client.tables["Product"]), 1)
            self.assertEqual(len(client.tables["ProductModel"]), 1)
            self.assertEqual(len(client.tables["ProductModelCode"]), 2)
            self.assertEqual(client.tables["ProductModel"][0]["fields"]["Product"], client.tables["Product"][0]["id"])
            self.assertEqual(
                client.tables["ProductModelCode"][0]["fields"]["SourceValues"],
                ["L", *result.codes[0].source_values],
            )
            batch_fields = client.tables["ImportBatch"][0]["fields"]
            self.assertIsInstance(batch_fields["StartedAt"], float)
            self.assertIsInstance(batch_fields["CompletedAt"], float)
            writes_after_apply = list(client.writes)

            retry = sync_catalog_to_grist(client, result, apply=True)
            self.assertTrue(retry.idempotent)
            self.assertEqual(client.writes, writes_after_apply)

            source.write_bytes(b"catalog version 2")
            changed = import_catalog_rows([
                ["Product", "Model", "Code", "Description"],
                ["Product One", "GC", "GCMC", "7.5 ton revised"],
                ["Product One", "GC", "GCMC", "10 ton"],
            ], source_file=str(source))
            changed_plan = sync_catalog_to_grist(client, changed)
            self.assertEqual(changed_plan.updates, {"ProductModelCode": 1})
            self.assertTrue(any(change["action"] == "update" for change in changed_plan.changes))
            sync_catalog_to_grist(client, changed, apply=True, expected_plan=changed_plan)
            self.assertEqual(len(client.tables["ProductModelCode"]), 2)
            self.assertEqual(client.tables["ProductModelCode"][0]["fields"]["Description"], "7.5 ton revised")

    def test_refuses_duplicate_existing_canonical_records(self):
        client = FakeCatalogGristClient()
        client.tables["Product"] = [
            {"id": 1, "fields": {"Name": "Product One"}},
            {"id": 2, "fields": {"Name": "product one"}},
        ]
        result = import_catalog_rows([["Product", "Model", "Code"], ["Product One", "M1", "C1"]])
        with self.assertRaises(GristDuplicateNameError):
            sync_catalog_to_grist(client, result)

    def test_duplicate_code_is_planned_and_persisted_inactive(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "catalog.ods"
            source.write_bytes(b"duplicate code catalog")
            result = import_catalog_rows([
                ["Product", "Model", "Code", "Description"],
                ["Product One", "M1", "C1", "first"],
                ["Product One", "M1", "C1", "duplicate"],
            ], source_file=str(source))
            client = FakeCatalogGristClient()

            plan = sync_catalog_to_grist(client, result)
            self.assertEqual(plan.creates["ReconciliationIssue"], 1)
            self.assertFalse(result.codes[0].active)
            self.assertEqual(client.writes, [])

            sync_catalog_to_grist(client, result, apply=True, expected_plan=plan)

            self.assertFalse(client.tables["ProductModelCode"][0]["fields"]["Active"])
            issue = client.tables["ReconciliationIssue"][0]["fields"]
            self.assertEqual(issue["IssueType"], "duplicate_catalog_code")
            self.assertEqual(issue["Severity"], "error")

    def test_rejects_apply_if_reviewed_plan_is_stale(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "catalog.ods"
            source.write_bytes(b"version")
            result = import_catalog_rows([["Product", "Model", "Code"], ["Product One", "M1", "C1"]], source_file=str(source))
            client = FakeCatalogGristClient()
            plan = sync_catalog_to_grist(client, result)
            client.tables["Product"].append({"id": 99, "fields": {"Name": "Product One"}})
            with self.assertRaises(GristValidationError):
                sync_catalog_to_grist(client, result, apply=True, expected_plan=plan)
            self.assertEqual(client.writes, [])


if __name__ == "__main__":
    unittest.main()

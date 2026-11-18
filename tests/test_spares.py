from __future__ import annotations

import copy
from datetime import date, timedelta
from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
import tempfile
from threading import Barrier, Lock
import unittest
from pathlib import Path
from unittest.mock import patch

from app.schema import FOUNDATION_TABLES
from app.spare_import import _stable_key, apply_import_plan, build_import_plan, preview_payload
from app.spares import GristSpareRegistry, SpareRegistryError, SPARE_TABLE_IDS, parse_source_date, source_cost_age
from app.part_identity import request_fingerprint
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
        self.lose_response_once_on: str | None = None
        self.lose_response_once_on_update: tuple[str, str, object] | None = None
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
        if self.lose_response_once_on == table:
            self.lose_response_once_on = None
            raise RuntimeError(f"injected lost response after durable write at {table}")

    def update_table_records(self, table: str, records: list[dict]):
        saved = self.tables.setdefault(table, [])
        by_id = {row["id"]: row for row in saved}
        lost = False
        for record in records:
            by_id[int(record["id"])]["fields"].update(record["fields"])
            rule = self.lose_response_once_on_update
            if rule and rule[0] == table and record.get("fields", {}).get(rule[1]) == rule[2]:
                self.lose_response_once_on_update = None
                lost = True
        if lost:
            raise RuntimeError(f"injected lost response after durable update at {table}")


def _cell(row: int, column: int, value=None, *, formula=None, value_type=None, display="") -> OdsCell:
    return OdsCell(row, column, formula, value_type, value, display or (str(value) if value is not None else ""), {})


def _registry(directory: str, grist: _Grist) -> GristSpareRegistry:
    return GristSpareRegistry(grist, journal_path=Path(directory) / "coordination.sqlite3")


def _recovery_plan(root: Path, registry: GristSpareRegistry):
    product_path = root / "Interrupted.ods"
    template_path = root / "Template.ods"
    product_path.write_bytes(b"F3 import recovery fixture v1")
    template_path.write_bytes(b"F3 template recovery fixture v1")
    product = OdsDocument(product_path, {
        "SparesDB": OdsSheet("SparesDB", {8: {1: _cell(8, 1, "F3-ALIAS"), 2: _cell(8, 2, "Washer"), 3: _cell(8, 3, "Imported washer"), 4: _cell(8, 4, "EA"), 5: _cell(8, 5, 12.5)}}),
        "Spares Detail": OdsSheet("Spares Detail", {}),
        "Spares Summary": OdsSheet("Spares Summary", {}),
    })
    template = OdsDocument(template_path, {"Master": OdsSheet("Master", {2: {1: _cell(2, 1, "F3-ALIAS"), 4: _cell(2, 4, "EA"), 8: _cell(2, 8, "Store")}})})
    with patch("app.spare_import.read_ods", side_effect=lambda candidate, _sheets: product if Path(candidate) == product_path else template):
        plan = build_import_plan(product_path=product_path, template_path=template_path, registry=registry, costing_root=root, association_key="f3-import-recovery-test")
    return product_path, plan


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

    def test_completed_create_replay_preserves_creation_history_after_edit_and_retirement(self):
        request = {"description": "Original washer", "category": "Washer", "unit": "EA", "actor": "creator", "reason": "initial review", "request_key": "create-edit-retire-replay"}
        created = self.registry.create_spare(**request)
        original_history = copy.deepcopy(self.grist.tables["SpareHistory"])
        original_snapshot = json.loads(original_history[0]["fields"]["Snapshot"])

        updated = self.registry.update_spare(spare_key=created["spareKey"], expected_version=1, updates={"description": "Corrected washer"}, actor="editor", reason="description correction", request_key="update-before-create-replay")
        history_after_edit = copy.deepcopy(self.grist.tables["SpareHistory"])
        replay_after_edit = self.registry.create_spare(**request)
        self.assertEqual((replay_after_edit["idempotent"], replay_after_edit["version"], replay_after_edit["description"]), (True, 2, "Corrected washer"))
        self.assertEqual(self.grist.tables["SpareHistory"], history_after_edit)
        self.assertEqual(json.loads(self.grist.tables["SpareHistory"][0]["fields"]["Snapshot"]), original_snapshot)
        self.assertEqual(updated["history"][0]["snapshot"], original_snapshot)

        retired = self.registry.update_spare(spare_key=created["spareKey"], expected_version=2, updates={}, actor="retirer", reason="replacement", request_key="retire-before-create-replay", retire=True)
        history_after_retirement = copy.deepcopy(self.grist.tables["SpareHistory"])
        replay_after_retirement = self.registry.create_spare(**request)
        self.assertEqual((retired["version"], replay_after_retirement["status"], replay_after_retirement["version"], replay_after_retirement["idempotent"]), (3, "retired", 3, True))
        self.assertEqual(self.grist.tables["SpareHistory"], history_after_retirement)
        self.assertEqual([json.loads(row["fields"]["Snapshot"])["status"] for row in self.grist.tables["SpareHistory"]], ["active", "active", "retired"])

    def test_missing_creation_history_repairs_from_saved_original_snapshot_on_fresh_adapter(self):
        request = {"description": "Original repair washer", "category": "Washer", "unit": "EA", "actor": "creator", "reason": "initial review", "request_key": "repair-original-create-history"}
        created = self.registry.create_spare(**request)
        original = self.registry.create_request_record(request["request_key"])
        original_result = json.loads(original["fields"]["Result"])
        evidence = original_result["creationEvidence"]
        self.registry.update_spare(spare_key=created["spareKey"], expected_version=1, updates={"description": "Edited repair washer"}, actor="editor", reason="corrected", request_key="repair-update")
        retired = self.registry.update_spare(spare_key=created["spareKey"], expected_version=2, updates={}, actor="retirer", reason="retired", request_key="repair-retire", retire=True)
        later_history = copy.deepcopy([row for row in self.grist.tables["SpareHistory"] if row["fields"]["HistoryKey"] != f"{created['spareKey']}:v1"])
        self.grist.tables["SpareHistory"] = later_history

        fresh_grist = _Grist()
        fresh_grist.tables = copy.deepcopy(self.grist.tables)
        fresh_grist.next_id = self.grist.next_id
        with tempfile.TemporaryDirectory() as fresh_directory:
            fresh_registry = _registry(fresh_directory, fresh_grist)
            replay = fresh_registry.create_spare(**request)
            journal_path = Path(fresh_directory) / "coordination.sqlite3"
            db = sqlite3.connect(journal_path)
            try:
                reservation_count = db.execute("SELECT COUNT(*) FROM spare_registry_reservations").fetchone()[0]
                alias_reservation_count = db.execute("SELECT COUNT(*) FROM spare_registry_alias_reservations").fetchone()[0]
            finally:
                db.close()

        self.assertEqual((retired["status"], replay["status"], replay["version"], replay["idempotent"]), ("retired", "retired", 3, True))
        self.assertEqual(reservation_count, 0)
        self.assertEqual(alias_reservation_count, 0)
        repaired = next(row["fields"] for row in fresh_grist.tables["SpareHistory"] if row["fields"]["HistoryKey"] == f"{created['spareKey']}:v1")
        self.assertEqual(repaired["Version"], 1)
        self.assertEqual(repaired["EventType"], "created")
        self.assertEqual(repaired["RequestKey"], request["request_key"])
        self.assertEqual(repaired["SpareMaster"], created["recordId"])
        self.assertEqual(repaired["Actor"], evidence["actor"])
        self.assertEqual(repaired["Reason"], evidence["reason"])
        self.assertEqual(repaired["OccurredAt"], evidence["occurredAt"])
        self.assertEqual(json.loads(repaired["Snapshot"]), evidence["snapshot"])
        self.assertEqual([row for row in fresh_grist.tables["SpareHistory"] if row["fields"]["HistoryKey"] != f"{created['spareKey']}:v1"], later_history)

    def test_missing_creation_history_without_original_snapshot_fails_closed(self):
        request = {"description": "Legacy request", "actor": "creator", "reason": "initial", "request_key": "legacy-create-no-snapshot"}
        created = self.registry.create_spare(**request)
        self.registry.update_spare(spare_key=created["spareKey"], expected_version=1, updates={"description": "Current description"}, actor="editor", reason="legitimate edit", request_key="legacy-edit")
        request_row = self.registry.create_request_record(request["request_key"])
        result = json.loads(request_row["fields"]["Result"])
        result.pop("creationEvidence")
        stored_request = next(row for row in self.grist.tables["SpareMasterRequest"] if row["fields"]["RequestKey"] == request["request_key"])
        stored_request["fields"]["Result"] = json.dumps(result, sort_keys=True, separators=(",", ":"))
        self.grist.tables["SpareHistory"] = [row for row in self.grist.tables["SpareHistory"] if row["fields"]["HistoryKey"] != f"{created['spareKey']}:v1"]
        history_before = copy.deepcopy(self.grist.tables["SpareHistory"])
        aliases_before = copy.deepcopy(self.grist.tables.get("SpareAlias", []))

        with self.assertRaises(SpareRegistryError) as caught:
            self.registry.create_spare(**request)

        self.assertEqual((caught.exception.code, caught.exception.status_code), ("SPARE_WRITE_UNCONFIRMED", 503))
        self.assertIn("cannot safely reconstruct it from current metadata", str(caught.exception))
        self.assertEqual(self.grist.tables["SpareHistory"], history_before)
        self.assertEqual(self.grist.tables.get("SpareAlias", []), aliases_before)

    def test_legacy_completed_create_replay_uses_intact_version_one_history(self):
        request = {"description": "Legacy history", "actor": "creator", "reason": "initial", "request_key": "legacy-create-with-history"}
        created = self.registry.create_spare(**request)
        request_row = next(row for row in self.grist.tables["SpareMasterRequest"] if row["fields"]["RequestKey"] == request["request_key"])
        result = json.loads(request_row["fields"]["Result"])
        result.pop("creationEvidence")
        request_row["fields"]["Result"] = json.dumps(result, sort_keys=True, separators=(",", ":"))
        self.registry.update_spare(spare_key=created["spareKey"], expected_version=1, updates={"description": "Legacy history edited"}, actor="editor", reason="legitimate edit", request_key="legacy-history-edit")
        history_before = copy.deepcopy(self.grist.tables["SpareHistory"])

        replay = self.registry.create_spare(**request)

        self.assertEqual((replay["idempotent"], replay["version"], replay["description"]), (True, 2, "Legacy history edited"))
        self.assertEqual(self.grist.tables["SpareHistory"], history_before)

    def test_conflicting_original_creation_history_is_not_rewritten(self):
        request = {"description": "Protected history", "actor": "creator", "reason": "initial", "request_key": "protected-create-history"}
        created = self.registry.create_spare(**request)
        self.registry.update_spare(spare_key=created["spareKey"], expected_version=1, updates={"description": "Protected history edited"}, actor="editor", reason="legitimate edit", request_key="protected-history-edit")
        original = next(row for row in self.grist.tables["SpareHistory"] if row["fields"]["HistoryKey"] == f"{created['spareKey']}:v1")
        snapshot = json.loads(original["fields"]["Snapshot"])
        snapshot["description"] = "tampered history"
        original["fields"]["Snapshot"] = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
        history_before = copy.deepcopy(self.grist.tables["SpareHistory"])

        with self.assertRaises(SpareRegistryError) as caught:
            self.registry.create_spare(**request)

        self.assertEqual(caught.exception.code, "SPARE_WRITE_UNCONFIRMED")
        self.assertEqual(self.grist.tables["SpareHistory"], history_before)

    def test_completed_create_lost_response_replays_with_fresh_empty_journal(self):
        request = {"description": "Lost response washer", "actor": "creator", "reason": "initial", "request_key": "lost-final-create-response"}
        self.grist.lose_response_once_on_update = ("SpareMaster", "Status", "active")
        with self.assertRaisesRegex(RuntimeError, "lost response after durable update"):
            self.registry.create_spare(**request)
        self.assertEqual(self.registry.create_request_record(request["request_key"])["fields"]["Status"], "complete")
        saved_history = copy.deepcopy(self.grist.tables["SpareHistory"])

        fresh_grist = _Grist()
        fresh_grist.tables = copy.deepcopy(self.grist.tables)
        fresh_grist.next_id = self.grist.next_id
        with tempfile.TemporaryDirectory() as fresh_directory:
            fresh_registry = _registry(fresh_directory, fresh_grist)
            replay = fresh_registry.create_spare(**request)
            db = sqlite3.connect(Path(fresh_directory) / "coordination.sqlite3")
            try:
                reservation_count = db.execute("SELECT COUNT(*) FROM spare_registry_reservations").fetchone()[0]
            finally:
                db.close()

        self.assertTrue(replay["idempotent"])
        self.assertEqual(replay["status"], "active")
        self.assertEqual(len(fresh_grist.tables["SpareMaster"]), 1)
        self.assertEqual(fresh_grist.tables["SpareHistory"], saved_history)
        self.assertEqual(reservation_count, 0)

    def test_duplicate_alias_is_rejected_before_reservation_or_active_publication(self):
        first = self.registry.create_spare(description="First", actor="qa", reason="reviewed", request_key="alias-first", aliases=[{"value": "W-LEG-100"}])
        with self.assertRaises(SpareRegistryError) as caught:
            self.registry.create_spare(description="Rejected duplicate", actor="qa", reason="reviewed", request_key="alias-duplicate", aliases=[{"value": " W-LEG-100 "}])

        self.assertEqual((caught.exception.code, caught.exception.status_code, caught.exception.retry_disposition), ("SPARE_ALIAS_CONFLICT", 409, "safe_to_edit"))
        self.assertEqual([(item["spareCode"], item["status"]) for item in self.registry.list_spares(status="active")["items"]], [("SM-S-000001", "active")])
        self.assertEqual(len(self.grist.tables["SpareMaster"]), 1)
        self.assertFalse(any(row["fields"].get("RequestKey") == "alias-duplicate" for row in self.grist.tables.get("SpareMasterRequest", [])))

        second = self.registry.create_spare(description="Second", actor="qa", reason="reviewed", request_key="alias-second")
        self.assertEqual(second["spareCode"], "SM-S-000002")
        self.assertEqual(first["legacyAliases"], ["W-LEG-100"])

    def test_partial_create_stays_hidden_and_recovers_same_request_after_lost_alias_response(self):
        self.grist.lose_response_once_on = "SpareAlias"
        with self.assertRaisesRegex(RuntimeError, "lost response after durable write"):
            self.registry.create_spare(description="Recoverable", actor="qa", reason="reviewed", request_key="lost-alias-response", aliases=[{"value": "RECOVER-1"}])

        master = self.grist.tables["SpareMaster"][0]["fields"]
        self.assertEqual(master["Status"], "pending")
        self.assertEqual(self.registry.list_spares(status="active")["total"], 0)
        self.assertEqual(self.registry._find(self.grist.tables["SpareMasterRequest"], "RequestKey", "lost-alias-response")["fields"]["Status"], "started")

        recovered = self.registry.create_spare(description="Recoverable", actor="qa", reason="reviewed", request_key="lost-alias-response", aliases=[{"value": "RECOVER-1"}])
        replay = self.registry.create_spare(description="Recoverable", actor="qa", reason="reviewed", request_key="lost-alias-response", aliases=[{"value": "RECOVER-1"}])
        self.assertEqual((recovered["spareCode"], recovered["status"], replay["idempotent"]), ("SM-S-000001", "active", True))
        self.assertEqual((len(self.grist.tables["SpareMaster"]), len(self.grist.tables["SpareAlias"]), len(self.grist.tables["SpareHistory"])), (1, 1, 1))

    def test_existing_rejected_duplicate_orphan_is_retired_with_auditable_history(self):
        self.registry.create_spare(description="Existing", actor="qa", reason="reviewed", request_key="orphan-owner", aliases=[{"value": "W-LEG-100"}])
        request_key = "reviewer-partial-create"
        description = "Reviewer rejected duplicate alias probe"
        alias = "W-LEG-100"
        fingerprint = request_fingerprint({"type": "create", "description": description, "category": "", "unit": "", "sourcingType": "unclassified", "aliases": [{"value": alias, "provenance": {}}], "sourceProvenance": {}})
        spare_key, spare_code = self.registry._reserve_code(request_key, fingerprint)
        self.registry._ensure_request(request_key=request_key, request_type="create", fingerprint=fingerprint, spare_key=spare_key, spare_code=spare_code, actor="qa", reason="Isolated duplicate check")
        fields = {"SpareKey": spare_key, "SpareCode": spare_code, "Description": description, "Category": "", "Unit": "", "SourcingType": "unclassified", "Status": "active", "CreatedAt": "2026-11-17T00:00:00Z", "CreatedBy": "qa", "UpdatedAt": "2026-11-17T00:00:00Z", "UpdatedBy": "qa", "Version": 1, "SourceProvenance": "{}", "CreateRequestKey": request_key, "CreateFingerprint": fingerprint}
        self.grist.create_table_records("SpareMaster", [{"fields": fields}])

        with self.assertRaises(SpareRegistryError) as caught:
            self.registry.create_spare(description=description, actor="qa", reason="Isolated duplicate check", request_key=request_key, aliases=[{"value": alias}])

        self.assertEqual((caught.exception.code, caught.exception.retry_disposition), ("SPARE_ALIAS_CONFLICT", "safe_to_edit"))
        orphan_row = next(row for row in self.grist.tables["SpareMaster"] if row["fields"]["SpareCode"] == "SM-S-000002")
        orphan = orphan_row["fields"]
        self.assertEqual((orphan["Status"], orphan["Version"]), ("retired", 2))
        rejection = next(row["fields"] for row in self.grist.tables["SpareHistory"] if row["fields"]["SpareMaster"] == orphan_row["id"])
        self.assertEqual(rejection["EventType"], "creation_rejected")
        self.assertTrue(rejection["Reason"].startswith("Create rejected: Legacy code"))
        rejected_request = next(row["fields"] for row in self.grist.tables["SpareMasterRequest"] if row["fields"]["RequestKey"] == request_key)
        self.assertEqual(rejected_request["Status"], "rejected")
        self.assertEqual(self.registry.list_spares(status="active")["total"], 1)
        next_spare = self.registry.create_spare(description="Next number", actor="qa", reason="reviewed", request_key="after-orphan")
        self.assertEqual(next_spare["spareCode"], "SM-S-000003")

    def test_concurrent_create_alias_reservations_allow_only_one_identity(self):
        barrier = Barrier(2)
        with tempfile.TemporaryDirectory() as directory:
            first_registry = _registry(directory, self.grist)
            second_registry = _registry(directory, self.grist)
            for registry in (first_registry, second_registry):
                original = registry.identity_index
                gate = {"first": True}
                gate_lock = Lock()
                def synchronized(original=original, gate=gate, gate_lock=gate_lock):
                    with gate_lock:
                        wait = gate["first"]
                        gate["first"] = False
                    result = original()
                    if wait:
                        barrier.wait(timeout=5)
                    return result
                registry.identity_index = synchronized

            def create(registry, key):
                try:
                    return registry.create_spare(description=key, actor="qa", reason="race", request_key=key, aliases=[{"value": "RACE-ALIAS"}])
                except SpareRegistryError as exc:
                    return exc

            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda args: create(*args), [(first_registry, "race-a"), (second_registry, "race-b")]))

        self.assertEqual(sum(not isinstance(result, SpareRegistryError) for result in results), 1)
        conflict = next(result for result in results if isinstance(result, SpareRegistryError))
        self.assertEqual((conflict.code, conflict.retry_disposition), ("SPARE_ALIAS_CONFLICT", "safe_to_edit"))
        self.assertEqual(self.registry.list_spares(status="active")["total"], 1)
        self.assertEqual(len(self.grist.tables["SpareAlias"]), 1)
        self.assertEqual(len(self.grist.tables["SpareHistory"]), 1)

    def test_identical_ods_bytes_at_distinct_paths_keep_separate_import_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path_a, path_b, template_path = root / "A.ods", root / "B.ods", root / "Template.ods"
            path_a.write_bytes(b"identical associated workbook bytes")
            path_b.write_bytes(path_a.read_bytes())
            template_path.write_bytes(b"shared template workbook bytes")
            product = OdsDocument(path_a, {
                "SparesDB": OdsSheet("SparesDB", {8: {1: _cell(8, 1, "COPY-1"), 3: _cell(8, 3, "Copy washer"), 4: _cell(8, 4, "EA"), 5: _cell(8, 5, 10.0)}}),
                "Spares Detail": OdsSheet("Spares Detail", {9: {2: _cell(9, 2, "COPY-1"), 4: _cell(9, 4, "MCL"), 5: _cell(9, 5, "MCL-7"), 8: _cell(9, 8, 2), 10: _cell(9, 10, 3)}}),
                "Spares Summary": OdsSheet("Spares Summary", {}),
            })
            template = OdsDocument(template_path, {"Master": OdsSheet("Master", {2: {1: _cell(2, 1, "COPY-1"), 4: _cell(2, 4, "EA"), 8: _cell(2, 8, "Store")}})})

            def build(path: Path, association: str):
                with patch("app.spare_import.read_ods", side_effect=lambda candidate, _sheets: template if Path(candidate) == template_path else product):
                    return build_import_plan(product_path=path, template_path=template_path, registry=self.registry, costing_root=root, association_key=association)

            plan_a = build(path_a, "association-A")
            first = apply_import_plan(plan=plan_a, registry=self.registry, actor="qa", request_key="copy-import-A")
            plan_b = build(path_b, "association-B")
            preview_b = preview_payload(plan_b)

            self.assertEqual(path_a.read_bytes(), path_b.read_bytes())
            self.assertEqual(plan_b["identities"][0]["action"], "noop")
            self.assertEqual(plan_a["sourceHashes"]["associatedWorkbook"], plan_b["sourceHashes"]["associatedWorkbook"])
            self.assertNotEqual(plan_a["associationKey"], plan_b["associationKey"])
            self.assertNotEqual(plan_a["previewFingerprint"], plan_b["previewFingerprint"])
            self.assertNotEqual(next(row["key"] for row in plan_a["sourceEvidence"] if row["sourceKind"] == "SparesDB"), next(row["key"] for row in plan_b["sourceEvidence"] if row["sourceKind"] == "SparesDB"))
            self.assertNotEqual(plan_a["costEvidence"][0]["key"], plan_b["costEvidence"][0]["key"])
            self.assertNotEqual(plan_a["compositions"][0]["key"], plan_b["compositions"][0]["key"])
            self.assertFalse(any(key.startswith("_") for row in preview_b["costEvidenceRows"] + preview_b["compositionEvidenceRows"] for key in row))

            second = apply_import_plan(plan=plan_b, registry=self.registry, actor="qa", request_key="copy-import-B")
            repeat = apply_import_plan(plan=plan_b, registry=self.registry, actor="qa", request_key="copy-import-B")
            self.assertEqual(first["created"], {"sourceEvidence": 2, "costEvidence": 1, "compositionEvidence": 1})
            self.assertEqual(second["created"], {"sourceEvidence": 1, "costEvidence": 1, "compositionEvidence": 1})
            self.assertTrue(repeat["idempotent"])
            self.assertEqual({row["fields"]["SourceFile"] for row in self.grist.tables["SpareAlias"] if row["fields"].get("SourceKind") == "SparesDB"}, {"A.ods", "B.ods"})
            self.assertEqual({row["fields"]["SourceFile"] for row in self.grist.tables["SpareSourceEvidence"] if row["fields"].get("SourceKind") == "SparesDB"}, {"A.ods", "B.ods"})
            self.assertEqual({row["fields"]["SourceReference"].split("#", 1)[0] for row in self.grist.tables["SpareSourceCost"]}, {"A.ods", "B.ods"})
            self.assertEqual({row["fields"]["SourceFile"] for row in self.grist.tables["SpareCompositionEvidence"]}, {"A.ods", "B.ods"})

    def test_legacy_evidence_keys_are_read_idempotently_by_source_occurrence(self):
        source_file = "A.ods"
        source_hash = "same-content-hash"
        source_rows = [{"EvidenceKey": "src:legacy-content-key", "SourceFile": source_file, "SourceHash": source_hash, "SourceKind": "SparesDB", "SheetName": "SparesDB", "SourceRow": 8, "SourceCell": "A8", "EvidenceFingerprint": "source-fingerprint"}]
        self.grist.tables["SpareSourceEvidence"] = [{"id": 101, "fields": dict(source_rows[0])}]
        source_incoming = [{**source_rows[0], "EvidenceKey": "src:path-specific-key"}]
        result = self.registry.ensure_rows("SpareSourceEvidence", "EvidenceKey", source_incoming)
        self.assertEqual(result, {"created": 0, "existing": 1})
        self.assertEqual(len(self.grist.tables["SpareSourceEvidence"]), 1)
        self.assertEqual(self.grist.tables["SpareSourceEvidence"][0]["fields"]["EvidenceKey"], "src:legacy-content-key")

        cost_rows = [{"CostEvidenceKey": "cost:legacy-key", "SourceReference": "A.ods#SparesDB.E8", "SourceHash": source_hash, "SheetName": "SparesDB", "SourceRow": 8, "SourceCell": "E8", "CostKind": "source_purchase_price", "EvidenceFingerprint": "legacy-cost-fingerprint"}]
        self.grist.tables["SpareSourceCost"] = [{"id": 102, "fields": dict(cost_rows[0])}]
        cost_incoming = [{**cost_rows[0], "CostEvidenceKey": "cost:path-specific-key", "EvidenceFingerprint": "new-cost-fingerprint", "_legacyEvidenceFingerprint": "legacy-cost-fingerprint"}]
        self.assertEqual(self.registry.ensure_rows("SpareSourceCost", "CostEvidenceKey", cost_incoming), {"created": 0, "existing": 1})
        self.assertEqual(len(self.grist.tables["SpareSourceCost"]), 1)

        composition_rows = [{"CompositionKey": "composition:legacy-key", "SourceFile": source_file, "SourceHash": source_hash, "SourceKind": "SparesDetail", "SheetName": "Spares Detail", "SourceRow": 9, "EvidenceFingerprint": "legacy-composition-fingerprint"}]
        self.grist.tables["SpareCompositionEvidence"] = [{"id": 103, "fields": dict(composition_rows[0])}]
        composition_incoming = [{**composition_rows[0], "CompositionKey": "composition:path-specific-key", "EvidenceFingerprint": "new-composition-fingerprint", "_legacyFingerprint": "legacy-composition-fingerprint"}]
        self.assertEqual(self.registry.ensure_rows("SpareCompositionEvidence", "CompositionKey", composition_incoming), {"created": 0, "existing": 1})
        self.assertEqual(len(self.grist.tables["SpareCompositionEvidence"]), 1)

        master = self.registry.create_spare(description="Legacy alias master", actor="qa", reason="reviewed", request_key="legacy-alias-master")
        alias = self.registry.ensure_alias(spare_key=master["spareKey"], spare_record_id=master["recordId"], value="COPY-1", provenance={"sourceKind": "SparesDB", "sourceHash": source_hash, "sheetName": "SparesDB", "sourceRow": 8, "sourceCell": "A8"}, evidence_fingerprint="alias-fingerprint")
        self.grist.tables["SpareAlias"][0]["fields"]["SourceFile"] = source_file
        reused = self.registry.ensure_alias(spare_key=master["spareKey"], spare_record_id=master["recordId"], value="COPY-1", provenance={"sourceKind": "SparesDB", "sourceFile": source_file, "sourceHash": source_hash, "sheetName": "SparesDB", "sourceRow": 8, "sourceCell": "A8"}, evidence_fingerprint="alias-fingerprint")
        self.assertEqual(reused["id"], alias["id"])
        second_occurrence = self.registry.ensure_alias(spare_key=master["spareKey"], spare_record_id=master["recordId"], value="COPY-1", provenance={"sourceKind": "SparesDB", "sourceFile": "B.ods", "sourceHash": source_hash, "sheetName": "SparesDB", "sourceRow": 8, "sourceCell": "A8"}, evidence_fingerprint="alias-fingerprint-B")
        self.assertNotEqual(second_occurrence["id"], alias["id"])
        self.assertEqual(len(self.grist.tables["SpareAlias"]), 2)

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

    def test_rebuilt_route_retry_recovers_a_false_complete_alias_loss_auditably(self):
        import app.web as web
        from starlette.requests import Request

        request_key = "import-recovery-false-complete"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = _registry(str(root / "first"), self.grist)
            product_path, original_plan = _recovery_plan(root, first)
            self.grist.lose_response_once_on = "SpareAlias"
            with self.assertRaisesRegex(RuntimeError, "lost response after durable write"):
                apply_import_plan(plan=original_plan, registry=first, actor="qa", request_key=request_key)

            batch = next(row for row in self.grist.tables["SpareImportBatch"] if row["fields"].get("RequestKey") == request_key)
            batch["fields"].update({"Status": "complete", "CompletedAt": "legacy-false-completion", "Result": {"requestKey": request_key, "status": "complete", "legacyFalseComplete": True}})
            fresh = _registry(str(root / "fresh"), self.grist)
            rebuilt_plan = _recovery_plan(root, fresh)[1]
            self.assertEqual((rebuilt_plan["identities"][0]["action"], rebuilt_plan["identities"][0]["canonicalStatus"]), ("noop", "pending"))
            request = Request({"type": "http", "method": "POST", "path": "/api/spares/import-apply", "headers": [(b"x-authentik-username", b"Isolated QA")], "query_string": b""})
            payload = {"path": str(product_path), "previewFingerprint": original_plan["previewFingerprint"]}
            with patch.object(web, "_spare_registry", return_value=fresh), patch.object(web, "_spare_import_plan", return_value=rebuilt_plan):
                recovered = web.spare_import_apply(request, payload, idempotency_key=request_key)

            self.assertEqual(recovered["status"], "complete")
            self.assertFalse(recovered["idempotent"])
            status = fresh.import_status(request_key)
            self.assertEqual(status["status"], "complete")
            self.assertEqual(status["progress"]["recoveryHistory"][0]["event"], "reopened_after_incomplete_identity")
            detail = fresh.get_spare(rebuilt_plan["identities"][0]["spareKey"])
            self.assertEqual(detail["status"], "active")
            self.assertEqual(detail["legacyAliases"], ["F3-ALIAS"])
            self.assertEqual(len([row for row in detail["history"] if row.get("EventType") == "created"]), 1)
            request_row = fresh.create_request_record("import-create:" + _stable_key(request_key, "f3-alias"))
            self.assertEqual(request_row["fields"]["Status"], "complete")
            counts = {table: len(rows) for table, rows in self.grist.tables.items()}

            replay_plan = _recovery_plan(root, fresh)[1]
            with patch.object(web, "_spare_registry", return_value=fresh), patch.object(web, "_spare_import_plan", return_value=replay_plan):
                replay = web.spare_import_apply(request, payload, idempotency_key=request_key)
            self.assertTrue(replay["idempotent"])
            self.assertEqual({table: len(rows) for table, rows in self.grist.tables.items()}, counts)

    def test_import_never_adopts_another_request_pending_identity(self):
        request_key = "import-must-not-adopt-pending"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = _registry(str(root / "first"), self.grist)
            self.grist.lose_response_once_on = "SpareAlias"
            with self.assertRaisesRegex(RuntimeError, "lost response after durable write"):
                first.create_spare(description="Other request", actor="qa", reason="separate operation", request_key="other-pending-create", aliases=[{"value": "F3-ALIAS"}])
            product_path, plan = _recovery_plan(root, first)
            self.assertEqual((plan["identities"][0]["action"], plan["identities"][0]["canonicalStatus"]), ("noop", "pending"))

            with self.assertRaises(SpareRegistryError) as caught:
                apply_import_plan(plan=plan, registry=first, actor="qa", request_key=request_key)
            self.assertEqual(caught.exception.code, "SPARE_IMPORT_RECOVERY_REQUIRED")
            batch = first.import_status(request_key)
            self.assertEqual(batch["status"], "recovery_required")
            self.assertEqual(batch["progress"]["details"]["identities"][0]["reason"], "pending_identity_is_not_owned_by_this_import_create")
            self.assertIsNone(first.create_request_record("import-create:" + _stable_key(request_key, "f3-alias")))
            self.assertEqual(first.list_spares(status="active")["total"], 0)
            pending = first.get_spare(plan["identities"][0]["spareKey"])
            self.assertEqual((pending["status"], len(pending["history"])), ("pending", 0))

    def test_import_create_recovery_survives_durable_losses_across_write_boundaries(self):
        cases = [
            ("create-request", "SpareMasterRequest", None),
            ("master-create", "SpareMaster", None),
            ("alias-create", "SpareAlias", None),
            ("history-create", "SpareHistory", None),
            ("request-complete", None, ("SpareMasterRequest", "Status", "complete")),
            ("master-activate", None, ("SpareMaster", "Status", "active")),
            ("batch-complete", None, ("SpareImportBatch", "Status", "complete")),
        ]
        for label, create_table, update_rule in cases:
            with self.subTest(boundary=label), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                grist = _Grist()
                first = _registry(str(root / "first"), grist)
                _product_path, plan = _recovery_plan(root, first)
                request_key = f"import-boundary-{label}"
                if create_table:
                    grist.lose_response_once_on = create_table
                if update_rule:
                    grist.lose_response_once_on_update = update_rule
                with self.assertRaisesRegex(RuntimeError, "lost response after durable"):
                    apply_import_plan(plan=plan, registry=first, actor="qa", request_key=request_key)

                fresh = _registry(str(root / "fresh"), grist)
                rebuilt = _recovery_plan(root, fresh)[1]
                self.assertEqual(rebuilt["sourceHashes"], plan["sourceHashes"])
                recovered = apply_import_plan(plan=rebuilt, registry=fresh, actor="qa", request_key=request_key, expected_preview_fingerprint=plan["previewFingerprint"])
                self.assertEqual(recovered["status"], "complete")
                spare = fresh.get_spare(rebuilt["identities"][0].get("spareKey") or next(iter(fresh.identity_index()["masters_by_id"].values()))["fields"]["SpareKey"])
                self.assertEqual(spare["status"], "active")
                self.assertEqual(len(spare["legacyAliases"]), 1)
                self.assertEqual(len([row for row in spare["history"] if row.get("EventType") == "created"]), 1)
                self.assertEqual(fresh.import_status(request_key)["status"], "complete")
                self.assertEqual(len(grist.tables["SpareMaster"]), 1)
                self.assertEqual(len(grist.tables["SpareAlias"]), 2)
                self.assertEqual(len(grist.tables["SpareHistory"]), 1)
                replay_plan = _recovery_plan(root, fresh)[1]
                replay = apply_import_plan(plan=replay_plan, registry=fresh, actor="qa", request_key=request_key, expected_preview_fingerprint=plan["previewFingerprint"])
                self.assertTrue(replay["idempotent"])
                self.assertEqual((len(grist.tables["SpareMaster"]), len(grist.tables["SpareAlias"]), len(grist.tables["SpareHistory"])), (1, 2, 1))

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

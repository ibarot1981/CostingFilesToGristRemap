from __future__ import annotations

import hashlib
import json
import unittest

from app.domain import CostingFile
from app.milestone2 import compare_semantic_snapshots, resolve_semantic_ambiguities
from app.repository import GovernanceConflict, InMemorySafariRepository


MCL = "5. Material Cut List Price"
MCL_ID_FIELDS = [
    "product_part_name", "material_to_cut", "dimension_to_cut_mm", "qty",
    "optional_item_group_1",
]


def _line(*, process=MCL, piece="Frame", material="MS Flat 20 x 3", dimension="250", qty="2", option="", row=10, cost="120", weight="3", cr=""):
    fields = {
        "product_part_name": piece,
        "material_to_cut": material,
        "dimension_to_cut_mm": dimension,
        "qty": qty,
        "optional_item_group_1": option,
        "total_grams": str(float(weight) * 1000),
        "amount": cost,
        "remarks": "",
        "cr_log": cr,
    }
    identity_fields = MCL_ID_FIELDS if process == MCL else [
        "product_part_name", "item_name", "material_to_cut", "dimension_to_cut_mm", "qty", "optional_item_group_1",
    ]
    if process != MCL:
        fields["item_name"] = "Frame piece"
    identity_values = {name: str(fields.get(name, "")) for name in identity_fields}
    return {
        "process_list": process,
        "source_row": row,
        "status": "active",
        "identity_fields": identity_fields,
        "identity_values": identity_values,
        "fields": fields,
        "source_cells": {"qty": {"cell": f"D{row}", "cached_value": qty}},
        "current_cost": cost,
        "weight_kg": weight,
        "cr_reference": cr or None,
    }


def _snapshot(lines, *, rate="40", rate_date="2026-09-01", groups=None, total="120"):
    content = {
        "process_lists": {MCL: lines},
        "rate_state": {
            "MS Flat 20 x 3": {
                "status": "available",
                "effective_rate_per_kg": rate,
                "source": "rate_log",
                "rate_date": rate_date,
                "source_row": 41,
                "source_evidence": {"source_row": 41, "price": rate},
            },
        },
        "material_master_state": {},
        "summary_structure": {"option_groups": groups or [], "items": [], "grand_total": total},
        "parameters": {},
        "parameter_evidence": {},
    }
    hash_lines = sorted(({
        "process_list": line.get("process_list"),
        "status": line.get("status"),
        "identity_values": line.get("identity_values", {}),
        "fields": line.get("fields", {}),
        "current_cost": line.get("current_cost", line.get("cost_value")),
    } for line in lines), key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")))
    hash_content = {
        "process_lists": {MCL: hash_lines},
        "rate_state": {"MS Flat 20 x 3": {"status": "available", "effective_rate_per_kg": rate, "source": "rate_log", "rate_date": rate_date}},
        "summary_structure": {"option_groups": groups or [], "grand_total": total},
    }
    semantic_hash = hashlib.sha256(json.dumps(hash_content, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"snapshot_key": "baseline-key", "semantic_hash": semantic_hash, "content": content}


class SemanticReconciliationTests(unittest.TestCase):
    def test_identical_semantic_hash_skips_unchanged_duplicate_identity_ambiguity(self):
        previous = _snapshot([_line(row=10), _line(row=11)])
        current = _snapshot([_line(row=40), _line(row=41)])
        comparison = compare_semantic_snapshots(previous, current)
        self.assertEqual(previous["semantic_hash"], current["semantic_hash"])
        self.assertEqual(comparison["changes"], [])
        self.assertEqual(comparison["ambiguities"], [])
        self.assertFalse(comparison["owner_review_required"])

    def test_rate_change_is_reported_for_each_unchanged_costing_line(self):
        old = _snapshot([_line(row=10), _line(piece="Support", row=11, cost="80", weight="2")], rate="40")
        new = _snapshot([_line(row=91), _line(piece="Support", row=92, cost="86", weight="2")], rate="42")

        comparison = compare_semantic_snapshots(old, new)

        rates = [item for item in comparison["changes"] if item["classification"] == "rate_source_change"]
        self.assertEqual(len(rates), 2)
        self.assertEqual({item["cost_impact"] for item in rates}, {"6", "4"})
        self.assertTrue(all(len(item["affected_lines"]) == 1 for item in rates))
        self.assertTrue(all(item["affected_lines"][0]["previous"]["source_evidence"]["source_row"] in {10, 11} for item in rates))

    def test_line_addition_and_removal_preserve_before_after_states(self):
        old = _snapshot([_line(piece="Old"), _line(piece="Retain")])
        new = _snapshot([_line(piece="Retain"), _line(piece="New", row=20)])

        comparison = compare_semantic_snapshots(old, new)

        types = {item["change_type"] for item in comparison["changes"]}
        self.assertIn("costing_line_added", types)
        self.assertIn("costing_line_removed", types)
        added = next(item for item in comparison["changes"] if item["change_type"] == "costing_line_added")
        removed = next(item for item in comparison["changes"] if item["change_type"] == "costing_line_removed")
        self.assertIsNone(added["previous_state"])
        self.assertEqual(added["current_state"]["fields"]["product_part_name"], "New")
        self.assertIsNone(removed["current_state"])
        self.assertEqual(removed["previous_state"]["fields"]["product_part_name"], "Old")

    def test_changed_quantity_dimension_material_and_option_are_probable_modifications(self):
        cases = [
            (_line(), _line(qty="3"), "line_quantity_changed"),
            (_line(), _line(dimension="300"), "line_dimension_changed"),
            (_line(), _line(material="MS Flat 25 x 3"), "line_material_changed"),
            (_line(), _line(option="Motor"), "option_group_changed"),
        ]
        for old_line, new_line, expected_type in cases:
            with self.subTest(expected_type=expected_type):
                comparison = compare_semantic_snapshots(_snapshot([old_line]), _snapshot([new_line]))
                found = [item for item in comparison["changes"] if item["classification"] == "design_structure_change"]
                self.assertEqual(len(found), 1)
                self.assertEqual(found[0]["change_type"], expected_type)
                self.assertEqual(found[0]["match_kind"], "probable_modification")
                self.assertIsNotNone(found[0]["cost_impact"])
                self.assertFalse(comparison["owner_review_required"])

    def test_duplicate_option_headings_are_detected_as_add_remove(self):
        old = _snapshot([_line()], groups=["Motor"])
        new = _snapshot([_line()], groups=["Motor", "Tyres"])

        comparison = compare_semantic_snapshots(old, new)

        self.assertIn("option_group_added", {item["change_type"] for item in comparison["changes"]})

    def test_movement_between_material_and_tool_shop_lists_is_classified(self):
        old = _snapshot([_line()])
        moved = _line(process="Tool Shop Items", piece="Frame", material="MS Flat 20 x 3", row=20)
        new = _snapshot([moved])

        comparison = compare_semantic_snapshots(old, new)

        movement = next(item for item in comparison["changes"] if item["change_type"] == "item_moved_between_process_lists")
        self.assertEqual(movement["match_kind"], "movement")
        self.assertEqual(movement["previous_state"]["process_list"], MCL)
        self.assertEqual(movement["current_state"]["process_list"], "Tool Shop Items")

    def test_ambiguous_candidates_are_not_misreported_as_addition_or_removal(self):
        old = _snapshot([_line(material="Flat 20 x 3"), _line(material="Flat 25 x 3")])
        new = _snapshot([_line(material="Flat 30 x 3", row=30)])

        comparison = compare_semantic_snapshots(old, new)

        self.assertTrue(comparison["owner_review_required"])
        self.assertGreaterEqual(len(comparison["ambiguities"]), 2)
        self.assertFalse(any(item["change_type"] in {"costing_line_added", "costing_line_removed"} for item in comparison["changes"]))

    def test_explicit_owner_pair_records_structural_change_and_cr_reference(self):
        old_line = _line(material="MS Flat 20 x 3", dimension="250", cr="CR-100")
        new_line = _line(material="MS Flat 25 x 3", dimension="300", row=12, cr="CR-101")
        # Deliberately sparse identity evidence creates a probable candidate,
        # not an automatic match.
        for line in (old_line, new_line):
            line["identity_fields"] = ["product_part_name", "material_to_cut", "dimension_to_cut_mm"]
            line["identity_values"] = {name: line["fields"][name] for name in line["identity_fields"]}
        old = _snapshot([old_line])
        new = _snapshot([new_line])
        comparison = compare_semantic_snapshots(old, new)
        ambiguity = next(item for item in comparison["ambiguities"] if item["change_type"] == "ambiguous_line_match")

        resolved = resolve_semantic_ambiguities(comparison, {ambiguity["candidate_pair_key"]: "pair"})

        self.assertFalse(resolved["owner_review_required"])
        paired = next(item for item in resolved["changes"] if item.get("match_kind") == "owner_confirmed_pair")
        self.assertEqual(paired["change_type"], "line_material_changed")
        self.assertEqual(paired["cr_reference"], {"previous": "CR-100", "current": "CR-101"})

    def test_duplicate_exact_composite_key_requires_owner_resolution(self):
        old = _snapshot([_line(row=1), _line(row=2)])
        new = _snapshot([_line(row=9)])

        comparison = compare_semantic_snapshots(old, new)

        self.assertTrue(comparison["owner_review_required"])
        self.assertEqual(comparison["ambiguities"][0]["change_type"], "ambiguous_line_identity")

    def test_in_memory_acceptance_persists_snapshot_change_items_observation_and_audit(self):
        repository = InMemorySafariRepository()
        repository.add_file(CostingFile("file:s1khf", "S1KHF/model.ods", "s1khf/model.ods", "model.ods", ".ods", 10, "2026-09-29T00:00:00+00:00", "old-hash"))
        semantic = {
            "semantic_hash": "semantic-1",
            "observed_at": "2026-09-29T10:00:00+05:30",
            "source_hashes": {"selected_workbook_saved": "ods-hash", "raw_steel": "raw-hash", "rate_log_dump": "rate-hash"},
            "source_evidence": {"selected_workbook_saved": {"sha256": "ods-hash", "size_bytes": 10, "modified_at": "2026-09-29T00:00:00+00:00", "sheet_count": 5}},
            "content": {"process_lists": {MCL: []}},
        }
        change = {
            "change_key": "line-change-1", "change_type": "line_quantity_changed",
            "classification": "design_structure_change", "reason": "CR-9 changed the quantity.",
            "previous_state": {"fields": {"qty": 2}, "source_evidence": {"source_row": 8}},
            "current_state": {"fields": {"qty": 3}, "source_evidence": {"source_row": 9}},
            "cost_impact": "15.5", "cr_reference": {"previous": None, "current": "CR-9"},
        }

        result = repository.accept_costing_snapshot(
            costing_file_id="file:s1khf", semantic_snapshot=semantic, changes=[change],
            actor="Irshad", reason="Reconcile refreshed S1KHF ODS", expected_previous_snapshot_key=None,
            expected_semantic_hash="semantic-1", expected_source_hashes=semantic["source_hashes"], idempotency_key="accept-1",
        )

        saved = repository.latest_accepted_costing_snapshot("file:s1khf")
        self.assertEqual(saved.snapshot_key, result["snapshot"]["snapshot_key"])
        self.assertEqual(len(repository.costing_change_items), 1)
        self.assertEqual(repository.observations[-1].file_hash, "ods-hash")
        self.assertEqual(repository.files["file:s1khf"].file_hash, "ods-hash")
        events = list(repository.audit_events.values())
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].payload["costImpact"], "15.5")
        self.assertEqual(events[0].payload["crReference"], {"previous": None, "current": "CR-9"})
        replay = repository.accept_costing_snapshot(
            costing_file_id="file:s1khf", semantic_snapshot=semantic, changes=[change],
            actor="Irshad", reason="Reconcile refreshed S1KHF ODS", expected_previous_snapshot_key=None,
            expected_semantic_hash="semantic-1", expected_source_hashes=semantic["source_hashes"], idempotency_key="accept-1",
        )
        self.assertTrue(replay["idempotent"])
        self.assertEqual(len(repository.costing_snapshots), 1)

    def test_acceptance_refuses_ambiguous_and_unauthorized_changes(self):
        repository = InMemorySafariRepository()
        repository.add_file(CostingFile("file:s1khf", "S1KHF/model.ods", "s1khf/model.ods", "model.ods", ".ods", 10, "2026-09-29T00:00:00+00:00", "old-hash"))
        semantic = {
            "semantic_hash": "semantic-1", "source_hashes": {"selected_workbook_saved": "ods-hash", "raw_steel": "raw-hash", "rate_log_dump": "rate-hash"},
            "source_evidence": {"selected_workbook_saved": {"sha256": "ods-hash"}}, "content": {},
        }
        args = dict(costing_file_id="file:s1khf", semantic_snapshot=semantic, reason="Review", expected_previous_snapshot_key=None, expected_semantic_hash="semantic-1", expected_source_hashes=semantic["source_hashes"], idempotency_key="blocked-1")
        with self.assertRaises(GovernanceConflict) as unauthorized:
            repository.accept_costing_snapshot(changes=[], actor="someone else", **args)
        self.assertEqual(unauthorized.exception.code, "APPROVER_NOT_AUTHORIZED")
        with self.assertRaises(GovernanceConflict) as ambiguous:
            repository.accept_costing_snapshot(changes=[{"classification": "owner_review_required"}], actor="Irshad", **args)
        self.assertEqual(ambiguous.exception.code, "AMBIGUOUS_COSTING_CHANGES_REMAIN")

    def test_acceptance_rejects_stale_expected_baseline(self):
        repository = InMemorySafariRepository()
        repository.add_file(CostingFile("file:s1khf", "S1KHF/model.ods", "s1khf/model.ods", "model.ods", ".ods", 10, "2026-09-29T00:00:00+00:00", "old-hash"))
        source_hashes = {"selected_workbook_saved": "ods-hash", "raw_steel": "raw-hash", "rate_log_dump": "rate-hash"}
        first = {
            "semantic_hash": "semantic-1", "source_hashes": source_hashes,
            "source_evidence": {"selected_workbook_saved": {"sha256": "ods-hash"}}, "content": {},
        }
        repository.accept_costing_snapshot(
            costing_file_id="file:s1khf", semantic_snapshot=first, changes=[], actor="Irshad",
            reason="Initial baseline", expected_previous_snapshot_key=None,
            expected_semantic_hash="semantic-1", expected_source_hashes=source_hashes, idempotency_key="baseline-1",
        )
        changed = {**first, "semantic_hash": "semantic-2"}
        with self.assertRaises(GovernanceConflict) as raised:
            repository.accept_costing_snapshot(
                costing_file_id="file:s1khf", semantic_snapshot=changed, changes=[], actor="Irshad",
                reason="Changed baseline", expected_previous_snapshot_key=None,
                expected_semantic_hash="semantic-2", expected_source_hashes=source_hashes, idempotency_key="baseline-2",
            )
        self.assertEqual(raised.exception.code, "STALE_COSTING_BASELINE")


if __name__ == "__main__":
    unittest.main()

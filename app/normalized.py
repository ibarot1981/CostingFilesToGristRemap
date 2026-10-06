"""Deterministic, source-pinned Milestone 3 process-line projection.

The projection is storage neutral. It never treats row position, MCL ID,
description similarity, or a CR reference as approved business identity.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
from typing import Any

from app.material_resolution import resolve_material


PARSER_VERSION = "normalized-s1khf.v1"
DETAIL_TYPES = {
    "5. Material Cut List Price": "material_cut",
    "Tool Shop Items": "tool_shop",
    "CNC Cut List": "cnc",
    "Stores and Consumables List": "store",
    "Labour - Paint - Packing": "activity",
}
PHYSICAL_FIELDS = {
    "product_part_name", "material_to_cut", "material_used", "item_name", "item_code",
    "dimension_to_cut_mm", "dimension_to_cut_inches", "qty", "part_category",
    "toolshop_part_name", "activity", "optional_item_group_1", "in_use",
    "total_grams", "total_material_used_inches", "kg_per_mtr", "grams_per_inch",
    "weight_per_meter_kg", "total_weight_kg", "part_weight_kg", "length", "width", "thickness",
    "issue_slip_no", "issue_slip_desc",
}


def capture_source_rows(document: Any, sheets: set[str]) -> dict[tuple[str, int], dict[str, Any]]:
    """Retain every observed cell, including columns not yet interpreted."""
    captured: dict[tuple[str, int], dict[str, Any]] = {}
    for sheet_name in sheets:
        sheet = document.sheets.get(sheet_name)
        if sheet is None:
            continue
        for row_number, cells in sheet.nonempty_rows():
            captured[(sheet_name, row_number)] = {
                cell.coordinate: {"formula": cell.formula, "cached_value": str(cell.value) if cell.value is not None else None,
                                  "display_text": cell.display_text, "value_type": cell.value_type}
                for cell in cells.values() if cell.value is not None or cell.formula or cell.display_text
            }
    return captured


def _key(*values: Any) -> str:
    raw = json.dumps(values, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def _number(value: Any) -> str | None:
    try:
        return str(Decimal(str(value))) if value not in (None, "") else None
    except (InvalidOperation, ValueError):
        return None


def _activity_kind(value: str) -> str:
    low = value.casefold()
    hits = [kind for kind, words in {
        "paint": ("paint", "powder coating"),
        "packing": ("pack", "packaging"),
        "labour": ("labour", "labor", "welding", "assembly"),
    }.items() if any(word in low for word in words)]
    return hits[0] if len(hits) == 1 else "unclassified"


def _physical_signature(line: dict[str, Any]) -> str:
    fields = line.get("fields", {})
    formulas = {
        name: re.sub(r"(\$?[A-Z]{1,3}\$?)\d+", r"\1#", evidence["formula"])
        for name, evidence in line.get("source_cells", {}).items()
        if evidence.get("formula")
    }
    return _key({k: fields.get(k) for k in sorted(PHYSICAL_FIELDS) if k in fields}, formulas)


def project_snapshot(
    semantic: dict[str, Any], *, snapshot_key: str, source_hash: str,
    material_mappings: dict[str, str] | None = None,
    previous: dict[str, Any] | None = None,
    dependency_hashes: dict[str, str] | None = None,
    source_row_cells: dict[tuple[str, int], dict[str, Any]] | None = None,
    actor: str = "system", reason: str = "Accepted S1KHF baseline projection",
) -> dict[str, Any]:
    """Build normalized rows and review exceptions from accepted semantic content.

    A duplicate exact composite is unresolved; a changed key requires an
    explicit reviewed predecessor mapping in a later governed import.
    """
    lists = semantic.get("process_lists", {})
    if not isinstance(lists, dict) or not source_hash or not snapshot_key:
        raise ValueError("Accepted semantic content, snapshot key, and source hash are required")
    unknown = set(lists) - set(DETAIL_TYPES)
    if unknown:
        raise ValueError(f"Unsupported process lists: {sorted(unknown)}")
    mappings = dict(material_mappings or {})
    for canonical in semantic.get("material_master_state", {}):
        mappings.setdefault(canonical, canonical)
    output: dict[str, Any] = {name: [] for name in (
        "parts", "part_revisions", "materials", "purchase_items", "operations", "work_centers",
        "line_masters", "line_revisions", "line_details", "source_observations",
        "source_mappings", "audit", "exceptions",
    )}
    seen: dict[str, set[str]] = defaultdict(set)
    old_keys = {name: {row["key"] for row in (previous or {}).get(name, [])}
                for name in ("parts", "part_revisions", "materials", "purchase_items", "operations", "work_centers")}
    old_masters = {row["key"]: row for row in (previous or {}).get("line_masters", [])}
    old_revisions = {row["master_key"]: row for row in (previous or {}).get("line_revisions", [])}
    composite_counts = Counter(
        (sheet, json.dumps(line.get("identity_values", {}), sort_keys=True, default=str))
        for sheet, rows in lists.items() for line in rows
    )
    exact_current_keys = Counter(
        f"line:{_key(sheet, line.get('identity_values', {}))}"
        for sheet, rows in lists.items() for line in rows
    )
    changed_key_candidates: dict[tuple[str, int], list[str]] = {}
    for sheet, rows in lists.items():
        for candidate_line in rows:
            candidate_identity = candidate_line.get("identity_values", {})
            exact_key = f"line:{_key(sheet, candidate_identity)}"
            if exact_key in old_masters:
                continue
            matches: list[str] = []
            for changed in ("qty", "material_to_cut", "dimension_to_cut_mm"):
                if changed not in candidate_identity:
                    continue
                stable = {k: v for k, v in candidate_identity.items() if k != changed}
                if sum(bool(v) for v in stable.values()) < 2:
                    continue
                candidates = [old_key for old_key, old in old_masters.items()
                              if old.get("process_type") == DETAIL_TYPES[sheet]
                              and {k: old.get("identity", {}).get(k) for k in stable} == stable]
                if len(candidates) == 1:
                    matches.append(candidates[0])
            changed_key_candidates[(sheet, int(candidate_line["source_row"]))] = sorted(set(matches))
    claimed_old_keys = Counter(key for matches in changed_key_candidates.values() for key in matches)
    for sheet, rows in lists.items():
        kind = DETAIL_TYPES[sheet]
        for line in rows:
            row = int(line["source_row"])
            fields = line.get("fields", {})
            identity = line.get("identity_values", {})
            identity_json = json.dumps(identity, sort_keys=True, default=str)
            duplicate = composite_counts[(sheet, identity_json)] > 1
            master_key = f"line:{_key(sheet, identity)}" if not duplicate else f"unresolved:{_key(snapshot_key, sheet, row, source_hash)}"
            changed_ambiguous = False
            if master_key and not duplicate and master_key not in old_masters and old_masters:
                # D-051: one changed dimension, material, or quantity may be
                # paired only when the remaining identity is unique on both
                # sides. A description by itself never suffices.
                matches = changed_key_candidates.get((sheet, row), [])
                if len(matches) == 1 and claimed_old_keys[matches[0]] == 1 and not exact_current_keys[matches[0]]:
                    master_key = matches[0]
                elif matches:
                    changed_ambiguous = True
                    output["exceptions"].append({"code": "CHANGED_KEY_AMBIGUOUS", "sheet": sheet, "row": row, "candidate_masters": matches})
            observation_key = f"obs:{_key(snapshot_key, sheet, row, source_hash)}"
            part_name = str(fields.get("product_part_name") or "").strip()
            # A source description is evidence, not confirmation of reusable
            # ProductPart identity. Keep one explicitly temporary owner.
            part_key = "part:s1khf-unallocated"
            if part_key not in seen["parts"]:
                if part_key not in old_keys["parts"]:
                    output["parts"].append({"key": part_key, "display_name": "S1KHF unallocated", "status": "temporary_unallocated"})
                if f"{part_key}:1" not in old_keys["part_revisions"]:
                    output["part_revisions"].append({"key": f"{part_key}:1", "part_key": part_key, "revision": 1, "status": "temporary_unallocated", "snapshot_key": snapshot_key})
                seen["parts"].add(part_key)
            material_name = str(fields.get("material_to_cut") or fields.get("material_used") or "").strip()
            match = resolve_material(material_name, mappings, option_scope=str(fields.get("optional_item_group_1") or "")) if material_name else None
            material_key = f"material:{_key(match.canonical_name)}" if match and match.canonical_name and match.status in {"reviewed_alias", "exact_canonical"} else None
            if material_key and material_key not in seen["materials"]:
                if material_key not in old_keys["materials"]:
                    output["materials"].append({"key": material_key, "canonical_name": match.canonical_name, "ods_display_name": material_name, "mapping_status": match.status})
                seen["materials"].add(material_key)
            elif match and not material_key:
                output["exceptions"].append({"code": "MATERIAL_UNMATCHED", "sheet": sheet, "row": row, "value": material_name})
            item_name = str(fields.get("item_name") or fields.get("item_code") or "").strip()
            item_key = f"item:{_key(item_name)}" if item_name and kind in {"store", "activity"} else None
            if item_key and item_key not in seen["purchase_items"]:
                if item_key not in old_keys["purchase_items"]:
                    output["purchase_items"].append({"key": item_key, "display_name": item_name, "status": "source_candidate"})
                seen["purchase_items"].add(item_key)
            operation_key = f"operation:{kind}"
            if operation_key not in seen["operations"]:
                if operation_key not in old_keys["operations"]:
                    output["operations"].append({"key": operation_key, "name": kind})
                if f"center:{kind}" not in old_keys["work_centers"]:
                    output["work_centers"].append({"key": f"center:{kind}", "name": sheet, "status": "source_candidate"})
                seen["operations"].add(operation_key)
            observation = {
                "key": observation_key, "snapshot_key": snapshot_key, "source_hash": source_hash,
                "parser_version": PARSER_VERSION, "sheet": sheet, "row": row,
                "dependency_hashes": dict(dependency_hashes or {}),
                "status": line.get("status", "active"), "cells": {
                    **line.get("source_cells", {}),
                    **({"__all_cells__": source_row_cells[(sheet, row)]} if source_row_cells and (sheet, row) in source_row_cells else {}),
                },
                "observed_fields": fields, "part_display_name": part_name,
                "cached_cost": _number(line.get("cost_value")), "current_cost": _number(line.get("current_cost")),
                "cr_reference": line.get("cr_reference"),
            }
            output["source_observations"].append(observation)
            status = "ambiguous" if duplicate or changed_ambiguous else "proposed_exact"
            output["source_mappings"].append({"key": f"mapping:{_key(observation_key, master_key)}", "observation_key": observation_key, "master_key": master_key, "status": status})
            if duplicate:
                output["exceptions"].append({"code": "DUPLICATE_COMPOSITE", "sheet": sheet, "row": row, "identity": identity})
            signature = _physical_signature(line)
            if master_key not in seen["line_masters"] and master_key not in old_masters:
                output["line_masters"].append({"key": master_key, "part_key": part_key, "process_type": kind, "identity": identity, "status": "unresolved" if duplicate or changed_ambiguous else "proposed", "source_part_name": part_name})
            seen["line_masters"].add(master_key)
            prior = old_revisions.get(master_key)
            if prior and prior["physical_signature"] == signature:
                revision_key = prior["key"]
            else:
                revision_key = f"revision:{_key(master_key, signature)}"
                if revision_key not in seen["line_revisions"]:
                    output["line_revisions"].append({"key": revision_key, "master_key": master_key, "previous_key": prior["key"] if prior else None, "physical_signature": signature, "status": line.get("status", "active"), "snapshot_key": snapshot_key, "actor": actor, "reason": reason})
                    output["audit"].append({"key": f"audit:{revision_key}", "master_key": master_key, "revision_key": revision_key, "previous_key": prior["key"] if prior else None, "observation_key": observation_key, "actor": actor, "reason": reason, "cr_reference": line.get("cr_reference")})
                    seen["line_revisions"].add(revision_key)
            detail = {
                "key": f"detail:{revision_key}", "revision_key": revision_key, "process_type": kind,
                "material_key": material_key, "purchase_item_key": item_key,
                "operation_key": operation_key, "work_center_key": f"center:{kind}",
                "source_department": "Tool Shop" if kind == "tool_shop" else None,
                "issue_route": "Stores" if kind == "store" else None,
                "activity_kind": _activity_kind(str(fields.get("activity") or "")) if kind == "activity" else None,
                "quantity": _number(fields.get("qty")), "dimension_mm": _number(fields.get("dimension_to_cut_mm")),
                "dimension_inches": _number(fields.get("dimension_to_cut_inches")),
                "weight_grams": _number(fields.get("total_grams")),
                "weight_kg": _number(fields.get("total_weight_kg")),
                "part_weight_kg": _number(fields.get("part_weight_kg")),
                "length": _number(fields.get("length")), "width": _number(fields.get("width")),
                "thickness": _number(fields.get("thickness")),
                "quantity_uom": "L" if kind == "activity" else "Nos",
                "issue_slip_no": str(fields.get("issue_slip_no") or "") or None,
                "issue_slip_desc": str(fields.get("issue_slip_desc") or "") or None,
                "internal_making_cost": _number(fields.get("internal_making_cost")),
                "external_machining_cost": _number(fields.get("external_machining_cost")),
                "rate_cached": _number(fields.get("rate")), "cost_cached": _number(fields.get("amount")),
                "item_name": item_name, "material_display_name": material_name,
                "option_group": fields.get("optional_item_group_1"),
            }
            if not prior or prior["physical_signature"] != signature:
                output["line_details"].append(detail)
    if previous:
        current_master_keys = {row["master_key"] for row in output["source_mappings"]}
        prior_evidence = {row["master_key"]: row["observation_key"] for row in previous.get("source_mappings", [])}
        prior_details = {row["revision_key"]: row for row in previous.get("line_details", [])}
        for old_key in sorted(set(old_masters) - current_master_keys):
            prior = old_revisions.get(old_key)
            if not prior or prior.get("status") == "retired":
                continue
            revision_key = f"revision:{_key(old_key, prior['physical_signature'], 'retired')}"
            output["line_revisions"].append({"key": revision_key, "master_key": old_key,
                "previous_key": prior["key"], "physical_signature": prior["physical_signature"],
                "status": "retired", "snapshot_key": snapshot_key, "actor": actor, "reason": reason})
            detail = prior_details.get(prior["key"])
            if detail:
                output["line_details"].append({**detail, "key": f"detail:{revision_key}", "revision_key": revision_key})
            output["audit"].append({"key": f"audit:{revision_key}", "master_key": old_key,
                "revision_key": revision_key, "previous_key": prior["key"],
                "observation_key": prior_evidence.get(old_key), "actor": actor,
                "reason": reason, "cr_reference": None})
    output["reconciliation"] = reconcile(semantic, output, previous=previous)
    return output


def reconcile(semantic: dict[str, Any], projection: dict[str, Any], *, previous: dict[str, Any] | None = None) -> dict[str, Any]:
    expected = {sheet: Counter(line.get("status", "active") for line in rows) for sheet, rows in semantic.get("process_lists", {}).items()}
    actual: dict[str, Counter[str]] = defaultdict(Counter)
    for row in projection["source_observations"]:
        actual[row["sheet"]][row["status"]] += 1
    differences = [
        {"sheet": sheet, "status": status, "expected": count, "actual": actual[sheet][status]}
        for sheet, counts in expected.items() for status, count in counts.items()
        if actual[sheet][status] != count
    ]
    observation_by_key = {row["key"]: row for row in projection["source_observations"]}
    revision_by_master = {row["master_key"]: row for row in (previous or {}).get("line_revisions", [])}
    revision_by_master.update({row["master_key"]: row for row in projection["line_revisions"]})
    detail_by_revision = {row["revision_key"]: row for row in (previous or {}).get("line_details", [])}
    detail_by_revision.update({row["revision_key"]: row for row in projection["line_details"]})
    value_differences = []
    active_totals: dict[str, Decimal] = defaultdict(Decimal)
    for mapping in projection["source_mappings"]:
        observation = observation_by_key[mapping["observation_key"]]
        revision = revision_by_master.get(mapping["master_key"])
        detail = detail_by_revision.get(revision["key"]) if revision else None
        expected_cost = _number(observation["cached_cost"])
        # Cached/resolved rate is observation evidence. A reused physical
        # revision may have a newer cost without changing its detail row.
        actual_cost = (_number(detail.get("cost_cached")) if detail and revision in projection["line_revisions"]
                       else _number(observation["cached_cost"]))
        if expected_cost != actual_cost:
            value_differences.append({"sheet": observation["sheet"], "row": observation["row"], "expected_cost": expected_cost, "actual_cost": actual_cost})
        if observation["status"] == "active" and actual_cost is not None:
            active_totals[observation["sheet"]] += Decimal(actual_cost)
    return {"expected_counts": {k: dict(v) for k, v in expected.items()}, "actual_counts": {k: dict(v) for k, v in actual.items()}, "differences": differences,
            "value_differences": value_differences, "active_cached_totals": {k: str(v) for k, v in active_totals.items()},
            "unresolved_mappings": len([x for x in projection["source_mappings"] if x["status"] == "ambiguous"])}

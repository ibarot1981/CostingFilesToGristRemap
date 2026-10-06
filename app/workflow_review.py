"""Read-only processing evidence; never substitutes price parity for completion."""
from __future__ import annotations
from collections import Counter
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

PROCESS_SHEETS = (
    "5. Material Cut List Price", "Tool Shop Items", "Stores and Consumables List",
    "CNC Cut List", "Labour - Paint - Packing",
)
PART_SHEETS = frozenset({"5. Material Cut List Price", "Tool Shop Items", "CNC Cut List"})
REQUIRED_SHEETS = (*PROCESS_SHEETS, "Total Summary", "Spares Detail", "Spares Summary")
WEIGHT_FIELDS = {"total_grams": Decimal("0.001"), "total_weight_kg": Decimal(1),
                 "part_weight_kg": Decimal(1), "weight_kg": Decimal(1)}
PHYSICAL_FIELDS = frozenset({
    "qty", "quantity", "total_grams", "grams_per_inch", "kg_per_mtr", "weight_kg",
    "total_weight", "total_material_used_inches", "length", "width", "thickness",
    "dimension_to_cut_mm", "dimension_to_cut_inches", "product_part_name",
    "material_to_cut", "optional_item_group_1",
    "total_weight_kg", "part_weight_kg",
})


def numeric_match(previous: Any, current: Any, *, kg_factor: Decimal | None = None) -> bool:
    """Exact quantity; weights compared after conversion/rounding to 0.01 kg."""
    try:
        before, after = Decimal(str(previous)), Decimal(str(current))
        if not before.is_finite() or not after.is_finite():
            return False
        if kg_factor is not None:
            before = (before * kg_factor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            after = (after * kg_factor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return before == after
    except (InvalidOperation, ValueError, TypeError):
        return False


def processing_review_evidence(snapshot: dict[str, Any], comparison: dict[str, Any],
                               sheet_names: set[str], accepted_snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    """Inventory extracted evidence using the user's 5 October reconciliation rules."""
    content = snapshot.get("content", {})
    process_lists = content.get("process_lists", {})
    coverage = []
    part_rows = []
    unexpected_rows = []
    for name in PROCESS_SHEETS:
        rows = process_lists.get(name, [])
        counts = Counter(row.get("status", "unexpected") for row in rows)
        coverage.append({"sheet": name, "present": name in sheet_names,
                         "extracted": name in process_lists, "rowCount": len(rows),
                         "active": counts["active"], "historical": counts["historical"],
                         "unexpected": counts["unexpected"], "required": True})
        for row in rows:
            reference = {"sheet": name, "row": row.get("source_row"),
                         "sourceHash": snapshot.get("source_hashes", {}).get("selected_workbook_saved")}
            if row.get("status") not in {"active", "historical"}:
                unexpected_rows.append(reference)
            if name in PART_SHEETS and row.get("status") == "active":
                description = str(row.get("fields", {}).get("product_part_name") or "").strip()
                part_rows.append({**reference, "description": description, "blankDescription": not description,
                                  "assignmentStatus": "review_required"})
    structural, pricing = [], []
    for change in comparison.get("changes", []):
        fields = set(change.get("field_changes", {}))
        # Legacy semantic classification groups some weight evidence with cost.
        # Physical changes must remain structural in the processing workflow.
        price_only = change.get("classification") in {"rate_source_change", "cost_only_change"} and not (fields & PHYSICAL_FIELDS)
        (pricing if price_only else structural).append(change)
    rates = content.get("rate_state", {})
    missing_rates = [{"material": material, "status": rate.get("status"), "processingBlocker": False}
                     for material, rate in rates.items() if rate.get("effective_rate_per_kg") is None]
    summary = content.get("summary_structure", {})
    physical = []
    if accepted_snapshot:
        from app.milestone2 import _pair_structural_lines
        old_lists = accepted_snapshot.get("content", {}).get("process_lists", {})
        previous = [row for rows in old_lists.values() for row in rows]
        current = [row for rows in process_lists.values() for row in rows]
        paired, _, _, _ = _pair_structural_lines(previous, current)
        for old, new, _, _ in paired:
            if new.get("status") != "active":
                continue
            before, after = old.get("fields", {}), new.get("fields", {})
            for field in ("qty", "quantity", *WEIGHT_FIELDS):
                if field not in before and field not in after:
                    continue
                factor = WEIGHT_FIELDS.get(field)
                physical.append({"sheet": new.get("process_list"), "row": new.get("source_row"),
                                 "field": field, "previous": before.get(field), "current": after.get(field),
                                 "matches": numeric_match(before.get(field), after.get(field), kg_factor=factor),
                                 "status": "missing_previous_evidence" if before.get(field) in (None, "") else
                                           "missing_current_evidence" if after.get(field) in (None, "") else
                                           "matches" if numeric_match(before.get(field), after.get(field), kg_factor=factor) else "value_mismatch",
                                 "basis": "kg rounded to 2 decimals" if factor is not None else "exact quantity"})
    missing_sheets = [name for name in REQUIRED_SHEETS if name not in sheet_names]
    return {
        "sourceHash": snapshot.get("source_hashes", {}).get("selected_workbook_saved"),
        "observedAt": snapshot.get("observed_at"), "sheetCoverage": coverage,
        "requiredSheets": list(REQUIRED_SHEETS), "missingRequiredSheets": missing_sheets,
        "physicalComparisons": physical, "physicalComparisonAvailable": bool(accepted_snapshot),
        "structuralDifferences": structural, "pricingDifferences": pricing,
        "ambiguousMatches": comparison.get("ambiguities", []), "unexpectedRows": unexpected_rows,
        "partRowsRequiringReview": part_rows,
        "summary": {"present": "Total Summary" in sheet_names,
                    "extractedItemCount": len(summary.get("items", [])),
                    "optionGroups": summary.get("option_groups", []),
                    "configurationStatus": "review_required"},
        "missingRateEvidence": missing_rates, "pricingBlocksProcessing": False,
        "completionAvailable": False,
        "rules": {"quantity": "exact", "weight": "kg rounded to 2 decimals", "weightRounding": "half_up",
                  "finalCostTotalBlocksProcessing": False, "rateSelection": "latest entered MaterialRateLog; fallback Default Material rate"},
        "pendingPolicies": ["Reviewed Part assignments", "Model Code Summary configuration", "Spares extraction"],
    }

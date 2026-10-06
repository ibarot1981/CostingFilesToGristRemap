"""Read-only S1KHF import planning and explicit guarded apply support."""

from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.config import load_material_mapping
from app.grist import GristClient
from app.grist_admin import GristAdminClient
from app.grist_repository import _ref
from app.milestone2 import DEFAULT_RATE_DUMP, DEFAULT_RAW_STEEL, _configured_process_lines, _line_comparable_state, extract_product_workbook, extract_raw_steel, read_ods
from app.normalized import capture_source_rows, project_snapshot
from app.normalized_store import GristNormalizedStore
from app.repository import sha256_file
from app.schema import plan_schema


def _object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        parsed = json.loads(value)
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("Expected a JSON object in the accepted Safari snapshot")


def assess_cost_drift(accepted: dict[str, Any], current: dict[str, Any], workbook_total: Any) -> dict[str, Any]:
    """Allow a newer source revision only when known line structure is unchanged.

    New parser columns are reported as newly covered evidence; they cannot be
    treated as differences from fields that the accepted snapshot never read.
    """
    old_lists, new_lists = accepted.get("process_lists", {}), current.get("process_lists", {})
    differences: list[dict[str, Any]] = []
    newly_covered: dict[str, list[str]] = {}
    for sheet in sorted(set(old_lists) | set(new_lists)):
        old, new = old_lists.get(sheet, []), new_lists.get(sheet, [])
        old_fields = set().union(*(row.get("fields", {}) for row in old)) if old else set()
        new_fields = set().union(*(row.get("fields", {}) for row in new)) if new else set()
        newly_covered[sheet] = sorted(new_fields - old_fields)
        common = old_fields & new_fields

        def signature(row: dict[str, Any]) -> str:
            return json.dumps((row.get("status"), row.get("identity_values", {}),
                               {k: v for k, v in _line_comparable_state(row).items() if k in common}),
                              sort_keys=True, ensure_ascii=False, default=str)

        before, after = Counter(map(signature, old)), Counter(map(signature, new))
        removed, added = sum((before - after).values()), sum((after - before).values())
        if removed or added:
            differences.append({"sheet": sheet, "removed_or_changed": removed, "added_or_changed": added})

    mcl = "5. Material Cut List Price"
    def total(rows: list[dict[str, Any]], status: str) -> Decimal:
        return sum((Decimal(str(row["cost_value"])) for row in rows
                    if row.get("status") == status and row.get("cost_value") not in (None, "")), Decimal(0))

    old_active = total(old_lists.get(mcl, []), "active")
    active = total(new_lists.get(mcl, []), "active")
    historical = total(new_lists.get(mcl, []), "historical")
    displayed = Decimal(str(workbook_total))
    delta = displayed - active - historical
    # ODS formula caches are binary floating-point values; this bound covers
    # representation noise only, never a business-cost discrepancy.
    totals_match = abs(delta) <= Decimal("0.00000001")
    return {"cost_only_confirmed": not differences and totals_match,
            "structural_differences": differences, "newly_covered_fields": newly_covered,
            "accepted_active_mcl": str(old_active), "current_active_mcl": str(active),
            "current_historical_mcl_cache": str(historical), "workbook_mcl_c7": str(displayed),
            "mcl_cache_difference": str(delta), "totals_match": totals_match}


def local_preview(workbook: Path, raw_steel: Path) -> dict[str, Any]:
    """Inspect the current source without claiming an accepted Safari baseline."""
    before = sha256_file(workbook)
    raw_before = sha256_file(raw_steel)
    document = read_ods(workbook)
    lines = _configured_process_lines(document)
    raw_rows, _ = extract_raw_steel(read_ods(raw_steel, {"RawSteel"}))
    after = sha256_file(workbook)
    if before != after or raw_before != sha256_file(raw_steel):
        raise ValueError("S1KHF or RawSteel source changed during the read-only projection")
    content = {"process_lists": lines, "material_master_state": {
        row["unique_item_list"]: {} for row in raw_rows if row.get("unique_item_list")
    }}
    dependency_hashes = {"raw_steel": raw_before, **({"rate_log_dump": sha256_file(DEFAULT_RATE_DUMP)} if DEFAULT_RATE_DUMP.is_file() else {})}
    projection = project_snapshot(content, snapshot_key=f"local-unaccepted:{before}", source_hash=before,
                                  material_mappings=load_material_mapping(), dependency_hashes=dependency_hashes,
                                  source_row_cells=capture_source_rows(document, set(lines)))
    return {"mode": "local_unaccepted", "sourceHash": before, "dependencyHashes": dependency_hashes,
            "counts": {key: len(value) for key, value in projection.items() if isinstance(value, list)},
            "reconciliation": projection["reconciliation"], "exceptions": projection["exceptions"]}


def safari_plan(workbook: Path, relative_path: str) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Revalidate Safari target, accepted baseline, source hash, and v5 diff."""
    client = GristClient.from_safari_environment()
    before = sha256_file(workbook)
    files = client.fetch_table_records_with_ids("CostingFile")
    matches = [row for row in files if str(row["fields"].get("RelativePath") or "").casefold() == relative_path.casefold()]
    if len(matches) != 1:
        raise ValueError("Expected one mapped S1KHF CostingFile in validated Safari document")
    file_id = int(matches[0]["id"])
    snapshots = client.fetch_table_records_with_ids("CostingSnapshot")
    accepted = [row for row in snapshots if row["fields"].get("Status") == "accepted" and _ref(row["fields"].get("CostingFile")) == str(file_id)]
    if len(accepted) != 1:
        raise ValueError("Expected exactly one current accepted S1KHF snapshot")
    fields = accepted[0]["fields"]
    hashes = _object(fields.get("SourceHashes"))
    baseline_source_hash = str(hashes.get("selected_workbook_saved") or "")
    if not baseline_source_hash:
        raise ValueError("Accepted Safari snapshot lacks the selected workbook hash")
    snapshot_key = str(fields["SnapshotKey"])
    semantic_content = _object(fields.get("SemanticContent"))
    document = read_ods(workbook)
    current_lists = _configured_process_lines(document)
    workbook_total = extract_product_workbook(document)["summary_totals"]["grand_total"]["cached_value"]
    if workbook_total is None:
        raise ValueError("S1KHF MCL C7 cached total is missing; cost drift cannot be confirmed")
    drift = assess_cost_drift(semantic_content, {"process_lists": current_lists}, workbook_total)
    if not drift["cost_only_confirmed"]:
        raise ValueError(f"S1KHF current source does not reconcile as cost-only drift: {json.dumps(drift, ensure_ascii=False)}")
    projection_content = {**semantic_content, "process_lists": current_lists}
    source_rows = capture_source_rows(document, set(current_lists))
    current_dependencies = {name: sha256_file(path) for name, path in {
        "raw_steel": DEFAULT_RAW_STEEL, "rate_log_dump": DEFAULT_RATE_DUMP,
    }.items() if path.is_file()}
    projection = project_snapshot(projection_content, snapshot_key=snapshot_key,
                                  source_hash=before, material_mappings=load_material_mapping(),
                                  dependency_hashes={**current_dependencies, "accepted_workbook": baseline_source_hash}, source_row_cells=source_rows,
                                  actor="import-plan", reason="Current source cost reconciled to accepted physical baseline")
    after = sha256_file(workbook)
    if before != after or any(sha256_file(path) != current_dependencies.get(name) for name, path in {
        "raw_steel": DEFAULT_RAW_STEEL, "rate_log_dump": DEFAULT_RATE_DUMP,
    }.items() if path.is_file()):
        raise ValueError("S1KHF or rate dependency source changed while planning")
    admin = GristAdminClient.from_environment()
    schema = plan_schema(admin, client.doc_id, workspace_id=client.safari_workspace_id,
                         legacy_doc_id=os.getenv("GRIST_DOC_ID", "").strip())
    schema_diff = schema.to_dict()
    result: dict[str, Any] = {"mode": "safari_dry_run", "documentId": client.doc_id,
                              "snapshotKey": snapshot_key, "sourceHash": before, "baselineSourceHash": baseline_source_hash,
                              "costDriftAssessment": drift, "dependencyHashes": current_dependencies,
                              "schemaDiff": schema_diff,
                              "counts": {key: len(value) for key, value in projection.items() if isinstance(value, list)},
                              "reconciliation": projection["reconciliation"], "exceptions": projection["exceptions"]}
    has_schema_diff = bool(schema.create_tables or schema.add_columns or schema.update_columns)
    if not has_schema_diff:
        result["importDiff"] = GristNormalizedStore(client).plan(projection, snapshot_key=snapshot_key, source_hash=before,
                                                                   baseline_source_hash=baseline_source_hash)["create_counts"]
    else:
        result["importDiff"] = None
        result["blockedReason"] = "Apply the reviewed Safari v5 schema before planning row writes"
    # Row counts may shrink during a resumed multi-table write. The reviewed
    # source/schema/content fingerprint must remain stable across that retry.
    fingerprint = hashlib.sha256(json.dumps({
        "documentId": client.doc_id, "snapshotKey": snapshot_key, "sourceHash": before,
        "baselineSourceHash": baseline_source_hash, "costDriftAssessment": drift,
        "dependencyHashes": current_dependencies,
        "schemaVersion": schema.schema_version, "projection": projection,
    }, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()
    result["planFingerprint"] = fingerprint
    return result, projection if not has_schema_diff else None


def apply_reviewed_plan(workbook: Path, relative_path: str, *, expected_plan_fingerprint: str) -> dict[str, Any]:
    plan, projection = safari_plan(workbook, relative_path)
    if plan["planFingerprint"] != expected_plan_fingerprint:
        raise ValueError("Reviewed Safari schema/import plan changed; no rows were written")
    if projection is None:
        raise ValueError("Safari schema is not ready for normalized rows")
    client = GristClient.from_safari_environment()
    store = GristNormalizedStore(client)
    import_plan = store.plan(projection, snapshot_key=plan["snapshotKey"], source_hash=plan["sourceHash"],
                             baseline_source_hash=plan["baselineSourceHash"])
    dependencies = {name: path for name, path in {"raw_steel": DEFAULT_RAW_STEEL,
                    "rate_log_dump": DEFAULT_RATE_DUMP}.items() if name in plan["dependencyHashes"]}
    return {"created": store.apply(projection, import_plan, source_path=workbook,
                                    dependency_paths=dependencies, dependency_hashes=plan["dependencyHashes"]),
            "sourceHash": plan["sourceHash"], "snapshotKey": plan["snapshotKey"]}

"""Guarded, resumable Grist writer for the normalized S1KHF subset."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.grist import GristClient
from app.repository import sha256_file


TABLES: tuple[tuple[str, str, str, dict[str, str]], ...] = (
    ("parts", "ProductPart", "PartKey", {"display_name": "DisplayName", "status": "Status"}),
    ("part_revisions", "PartRevision", "RevisionKey", {"part_key": "ProductPart", "revision": "Revision", "status": "Status", "snapshot_key": "Snapshot"}),
    ("materials", "Material", "MaterialKey", {"canonical_name": "CanonicalName", "ods_display_name": "ODSDisplayName", "mapping_status": "MappingStatus"}),
    ("purchase_items", "PurchaseItem", "ItemKey", {"display_name": "DisplayName", "status": "Status"}),
    ("operations", "ProcessOperation", "OperationKey", {"name": "Name"}),
    ("work_centers", "WorkCenter", "CenterKey", {"name": "Name", "status": "Status"}),
    ("line_masters", "LineMaster", "LineKey", {"part_key": "ProductPart", "process_type": "ProcessType", "identity": "Identity", "status": "Status", "source_part_name": "SourcePartName"}),
    ("line_revisions", "LineRevision", "RevisionKey", {"master_key": "LineMaster", "previous_key": "PreviousRevision", "physical_signature": "PhysicalSignature", "status": "Status", "snapshot_key": "Snapshot", "actor": "Actor", "reason": "Reason"}),
    ("line_details", "LineDetail", "DetailKey", {"revision_key": "LineRevision", "process_type": "ProcessType", "material_key": "Material", "purchase_item_key": "PurchaseItem", "operation_key": "ProcessOperation", "work_center_key": "WorkCenter", "source_department": "SourceDepartment", "issue_route": "IssueRoute", "activity_kind": "ActivityKind", "quantity": "Quantity", "quantity_uom": "QuantityUOM", "dimension_mm": "DimensionMM", "dimension_inches": "DimensionInches", "weight_grams": "WeightGrams", "weight_kg": "WeightKg", "part_weight_kg": "PartWeightKg", "length": "Length", "width": "Width", "thickness": "Thickness", "issue_slip_no": "IssueSlipNo", "issue_slip_desc": "IssueSlipDesc", "internal_making_cost": "InternalMakingCost", "external_machining_cost": "ExternalMachiningCost", "rate_cached": "RateCached", "cost_cached": "CostCached", "item_name": "ItemName", "material_display_name": "MaterialDisplayName", "option_group": "OptionGroup"}),
    ("source_observations", "SourceLineObservation", "ObservationKey", {"snapshot_key": "Snapshot", "source_hash": "SourceHash", "dependency_hashes": "DependencyHashes", "parser_version": "ParserVersion", "sheet": "SheetName", "row": "SourceRow", "status": "Status", "cells": "Cells", "observed_fields": "ObservedFields", "part_display_name": "PartDisplayName", "cached_cost": "CachedCost", "current_cost": "CurrentCost", "cr_reference": "CRReference"}),
    ("source_mappings", "SourceLineMapping", "MappingKey", {"observation_key": "Observation", "master_key": "LineMaster", "status": "Status"}),
    ("audit", "LineAuditItem", "AuditKey", {"master_key": "LineMaster", "revision_key": "LineRevision", "previous_key": "PreviousRevision", "observation_key": "Observation", "actor": "Actor", "reason": "Reason", "cr_reference": "CRReference"}),
)
REFS = {"ProductPart", "PartRevision", "Material", "PurchaseItem", "ProcessOperation", "WorkCenter", "LineMaster", "LineRevision", "CostingSnapshot", "SourceLineObservation"}
REF_TARGETS = {"Snapshot": "CostingSnapshot", "PreviousRevision": "LineRevision", "Observation": "SourceLineObservation"}
KEY_COLUMNS = {table: key for _, table, key, _ in TABLES}


class NormalizedStore:
    """Memory adapter with the same append-only key contract as Grist."""

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, dict[str, Any]]] = {table: {} for _, table, _, _ in TABLES}

    def apply(self, projection: dict[str, Any]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for group, table, _, _ in TABLES:
            created = 0
            for item in projection.get(group, []):
                key = item["key"]
                previous = self.rows[table].get(key)
                if previous is not None and previous != item:
                    raise ValueError(f"Immutable {table} key changed: {key}")
                if previous is None:
                    self.rows[table][key] = item
                    created += 1
            counts[table] = created
        return counts


class GristNormalizedStore:
    """Writes each table by deterministic key; interrupted imports resume."""

    def __init__(self, client: GristClient) -> None:
        self.client = client

    def query(self, *, snapshot_key: str, filters: dict[str, str], offset: int, limit: int) -> dict[str, Any]:
        """Join individual Grist child records into the storage-neutral UI DTO."""
        self.client.validate_safari_write_target()
        records = {table: self.client.fetch_table_records_with_ids(table) for table in (
            "CostingSnapshot", "LineMaster", "LineRevision", "LineDetail",
            "SourceLineObservation", "SourceLineMapping", "LineAuditItem",
        )}
        snapshots = [item for item in records["CostingSnapshot"]
                     if item["fields"].get("SnapshotKey") == snapshot_key and item["fields"].get("Status") == "accepted"]
        if len(snapshots) != 1:
            raise ValueError("Exactly one accepted Safari snapshot must match the normalized query")
        snapshot = snapshots[0]
        sid = int(snapshot["id"])

        def ref(value: Any) -> int | None:
            if isinstance(value, int): return value
            if isinstance(value, list) and value and value[0] == "R" and len(value) > 2: return int(value[2])
            return None

        def obj(value: Any) -> Any:
            if isinstance(value, str):
                try: return json.loads(value)
                except json.JSONDecodeError: return value
            return value

        by_id = {table: {int(item["id"]): item["fields"] for item in rows} for table, rows in records.items()}
        # Unchanged lines can reuse a revision from an accepted predecessor.
        # Follow explicit lineage; row IDs alone cannot establish chronology.
        lineage: dict[int, int] = {}
        ancestor = snapshot
        while ancestor is not None:
            ancestor_id = int(ancestor["id"])
            if ancestor_id in lineage:
                raise ValueError("Accepted snapshot lineage contains a cycle")
            lineage[ancestor_id] = len(lineage)
            previous = ancestor["fields"].get("PreviousSnapshotKey")
            if not previous:
                break
            parents = [item for item in records["CostingSnapshot"] if item["fields"].get("SnapshotKey") == previous
                       and item["fields"].get("Status") == "accepted"
                       and ref(item["fields"].get("CostingFile")) == ref(snapshot["fields"].get("CostingFile"))]
            if len(parents) != 1:
                raise ValueError("Accepted snapshot predecessor is missing or ambiguous")
            ancestor = parents[0]
        revisions: dict[int, list[tuple[int, dict[str, Any]]]] = {}
        for item in records["LineRevision"]:
            fields = item["fields"]
            if ref(fields.get("Snapshot")) not in lineage:
                continue
            master_id = ref(fields.get("LineMaster"))
            if master_id is not None:
                revisions.setdefault(master_id, []).append((int(item["id"]), fields))
        details = {ref(item["fields"].get("LineRevision")): item["fields"] for item in records["LineDetail"]}
        audits: dict[int, list[dict[str, Any]]] = {}
        for item in records["LineAuditItem"]:
            fields = item["fields"]
            revision = by_id["LineRevision"].get(ref(fields.get("LineRevision")))
            if not revision or ref(revision.get("Snapshot")) not in lineage:
                continue
            master_id = ref(fields.get("LineMaster"))
            if master_id is not None:
                audits.setdefault(master_id, []).append({"key": fields.get("AuditKey"), "actor": fields.get("Actor"),
                    "reason": fields.get("Reason"), "cr_reference": fields.get("CRReference")})
        rows: list[dict[str, Any]] = []
        for item in records["SourceLineMapping"]:
            mapping = item["fields"]
            observation = by_id["SourceLineObservation"].get(ref(mapping.get("Observation")))
            if not observation or ref(observation.get("Snapshot")) != sid:
                continue
            master_id = ref(mapping.get("LineMaster"))
            master = by_id["LineMaster"].get(master_id) if master_id is not None else None
            revision_id, revision = max(revisions.get(master_id, []), default=(None, None),
                                        key=lambda row: (-lineage[ref(row[1].get("Snapshot"))], row[0]))
            detail = details.get(revision_id)
            row = {"mapping": {"status": mapping.get("Status"), "master_key": master.get("LineKey") if master else None},
                   "observation": {"sheet": observation.get("SheetName"), "row": observation.get("SourceRow"),
                       "status": observation.get("Status"), "part_display_name": observation.get("PartDisplayName") or "",
                       "cells": obj(observation.get("Cells")) or {}, "cached_cost": observation.get("CachedCost"),
                       "dependency_hashes": obj(observation.get("DependencyHashes")) or {}},
                   "master": {"key": master.get("LineKey"), "process_type": master.get("ProcessType")} if master else None,
                   "revision": {"key": revision.get("RevisionKey"), "previous_key": (
                       by_id["LineRevision"].get(ref(revision.get("PreviousRevision")) or -1, {}).get("RevisionKey"))}
                       if revision else None,
                   "detail": {"material_display_name": detail.get("MaterialDisplayName") or "",
                       "quantity": detail.get("Quantity"), "cost_cached": detail.get("CostCached"),
                       "activity_kind": detail.get("ActivityKind")} if detail else None,
                   "audit": audits.get(master_id, [])}
            if filters.get("sheet") and row["observation"]["sheet"] != filters["sheet"]: continue
            if filters.get("process") and (row["master"] or {}).get("process_type") != filters["process"]: continue
            if filters.get("master") and (row["master"] or {}).get("key") != filters["master"]: continue
            if filters.get("part") and filters["part"].casefold() not in row["observation"]["part_display_name"].casefold(): continue
            if filters.get("material") and filters["material"].casefold() not in (row["detail"] or {}).get("material_display_name", "").casefold(): continue
            if filters.get("status") and row["observation"]["status"] != filters["status"]: continue
            rows.append(row)
        rows.sort(key=lambda row: (row["observation"]["sheet"], row["observation"]["row"]))
        return {"total": len(rows), "items": rows[offset:offset + limit]}

    def plan(self, projection: dict[str, Any], *, snapshot_key: str, source_hash: str,
             baseline_source_hash: str | None = None) -> dict[str, Any]:
        self.client.validate_safari_write_target()
        snapshots = self.client.fetch_table_records_with_ids("CostingSnapshot")
        matching = [row for row in snapshots if row["fields"].get("SnapshotKey") == snapshot_key and row["fields"].get("Status") == "accepted"]
        if len(matching) != 1:
            raise ValueError("Exactly one accepted Safari snapshot must match the requested baseline")
        source_hashes = matching[0]["fields"].get("SourceHashes")
        if isinstance(source_hashes, str):
            source_hashes = json.loads(source_hashes)
        expected_baseline_hash = baseline_source_hash or source_hash
        if (source_hashes or {}).get("selected_workbook_saved") != expected_baseline_hash:
            raise ValueError("Accepted Safari workbook baseline changed")
        ids: dict[str, dict[str, int]] = {"CostingSnapshot": {snapshot_key: int(matching[0]["id"])}}
        existing_fields: dict[str, dict[str, dict[str, Any]]] = {}
        counts: dict[str, int] = {}
        for group, table, key_column, _ in TABLES:
            records = self.client.fetch_table_records_with_ids(table)
            by_key: dict[str, int] = {}
            for record in records:
                key = str(record["fields"].get(key_column) or "")
                if key in by_key:
                    raise ValueError(f"Duplicate {table}.{key_column} key: {key}")
                by_key[key] = int(record["id"])
            ids[table] = by_key
            existing_fields[table] = {str(record["fields"].get(key_column) or ""): record["fields"] for record in records}
            counts[table] = sum(item["key"] not in by_key for item in projection.get(group, []))
        return {"create_counts": counts, "ids": ids, "existing_fields": existing_fields,
                "snapshot_key": snapshot_key, "source_hash": source_hash,
                "baseline_source_hash": expected_baseline_hash}

    def apply(self, projection: dict[str, Any], plan: dict[str, Any], *, source_path: Path | None = None,
              dependency_paths: dict[str, Path] | None = None,
              dependency_hashes: dict[str, str] | None = None) -> dict[str, int]:
        fresh = self.plan(projection, snapshot_key=plan["snapshot_key"], source_hash=plan["source_hash"],
                          baseline_source_hash=plan.get("baseline_source_hash"))
        ids = fresh["ids"]
        created: dict[str, int] = {}
        def check_guards() -> None:
            if source_path is not None and sha256_file(source_path) != plan["source_hash"]:
                raise ValueError("S1KHF source changed during staged Grist import")
            for name, path in (dependency_paths or {}).items():
                if sha256_file(path) != (dependency_hashes or {}).get(name):
                    raise ValueError(f"{name} source changed during staged Grist import")
            live_baselines = self.client.fetch_table_records_with_ids("CostingSnapshot")
            if not any(row["fields"].get("SnapshotKey") == plan["snapshot_key"] and row["fields"].get("Status") == "accepted"
                       and (json.loads(row["fields"]["SourceHashes"]) if isinstance(row["fields"].get("SourceHashes"), str)
                            else row["fields"].get("SourceHashes", {})).get("selected_workbook_saved") == plan["baseline_source_hash"]
                       for row in live_baselines):
                raise ValueError("Accepted Safari baseline changed during staged import")

        for group, table, key_column, columns in TABLES:
            count = 0
            pending: list[tuple[str, dict[str, Any]]] = []

            def flush() -> None:
                nonlocal count
                if not pending:
                    return
                check_guards()
                self.client.validate_safari_write_target()
                saved = self.client.create_table_records(table, [{"fields": fields} for _, fields in pending])
                if len(saved) != len(pending):
                    raise ValueError(f"Grist returned an incomplete {table} batch; replan before retry")
                for (key, _), record in zip(pending, saved):
                    ids[table][key] = int(record["id"])
                    count += 1
                pending.clear()

            for item in projection.get(group, []):
                fields: dict[str, Any] = {key_column: item["key"]}
                for source, target in columns.items():
                    value = item.get(source)
                    if value is None:
                        continue
                    if target in REFS or target in REF_TARGETS:
                        referenced_table = REF_TARGETS.get(target, target)
                        if value not in ids[referenced_table] and referenced_table == table:
                            flush()
                        value = ids[referenced_table][value]
                    elif isinstance(value, (dict, list)):
                        value = json.dumps(value, ensure_ascii=False, sort_keys=True)
                    elif target in {"Quantity", "DimensionMM", "DimensionInches", "WeightGrams", "WeightKg", "PartWeightKg", "Length", "Width", "Thickness", "InternalMakingCost", "ExternalMachiningCost", "RateCached", "CostCached", "CachedCost", "CurrentCost"}:
                        value = float(value)
                    fields[target] = value
                if item["key"] in ids[table]:
                    existing = fresh["existing_fields"][table][item["key"]]
                    mismatched = [name for name, value in fields.items() if existing.get(name) != value]
                    if mismatched:
                        raise ValueError(f"Immutable {table} key has different fields: {item['key']} ({', '.join(mismatched)})")
                    continue
                pending.append((item["key"], fields))
                if len(pending) >= 25:
                    flush()
            flush()
            created[table] = count
        return created

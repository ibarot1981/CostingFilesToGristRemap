"""Canonical Parts and immutable, source-scoped mapping review."""
from __future__ import annotations
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import sqlite3
from threading import RLock
from time import monotonic
import unicodedata
from typing import Any
from app.domain import utc_now
from app.exceptions import GristError
from app.grist_types import decode_grist_any_object, grist_datetime, datetime_text
from app.part_identity import PartIdentityError


PART_SHEETS = {"5. Material Cut List Price", "Tool Shop Items", "CNC Cut List"}
MAPPING_TABLE = "PartMappingReview"
MAPPING_POLICY_VERSION = "part-source-labels-v2"
MAPPING_SEMANTIC_POLICY_VERSION = "part-mapping-semantic-v1"
MAPPING_LINE_EVIDENCE_VERSION = "part-mapping-line-v1"
MAPPING_LABEL_FIELD = {
    "5. Material Cut List Price": "product_part_name",
    "Tool Shop Items": "product_part_name",
    "CNC Cut List": "part_category",
}

# This policy deliberately names the source-owned manufacturing fields used to
# preserve mappings. Rates, calculated totals, presentation formatting, remarks
# that are not engineering attributes, CR-log metadata and document properties
# remain provenance/costing evidence and do not invalidate a Part assignment.
_MAPPING_SEMANTIC_FIELDS = {
    "5. Material Cut List Price": (
        "product_part_name", "material_to_cut", "dimension_to_cut_mm",
        "dimension_to_cut_inches", "qty", "optional_item_group_1", "in_use",
    ),
    "Tool Shop Items": (
        "product_part_name", "item_name", "material_to_cut", "dimension_to_cut_mm",
        "weight_per_meter_kg", "total_weight_kg", "qty", "optional_item_group_1", "in_use",
        "toolshop_part_name", "toolshop_remarks", "job_remarks",
    ),
    "CNC Cut List": (
        "part_category", "product_part_name", "material_to_cut", "length", "width",
        "thickness", "part_weight_kg", "total_weight_kg", "qty", "optional_item_group_1", "in_use",
    ),
}
_MAPPING_NUMERIC_FIELDS = frozenset({
    "dimension_to_cut_mm", "dimension_to_cut_inches", "weight_per_meter_kg", "total_weight_kg",
    "part_weight_kg", "qty", "length", "width", "thickness",
})
_MAPPING_MODIFICATION_ANCHORS = {
    "5. Material Cut List Price": ("material_to_cut", "dimension_to_cut_mm", "dimension_to_cut_inches"),
    "Tool Shop Items": ("item_name", "material_to_cut", "dimension_to_cut_mm", "toolshop_part_name"),
    "CNC Cut List": ("product_part_name", "material_to_cut", "length", "width", "thickness"),
}

# Part searches happen on each debounced keystroke. Keep the combined registry
# records briefly per Grist document; every mapping write bypasses this cache,
# and Part mutations invalidate it. The UI receives the cache age so it never
# labels this advisory search projection as a newly confirmed save.
_SEARCH_RECORD_CACHE: dict[tuple[str, str], tuple[float, list[dict[str, Any]]]] = {}
_SEARCH_RECORD_CACHE_LOCK = RLock()
_SEARCH_RECORD_CACHE_TTL_SECONDS = 5.0


def invalidate_part_search_cache(document_key: str | None = None) -> None:
    with _SEARCH_RECORD_CACHE_LOCK:
        if document_key is None:
            _SEARCH_RECORD_CACHE.clear()
        else:
            for key in [key for key in _SEARCH_RECORD_CACHE if key[0] == str(document_key)]:
                _SEARCH_RECORD_CACHE.pop(key, None)


class PartSourceGroups(list):
    """List-compatible source groups carrying extraction diagnostics for the API."""

    def __init__(self, groups=(), diagnostics=None):
        super().__init__(groups)
        self.diagnostics = diagnostics or {}


class PartConflict(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def name_key(name: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", name).split()).casefold()


def fingerprint(value: Any) -> str:
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _mapping_display(value: Any) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value)).split())


def _mapping_normalized(value: Any, field: str) -> str:
    display = _mapping_display(value)
    if field in _MAPPING_NUMERIC_FIELDS:
        try:
            number = Decimal(display.replace(",", ""))
            if number.is_finite():
                normalized = format(number.normalize(), "f")
                return "0" if normalized in {"-0", ""} else normalized
        except (InvalidOperation, ValueError):
            pass
    if field == "in_use":
        folded = display.casefold()
        if not folded or folded in {"yes", "y", "true", "1", "active", "in use", "used"}:
            return "active"
        if folded in {"no", "n", "false", "0", "inactive", "not in use", "unused", "delete"}:
            return "inactive"
        return "unexpected:" + folded
    return display.casefold()


def _quantity_unit(row: dict[str, Any]) -> str:
    # These three configured Part sheets describe counts of physical pieces.
    # The header is retained in provenance; the semantic convention is Nos.
    return "nos"


def _line_semantic_evidence(sheet: str, row: dict[str, Any]) -> dict[str, Any]:
    fields = row.get("fields") if isinstance(row.get("fields"), dict) else {}
    available = row.get("available_fields", row.get("availableFields"))
    available_fields = set(available) if isinstance(available, (list, tuple, set)) else set(fields)
    values: list[dict[str, Any]] = []
    for field in _MAPPING_SEMANTIC_FIELDS[sheet]:
        if field not in available_fields:
            values.append({"field": field, "state": "unavailable", "value": None, "display": None})
            continue
        raw = fields.get(field)
        display = _mapping_display(raw) if raw is not None else ""
        if field == "in_use":
            # Blank and explicit active flags have the same effective meaning;
            # sourceState still preserves which representation was observed.
            state = "known"
            normalized = _mapping_normalized(raw or "", field)
            values.append({"field": field, "state": state, "sourceState": "blank" if not display else "value",
                "value": normalized, "display": display or None})
        elif raw is None or not display:
            values.append({"field": field, "state": "blank", "value": None, "display": None})
        else:
            values.append({"field": field, "state": "value", "value": _mapping_normalized(raw, field), "display": display})
    if "qty" in available_fields:
        values.append({"field": "quantity_uom", "state": "known", "value": _quantity_unit(row), "display": "Nos"})
    else:
        values.append({"field": "quantity_uom", "state": "unavailable", "value": None, "display": None})
    if sheet == "CNC Cut List":
        for dimension in ("length", "width", "thickness"):
            values.append({"field": dimension + "_uom", "state": "value" if dimension in available_fields else "unavailable",
                "value": "mm" if dimension in available_fields else None,
                "display": "mm" if dimension in available_fields else None})
    values.sort(key=lambda item: item["field"])
    line_projection = [{"field": item["field"], "state": item["state"], "value": item.get("value")}
        for item in values]
    return {"fields": values, "fingerprint": fingerprint([MAPPING_SEMANTIC_POLICY_VERSION, sheet, line_projection])}


def _group_semantic_fingerprint(sheet: str, lines: list[dict[str, Any]]) -> str:
    return fingerprint([MAPPING_SEMANTIC_POLICY_VERSION, sheet, sorted(str(line.get("fingerprint") or "") for line in lines)])


def _ensure_group_semantic_evidence(groups: list[dict[str, Any]]) -> None:
    """Normalize API-shaped source groups before review or persistence.

    The normal extractor attaches these fields directly. A few supported
    callers build ``PartSourceGroups`` from already-extracted rows; deriving
    their current snapshot here preserves the same policy without weakening
    the completeness check on persisted evidence.
    """
    for group in groups:
        sheet = str(group.get("sheet") or "")
        if sheet not in _MAPPING_SEMANTIC_FIELDS:
            continue
        rows = group.get("rows") if isinstance(group.get("rows"), list) else []
        evidence: dict[int, dict[str, Any]] = {}
        for row in rows:
            try:
                source_row = int(row.get("row", row.get("source_row")))
            except (TypeError, ValueError):
                continue
            evidence[source_row] = _line_semantic_evidence(sheet, row)
        group["semanticPolicyVersion"] = MAPPING_SEMANTIC_POLICY_VERSION
        group["semanticEvidence"] = evidence
        group["semanticFingerprint"] = _group_semantic_fingerprint(sheet, list(evidence.values()))


def _snapshot_lines_from_records(rows: list[dict[str, Any]], sheet: str) -> tuple[list[dict[str, Any]] | None, str | None]:
    if not rows:
        return None, None
    policy_versions = {str(row.get("SemanticPolicyVersion") or "") for row in rows}
    fingerprints = {str(row.get("SemanticFingerprint") or "") for row in rows}
    if policy_versions != {MAPPING_SEMANTIC_POLICY_VERSION} or len(fingerprints) != 1 or not next(iter(fingerprints)):
        return None, None
    lines = []
    coordinates = set()
    for row in rows:
        evidence = decode_grist_any_object(row.get("SemanticEvidence"))
        if evidence is None or not isinstance(evidence.get("fields"), list):
            return None, None
        projected = [{"field": item.get("field"), "state": item.get("state"), "value": item.get("value")}
            for item in evidence["fields"] if isinstance(item, dict)]
        if len(projected) != len(evidence["fields"]) or evidence.get("fingerprint") != fingerprint([MAPPING_SEMANTIC_POLICY_VERSION, sheet, projected]):
            return None, None
        coordinate = (str(row.get("SheetName") or ""), int(row.get("SourceRow") or 0))
        if coordinate in coordinates:
            return None, None
        coordinates.add(coordinate)
        lines.append(evidence)
    expected = next(iter(fingerprints))
    if len(lines) != len(rows) or _group_semantic_fingerprint(sheet, lines) != expected:
        return None, None
    return lines, expected


def _line_field_map(line: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(item.get("field")): item for item in line.get("fields", []) if isinstance(item, dict)}


def _semantic_differences(previous: list[dict[str, Any]], current: list[dict[str, Any]], sheet: str, *, blank_label: bool = False) -> list[dict[str, Any]]:
    """Compare line multisets, pairing only unique, explainable modifications."""
    old_by_fp: dict[str, list[dict[str, Any]]] = defaultdict(list)
    new_by_fp: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for line in previous:
        old_by_fp[str(line.get("fingerprint") or "")].append(line)
    for line in current:
        new_by_fp[str(line.get("fingerprint") or "")].append(line)
    duplicate_signatures = any(len(rows) > 1 for rows in old_by_fp.values()) or any(len(rows) > 1 for rows in new_by_fp.values())
    matched_old: set[int] = set()
    matched_new: set[int] = set()
    for key in set(old_by_fp) & set(new_by_fp):
        old_rows, new_rows = old_by_fp[key], new_by_fp[key]
        for old, new in zip(old_rows, new_rows):
            matched_old.add(id(old)); matched_new.add(id(new))
    old_left = [line for line in previous if id(line) not in matched_old]
    new_left = [line for line in current if id(line) not in matched_new]
    paired_old: set[int] = set()
    paired_new: set[int] = set()
    candidates: dict[int, list[int]] = {}
    reverse: dict[int, list[int]] = defaultdict(list)
    anchors = _MAPPING_MODIFICATION_ANCHORS.get(sheet, ())
    for n_idx, incoming in enumerate(new_left):
        incoming_fields = _line_field_map(incoming)
        hits = []
        if not blank_label:
            for b_idx, baseline in enumerate(old_left):
                baseline_fields = _line_field_map(baseline)
                shared = [name for name in anchors if baseline_fields.get(name, {}).get("state") == "value"
                    and incoming_fields.get(name, {}).get("state") == "value"]
                if shared and all(baseline_fields[name].get("value") == incoming_fields[name].get("value") for name in shared):
                    hits.append(b_idx)
        candidates[n_idx] = hits
        for b_idx in hits:
            reverse[b_idx].append(n_idx)
    differences: list[dict[str, Any]] = []
    for n_idx, hits in candidates.items():
        if len(hits) == 1 and len(reverse[hits[0]]) == 1 and not duplicate_signatures:
            b_idx = hits[0]
            baseline, incoming = old_left[b_idx], new_left[n_idx]
            old_fields, new_fields = _line_field_map(baseline), _line_field_map(incoming)
            changes = [{"field": name, "previous": old_fields.get(name), "current": new_fields.get(name)}
                for name in sorted(set(old_fields) | set(new_fields)) if old_fields.get(name) != new_fields.get(name)]
            differences.append({"type": "modified", "previous": baseline, "current": incoming, "fieldDifferences": changes})
            paired_old.add(b_idx); paired_new.add(n_idx)
    remaining_old = [index for index in range(len(old_left)) if index not in paired_old]
    remaining_new = [index for index in range(len(new_left)) if index not in paired_new]
    if not blank_label and not duplicate_signatures and len(remaining_old) == 1 and len(remaining_new) == 1:
        b_idx, n_idx = remaining_old[0], remaining_new[0]
        baseline, incoming = old_left[b_idx], new_left[n_idx]
        old_fields, new_fields = _line_field_map(baseline), _line_field_map(incoming)
        changes = [{"field": name, "previous": old_fields.get(name), "current": new_fields.get(name)}
            for name in sorted(set(old_fields) | set(new_fields)) if old_fields.get(name) != new_fields.get(name)]
        differences.append({"type": "modified", "previous": baseline, "current": incoming, "fieldDifferences": changes})
        paired_old.add(b_idx); paired_new.add(n_idx)
    unmatched_old = [index for index in range(len(old_left)) if index not in paired_old]
    unmatched_new = [index for index in range(len(new_left)) if index not in paired_new]
    duplicate_match_is_ambiguous = duplicate_signatures and any(candidates.get(index) for index in unmatched_new)
    if (blank_label or duplicate_match_is_ambiguous) and unmatched_old and unmatched_new:
        differences.extend({"type": "ambiguous", "previous": old_left[index], "current": None,
            "fieldDifferences": [], "reason": "Blank labels or duplicate source lines prevent a unique correspondence."} for index in unmatched_old)
        differences.extend({"type": "ambiguous", "previous": None, "current": new_left[index],
            "fieldDifferences": [], "reason": "Blank labels or duplicate source lines prevent a unique correspondence."} for index in unmatched_new)
    elif unmatched_old and unmatched_new and (len(unmatched_old) > 1 or len(unmatched_new) > 1):
        for index in unmatched_old:
            differences.append({"type": "ambiguous", "previous": old_left[index], "current": None,
                "fieldDifferences": [], "reason": "Several unmatched source lines prevent a unique correspondence."})
        for index in unmatched_new:
            differences.append({"type": "ambiguous", "previous": None, "current": new_left[index],
                "fieldDifferences": [], "reason": "Several unmatched source lines prevent a unique correspondence."})
    else:
        differences.extend({"type": "deleted", "previous": old_left[index], "current": None, "fieldDifferences": []} for index in unmatched_old)
        differences.extend({"type": "added", "previous": None, "current": new_left[index], "fieldDifferences": []} for index in unmatched_new)
    order = {"modified": 0, "added": 1, "deleted": 2, "ambiguous": 3}
    return sorted(differences, key=lambda item: (order.get(item["type"], 9),
        str((item.get("previous") or item.get("current") or {}).get("fingerprint") or "")))


class MemoryPartStore:
    available = True
    lock = RLock()

    def __init__(self):
        self.tables: dict[str, list[dict[str, Any]]] = {"ProductPart": [], MAPPING_TABLE: []}

    def records(self, table: str):
        return self.tables[table]

    def append(self, table: str, fields: list[dict[str, Any]]):
        rows = [{"id": len(self.tables[table]) + index + 1, "fields": dict(item)} for index, item in enumerate(fields)]
        self.tables[table].extend(rows)
        return rows


class GristPartStore:
    lock = RLock()

    def __init__(self, client):
        self.client = client
        self.available = True

    def legacy_part_records(self):
        return self.records("ProductPart")

    def records(self, table: str):
        try:
            rows = self.client.fetch_table_records_with_ids(table)
            self.available = True
            return rows
        except GristError as exc:
            if "404" not in str(exc):
                raise
            self.available = False
            return []

    def append(self, table: str, fields: list[dict[str, Any]]):
        raise PartConflict("PART_LEGACY_READ_ONLY", "Legacy Grist Part records are read-only; new Parts and mappings use the local Part registry.")


class PartRegistryMappingStore:
    """Read legacy and managed Grist Parts; persist all mapping evidence in Grist."""

    def __init__(self, legacy_store, identity_store):
        self.legacy_store = legacy_store
        self.identity_store = identity_store
        self.lock = identity_store.lock
        self.available = True
        self.schema_message = None
        self.schema_message = None
        self.legacy_history_available = True
        self.legacy_parts_available = True
        self.document_key = str(getattr(getattr(identity_store, "client", None), "doc_id", ""))
        self.search_cache_age_seconds = 0.0

    def _same_grist_document(self) -> bool:
        left = getattr(self.legacy_store, "client", None)
        right = getattr(self.identity_store, "client", None)
        left_doc = str(getattr(left, "doc_id", "") or "")
        right_doc = str(getattr(right, "doc_id", "") or "")
        return bool(left_doc and left_doc == right_doc)

    @staticmethod
    def _merge_record_rows(*collections: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Combine compatible stores without duplicating the same Grist row."""
        merged: dict[str, dict[str, Any]] = {}
        anonymous = 0
        for rows in collections:
            for row in rows:
                row_id = row.get("id")
                fields = row.get("fields", {})
                if row_id is not None:
                    key = f"record:{row_id}"
                else:
                    key = str(fields.get("ReviewKey") or fields.get("PartKey") or fields.get("StablePartId") or "")
                    if not key:
                        anonymous += 1
                        key = f"anonymous:{anonymous}"
                # Later, managed rows take precedence when both readers return
                # the same record ID with slightly different projections.
                merged[key] = row
        return list(merged.values())

    def legacy_part_records(self):
        rows = self.legacy_store.legacy_part_records() if hasattr(self.legacy_store, "legacy_part_records") else self.legacy_store.records("ProductPart")
        self.legacy_parts_available = getattr(self.legacy_store, "available", True)
        return rows

    def records(self, table: str, *, fresh: bool = False):
        cache_key = (self.document_key, table)
        if table in {"ProductPart", "PartNameAlias"} and self.document_key and not fresh:
            with _SEARCH_RECORD_CACHE_LOCK:
                cached = _SEARCH_RECORD_CACHE.get(cache_key)
                if cached and monotonic() - cached[0] <= _SEARCH_RECORD_CACHE_TTL_SECONDS:
                    self.search_cache_age_seconds = max(self.search_cache_age_seconds, monotonic() - cached[0])
                    return cached[1]
        if table == "ProductPart":
            if self._same_grist_document() and hasattr(self.identity_store, "_rows"):
                # The managed and legacy identities share ProductPart in Grist.
                # One full-table read already contains both; do not read it again
                # through the repository's legacy adapter.
                result = self.identity_store._rows("ProductPart")
                self.legacy_parts_available = True
            elif hasattr(self.identity_store, "canonical_part_records"):
                result = self._merge_record_rows(self.legacy_part_records(), self.identity_store.canonical_part_records())
            else:
                result = self.legacy_part_records()
        elif table == MAPPING_TABLE:
            schema_check = getattr(self.identity_store, "mapping_evidence_schema_available", None)
            if callable(schema_check):
                try:
                    if not schema_check():
                        self.available = False
                        self.schema_message = "Part mapping history is readable, but Save requires Grist schema v14 semantic evidence columns."
                except Exception:
                    self.available = False
                    self.schema_message = "Part mapping schema could not be verified; Save is disabled until schema v14 is confirmed."
            if self._same_grist_document() and hasattr(self.identity_store, "mapping_records"):
                return self.identity_store.mapping_records()
            legacy = self.legacy_store.records(MAPPING_TABLE)
            self.legacy_history_available = getattr(self.legacy_store, "available", True)
            if hasattr(self.identity_store, "mapping_records"):
                return self._merge_record_rows(legacy, self.identity_store.mapping_records())
            return legacy
        else:
            result = self.legacy_store.records(table)
        if table in {"ProductPart", "PartNameAlias"} and self.document_key:
            with _SEARCH_RECORD_CACHE_LOCK:
                _SEARCH_RECORD_CACHE[cache_key] = (monotonic(), result)
                # Bound the process-wide cache even if tests or a multi-doc
                # installation construct many registry clients.
                while len(_SEARCH_RECORD_CACHE) > 48:
                    oldest = min(_SEARCH_RECORD_CACHE, key=lambda key: _SEARCH_RECORD_CACHE[key][0])
                    _SEARCH_RECORD_CACHE.pop(oldest, None)
        return result

    def append(self, table: str, fields: list[dict[str, Any]]):
        if table == MAPPING_TABLE:
            try:
                if hasattr(self.identity_store, "client"):
                    return self.identity_store.append_mapping_records(fields)
                return self.identity_store.append_mapping_records(fields)
            except (sqlite3.IntegrityError, PartIdentityError) as exc:
                raise PartConflict("PART_REVIEW_CONFLICT", "The Part review batch conflicts with saved Grist evidence.") from exc
        raise PartConflict("PART_LEGACY_READ_ONLY", "Legacy Part masters are read-only; managed Part changes use the canonical Grist registry.")


def _records(store, table: str, *, refresh: bool = False):
    try:
        return store.records(table, fresh=refresh)
    except TypeError:
        return store.records(table)


def list_parts(store, *, refresh: bool = False) -> list[dict[str, Any]]:
    rows = _records(store, "ProductPart", refresh=refresh)
    names: dict[str, list[str]] = {}
    aliases_by_part: dict[str, list[str]] = {}
    part_id_by_record = {str(row.get("id")): str(row.get("fields", {}).get("StablePartId") or row.get("id")) for row in rows}
    try:
        alias_rows = _records(store, "PartNameAlias", refresh=refresh)
    except (KeyError, GristError):
        alias_rows = []
    for alias in alias_rows:
        fields = alias.get("fields", {})
        owner_id = part_id_by_record.get(_record_ref(fields.get("ProductPart")))
        label = str(fields.get("DisplayName") or "")
        if owner_id and label:
            aliases_by_part.setdefault(owner_id, []).append(label)
            if name_key(label):
                names.setdefault(name_key(label), []).append(owner_id)
    for row in rows:
        fields = row["fields"]
        labels = [str(fields.get("DisplayName") or ""), *(fields.get("Aliases") or [])]
        for label in labels:
            if name_key(label):
                names.setdefault(name_key(label), []).append(str(fields.get("StablePartId") or row["id"]))
    result = []
    for row in rows:
        fields = row["fields"]
        stable_id = str(fields.get("StablePartId") or "")
        identity_id = stable_id or str(row["id"])
        for label in fields.get("Aliases") or []:
            if label and str(label) not in aliases_by_part.setdefault(identity_id, []):
                aliases_by_part[identity_id].append(str(label))
        try:
            record_id = int(row["id"])
        except (TypeError, ValueError):
            record_id = None
        name = str(fields.get("DisplayName") or "")
        labels = [name, *(fields.get("Aliases") or []), *aliases_by_part.get(identity_id, [])]
        duplicate = any(len(set(names.get(name_key(label), []))) > 1 for label in labels if name_key(label))
        result.append({"id": identity_id, "gristRecordId": record_id, "key": fields.get("PartKey"),
            "name": name, "status": fields.get("Status"), "aliases": aliases_by_part.get(identity_id, []),
            "selectable": bool(fields.get("PartKey")) and fields.get("Status") in {"canonical", "active", "reviewed"} and fields.get("PublishStatus", "published") == "published",
            "duplicateName": duplicate, "partNumber": fields.get("PartNumber"),
            "description": str(fields.get("Description") or fields.get("PartDescription") or ""),
            "variant": str(fields.get("DesignVariant") or fields.get("Variant") or ""),
            "engineeringRevision": fields.get("EngineeringRevision"), "scope": fields.get("ScopeType"),
            "scopeTarget": fields.get("ScopeTargetLabel") or fields.get("ScopeTarget"), "scopeTargetId": fields.get("ScopeTargetId"), "stableIdentity": bool(fields.get("StablePartId")),
            "stablePartId": stable_id or None, "partRevisionRecordId": fields.get("CurrentPartRevision"),
            "partMetadataRecordId": fields.get("CurrentMetadataVersion"), "legacy": not bool(stable_id)})
    return result


def source_groups(process_lists: dict[str, list[dict[str, Any]]], *, sheet_diagnostics: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for sheet in ("5. Material Cut List Price", "Tool Shop Items", "CNC Cut List"):
        for row in process_lists.get(sheet, []):
            if row.get("status") != "active":
                continue
            label_field = MAPPING_LABEL_FIELD[sheet]
            raw_description = row.get("fields", {}).get(label_field)
            description = "" if raw_description is None else str(raw_description)
            blank = not description.strip()
            number = int(row["source_row"])
            key = f"{MAPPING_POLICY_VERSION}:" + fingerprint([sheet, "blank", number] if blank else [sheet, description])
            legacy_description = str(row.get("fields", {}).get("product_part_name") or "").strip()
            legacy_key = "description:" + fingerprint(legacy_description) if legacy_description else "blank:" + fingerprint([sheet, number])
            group = groups.setdefault(key, {"key": key, "mappingPolicyVersion": MAPPING_POLICY_VERSION,
                "sheet": sheet, "labelField": label_field, "description": description,
                "blankDescription": blank, "rows": [], "legacyGroupKeys": set()})
            group["rows"].append({"sheet": sheet, "row": number, "fields": row.get("fields", {}),
                "sourceHeaders": row.get("source_headers", {}), "sourceHeaderCells": row.get("source_header_cells", {}),
                "sourceCells": row.get("source_cells", {}), "headerRow": row.get("header_row"),
                "availableFields": row.get("available_fields", list(row.get("fields") or {}))})
            group["legacyGroupKeys"].add(legacy_key)
    for group in groups.values():
        diagnostic = (sheet_diagnostics or {}).get(group["sheet"], {})
        group["sourceDiagnostic"] = diagnostic
        group["legacyGroupKeys"] = sorted(group["legacyGroupKeys"])
        group["evidenceFingerprint"] = fingerprint([
            [row["sheet"], row["row"], row["fields"], row["sourceHeaders"], row["sourceHeaderCells"], row["sourceCells"]]
            for row in group["rows"]
        ])
        by_row = {}
        semantic_lines = []
        for row in group["rows"]:
            source_row = {"fields": row["fields"], "available_fields": row["availableFields"],
                "source_headers": row["sourceHeaders"]}
            line = _line_semantic_evidence(group["sheet"], source_row)
            by_row[row["row"]] = line
            semantic_lines.append(line)
        semantic_lines.sort(key=lambda item: str(item.get("fingerprint") or ""))
        group["semanticPolicyVersion"] = MAPPING_SEMANTIC_POLICY_VERSION
        group["semanticFingerprint"] = _group_semantic_fingerprint(group["sheet"], semantic_lines)
        group["semanticEvidence"] = by_row
        group["semanticLineCount"] = len(semantic_lines)
    return PartSourceGroups(groups.values(), sheet_diagnostics)


def search_parts(store, *, query: str = "", offset: int = 0, limit: int = 50) -> dict[str, Any]:
    """Search canonical Parts and aliases, returning a bounded stable page."""
    wanted = name_key(query)
    matches = []
    for part in list_parts(store):
        if not part.get("selectable") or part.get("duplicateName"):
            continue
        searchable = name_key(" ".join(str(value or "") for value in (
            part.get("id"), part.get("partNumber"), part.get("name"), part.get("description"),
            part.get("variant"), *(part.get("aliases") or []))))
        if not wanted or wanted in searchable:
            matches.append(part)
    matches.sort(key=lambda part: (str(part.get("partNumber") or "").casefold(), str(part.get("name") or "").casefold(), part["id"]))
    return {"items": matches[offset:offset + limit], "total": len(matches), "offset": offset,
        "limit": limit, "hasMore": offset + limit < len(matches),
        "registryCacheAgeSeconds": round(float(getattr(store, "search_cache_age_seconds", 0.0)), 3),
        "registryCacheMayBeStale": bool(getattr(store, "search_cache_age_seconds", 0.0))}


def _history(store, file_id: str):
    rows = [row for row in store.records(MAPPING_TABLE) if row["fields"].get("FileKey") == file_id]
    requests: dict[int, set[str]] = {}
    batches: dict[str, list[dict[str, Any]]] = {}
    keys = set()
    for row in rows:
        fields = row["fields"]
        requests.setdefault(int(fields["Version"]), set()).add(fields["RequestKey"])
        batches.setdefault(fields["RequestKey"], []).append(fields)
        if fields["ReviewKey"] in keys:
            raise PartConflict("PART_REVIEW_CONFLICT", "A duplicate Part review row needs investigation")
        keys.add(fields["ReviewKey"])
    if any(len(items) != 1 for items in requests.values()):
        raise PartConflict("PART_REVIEW_CONFLICT", "Concurrent mapping versions conflict; review is required")
    for batch in batches.values():
        if (any(row.get("RequestRowCount") != len(batch) for row in batch)
                or len({row.get("RequestFingerprint") for row in batch}) != 1
                or len({row.get("Version") for row in batch}) != 1):
            raise PartConflict("PART_REVIEW_CONFLICT", "An incomplete or inconsistent Part review batch needs investigation")
    return rows


def _is_source_deletion_review(record: dict[str, Any]) -> bool:
    evidence = decode_grist_any_object(record.get("SemanticEvidence")) or {}
    return bool(record.get("ActionType") == "source_deletion_review" and evidence.get("sourceDeletionReview") is True)


def _group_records_by_key(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row.get("GroupKey") or ""), []).append(row)
    return grouped


def _group_mapping_detail(store, *, file_id: str, source_hash: str, association, groups: list[dict[str, Any]], refresh_parts: bool = False):
    _ensure_group_semantic_evidence(groups)
    parts = list_parts(store, refresh=refresh_parts)
    history = _history(store, file_id)
    # A deletion acknowledgement is durable audit evidence, not a mapping for
    # a source row. Keep it in history/versioning, but never let it replace a
    # group's last complete manufacturing snapshot.
    mapping_history = [row["fields"] for row in history if not _is_source_deletion_review(row["fields"])]
    rows_by_group = _group_records_by_key([row for row in mapping_history if row.get("GroupKey")])
    latest_by_group = {}
    for key, rows in rows_by_group.items():
        latest_version = max((int(row.get("Version") or 0) for row in rows), default=0)
        latest_by_group[key] = [row for row in rows if int(row.get("Version") or 0) == latest_version]
    resolved = []
    resolved_parts: dict[str, dict[str, Any]] = {}
    for group in groups:
        latest = latest_by_group.get(group["key"], [])
        version = max((int(row.get("Version") or 0) for row in latest), default=0)
        refs = {str(row.get("StablePartId") or row.get("PartIdentity") or _record_ref(row.get("ProductPart"))) for row in latest}
        prior_part = next((candidate for candidate in parts if len(refs) == 1 and next(iter(refs)) and candidate["id"] in refs), None)
        saved_lines, saved_fingerprint = _snapshot_lines_from_records(latest, group["sheet"])
        saved_association_matches = bool(latest and association and all(
            row.get("AssociationKey") == association.id and int(row.get("AssociationVersion") or 0) == int(association.version)
            for row in latest))
        current_semantic_fingerprint = str(group.get("semanticFingerprint") or "")
        semantic_unchanged = bool(saved_lines is not None and saved_fingerprint == current_semantic_fingerprint)
        blank_identity = bool(group.get("blankDescription"))
        if not latest:
            semantic_status = "no_prior_evidence"
            semantic_differences = []
        elif saved_lines is None:
            semantic_status = "legacy_evidence_missing"
            semantic_differences = []
        elif blank_identity:
            semantic_status = "ambiguous_identity"
            semantic_differences = _semantic_differences(saved_lines, list(group.get("semanticEvidence", {}).values()), group["sheet"], blank_label=True)
        elif not semantic_unchanged:
            semantic_differences = _semantic_differences(saved_lines, list(group.get("semanticEvidence", {}).values()), group["sheet"])
            semantic_status = "ambiguous_correspondence" if any(item["type"] == "ambiguous" for item in semantic_differences) else "changed"
        elif not saved_association_matches:
            semantic_status = "association_changed"
            semantic_differences = []
        elif len(refs) != 1:
            semantic_status = "assignment_conflict"
            semantic_differences = []
        else:
            semantic_status = "unchanged"
            semantic_differences = []
        part = prior_part if semantic_status == "unchanged" else None
        valid = bool(part and part["selectable"] and not part["duplicateName"])
        if valid and part:
            resolved_parts[part["id"]] = part
        source_refs = {(row["sheet"], row["row"]) for row in group["rows"]}
        prior_rows = []
        if not latest:
            prior_rows = [row for row in mapping_history
                if (str(row.get("SheetName") or ""), int(row.get("SourceRow") or 0)) in source_refs]
        prior_rows.sort(key=lambda row: (int(row.get("Version") or 0), str(row.get("OccurredAt") or ""), str(row.get("RequestKey") or "")))
        latest_by_source: dict[tuple[str, int], dict[str, Any]] = {}
        for prior in prior_rows:
            coordinate = (str(prior.get("SheetName") or ""), int(prior.get("SourceRow") or 0))
            if coordinate in source_refs:
                latest_by_source[coordinate] = prior
        fallback_rows = list(latest_by_source.values())
        assignment_rows = latest or fallback_rows
        assigned_ids = sorted({str(row.get("StablePartId") or row.get("PartIdentity") or _record_ref(row.get("ProductPart")))
            for row in assignment_rows if row.get("StablePartId") or row.get("PartIdentity") or row.get("ProductPart")})
        assignment_refs = {str(row.get("StablePartId") or row.get("PartIdentity") or _record_ref(row.get("ProductPart"))) for row in assignment_rows}
        assignments_agree = bool(assignment_rows and len(assignment_refs) == 1)
        proposal_part = prior_part
        if not latest and assignments_agree and assigned_ids:
            proposal_part = next((candidate for candidate in parts if candidate["id"] == assigned_ids[0]), None)
        if proposal_part:
            resolved_parts[proposal_part["id"]] = proposal_part
        sample = max(assignment_rows, key=lambda row: (int(row.get("Version") or 0), str(row.get("OccurredAt") or ""))) if assignment_rows else None
        prior_evidence = None
        if assignment_rows:
            source_context_changed = any(row.get("SourceHash") != source_hash for row in assignment_rows)
            association_context_changed = bool(association and any(
                row.get("AssociationKey") != association.id or int(row.get("AssociationVersion") or 0) != int(association.version)
                for row in assignment_rows))
            group_context_changed = any(str(row.get("GroupKey") or "") != str(group["key"]) for row in assignment_rows)
            prior_evidence = {"partId": proposal_part["id"] if proposal_part and assignments_agree else None,
                "partNumber": ((proposal_part or {}).get("partNumber") or (sample or {}).get("PartNumberUsed")) if assignments_agree else None,
                "name": ((proposal_part or {}).get("name") or (sample or {}).get("NameUsed")) if assignments_agree else None,
                "sourceDescription": (sample or {}).get("SourceDescription") or "", "version": (sample or {}).get("Version"),
                "sourceChanged": source_context_changed, "associationChanged": association_context_changed,
                "requiresReview": not valid, "previousRowsReviewed": len(assignment_rows), "currentRows": len(source_refs),
                "previousRowsAgree": assignments_agree and (bool(latest) or source_refs.issubset(latest_by_source)),
                "assignedPartIds": assigned_ids, "hasPriorAssignment": bool(assigned_ids),
                "contextChanged": source_context_changed or association_context_changed or group_context_changed,
                "groupingPolicyChanged": group_context_changed,
                "evidenceStatus": "verified" if saved_lines is not None else "legacy_unverified",
                "sourceRows": sorted({int(row.get("SourceRow") or 0) for row in assignment_rows})}
        if not latest and prior_evidence and prior_evidence.get("groupingPolicyChanged"):
            # Row coordinates may locate a proposal after a grouping-policy
            # change, but they do not make that prior assignment equivalent.
            semantic_status = "grouping_policy_changed"
        # Older reviews grouped by description alone and used Plate Part to Cut on CNC.
        # Keep them immutable and visible as review evidence; never apply them to the new
        # sheet-scoped identity without an explicit new Save.
        explicit_unassigned = bool(latest and assignment_refs == {""} and semantic_unchanged and saved_association_matches)
        group["semanticComparison"] = {"status": semantic_status, "previousFingerprint": saved_fingerprint,
            "currentFingerprint": current_semantic_fingerprint, "previousPolicyVersion": next((str(row.get("SemanticPolicyVersion") or "") for row in latest), ""),
            "sourceHashChanged": bool(latest and any(row.get("SourceHash") != source_hash for row in latest)),
            "associationChanged": bool(latest and not saved_association_matches),
            "groupingPolicyChanged": bool(prior_evidence and prior_evidence.get("groupingPolicyChanged")),
            "differences": semantic_differences,
            "requiresReview": semantic_status != "unchanged"}
        resolved.append({**group, "part": part if valid else None, "reviewed": valid, "version": version,
            "explicitlyUnassigned": explicit_unassigned, "previousAssignment": prior_evidence,
            "needsCompatibilityReview": bool(prior_evidence and not valid)})
    current_keys = {str(group.get("key") or "") for group in groups}
    removed_groups = []
    if association:
        for key, latest in latest_by_group.items():
            if key in current_keys or not latest or not all(row.get("AssociationKey") == association.id
                    and int(row.get("AssociationVersion") or 0) == int(association.version) for row in latest):
                continue
            sheet = str(latest[0].get("SheetName") or "")
            if sheet not in _MAPPING_SEMANTIC_FIELDS:
                continue
            lines, saved_fingerprint = _snapshot_lines_from_records(latest, sheet)
            refs = {str(row.get("StablePartId") or row.get("PartIdentity") or _record_ref(row.get("ProductPart"))) for row in latest}
            part = next((candidate for candidate in parts if len(refs) == 1 and next(iter(refs)) and candidate["id"] in refs), None)
            sample = latest[0]
            previous_assignment = {"partId": part["id"] if part and len(refs) == 1 else None,
                "partNumber": (part or {}).get("partNumber") or sample.get("PartNumberUsed"),
                "name": (part or {}).get("name") or sample.get("NameUsed"), "assignedPartIds": sorted(value for value in refs if value),
                "hasPriorAssignment": any(refs), "previousRowsAgree": len(refs) == 1,
                "previousRowsReviewed": len(latest), "currentRows": 0, "sourceDescription": sample.get("SourceDescription") or "",
                "evidenceStatus": "verified" if lines is not None else "legacy_unverified",
                "contextChanged": False, "sourceChanged": sample.get("SourceHash") != source_hash,
                "associationChanged": False, "groupingPolicyChanged": False}
            removed_differences = ([{"type": "deleted", "previous": line, "current": None, "fieldDifferences": []} for line in lines]
                if lines is not None else [])
            removed_groups.append({"key": key, "sheet": sheet, "description": str(sample.get("SourceDescription") or ""),
                "removed": True, "rows": [], "part": None, "reviewed": False, "previousAssignment": previous_assignment,
                "semanticComparison": {"status": "group_removed" if lines is not None else "legacy_evidence_missing",
                    "previousFingerprint": saved_fingerprint, "currentFingerprint": None,
                    "previousPolicyVersion": sample.get("SemanticPolicyVersion") or "", "sourceHashChanged": True,
                    "associationChanged": False, "differences": removed_differences, "requiresReview": True}})
    removed_groups.sort(key=lambda item: (item["sheet"], item["description"].casefold(), item["key"]))
    return {"fileId": file_id, "sourceHash": source_hash, "sourceBasis": "saved_workbook",
            "associationKey": association.id if association else "", "associationVersion": association.version if association else 0,
            "version": max((int(row["fields"]["Version"]) for row in history), default=0),
            "schemaAvailable": store.available, "schemaMessage": getattr(store, "schema_message", None),
            "legacyHistoryAvailable": getattr(store, "legacy_history_available", True),
            "mappingPolicyVersion": MAPPING_POLICY_VERSION, "sourceSheets": getattr(groups, "diagnostics", {}),
            "semanticPolicyVersion": MAPPING_SEMANTIC_POLICY_VERSION,
            "parts": list(resolved_parts.values()), "groups": resolved, "removedGroups": removed_groups,
            "unresolvedGroups": sum(not group["reviewed"] for group in resolved) + len(removed_groups), "authorityChanged": False,
            "history": [{**row["fields"], "OccurredAt": datetime_text(row["fields"].get("OccurredAt"))} for row in history]}


def _stored_line_key(row: dict[str, Any]) -> str:
    evidence = decode_grist_any_object(row.get("SemanticEvidence")) or {}
    return str(evidence.get("lineKey") or "")


def _stored_line_evidence(row: dict[str, Any], sheet: str) -> dict[str, Any] | None:
    evidence = decode_grist_any_object(row.get("SemanticEvidence"))
    if not isinstance(evidence, dict) or not isinstance(evidence.get("fields"), list):
        return None
    projected = [{"field": item.get("field"), "state": item.get("state"), "value": item.get("value")}
        for item in evidence["fields"] if isinstance(item, dict)]
    if len(projected) != len(evidence["fields"]):
        return None
    expected = fingerprint([MAPPING_SEMANTIC_POLICY_VERSION, sheet, projected])
    return evidence if evidence.get("fingerprint") == expected else None


def _stored_part_id(row: dict[str, Any]) -> str:
    return str(row.get("StablePartId") or row.get("PartIdentity") or _record_ref(row.get("ProductPart")) or "")


def _current_line_key(file_id: str, source_hash: str, group: dict[str, Any], source: dict[str, Any], line_evidence: dict[str, Any]) -> str:
    return "part-line:" + fingerprint([MAPPING_LINE_EVIDENCE_VERSION, file_id, source_hash,
        group.get("key"), source.get("sheet"), source.get("row"), line_evidence.get("fingerprint")])


def mapping_detail(store, *, file_id: str, source_hash: str, association, groups: list[dict[str, Any]], refresh_parts: bool = False):
    """Return group display data with independently resolved source-line assignments.

    Legacy group snapshots remain readable. New records carry their line identity
    inside the already schema-v14 ``Any`` evidence cell, so no new Grist column is
    needed for line-level history.
    """
    detail = _group_mapping_detail(store, file_id=file_id, source_hash=source_hash,
        association=association, groups=groups, refresh_parts=refresh_parts)
    history = [row["fields"] for row in _history(store, file_id)]
    deletion_review_events = [record for record in history if _is_source_deletion_review(record)]
    mapping_history = [record for record in history if not _is_source_deletion_review(record)]
    acknowledged_deletions = {}
    for record in deletion_review_events:
        evidence = decode_grist_any_object(record.get("SemanticEvidence")) or {}
        key = str(evidence.get("deletionReviewKey") or "")
        if key:
            acknowledged_deletions[key] = record
    all_parts = list_parts(store, refresh=refresh_parts)
    parts_by_id = {part["id"]: part for part in all_parts}
    line_events: dict[str, dict[str, Any]] = {}
    legacy_events: dict[tuple[str, str, int], dict[str, Any]] = {}
    for record in mapping_history:
        line_key = _stored_line_key(record)
        if line_key:
            current = line_events.get(line_key)
            if current and int(current.get("Version") or 0) == int(record.get("Version") or 0):
                raise PartConflict("PART_REVIEW_CONFLICT", "A line assignment was recorded more than once in one mapping version")
            if current is None or int(record.get("Version") or 0) > int(current.get("Version") or 0):
                line_events[line_key] = record
            continue
        coordinate = (str(record.get("GroupKey") or ""), str(record.get("SheetName") or ""), int(record.get("SourceRow") or 0))
        current = legacy_events.get(coordinate)
        if current is None or int(record.get("Version") or 0) > int(current.get("Version") or 0):
            legacy_events[coordinate] = record

    # A later explicit line decision may have different semantic evidence and
    # therefore a different line key.  Follow event-level supersession links
    # so the older event stays in the audit log without remaining eligible to
    # restore a Part when the source semantics later revert.
    superseded_review_keys: set[str] = set()
    for record in history:
        evidence = _stored_line_evidence(record, str(record.get("SheetName") or ""))
        if not evidence:
            continue
        linked = evidence.get("supersedesReviewKeys")
        if isinstance(linked, list):
            superseded_review_keys.update(str(key) for key in linked if isinstance(key, str) and key)
    line_events = {key: record for key, record in line_events.items()
        if str(record.get("ReviewKey") or "") not in superseded_review_keys}
    legacy_events = {key: record for key, record in legacy_events.items()
        if str(record.get("ReviewKey") or "") not in superseded_review_keys}

    current_lines: list[dict[str, Any]] = []
    group_comparisons = {str(group.get("key") or ""): group.get("semanticComparison") or {}
        for group in detail["groups"]}
    removed_comparisons = {str(group.get("key") or ""): group.get("semanticComparison") or {}
        for group in detail.get("removedGroups") or []}
    for group in detail["groups"]:
        group["mappingMode"] = "line"
        for source in group.get("rows") or []:
            line_evidence = (group.get("semanticEvidence") or {}).get(source.get("row"))
            if not isinstance(line_evidence, dict):
                continue
            source["semanticEvidence"] = line_evidence
            source["lineKey"] = _current_line_key(file_id, source_hash, group, source, line_evidence)
            source["part"] = None
            source["reviewed"] = False
            source["explicitlyUnassigned"] = False
            source["semanticComparison"] = {"status": "new", "requiresReview": False}
            source["previousAssignment"] = None
            current_lines.append({"group": group, "source": source, "evidence": line_evidence})

    def context_matches(record: dict[str, Any]) -> bool:
        return bool(association and record.get("AssociationKey") == association.id
            and int(record.get("AssociationVersion") or 0) == int(association.version))

    current_by_sheet_fp: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    old_by_sheet_fp: dict[tuple[str, str], list[tuple[dict[str, Any], dict[str, Any]]]] = defaultdict(list)
    for item in current_lines:
        current_by_sheet_fp[(item["group"]["sheet"], str(item["evidence"].get("fingerprint") or ""))].append(item)
    for record in [*line_events.values(), *legacy_events.values()]:
        sheet = str(record.get("SheetName") or "")
        evidence = _stored_line_evidence(record, sheet)
        if evidence:
            old_by_sheet_fp[(sheet, str(evidence.get("fingerprint") or ""))].append((record, evidence))

    matched_record_keys: set[tuple[str, str]] = set()
    ambiguous_current: set[int] = set()
    ambiguous_record_keys: set[tuple[str, str]] = set()

    def record_event_key(record: dict[str, Any]) -> tuple[str, str]:
        stored_key = _stored_line_key(record)
        return ("line", stored_key) if stored_key else ("legacy", str(record.get("ReviewKey") or ""))

    for item in current_lines:
        group, source, evidence = item["group"], item["source"], item["evidence"]
        sheet_fp = (str(group["sheet"]), str(evidence.get("fingerprint") or ""))
        candidates = old_by_sheet_fp.get(sheet_fp, [])
        allowed_group_keys = {str(group.get("key") or ""), *(str(key) for key in group.get("legacyGroupKeys") or [])}
        same_context = [record for record, _ in candidates if record.get("SourceHash") == source_hash
            and int(record.get("SourceRow") or 0) == int(source.get("row") or 0)
            and (_stored_line_key(record) or str(record.get("GroupKey") or "") in allowed_group_keys)]
        selected_record = max(same_context, key=lambda row: int(row.get("Version") or 0)) if same_context else None
        if selected_record is None and candidates:
            # A moved line is reusable only when its semantic signature is unique
            # on both sides. Duplicate rows cannot lend an assignment by order.
            current_matches = current_by_sheet_fp.get(sheet_fp, [])
            line_candidates = [candidate for candidate in candidates if _stored_line_key(candidate[0])]
            legacy_candidates = [candidate for candidate in candidates if not _stored_line_key(candidate[0])
                and str(candidate[0].get("GroupKey") or "") in allowed_group_keys]
            preferred = line_candidates or legacy_candidates
            if len(preferred) == 1 and len(current_matches) == 1:
                selected_record = preferred[0][0]
            else:
                ambiguous_current.add(id(item))
                ambiguous_record_keys.update(record_event_key(record) for record, _ in preferred)
                source["semanticComparison"] = {"status": "ambiguous_correspondence", "requiresReview": True,
                    "reason": "Duplicate source lines prevent an unambiguous moved-line match."}
        if selected_record is None:
            continue
        event_key = record_event_key(selected_record)
        if event_key in matched_record_keys:
            ambiguous_current.add(id(item))
            ambiguous_record_keys.add(event_key)
            continue
        matched_record_keys.add(event_key)
        source["lineKey"] = _stored_line_key(selected_record) or source["lineKey"]
        source["authoritativeReviewKey"] = str(selected_record.get("ReviewKey") or "")
        source["authoritativeReviewKeys"] = [source["authoritativeReviewKey"]] if source["authoritativeReviewKey"] else []
        part_id = _stored_part_id(selected_record)
        part = parts_by_id.get(part_id)
        association_ok = context_matches(selected_record)
        valid_part = bool(part and part.get("selectable") and not part.get("duplicateName"))
        legacy_policy_change = not _stored_line_key(selected_record) and str(selected_record.get("GroupKey") or "") != str(group.get("key") or "")
        explicitly_unassigned = not part_id
        if association_ok and valid_part and not legacy_policy_change:
            source["part"] = part
            source["reviewed"] = True
            source["semanticComparison"] = {"status": "unchanged",
                "sourceHashChanged": selected_record.get("SourceHash") != source_hash, "requiresReview": False}
        elif association_ok and explicitly_unassigned and selected_record.get("ActionType") in {"clear_assignment", "initial_unassigned"}:
            source["reviewed"] = True
            source["explicitlyUnassigned"] = True
            source["semanticComparison"] = {"status": "unchanged", "requiresReview": False}
        elif part_id:
            source["previousAssignment"] = {"partId": part["id"] if part else None,
                "partNumber": (part or {}).get("partNumber") or selected_record.get("PartNumberUsed"),
                "name": (part or {}).get("name") or selected_record.get("NameUsed"),
                "assignedPartIds": [part_id], "hasPriorAssignment": True,
                "sourceChanged": selected_record.get("SourceHash") != source_hash,
                "associationChanged": not association_ok, "requiresReview": True,
                "reviewKey": source["authoritativeReviewKey"], "reviewKeys": source["authoritativeReviewKeys"],
                "evidenceStatus": "verified" if _stored_line_evidence(selected_record, str(group["sheet"])) else "legacy_unverified"}
            source["semanticComparison"] = {"status": "association_changed" if not association_ok else
                "grouping_policy_changed" if legacy_policy_change else "part_unavailable", "requiresReview": True}
        source["semanticEvidenceRecordKey"] = event_key[1]
        source["lastSavedVersion"] = int(selected_record.get("Version") or 0)
        source["explicitlyUnassigned"] = explicitly_unassigned and association_ok

    # Older rows without semantic evidence cannot be matched as verified line
    # identity.  A same-group/sheet/row proposal may still expose the known
    # prior Part after a hash change, but it always remains unverified and
    # requires an explicit user confirmation before it affects the line.
    for item in current_lines:
        group, source = item["group"], item["source"]
        if source.get("reviewed") or source.get("previousAssignment") or id(item) in ambiguous_current:
            continue
        allowed_group_keys = {str(group.get("key") or ""), *(str(key) for key in group.get("legacyGroupKeys") or [])}
        candidates = [record for (group_key, sheet, row), record in legacy_events.items()
            if sheet == group["sheet"] and row == int(source.get("row") or 0) and group_key in allowed_group_keys
            and context_matches(record)]
        if not candidates:
            continue
        latest_version = max(int(record.get("Version") or 0) for record in candidates)
        latest_candidates = [record for record in candidates if int(record.get("Version") or 0) == latest_version]
        # A row-scoped legacy proposal still owns its predecessor event. It
        # may not have a line key, but allowing the later changed-line pass to
        # reuse it would let a newly added row supersede the known prior Part
        # for this current row.
        matched_record_keys.update(record_event_key(record) for record in latest_candidates)
        latest_part_ids = {_stored_part_id(record) for record in latest_candidates}
        if len(latest_part_ids) > 1:
            ambiguous_current.add(id(item))
            ambiguous_record_keys.update(record_event_key(record) for record in latest_candidates)
            source["semanticComparison"] = {"status": "ambiguous_correspondence", "requiresReview": True,
                "reason": "Legacy row history contains conflicting prior Part assignments."}
            continue
        record = max(latest_candidates, key=lambda row: (
            str(row.get("GroupKey") or "") == str(group.get("key") or ""), str(row.get("ReviewKey") or "")))
        prior_review_keys = sorted({str(candidate.get("ReviewKey") or "") for candidate in latest_candidates
            if candidate.get("ReviewKey")})
        part_id = _stored_part_id(record)
        part = parts_by_id.get(part_id)
        source["authoritativeReviewKey"] = str(record.get("ReviewKey") or "")
        source["authoritativeReviewKeys"] = prior_review_keys
        if part_id:
            source["previousAssignment"] = {"partId": (part or {}).get("id"),
                "partNumber": (part or {}).get("partNumber") or record.get("PartNumberUsed"),
                "name": (part or {}).get("name") or record.get("NameUsed"), "assignedPartIds": [part_id],
                "hasPriorAssignment": True, "requiresReview": True,
                "sourceChanged": record.get("SourceHash") != source_hash,
                "associationChanged": False, "reviewKey": source["authoritativeReviewKey"],
                "reviewKeys": prior_review_keys, "evidenceStatus": "legacy_unverified"}
            source["semanticComparison"] = {"status": "legacy_evidence_missing", "requiresReview": True}
        else:
            source["explicitlyUnassigned"] = True
            source["semanticComparison"] = {"status": "legacy_evidence_missing", "requiresReview": True}

    # Pair changed lines with prior evidence only as proposals. First reserve
    # every event already matched to an unchanged current line. Then accept a
    # predecessor edge only when it is unique from both the current-line and
    # prior-event sides; otherwise a shared modification anchor is not enough
    # to claim authoritative lineage.
    changed_candidates: dict[int, dict[tuple[str, str], tuple[dict[str, Any], dict[str, Any]]]] = {}
    candidate_users: dict[tuple[str, str], set[int]] = defaultdict(set)
    for item in current_lines:
        if item["source"].get("reviewed") or item["source"].get("previousAssignment") or id(item) in ambiguous_current:
            continue
        group, source, evidence = item["group"], item["source"], item["evidence"]
        current_fields = _line_field_map(evidence)
        candidates: dict[tuple[str, str], tuple[dict[str, Any], dict[str, Any]]] = {}
        for records in (line_events.values(), legacy_events.values()):
            for record in records:
                if str(record.get("SheetName") or "") != str(group["sheet"]):
                    continue
                event_key = record_event_key(record)
                if event_key in matched_record_keys:
                    continue
                previous_evidence = _stored_line_evidence(record, str(group["sheet"]))
                if not previous_evidence or previous_evidence.get("fingerprint") == evidence.get("fingerprint"):
                    continue
                previous_fields = _line_field_map(previous_evidence)
                anchors = _MAPPING_MODIFICATION_ANCHORS.get(str(group["sheet"]), ())
                shared = next((name for name in anchors if previous_fields.get(name, {}).get("state") == "value"
                    and current_fields.get(name, {}).get("state") == "value"), None)
                if shared and previous_fields[shared].get("value") == current_fields[shared].get("value"):
                    candidates[event_key] = (record, previous_evidence)
        changed_candidates[id(item)] = candidates
        for event_key in candidates:
            candidate_users[event_key].add(id(item))

    for item in current_lines:
        candidates = changed_candidates.get(id(item))
        if candidates is None:
            continue
        group, source, evidence = item["group"], item["source"], item["evidence"]
        unique_candidate = next(iter(candidates.items())) if len(candidates) == 1 else None
        if unique_candidate and len(candidate_users[unique_candidate[0]]) == 1:
            event_key, (record, previous) = unique_candidate
            # A uniquely paired edit is not a deleted predecessor. Reserve it
            # just like an unchanged match so the deletion inventory below
            # cannot duplicate the line as a removal.
            matched_record_keys.add(event_key)
            authoritative_review_key = str(record.get("ReviewKey") or "")
            source["authoritativeReviewKey"] = authoritative_review_key
            source["authoritativeReviewKeys"] = [authoritative_review_key] if authoritative_review_key else []
            part_id = _stored_part_id(record)
            part = parts_by_id.get(part_id)
            source["previousAssignment"] = {"partId": (part or {}).get("id") or None,
                "partNumber": (part or {}).get("partNumber") or record.get("PartNumberUsed"),
                "name": (part or {}).get("name") or record.get("NameUsed"),
                "assignedPartIds": [part_id] if part_id else [], "hasPriorAssignment": bool(part_id),
                "sourceChanged": record.get("SourceHash") != source_hash, "associationChanged": not context_matches(record),
                "requiresReview": True, "reviewKey": authoritative_review_key,
                "reviewKeys": source["authoritativeReviewKeys"], "evidenceStatus": "verified"}
            source["semanticComparison"] = {"status": "changed", "requiresReview": True,
                "previous": previous, "current": evidence}
        elif candidates:
            ambiguous_current.add(id(item))
            ambiguous_record_keys.update(candidates)
            source["semanticComparison"] = {"status": "ambiguous_correspondence", "requiresReview": True,
                "reason": ("Several current source lines compete for the same previous source line."
                    if len(candidates) == 1 else "Several previous source lines have the same modification anchors.")}
        elif old_by_sheet_fp.get((str(group["sheet"]), str(evidence.get("fingerprint") or ""))):
            source["semanticComparison"] = {"status": "ambiguous_correspondence", "requiresReview": True,
                "reason": "Duplicate source lines prevent an unambiguous moved-line match."}

    current_groups_by_key = {str(group.get("key") or ""): group for group in detail["groups"]}
    removed_groups_by_key = {str(group.get("key") or ""): group for group in detail.get("removedGroups") or []}
    current_or_removed = {**removed_groups_by_key, **current_groups_by_key}
    for group in current_or_removed.values():
        group["deletedLines"] = []
    attached_deletions: dict[str, set[str]] = defaultdict(set)
    deleted_signatures: set[tuple[str, str, str]] = set()
    for group in [*detail["groups"], *(detail.get("removedGroups") or [])]:
        comparison = group_comparisons.get(str(group.get("key") or "")) or removed_comparisons.get(str(group.get("key") or "")) or {}
        for difference in comparison.get("differences") or []:
            if difference.get("type") == "deleted" and isinstance(difference.get("previous"), dict):
                deleted_signatures.add((str(group.get("key") or ""), str(group.get("sheet") or ""),
                    str(difference["previous"].get("fingerprint") or "")))

    active_records: dict[tuple[str, str], dict[str, Any]] = {}
    for record in [*line_events.values(), *legacy_events.values()]:
        active_records[record_event_key(record)] = record
    acknowledged_by_signature: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in deletion_review_events:
        evidence = decode_grist_any_object(record.get("SemanticEvidence")) or {}
        signature = (str(evidence.get("priorGroupKey") or record.get("GroupKey") or ""),
            str(record.get("SheetName") or ""), str(evidence.get("fingerprint") or ""))
        if evidence.get("deletionReviewKey") and signature[2]:
            acknowledged_by_signature[signature].append(record)
    for records in acknowledged_by_signature.values():
        records.sort(key=lambda row: (int(row.get("Version") or 0), str(row.get("ReviewKey") or "")))

    def deletion_context(group_key: str, sheet: str):
        exact = current_or_removed.get(group_key)
        if exact and str(exact.get("sheet") or "") == sheet:
            return exact
        return next((group for group in detail["groups"] if str(group.get("sheet") or "") == sheet
            and group_key in {str(group.get("key") or ""), *(str(key) for key in group.get("legacyGroupKeys") or [])}), None)

    def add_deleted_line(group: dict[str, Any], *, evidence: dict[str, Any], record: dict[str, Any] | None,
                         review_key: str | None = None, comparison_type: str = "deleted",
                         resolution: dict[str, Any] | None = None, fallback_assignment: dict[str, Any] | None = None):
        group_key = str((evidence.get("priorGroupKey") if resolution else None) or
            (record or {}).get("GroupKey") or group.get("key") or "")
        sheet = str((record or {}).get("SheetName") or group.get("sheet") or "")
        semantic_fingerprint = str(evidence.get("fingerprint") or "")
        review_key = review_key or str((record or {}).get("ReviewKey") or "")
        if not review_key:
            review_key = "deleted-line:" + fingerprint([file_id, group_key, sheet, semantic_fingerprint])
        if review_key in attached_deletions[group_key]:
            return
        ack = resolution or acknowledged_deletions.get(review_key)
        prior_record = record
        if ack:
            ack_evidence = decode_grist_any_object(ack.get("SemanticEvidence")) or {}
            prior_record = {**ack, "GroupKey": ack_evidence.get("priorGroupKey") or ack.get("GroupKey"),
                "SheetName": ack.get("SheetName"), "SourceRow": ack_evidence.get("priorSourceRow") or ack.get("SourceRow"),
                "ReviewKey": ack_evidence.get("priorReviewKey") or review_key}
        part_id = _stored_part_id(prior_record or {})
        part = parts_by_id.get(part_id)
        if prior_record:
            previous_assignment = {"partId": (part or {}).get("id") or (part_id or None),
                "partNumber": (part or {}).get("partNumber") or prior_record.get("PartNumberUsed"),
                "name": (part or {}).get("name") or prior_record.get("NameUsed"),
                "assignedPartIds": [part_id] if part_id else [], "hasPriorAssignment": bool(part_id),
                "requiresReview": not bool(ack), "reviewKey": review_key, "reviewKeys": [review_key],
                "evidenceStatus": "verified" if _stored_line_evidence(prior_record, sheet) else "legacy_unverified",
                "sourceChanged": prior_record.get("SourceHash") != source_hash,
                "associationChanged": not context_matches(prior_record)}
        else:
            previous_assignment = dict(fallback_assignment or group.get("previousAssignment") or {})
            if not previous_assignment.get("previousRowsAgree"):
                previous_assignment.update({"partId": None, "partNumber": None, "name": None, "assignedPartIds": [],
                    "hasPriorAssignment": False})
            previous_assignment.update({"requiresReview": not bool(ack), "reviewKey": review_key,
                "reviewKeys": [review_key], "evidenceStatus": previous_assignment.get("evidenceStatus") or "legacy_unverified"})
        prior_review_key = str((prior_record or {}).get("ReviewKey") or review_key)
        ack_evidence = decode_grist_any_object(ack.get("SemanticEvidence")) if ack else None
        provenance_source = prior_record or {}
        if ack and ack_evidence:
            provenance_source = {**provenance_source,
                "Version": ack_evidence.get("priorVersion", provenance_source.get("Version")),
                "Actor": ack_evidence.get("priorActor", provenance_source.get("Actor")),
                "Reason": ack_evidence.get("priorReason", provenance_source.get("Reason")),
                "OccurredAt": ack_evidence.get("priorOccurredAt", provenance_source.get("OccurredAt"))}
        line = {"reviewKey": review_key, "priorReviewKey": prior_review_key,
            "lineKey": _stored_line_key(record or {}) or str(evidence.get("lineKey") or ""),
            "groupKey": group_key, "sheet": sheet,
            "description": str((record or {}).get("SourceDescription") or group.get("description") or ""),
            "row": int((record or {}).get("SourceRow") or (ack_evidence or {}).get("priorSourceRow") or 0) or None,
            "semanticEvidence": evidence, "previousAssignment": previous_assignment,
            "semanticComparison": {"status": "deleted" if comparison_type == "deleted" else "unmatched_previous",
                "previousFingerprint": semantic_fingerprint, "currentFingerprint": None,
                "differences": [{"type": comparison_type, "previous": evidence, "current": None, "fieldDifferences": []}],
                "requiresReview": not bool(ack)},
            "provenance": {"reviewKey": prior_review_key, "version": provenance_source.get("Version"),
                "actor": provenance_source.get("Actor"), "reason": provenance_source.get("Reason"),
                "occurredAt": provenance_source.get("OccurredAt"), "sourceHash": provenance_source.get("SourceHash")},
            "reviewed": bool(ack), "resolution": ({"reviewKey": ack.get("ReviewKey"), "version": ack.get("Version"),
                "actor": ack.get("Actor"), "reason": ack.get("Reason"), "occurredAt": ack.get("OccurredAt")} if ack else None)}
        group["deletedLines"].append(line)
        attached_deletions[group_key].add(review_key)

    # The group snapshot can prove a deletion even when the line has only
    # legacy/group-level audit evidence. Prefer a matching line event to keep
    # the canonical Part and actor/reason provenance line-specific.
    consumed_deletion_events: set[tuple[str, str]] = set()
    for group in [*detail["groups"], *(detail.get("removedGroups") or [])]:
        group_key, sheet = str(group.get("key") or ""), str(group.get("sheet") or "")
        comparison = group_comparisons.get(group_key) or removed_comparisons.get(group_key) or {}
        for difference in comparison.get("differences") or []:
            previous = difference.get("previous") if isinstance(difference.get("previous"), dict) else None
            if difference.get("type") != "deleted" or not previous:
                continue
            signature = str(previous.get("fingerprint") or "")
            if not signature or current_by_sheet_fp.get((sheet, signature)):
                continue
            allowed = {group_key, *(str(key) for key in group.get("legacyGroupKeys") or [])}
            candidates = [(key, record) for key, record in active_records.items()
                if key not in matched_record_keys and key not in ambiguous_record_keys and key not in consumed_deletion_events
                and str(record.get("SheetName") or "") == sheet and str(record.get("GroupKey") or "") in allowed
                and (_stored_line_evidence(record, sheet) or {}).get("fingerprint") == signature]
            ack_candidates = [record for record in acknowledged_by_signature.get((group_key, sheet, signature), [])
                if str((decode_grist_any_object(record.get("SemanticEvidence")) or {}).get("deletionReviewKey") or "")
                not in attached_deletions[group_key]]
            selected_record = candidates[0][1] if candidates else None
            resolution = ack_candidates[0] if ack_candidates else None
            if selected_record:
                consumed_deletion_events.add(record_event_key(selected_record))
            stable_key = (str((decode_grist_any_object(resolution.get("SemanticEvidence")) or {}).get("deletionReviewKey") or "")
                if resolution else str((selected_record or {}).get("ReviewKey") or ""))
            add_deleted_line(group, evidence=previous, record=selected_record,
                review_key=stable_key or None, resolution=resolution,
                fallback_assignment=group.get("previousAssignment"))

    # A partial line save does not contain a complete group snapshot. Its
    # unmatched authoritative line event still needs a visible, conservative
    # review card. Changed or duplicate predecessors are excluded above.
    for event_key, record in active_records.items():
        if event_key in matched_record_keys or event_key in ambiguous_record_keys or event_key in consumed_deletion_events:
            continue
        sheet = str(record.get("SheetName") or "")
        evidence = _stored_line_evidence(record, sheet)
        if not evidence or not context_matches(record) or record.get("SourceHash") == source_hash:
            continue
        if current_by_sheet_fp.get((sheet, str(evidence.get("fingerprint") or ""))):
            continue
        group_key = str(record.get("GroupKey") or "")
        target = deletion_context(group_key, sheet)
        if not target:
            continue
        comp_type = "deleted" if (group_key, sheet, str(evidence.get("fingerprint") or "")) in deleted_signatures else "unmatched_previous"
        resolution = acknowledged_deletions.get(str(record.get("ReviewKey") or ""))
        add_deleted_line(target, evidence=evidence, record=record, comparison_type=comp_type, resolution=resolution)

    # Keep resolved audit evidence visible on reload even if a later partial
    # mapping save means the old full-group snapshot is no longer reconstructable.
    for record in deletion_review_events:
        evidence = decode_grist_any_object(record.get("SemanticEvidence")) or {}
        review_key = str(evidence.get("deletionReviewKey") or "")
        group_key = str(evidence.get("priorGroupKey") or record.get("GroupKey") or "")
        sheet = str(record.get("SheetName") or "")
        signature = str(evidence.get("fingerprint") or "")
        if (not review_key or not signature or review_key in attached_deletions[group_key]
                or current_by_sheet_fp.get((sheet, signature))):
            continue
        target = deletion_context(group_key, sheet)
        if target:
            add_deleted_line(target, evidence=evidence, record=None, review_key=review_key,
                comparison_type="deleted", resolution=record)

    pending_deleted_count = 0
    for group in current_or_removed.values():
        group["deletedLines"].sort(key=lambda item: (int(item.get("row") or 0), item["reviewKey"]))
        pending = [item for item in group["deletedLines"] if not item.get("reviewed")]
        pending_deleted_count += len(pending)
        if group in detail.get("removedGroups", []):
            group["reviewed"] = bool(group["deletedLines"]) and not pending
            if group["deletedLines"] and not pending:
                group["semanticComparison"] = {**(group.get("semanticComparison") or {}),
                    "status": "deleted_lines_reviewed", "requiresReview": False}

    for group in detail["groups"]:
        rows = group.get("rows") or []
        assigned = [row["part"] for row in rows if row.get("reviewed") and row.get("part")]
        part_ids = {part["id"] for part in assigned}
        mapped_count = len(assigned)
        group["assignmentSummary"] = {"partCount": len(part_ids), "mappedLineCount": mapped_count,
            "unassignedLineCount": max(0, len(rows) - mapped_count)}
        group["part"] = assigned[0] if rows and all(row.get("reviewed") and row.get("part") for row in rows) and len(part_ids) == 1 else None
        group["reviewed"] = bool(rows) and all(row.get("reviewed") for row in rows) and not any(
            not item.get("reviewed") for item in group.get("deletedLines") or [])
        group["explicitlyUnassigned"] = bool(rows) and all(row.get("explicitlyUnassigned") for row in rows)
        group["pendingLineKeys"] = [row["lineKey"] for row in rows if not row.get("reviewed")]
        statuses = {str(row.get("semanticComparison", {}).get("status") or "") for row in rows}
        base_comparison = group_comparisons.get(str(group.get("key") or "")) or group.get("semanticComparison") or {}
        if any(not item.get("reviewed") for item in group.get("deletedLines") or []):
            group["semanticComparison"] = {**base_comparison, "status": "deleted_lines_pending", "requiresReview": True}
        elif group.get("deletedLines"):
            group["semanticComparison"] = {**base_comparison, "status": "deleted_lines_reviewed", "requiresReview": False}
        elif rows and group["reviewed"] and statuses.issubset({"", "new", "unchanged"}):
            group["semanticComparison"] = {**(group.get("semanticComparison") or {}),
                "status": "unchanged", "differences": [], "requiresReview": False,
                "groupingPolicyChanged": False}
        elif "ambiguous_correspondence" in statuses:
            group["semanticComparison"] = {**(group.get("semanticComparison") or {}), "status": "ambiguous_correspondence", "requiresReview": True}
        elif "changed" in statuses:
            group["semanticComparison"] = {**(group.get("semanticComparison") or {}), "status": "changed", "requiresReview": True}
        group["needsCompatibilityReview"] = any(row.get("previousAssignment") for row in rows)
        for part in assigned:
            if all(existing["id"] != part["id"] for existing in detail["parts"]):
                detail["parts"].append(part)
    detail["unresolvedDeletedLines"] = pending_deleted_count
    detail["unresolvedGroups"] = sum(not group["reviewed"] for group in detail["groups"]) + sum(
        not group.get("reviewed") for group in detail.get("removedGroups") or [])
    detail["unresolvedLines"] = sum(1 for item in current_lines if not item["source"].get("reviewed"))
    return detail


def save_mapping(store, *, file_id: str, source_hash: str, association, groups: list[dict[str, Any]],
                 decisions: dict[str, str] | None = None, line_decisions: dict[str, str] | None = None,
                 deleted_line_reviews: dict[str, str] | None = None,
                 expected_hash: str, expected_version: int, expected_association: str,
                 expected_association_version: int, actor: str, reason: str = "",
                 group_reasons: dict[str, str] | None = None, line_reasons: dict[str, str] | None = None,
                 request_key: str, before_write=None):
    _ensure_group_semantic_evidence(groups)
    decisions = decisions or {}
    line_decisions = line_decisions or {}
    deleted_line_reviews = deleted_line_reviews or {}
    group_reasons = group_reasons or {}
    line_reasons = line_reasons or {}
    if not (decisions or line_decisions or deleted_line_reviews) or not request_key.strip() or not actor.strip():
        raise PartConflict("PART_REVIEW_INPUT_REQUIRED", "Select a Part for a source line or explicitly review a deleted line")
    if any(not isinstance(key, str) or not isinstance(value, str) for mapping in (decisions, line_decisions, deleted_line_reviews) for key, value in mapping.items()):
        raise PartConflict("PART_REVIEW_INPUT_INVALID", "Mapping decisions and review reasons must use text keys and values")
    payload_hash = fingerprint([file_id, expected_hash, expected_version, expected_association,
        expected_association_version, sorted(decisions.items()), sorted(line_decisions.items()),
        sorted((key, str(value or "").strip()) for key, value in deleted_line_reviews.items()),
        {key: str(value or "").strip() for key, value in sorted(group_reasons.items())},
        {key: str(value or "").strip() for key, value in sorted(line_reasons.items())}, reason.strip(), actor])
    with store.lock:
        all_rows = store.records(MAPPING_TABLE)
        replay = [row["fields"] for row in all_rows if row["fields"].get("RequestKey") == request_key]
        if replay:
            expected_count = replay[0].get("RequestRowCount")
            if len(replay) != expected_count or any(row.get("RequestFingerprint") != payload_hash for row in replay):
                raise PartConflict("PART_REQUEST_CONFLICT", "This mapping request key has a different or incomplete saved payload")
            _history(store, file_id)
            return {"idempotent": True, "version": replay[0]["Version"], "savedRows": len(replay),
                "assignments": {}, "lineAssignments": {_stored_line_key(row): {"partId": _stored_part_id(row) or None,
                    "actionType": row.get("ActionType") or "legacy_review", "requiresUserReason": row.get("ActionType") in {"replace_assignment", "clear_assignment"}}
                    for row in replay if _stored_line_key(row)},
                "deletedLineReviews": {str((decode_grist_any_object(row.get("SemanticEvidence")) or {}).get("deletionReviewKey") or ""):
                    {"actionType": row.get("ActionType"), "requiresUserReason": True}
                    for row in replay if _is_source_deletion_review(row)},
                "confirmedRows": replay, "sourceHash": source_hash, "associationKey": association.id if association else "",
                "associationVersion": association.version if association else 0}
        detail = mapping_detail(store, file_id=file_id, source_hash=source_hash, association=association,
            groups=groups, refresh_parts=True)
        if not detail["schemaAvailable"]:
            raise PartConflict("PART_SCHEMA_UNAVAILABLE", "The Part review schema is unavailable")
        if expected_hash != source_hash or expected_version != detail["version"]:
            raise PartConflict("PART_REVIEW_STALE", "The workbook or mapping version changed; reload the review")
        if not association or association.id != expected_association or association.version != expected_association_version:
            raise PartConflict("PART_ASSOCIATION_STALE", "The saved association changed; reload the review")

        current_groups = {group["key"]: group for group in detail["groups"]}
        current_lines: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
        for group in detail["groups"]:
            for source in group.get("rows") or []:
                if source.get("lineKey"):
                    current_lines[str(source["lineKey"])] = (group, source)
        expanded: dict[str, str] = {}
        source_groups: dict[str, str] = {}
        for group_key, part_id in decisions.items():
            group = current_groups.get(group_key)
            if group is None:
                raise PartConflict("PART_GROUP_UNKNOWN", "A selected source group is no longer available")
            for source in group.get("rows") or []:
                line_key = str(source.get("lineKey") or "")
                if not line_key:
                    raise PartConflict("PART_SEMANTIC_EVIDENCE_UNAVAILABLE", "A source line has no safe current identity")
                expanded[line_key] = part_id
                source_groups[line_key] = group_key
        for line_key, part_id in line_decisions.items():
            if line_key not in current_lines:
                raise PartConflict("PART_LINE_UNKNOWN", "A selected source line is no longer available; reload before saving")
            if line_key in expanded and expanded[line_key] != part_id:
                raise PartConflict("PART_REVIEW_INPUT_INVALID", "The same source line has conflicting group and line assignments")
            expanded[line_key] = part_id
            source_groups[line_key] = current_lines[line_key][0]["key"]
        if set(line_reasons) - set(expanded):
            raise PartConflict("PART_REVIEW_INPUT_INVALID", "Mapping reasons must identify lines included in this save")
        if set(group_reasons) - set(decisions) and set(group_reasons) - set(source_groups.values()):
            raise PartConflict("PART_REVIEW_INPUT_INVALID", "Mapping reasons must identify groups included in this save")

        deleted_line_items = {str(line.get("reviewKey") or ""): line
            for group in [*detail.get("groups", []), *(detail.get("removedGroups") or [])]
            for line in group.get("deletedLines") or []}
        for review_key, user_reason in deleted_line_reviews.items():
            line = deleted_line_items.get(review_key)
            if line is None:
                raise PartConflict("PART_DELETED_LINE_UNKNOWN", "A deleted source line is no longer awaiting review; reload the mapping")
            if line.get("reviewed"):
                raise PartConflict("PART_DELETED_LINE_ALREADY_REVIEWED", "This deleted source line was already reviewed; reload the mapping")
            if not str(user_reason or "").strip():
                raise PartConflict("PART_DELETED_LINE_REASON_REQUIRED", "Confirming a removed source line requires an audited reason")

        parts = {part["id"]: part for part in list_parts(store, refresh=True)}
        for part_id in expanded.values():
            if part_id:
                part = parts.get(part_id)
                if not part or not part["selectable"] or part["duplicateName"]:
                    raise PartConflict("PART_SELECTION_INVALID", "Select a canonical Part with an unambiguous name")
        version = detail["version"] + 1
        rows = []
        line_actions: dict[str, dict[str, Any]] = {}
        grouped_actions: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for line_key, part_id in sorted(expanded.items()):
            group, source = current_lines[line_key]
            diagnostic = group.get("sourceDiagnostic", {})
            if diagnostic and diagnostic.get("status") not in {None, "ok"}:
                raise PartConflict("PART_SOURCE_LABEL_UNAVAILABLE", "The source Part label column is missing or ambiguous; resolve its header before saving")
            line_evidence = source.get("semanticEvidence")
            if (not isinstance(line_evidence, dict) or not line_evidence.get("fingerprint")
                    or len(line_evidence.get("fields") or []) == 0):
                raise PartConflict("PART_SEMANTIC_EVIDENCE_UNAVAILABLE", "A source line is missing its normalized manufacturing evidence")
            previous = source.get("previousAssignment") or {}
            old_part = source.get("part") or {}
            prior_ids = set(previous.get("assignedPartIds") or [])
            if old_part.get("id"):
                prior_ids.add(str(old_part["id"]))
            selected_id = str(part_id or "")
            replacing = bool(prior_ids and (not selected_id or any(prior != selected_id for prior in prior_ids)))
            user_reason = str(line_reasons.get(line_key) or group_reasons.get(group["key"]) or reason or "").strip()
            if replacing and not user_reason:
                action = "clear_assignment" if not selected_id else "replace_assignment"
                description = "Cleared saved Part assignment" if not selected_id else "Replaced saved Part assignment"
                raise PartConflict("PART_REVIEW_REASON_REQUIRED", f"{description} for {group['description'] or group['sheet'] + ' row ' + str(source['row'])} requires a reason")
            if not selected_id:
                action_type = "clear_assignment" if replacing else "initial_unassigned"
                audit_reason = user_reason if replacing else "Initial explicit unassigned review"
            elif replacing:
                action_type, audit_reason = "replace_assignment", user_reason
            elif (previous.get("contextChanged") or previous.get("sourceChanged") or previous.get("associationChanged")
                    or (source.get("semanticComparison") or {}).get("sourceHashChanged")
                    or (source.get("semanticComparison") or {}).get("associationChanged")):
                action_type, audit_reason = "context_reconfirmation", "Reconfirmed source-line assignment against refreshed workbook or association evidence"
            elif old_part.get("id") == selected_id:
                action_type, audit_reason = "reconfirmation", "Reconfirmed existing source-line Part assignment"
            else:
                action_type, audit_reason = "initial_assignment", "Initial Part assignment"
            selected = parts.get(selected_id) if selected_id else None
            line_actions[line_key] = {"actionType": action_type, "reason": audit_reason,
                "partId": selected_id or None, "requiresUserReason": replacing}
            grouped_actions[group["key"]].append(line_actions[line_key])
            persisted_evidence = {**line_evidence, "lineKey": line_key,
                "linePolicyVersion": MAPPING_LINE_EVIDENCE_VERSION}
            supersedes_review_keys = previous.get("reviewKeys") or source.get("authoritativeReviewKeys") or (
                [previous.get("reviewKey")] if previous.get("reviewKey") else
                ([source["authoritativeReviewKey"]] if source.get("authoritativeReviewKey") else []))
            supersedes_review_keys = sorted({str(key) for key in supersedes_review_keys if isinstance(key, str) and key})
            if supersedes_review_keys:
                persisted_evidence["supersedesReviewKeys"] = supersedes_review_keys
            row = {"ReviewKey": "part-review:" + fingerprint([request_key, line_key]),
                "FileKey": file_id, "SourceHash": source_hash, "AssociationKey": association.id,
                "AssociationVersion": association.version, "GroupKey": group["key"],
                "SheetName": source["sheet"], "SourceRow": source["row"],
                "SourceDescription": group["description"],
                "ProductPart": (int(selected["gristRecordId"]) if selected and selected.get("gristRecordId") is not None
                    else (None if not selected or selected.get("stableIdentity") else int(selected_id))),
                "StablePartId": selected_id if selected and selected.get("stableIdentity") and selected.get("gristRecordId") is not None else "",
                "PartIdentity": selected_id if selected and selected.get("stableIdentity") and selected.get("gristRecordId") is None else None,
                "PartRevision": _grist_ref(selected.get("partRevisionRecordId")) if selected and selected.get("gristRecordId") is not None else None,
                "PartMetadataVersion": _grist_ref(selected.get("partMetadataRecordId")) if selected and selected.get("gristRecordId") is not None else None,
                "PartNumberUsed": str(selected.get("partNumber") or "") if selected else "",
                "NameUsed": str(selected.get("name") or "") if selected else "",
                "Version": version, "Actor": actor, "Reason": audit_reason, "ActionType": action_type,
                "MappingPolicyVersion": MAPPING_POLICY_VERSION,
                "SemanticPolicyVersion": MAPPING_SEMANTIC_POLICY_VERSION,
                "SemanticFingerprint": group["semanticFingerprint"], "SemanticEvidence": persisted_evidence,
                "OccurredAt": grist_datetime(utc_now()), "RequestKey": request_key,
                "RequestFingerprint": payload_hash, "RequestRowCount": len(expanded) + len(deleted_line_reviews)}
            if not (row.get("ProductPart") is None and row.get("PartIdentity")):
                row.pop("PartIdentity", None)
            rows.append(row)
        deleted_review_actions: dict[str, dict[str, Any]] = {}
        history_by_key = {str(row["fields"].get("ReviewKey") or ""): row["fields"] for row in all_rows}
        groups_by_key = {str(group.get("key") or ""): group for group in detail.get("groups", [])}
        for review_key, user_reason in sorted(deleted_line_reviews.items()):
            deleted = deleted_line_items[review_key]
            prior_record = history_by_key.get(str(deleted.get("priorReviewKey") or ""))
            previous = deleted.get("previousAssignment") or {}
            part_id = str(previous.get("partId") or "")
            selected = parts.get(part_id)
            prior_evidence = deleted.get("semanticEvidence") or {}
            persisted_evidence = {**prior_evidence, "sourceDeletionReview": True,
                "deletionReviewKey": review_key, "priorReviewKey": str(deleted.get("priorReviewKey") or review_key),
                "priorGroupKey": str(deleted.get("groupKey") or ""), "priorSourceRow": deleted.get("row"),
                "priorVersion": (prior_record or {}).get("Version"), "priorActor": (prior_record or {}).get("Actor"),
                "priorReason": (prior_record or {}).get("Reason"), "priorOccurredAt": (prior_record or {}).get("OccurredAt"),
                "reviewedAgainstSourceHash": source_hash, "reviewedAgainstAssociationKey": association.id,
                "reviewedAgainstAssociationVersion": association.version}
            supersedes = str((prior_record or {}).get("ReviewKey") or "")
            if supersedes:
                prior_links = prior_evidence.get("supersedesReviewKeys")
                persisted_evidence["supersedesReviewKeys"] = sorted({
                    *(str(key) for key in prior_links if isinstance(key, str) and key), supersedes}) if isinstance(prior_links, list) else [supersedes]
            event = {"ReviewKey": "part-deletion-review:" + fingerprint([request_key, review_key]),
                "FileKey": file_id, "SourceHash": source_hash, "AssociationKey": association.id,
                "AssociationVersion": association.version, "GroupKey": str(deleted.get("groupKey") or ""),
                "SheetName": str(deleted.get("sheet") or ""), "SourceRow": int(deleted.get("row") or 0),
                "SourceDescription": str(deleted.get("description") or ""),
                "ProductPart": (prior_record or {}).get("ProductPart") if prior_record else (
                    int(selected["gristRecordId"]) if selected and selected.get("gristRecordId") is not None else None),
                "StablePartId": (prior_record or {}).get("StablePartId") if prior_record else (
                    part_id if selected and selected.get("stableIdentity") and selected.get("gristRecordId") is not None else ""),
                "PartRevision": (prior_record or {}).get("PartRevision") if prior_record else (
                    _grist_ref(selected.get("partRevisionRecordId")) if selected and selected.get("gristRecordId") is not None else None),
                "PartMetadataVersion": (prior_record or {}).get("PartMetadataVersion") if prior_record else (
                    _grist_ref(selected.get("partMetadataRecordId")) if selected and selected.get("gristRecordId") is not None else None),
                "PartNumberUsed": str((prior_record or {}).get("PartNumberUsed") or previous.get("partNumber") or ""),
                "NameUsed": str((prior_record or {}).get("NameUsed") or previous.get("name") or ""),
                "Version": version, "Actor": actor, "Reason": str(user_reason or "").strip(),
                "ActionType": "source_deletion_review", "MappingPolicyVersion": MAPPING_POLICY_VERSION,
                "SemanticPolicyVersion": MAPPING_SEMANTIC_POLICY_VERSION,
                "SemanticFingerprint": (groups_by_key.get(str(deleted.get("groupKey") or "")) or {}).get("semanticFingerprint")
                    or (prior_record or {}).get("SemanticFingerprint") or "",
                "SemanticEvidence": persisted_evidence, "OccurredAt": grist_datetime(utc_now()), "RequestKey": request_key,
                "RequestFingerprint": payload_hash, "RequestRowCount": len(expanded) + len(deleted_line_reviews)}
            if not (event.get("ProductPart") is None and event.get("PartIdentity")):
                event.pop("PartIdentity", None)
            rows.append(event)
            deleted_review_actions[review_key] = {"actionType": "source_deletion_review", "requiresUserReason": True}
        if before_write:
            before_write()
        store.append(MAPPING_TABLE, rows)
        confirmed_history = _history(store, file_id)
        confirmed_rows = [row["fields"] for row in confirmed_history if row["fields"].get("RequestKey") == request_key]
        if len(confirmed_rows) != len(rows) or any(row.get("RequestFingerprint") != payload_hash for row in confirmed_rows):
            raise PartConflict("PART_WRITE_UNCONFIRMED", "Grist did not confirm every source-line mapping record after Save")
        assignments = {key: actions[0] for key, actions in grouped_actions.items() if len(actions) == 1}
        return {"idempotent": False, "version": version, "savedRows": len(rows), "assignments": assignments,
            "lineAssignments": line_actions, "deletedLineReviews": deleted_review_actions,
            "confirmedRows": confirmed_rows, "sourceHash": source_hash,
            "associationKey": association.id if association else "",
            "associationVersion": association.version if association else 0}


def _record_ref(value: Any) -> str:
    if isinstance(value, list) and len(value) > 2 and value[0] == "R":
        return str(value[2])
    return str(value or "")


def _grist_ref(value: Any):
    if value in (None, ""):
        return None
    if isinstance(value, list) and len(value) > 2 and value[0] == "R":
        return int(value[2])
    return int(value)


def attach_mapping_evidence(evidence: dict[str, Any], detail: dict[str, Any]):
    """Only lines with a current canonical Part reduce downstream review work."""
    reviewed = {(row["sheet"], row["row"]) for group in detail["groups"] for row in group["rows"]
        if row.get("reviewed", group.get("reviewed")) and (row.get("part") or {}).get("id")}
    rows = evidence["partRowsRequiringReview"]
    evidence["partRowsRequiringReview"] = [row for row in rows if (row["sheet"], row["row"]) not in reviewed]
    evidence["partMapping"] = {"version": detail["version"], "schemaAvailable": detail["schemaAvailable"],
        "reviewedRows": len(rows) - len(evidence["partRowsRequiringReview"]), "unresolvedGroups": detail["unresolvedGroups"]}
    if detail["schemaAvailable"] and not evidence["partRowsRequiringReview"] and not detail.get("unresolvedDeletedLines"):
        evidence["pendingPolicies"] = [policy for policy in evidence["pendingPolicies"] if policy != "Reviewed Part assignments"]

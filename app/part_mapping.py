"""Canonical Parts and immutable, source-scoped mapping review."""
from __future__ import annotations
from hashlib import sha256
import json
import sqlite3
from threading import RLock
from time import monotonic
import unicodedata
from typing import Any
from app.domain import utc_now
from app.exceptions import GristError
from app.grist_types import grist_datetime, datetime_text
from app.part_identity import PartIdentityError


PART_SHEETS = {"5. Material Cut List Price", "Tool Shop Items", "CNC Cut List"}
MAPPING_TABLE = "PartMappingReview"
MAPPING_POLICY_VERSION = "part-source-labels-v2"
MAPPING_LABEL_FIELD = {
    "5. Material Cut List Price": "product_part_name",
    "Tool Shop Items": "product_part_name",
    "CNC Cut List": "part_category",
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
                "availableFields": row.get("available_fields", [])})
            group["legacyGroupKeys"].add(legacy_key)
    for group in groups.values():
        diagnostic = (sheet_diagnostics or {}).get(group["sheet"], {})
        group["sourceDiagnostic"] = diagnostic
        group["legacyGroupKeys"] = sorted(group["legacyGroupKeys"])
        group["evidenceFingerprint"] = fingerprint([
            [row["sheet"], row["row"], row["fields"], row["sourceHeaders"], row["sourceHeaderCells"], row["sourceCells"]]
            for row in group["rows"]
        ])
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


def _group_records_by_key(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row.get("GroupKey") or ""), []).append(row)
    return grouped


def mapping_detail(store, *, file_id: str, source_hash: str, association, groups: list[dict[str, Any]], refresh_parts: bool = False):
    parts = list_parts(store, refresh=refresh_parts)
    history = _history(store, file_id)
    current = [row for row in history if row["fields"].get("SourceHash") == source_hash
               and association and row["fields"].get("AssociationKey") == association.id
               and row["fields"].get("AssociationVersion") == association.version]
    by_group: dict[str, list[dict[str, Any]]] = {}
    for row in current:
        by_group.setdefault(row["fields"]["GroupKey"], []).append(row)
    resolved = []
    resolved_parts: dict[str, dict[str, Any]] = {}
    for group in groups:
        candidates = by_group.get(group["key"], [])
        version = max((row["fields"]["Version"] for row in candidates), default=0)
        latest = [row["fields"] for row in candidates if row["fields"]["Version"] == version]
        refs = {str(row.get("StablePartId") or row.get("PartIdentity") or _record_ref(row.get("ProductPart"))) for row in latest}
        expected = {(row["sheet"], row["row"]) for row in group["rows"]}
        actual = {(row["SheetName"], int(row["SourceRow"])) for row in latest}
        part = next((part for part in parts if len(refs) == 1 and part["id"] in refs), None)
        valid = bool(part and part["selectable"] and not part["duplicateName"] and expected == actual)
        if valid and part:
            resolved_parts[part["id"]] = part
        # Older reviews grouped by description alone and used Plate Part to Cut on CNC.
        # Keep them immutable and visible as review evidence; never apply them to the new
        # sheet-scoped identity without an explicit new Save.
        source_refs = {(row["sheet"], row["row"]) for row in group["rows"]}
        prior_rows = [row["fields"] for row in history
            if (row["fields"].get("SheetName"), int(row["fields"].get("SourceRow") or 0)) in source_refs
            and (row["fields"].get("GroupKey") != group["key"]
                or row["fields"].get("SourceHash") != source_hash
                or not association or row["fields"].get("AssociationKey") != association.id
                or row["fields"].get("AssociationVersion") != association.version)]
        prior_rows.sort(key=lambda row: (int(row.get("Version") or 0), str(row.get("OccurredAt") or ""), str(row.get("RequestKey") or "")))
        # Old groups may have spanned several sheets or several current groups.
        # Resolve their latest decision independently for each source coordinate;
        # slicing the last N records can accidentally borrow a neighboring row's
        # Part when history was interleaved across groups.
        latest_by_source: dict[tuple[str, int], dict[str, Any]] = {}
        for prior in prior_rows:
            coordinate = (str(prior.get("SheetName") or ""), int(prior.get("SourceRow") or 0))
            if coordinate in source_refs:
                latest_by_source[coordinate] = prior
        latest_prior_rows = list(latest_by_source.values())
        prior_ids = {str(row.get("StablePartId") or row.get("PartIdentity") or _record_ref(row.get("ProductPart")))
            for row in latest_prior_rows if row.get("StablePartId") or row.get("PartIdentity") or row.get("ProductPart")}
        all_coordinates_reviewed = source_refs.issubset(latest_by_source)
        all_coordinates_agree = bool(latest_prior_rows) and len(prior_ids) == 1 and all(
            bool(row.get("StablePartId") or row.get("PartIdentity") or row.get("ProductPart")) for row in latest_prior_rows)
        prior_part = next((candidate for candidate in parts if all_coordinates_reviewed and all_coordinates_agree
            and len(prior_ids) == 1 and candidate["id"] in prior_ids), None)
        prior_evidence = None
        if latest_prior_rows:
            sample = max(latest_prior_rows, key=lambda row: (int(row.get("Version") or 0), str(row.get("OccurredAt") or "")))
            latest_assigned_ids = sorted({str(row.get("StablePartId") or row.get("PartIdentity") or _record_ref(row.get("ProductPart")))
                for row in latest_prior_rows if row.get("StablePartId") or row.get("PartIdentity") or row.get("ProductPart")})
            # Reason requirements are based on the most recent persisted action
            # for each source coordinate across hashes, associations and group
            # policy versions. New source rows have no prior assignment and do
            # not by themselves turn an initial assignment into a replacement.
            prior_context_changed = any(row.get("SourceHash") != source_hash
                or (association and (row.get("AssociationKey") != association.id
                    or int(row.get("AssociationVersion") or 0) != int(association.version)))
                or row.get("GroupKey") != group["key"] for row in latest_prior_rows)
            prior_evidence = {"partNumber": ((prior_part or {}).get("partNumber") or sample.get("PartNumberUsed")) if all_coordinates_agree else None,
                "name": ((prior_part or {}).get("name") or sample.get("NameUsed")) if all_coordinates_agree else None,
                "sourceDescription": sample.get("SourceDescription") or "", "version": sample.get("Version"),
                "sourceChanged": sample.get("SourceHash") != source_hash,
                "associationChanged": bool(association and (sample.get("AssociationKey") != association.id
                    or sample.get("AssociationVersion") != association.version)), "requiresReview": True,
                "previousRowsReviewed": len(latest_prior_rows), "currentRows": len(source_refs),
                "previousRowsAgree": all_coordinates_reviewed and all_coordinates_agree,
                "assignedPartIds": latest_assigned_ids,
                "hasPriorAssignment": bool(latest_assigned_ids),
                "contextChanged": prior_context_changed,
                "groupingPolicyChanged": any(row.get("GroupKey") != group["key"] for row in latest_prior_rows)}
        if prior_part:
            resolved_parts[prior_part["id"]] = prior_part
        explicit_unassigned = bool(latest and all(not str(value or "") for value in refs)
            and expected == actual and len(latest) == len(expected))
        resolved.append({**group, "part": part if valid else None, "reviewed": valid, "version": version,
            "explicitlyUnassigned": explicit_unassigned, "previousAssignment": prior_evidence,
            "needsCompatibilityReview": bool(prior_evidence and not valid)})
    return {"fileId": file_id, "sourceHash": source_hash, "sourceBasis": "saved_workbook",
            "associationKey": association.id if association else "", "associationVersion": association.version if association else 0,
            "version": max((int(row["fields"]["Version"]) for row in history), default=0),
            "schemaAvailable": store.available, "legacyHistoryAvailable": getattr(store, "legacy_history_available", True),
            "mappingPolicyVersion": MAPPING_POLICY_VERSION, "sourceSheets": getattr(groups, "diagnostics", {}),
            "parts": list(resolved_parts.values()), "groups": resolved,
            "unresolvedGroups": sum(not group["reviewed"] for group in resolved), "authorityChanged": False,
            "history": [{**row["fields"], "OccurredAt": datetime_text(row["fields"].get("OccurredAt"))} for row in history]}


def save_mapping(store, *, file_id: str, source_hash: str, association, groups: list[dict[str, Any]],
                 decisions: dict[str, str], expected_hash: str, expected_version: int,
                 expected_association: str, expected_association_version: int,
                 actor: str, reason: str = "", group_reasons: dict[str, str] | None = None,
                 request_key: str, before_write=None):
    group_reasons = group_reasons or {}
    if not decisions or not request_key.strip() or not actor.strip():
        raise PartConflict("PART_REVIEW_INPUT_REQUIRED", "Select a Part for at least one group and provide a request identity")
    if set(group_reasons) - set(decisions):
        raise PartConflict("PART_REVIEW_INPUT_INVALID", "Mapping reasons must identify groups included in this save")
    payload_hash = fingerprint([file_id, expected_hash, expected_version, expected_association,
                                expected_association_version, decisions,
                                {key: str(value or "").strip() for key, value in sorted(group_reasons.items())},
                                reason.strip(), actor])
    with store.lock:
        all_rows = store.records(MAPPING_TABLE)
        replay = [row["fields"] for row in all_rows if row["fields"].get("RequestKey") == request_key]
        if replay:
            expected_count = replay[0].get("RequestRowCount")
            if len(replay) != expected_count or any(row.get("RequestFingerprint") != payload_hash for row in replay):
                raise PartConflict("PART_REQUEST_CONFLICT", "This mapping request key has a different or incomplete saved payload")
            _history(store, file_id)
            assignments = {}
            for key, records in _group_records_by_key(replay).items():
                sample = records[0]
                assignments[key] = {"actionType": sample.get("ActionType") or "legacy_review",
                    "reason": sample.get("Reason") or "", "partId": sample.get("StablePartId") or sample.get("PartIdentity") or _record_ref(sample.get("ProductPart")) or None,
                    "requiresUserReason": sample.get("ActionType") in {"replace_assignment", "clear_assignment"}}
            return {"idempotent": True, "version": replay[0]["Version"], "savedRows": len(replay),
                "assignments": assignments, "confirmedRows": replay, "sourceHash": source_hash,
                "associationKey": association.id if association else "",
                "associationVersion": association.version if association else 0}
        detail = mapping_detail(store, file_id=file_id, source_hash=source_hash, association=association, groups=groups, refresh_parts=True)
        if not detail["schemaAvailable"]:
            raise PartConflict("PART_SCHEMA_UNAVAILABLE", "The Part review schema is unavailable")
        if expected_hash != source_hash or expected_version != detail["version"]:
            raise PartConflict("PART_REVIEW_STALE", "The workbook or mapping version changed; reload the review")
        if not association or association.id != expected_association or association.version != expected_association_version:
            raise PartConflict("PART_ASSOCIATION_STALE", "The saved association changed; reload the review")
        # Validate new choices against the complete canonical registry even though
        # GET mapping responses only return Parts already attached to visible groups.
        parts = {part["id"]: part for part in list_parts(store, refresh=True)}
        known = {group["key"]: group for group in groups}
        if set(decisions) - set(known):
            raise PartConflict("PART_GROUP_UNKNOWN", "A selected source group is no longer available")
        for part_id in decisions.values():
            if not part_id:
                continue
            part = parts.get(part_id)
            if not part or not part["selectable"] or part["duplicateName"]:
                raise PartConflict("PART_SELECTION_INVALID", "Select a canonical Part with an unambiguous name")
        version = detail["version"] + 1
        rows = []
        saved_actions: dict[str, dict[str, Any]] = {}
        current_by_key = {item["key"]: item for item in detail["groups"]}
        for key, part_id in sorted(decisions.items()):
            diagnostic = known[key].get("sourceDiagnostic", {})
            if diagnostic and diagnostic.get("status") not in {None, "ok"}:
                raise PartConflict("PART_SOURCE_LABEL_UNAVAILABLE", "The source Part label column is missing or ambiguous; resolve its header before saving this group")
            current = current_by_key.get(key, {})
            evidence = current.get("previousAssignment") or {}
            previous_ids = set(evidence.get("assignedPartIds") or [])
            if current.get("part"):
                previous_ids.add(str(current["part"]["id"]))
            selected_id = str(part_id or "")
            replacing = bool(previous_ids and (not selected_id or any(prior != selected_id for prior in previous_ids)))
            user_reason = str(group_reasons.get(key) or reason or "").strip()
            if replacing and not user_reason:
                action = "clear_assignment" if not selected_id else "replace_assignment"
                description = "Cleared saved Part assignment" if not selected_id else "Replaced saved Part assignment"
                raise PartConflict("PART_REVIEW_REASON_REQUIRED", f"{description} for {known[key]['description'] or key} requires a reason")
            if not selected_id:
                action_type = "clear_assignment" if replacing else "initial_unassigned"
                audit_reason = user_reason if replacing else "Initial explicit unassigned review"
            elif replacing:
                action_type, audit_reason = "replace_assignment", user_reason
            elif evidence.get("contextChanged"):
                action_type, audit_reason = "context_reconfirmation", "Reconfirmed unchanged Part assignment against refreshed workbook or association evidence"
            elif current.get("part") and current["part"]["id"] == selected_id:
                action_type, audit_reason = "reconfirmation", "Reconfirmed existing Part assignment"
            else:
                action_type, audit_reason = "initial_assignment", "Initial Part assignment"
            saved_actions[key] = {"actionType": action_type, "reason": audit_reason,
                "requiresUserReason": replacing, "partId": selected_id or None}
            for source in known[key]["rows"]:
                selected = parts.get(part_id) if part_id else None
                rows.append({"ReviewKey": "part-review:" + fingerprint([request_key, key, source]),
                             "FileKey": file_id, "SourceHash": source_hash, "AssociationKey": association.id,
                             "AssociationVersion": association.version, "GroupKey": key,
                             "SheetName": source["sheet"], "SourceRow": source["row"],
                             "SourceDescription": known[key]["description"],
                             "ProductPart": (int(selected["gristRecordId"]) if selected and selected.get("gristRecordId") is not None
                                else (None if not selected or selected.get("stableIdentity") else int(part_id))),
                             "StablePartId": part_id if selected and selected.get("stableIdentity") and selected.get("gristRecordId") is not None else "",
                             "PartIdentity": part_id if selected and selected.get("stableIdentity") and selected.get("gristRecordId") is None else None,
                             "PartRevision": _grist_ref(selected.get("partRevisionRecordId")) if selected and selected.get("gristRecordId") is not None else None,
                             "PartMetadataVersion": _grist_ref(selected.get("partMetadataRecordId")) if selected and selected.get("gristRecordId") is not None else None,
                             "PartNumberUsed": str(selected.get("partNumber") or "") if selected else "",
                             "NameUsed": str(selected.get("name") or "") if selected else "",
                             "Version": version, "Actor": actor, "Reason": audit_reason, "ActionType": action_type,
                             "MappingPolicyVersion": MAPPING_POLICY_VERSION, "OccurredAt": grist_datetime(utc_now()),
                             "RequestKey": request_key, "RequestFingerprint": payload_hash})
        for row in rows:
            if not (row.get("ProductPart") is None and row.get("PartIdentity")):
                row.pop("PartIdentity", None)
            row["RequestRowCount"] = len(rows)
        if before_write:
            before_write()
        store.append(MAPPING_TABLE, rows)
        confirmed_history = _history(store, file_id)
        confirmed_rows = [row["fields"] for row in confirmed_history if row["fields"].get("RequestKey") == request_key]
        if len(confirmed_rows) != len(rows) or any(row.get("RequestFingerprint") != payload_hash for row in confirmed_rows):
            raise PartConflict("PART_WRITE_UNCONFIRMED", "Grist did not confirm every source-row mapping record after Save")
        return {"idempotent": False, "version": version, "savedRows": len(rows),
            "assignments": saved_actions, "confirmedRows": confirmed_rows, "sourceHash": source_hash,
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
    """Only current, source-scoped assignments reduce the outstanding review rows."""
    reviewed = {(row["sheet"], row["row"]) for group in detail["groups"] if group["reviewed"] for row in group["rows"]}
    rows = evidence["partRowsRequiringReview"]
    evidence["partRowsRequiringReview"] = [row for row in rows if (row["sheet"], row["row"]) not in reviewed]
    evidence["partMapping"] = {"version": detail["version"], "schemaAvailable": detail["schemaAvailable"],
        "reviewedRows": len(rows) - len(evidence["partRowsRequiringReview"]), "unresolvedGroups": detail["unresolvedGroups"]}
    if detail["schemaAvailable"] and not evidence["partRowsRequiringReview"]:
        evidence["pendingPolicies"] = [policy for policy in evidence["pendingPolicies"] if policy != "Reviewed Part assignments"]

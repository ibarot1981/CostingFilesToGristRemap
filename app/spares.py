"""Grist-canonical spare identities and source-cost date helpers.

SQLite in this module is limited to a local, transactional number/request
reservation journal. Spare business facts are read from and written to Grist.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
from threading import RLock
from typing import Any, Iterable
import unicodedata
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.grist import GristClient
from app.grist_admin import GristAdminClient
from app.grist_types import canonical_grist_json_cell, decode_grist_any_object, grist_datetime
from app.part_identity import request_fingerprint


SPARE_TABLE_IDS = {
    "SpareMaster", "SpareMasterRequest", "SpareAlias", "SpareHistory",
    "SpareSourceEvidence", "SpareCompositionEvidence", "SpareSourceCost",
    "SpareImportBatch",
}
SOURCING_TYPES = {"purchased", "in_house", "unclassified"}
_SPARE_CODE = re.compile(r"^SM-S-(\d{6,})$", re.IGNORECASE)
_INDIA = ZoneInfo("Asia/Kolkata")


class SpareRegistryError(ValueError):
    def __init__(self, code: str, message: str, status_code: int = 422, retry_disposition: str = "retry_same_request"):
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.retry_disposition = retry_disposition


def normalize_alias(value: Any) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).split()).casefold()


def normalize_sourcing_type(value: Any) -> str:
    text = " ".join(str(value or "").strip().casefold().replace("-", "_").split())
    if text in {"purchase", "purchased", "store", "stores", "bought", "external"}:
        return "purchased"
    if text in {"in house", "in_house", "manufactured", "manufacturing", "internal"}:
        return "in_house"
    if text in {"", "unknown", "unclassified", "unresolved", "not set"}:
        return "unclassified"
    raise SpareRegistryError("SPARE_SOURCING_TYPE_INVALID", "Choose Purchased, In-house, or Unclassified.")


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def parse_source_date(value: Any) -> tuple[str | None, str]:
    """Parse explicit ODS date values without using file timestamps or locale guesses."""
    if isinstance(value, datetime):
        return value.date().isoformat(), "typed_date"
    if isinstance(value, date):
        return value.isoformat(), "typed_date"
    text = str(value or "").strip()
    if not text:
        return None, "missing"
    try:
        return date.fromisoformat(text[:10]).isoformat(), "source_text_iso"
    except ValueError:
        pass
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y", "%d-%m-%y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat(), "source_text_day_first"
        except ValueError:
            continue
    return None, "unparsed"


def source_cost_age(source_date_iso: str | None, *, as_of: date | datetime | None = None) -> dict[str, Any]:
    """Return age from the explicit source date using Asia/Kolkata calendar days."""
    if not source_date_iso:
        return {"status": "unknown", "ageDays": None, "asOfDate": _as_india_date(as_of).isoformat()}
    try:
        source_date = date.fromisoformat(str(source_date_iso)[:10])
    except ValueError:
        return {"status": "unknown", "ageDays": None, "asOfDate": _as_india_date(as_of).isoformat()}
    today = _as_india_date(as_of)
    days = (today - source_date).days
    if days < 0:
        return {"status": "future_date", "ageDays": None, "daysUntil": abs(days), "sourceDate": source_date.isoformat(), "asOfDate": today.isoformat()}
    return {"status": "dated", "ageDays": days, "sourceDate": source_date.isoformat(), "asOfDate": today.isoformat()}


def _as_india_date(value: date | datetime | None) -> date:
    if value is None:
        return datetime.now(_INDIA).date()
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=_INDIA)
        return value.astimezone(_INDIA).date()
    return value


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_id(row: dict[str, Any] | None) -> int | None:
    if not row:
        return None
    try:
        result = int(row.get("id"))
        return result if result > 0 else None
    except (TypeError, ValueError):
        return None


def _ref_id(value: Any) -> int | None:
    if isinstance(value, (list, tuple)) and len(value) > 2 and value[0] == "R":
        value = value[2]
    try:
        result = int(value)
        return result if result > 0 else None
    except (TypeError, ValueError):
        return None


def _object_cell(value: Any) -> dict[str, Any]:
    decoded = decode_grist_any_object(value)
    return decoded if decoded is not None else {}


class GristSpareRegistry:
    """Spare master service backed exclusively by the configured Grist document."""

    def __init__(self, client: GristClient, *, journal_path: str | Path | None = None):
        self.client = client
        configured = journal_path or os.getenv("SAFARI_PART_DATABASE_PATH")
        self.path = Path(configured).expanduser() if configured else Path(__file__).resolve().parents[1] / "state" / "safari_parts.sqlite3"
        if str(self.path) == ":memory:" or str(self.path).startswith("\\\\"):
            raise SpareRegistryError("SPARE_JOURNAL_UNSUPPORTED", "Spare number coordination requires the supported local SQLite journal on the writer host.", 503)
        self.path = self.path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._thread_lock = RLock()
        self._read_scope: ContextVar[dict[str, list[dict[str, Any]]] | None] = ContextVar(f"spare-registry-read-{id(self)}", default=None)
        db = self._connect()
        try:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS spare_registry_settings (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1), next_spare_number INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS spare_registry_reservations (
                    request_key TEXT PRIMARY KEY, request_fingerprint TEXT NOT NULL,
                    spare_key TEXT NOT NULL, spare_code TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL, record_id TEXT, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS spare_registry_alias_reservations (
                    normalized_alias TEXT PRIMARY KEY, request_key TEXT NOT NULL,
                    spare_key TEXT NOT NULL, request_fingerprint TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                INSERT OR IGNORE INTO spare_registry_settings(singleton,next_spare_number) VALUES(1,1);
            """)
        finally:
            db.close()

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=45, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout = 30000")
        db.execute("PRAGMA synchronous = FULL")
        return db

    @contextmanager
    def read_scope(self):
        token = self._read_scope.set({})
        try:
            yield
        finally:
            self._read_scope.reset(token)

    def _rows(self, table: str, *, refresh: bool = False) -> list[dict[str, Any]]:
        scope = self._read_scope.get()
        if scope is not None and not refresh:
            if table not in scope:
                scope[table] = self.client.fetch_table_records_with_ids(table)
            return scope[table]
        rows = self.client.fetch_table_records_with_ids(table)
        if scope is not None:
            scope[table] = rows
        return rows

    def _assert_schema(self, *, write: bool = False) -> None:
        if write:
            validate = getattr(self.client, "validate_safari_write_target", None)
            if callable(validate):
                validate()
        admin = GristAdminClient(self.client.api_key, self.client.base_url)
        current = {str(item.get("id")) for item in admin.list_tables(self.client.doc_id, include_columns=False)}
        missing = sorted(SPARE_TABLE_IDS - current)
        if missing:
            raise SpareRegistryError("SPARE_SCHEMA_UNAVAILABLE", "Safari Grist is missing the reviewed spare tables: " + ", ".join(missing) + ". Apply the additive schema plan before using the spares workspace.", 503)

    @staticmethod
    def _find(rows: list[dict[str, Any]], key_field: str, key: str) -> dict[str, Any] | None:
        found = [row for row in rows if str(row.get("fields", {}).get(key_field) or "") == key]
        if len(found) > 1:
            raise SpareRegistryError("SPARE_DUPLICATE_KEY", f"Grist contains duplicate {key_field} rows; writes are paused until the duplicate is reviewed.", 409)
        return found[0] if found else None

    def _ensure_keyed(self, table: str, key_field: str, key: str, fields: dict[str, Any], fingerprint_field: str | None = None) -> dict[str, Any]:
        row = self._find(self._rows(table, refresh=True), key_field, key)
        expected = str(fields.get(fingerprint_field) or "") if fingerprint_field else ""
        if row:
            actual = row.get("fields", {})
            if expected and str(actual.get(fingerprint_field) or "") != expected:
                raise SpareRegistryError("SPARE_REQUEST_CONFLICT", f"The existing {table} record has a different immutable fingerprint.", 409)
            return row
        self.client.create_table_records(table, [{"fields": fields}])
        row = self._find(self._rows(table, refresh=True), key_field, key)
        if not row:
            raise SpareRegistryError("SPARE_WRITE_UNCONFIRMED", f"Grist did not confirm the {table} record after the write.", 503)
        if expected and str(row.get("fields", {}).get(fingerprint_field) or "") != expected:
            raise SpareRegistryError("SPARE_REQUEST_CONFLICT", f"The created {table} record did not match its immutable fingerprint.", 409)
        return row

    def identity_index(self) -> dict[str, Any]:
        self._assert_schema()
        masters = self._rows("SpareMaster")
        aliases = self._rows("SpareAlias")
        masters_by_id = {_row_id(row): row for row in masters if _row_id(row)}
        by_alias: dict[str, list[dict[str, Any]]] = {}
        for row in aliases:
            fields = row.get("fields", {})
            normalized = str(fields.get("NormalizedValue") or normalize_alias(fields.get("Value")))
            master = masters_by_id.get(_ref_id(fields.get("SpareMaster")))
            if normalized and master:
                by_alias.setdefault(normalized, []).append({"master": master, "alias": row})
        by_code: dict[str, dict[str, Any]] = {}
        for row in masters:
            code = str(row.get("fields", {}).get("SpareCode") or "").strip().casefold()
            if code:
                if code in by_code:
                    raise SpareRegistryError("SPARE_DUPLICATE_CODE", f"Grist contains duplicate spare code {code!r}; writes are paused for review.", 409)
                by_code[code] = row
        return {"masters": masters, "masters_by_id": masters_by_id, "aliases": aliases, "by_alias": by_alias, "by_code": by_code}

    def list_spares(self, *, search: str = "", sourcing_type: str = "", status: str = "active", offset: int = 0, limit: int = 100) -> dict[str, Any]:
        self._assert_schema()
        index = self.identity_index()
        needle = normalize_alias(search)
        filter_type = normalize_sourcing_type(sourcing_type) if sourcing_type else ""
        filter_status = status.strip().casefold()
        aliases_by_master: dict[int, list[str]] = {}
        for row in index["aliases"]:
            fields = row.get("fields", {})
            master_id = _ref_id(fields.get("SpareMaster"))
            if master_id:
                aliases_by_master.setdefault(master_id, []).append(str(fields.get("Value") or ""))
        costs_by_master: dict[int, list[dict[str, Any]]] = {}
        for row in self._rows("SpareSourceCost"):
            fields = row.get("fields", {})
            master_id = _ref_id(fields.get("SpareMaster"))
            if master_id:
                costs_by_master.setdefault(master_id, []).append(self._public_cost(fields))
        items = []
        for row in index["masters"]:
            fields = row.get("fields", {})
            master_id = _row_id(row)
            item = self._public_master(row)
            item["legacyAliases"] = sorted(set(aliases_by_master.get(master_id or -1, [])), key=str.casefold)
            item["sourceCosts"] = sorted(costs_by_master.get(master_id or -1, []), key=lambda x: str(x.get("observedAt") or ""), reverse=True)
            if filter_type and item["sourcingType"] != filter_type:
                continue
            if filter_status and filter_status != "all" and item["status"] != filter_status:
                continue
            searchable = " ".join([item["spareCode"], item["description"], item["category"], *item["legacyAliases"]]).casefold()
            if needle and needle not in searchable:
                continue
            items.append(item)
        items.sort(key=lambda x: (x["spareCode"].casefold(), x["description"].casefold()))
        return {"items": items[offset:offset + limit], "total": len(items), "offset": offset, "limit": limit}

    def get_spare(self, spare_key: str) -> dict[str, Any]:
        self._assert_schema()
        row = self._find(self._rows("SpareMaster"), "SpareKey", spare_key)
        if not row:
            raise SpareRegistryError("SPARE_NOT_FOUND", "The spare identity was not found.", 404)
        item = self._public_master(row)
        spare_id = _row_id(row)
        item["legacyAliases"] = sorted({str(r.get("fields", {}).get("Value") or "").strip() for r in self._rows("SpareAlias") if _ref_id(r.get("fields", {}).get("SpareMaster")) == spare_id and str(r.get("fields", {}).get("Value") or "").strip()}, key=str.casefold)
        item["sourceCosts"] = [self._public_cost(r.get("fields", {})) for r in self._rows("SpareSourceCost") if _ref_id(r.get("fields", {}).get("SpareMaster")) == spare_id]
        item["history"] = self.history(spare_key)
        item["sourceEvidence"] = [self._public_evidence(r.get("fields", {})) for table in ("SpareSourceEvidence", "SpareCompositionEvidence") for r in self._rows(table) if _ref_id(r.get("fields", {}).get("SpareMaster") or r.get("fields", {}).get("ParentSpare")) == spare_id]
        return item

    def history(self, spare_key: str) -> list[dict[str, Any]]:
        master = self._find(self._rows("SpareMaster"), "SpareKey", spare_key)
        if not master:
            raise SpareRegistryError("SPARE_NOT_FOUND", "The spare identity was not found.", 404)
        master_id = _row_id(master)
        rows = [row.get("fields", {}) for row in self._rows("SpareHistory") if _ref_id(row.get("fields", {}).get("SpareMaster")) == master_id]
        rows.sort(key=lambda row: (int(row.get("Version") or 0), str(row.get("OccurredAt") or "")))
        return [{**row, "snapshot": _object_cell(row.get("Snapshot"))} for row in rows]

    @staticmethod
    def _public_master(row: dict[str, Any]) -> dict[str, Any]:
        fields = row.get("fields", {})
        return {"recordId": _row_id(row), "spareKey": str(fields.get("SpareKey") or ""), "spareCode": str(fields.get("SpareCode") or ""), "description": str(fields.get("Description") or ""), "category": str(fields.get("Category") or ""), "unit": str(fields.get("Unit") or ""), "sourcingType": str(fields.get("SourcingType") or "unclassified"), "status": str(fields.get("Status") or "active").casefold(), "version": int(fields.get("Version") or 1), "createdAt": fields.get("CreatedAt"), "updatedAt": fields.get("UpdatedAt"), "createdBy": str(fields.get("CreatedBy") or ""), "updatedBy": str(fields.get("UpdatedBy") or "")}

    @staticmethod
    def _public_cost(fields: dict[str, Any]) -> dict[str, Any]:
        age = source_cost_age(str(fields.get("SourceDateISO") or "") or None)
        return {"kind": fields.get("CostKind"), "value": fields.get("SourceValue"), "unit": fields.get("Unit"), "currency": fields.get("Currency"), "sourceReference": fields.get("SourceReference"), "sourceDateRaw": fields.get("SourceDateRaw"), "sourceDate": fields.get("SourceDateISO"), "sourceDateMeaning": fields.get("SourceDateMeaning"), "dateBasis": fields.get("DateBasis"), "approvalStatus": fields.get("ApprovalStatus"), "formula": fields.get("Formula"), "cachedValue": fields.get("CachedValue"), "observedAt": fields.get("ObservedAt"), "age": age}

    @staticmethod
    def _public_evidence(fields: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in fields.items() if key not in {"SpareMaster", "ParentSpare"}}

    def _reservation_for_request(self, request_key: str) -> dict[str, Any] | None:
        db = self._connect()
        try:
            row = db.execute("SELECT * FROM spare_registry_reservations WHERE request_key=?", (request_key,)).fetchone()
            return dict(row) if row else None
        finally:
            db.close()

    def _reserve_code(self, request_key: str, fingerprint: str, aliases: Iterable[str] = ()) -> tuple[str, str]:
        remote_request = self._find(self._rows("SpareMasterRequest", refresh=True), "RequestKey", request_key)
        if remote_request:
            fields = remote_request.get("fields", {})
            if str(fields.get("RequestFingerprint") or "") != fingerprint or str(fields.get("RequestType") or "") != "create":
                raise SpareRegistryError("SPARE_REQUEST_CONFLICT", "This idempotency key was already used for a different spare operation.", 409)
            remote_key, remote_code = str(fields.get("SpareKey") or ""), str(fields.get("SpareCode") or "")
        else:
            remote_key = remote_code = ""
        existing_codes = []
        for row in self._rows("SpareMaster"):
            match = _SPARE_CODE.match(str(row.get("fields", {}).get("SpareCode") or ""))
            if match:
                existing_codes.append(int(match.group(1)))
        for row in self._rows("SpareMasterRequest"):
            match = _SPARE_CODE.match(str(row.get("fields", {}).get("SpareCode") or ""))
            if match:
                existing_codes.append(int(match.group(1)))
        with self._thread_lock:
            db = self._connect()
            try:
                db.execute("BEGIN IMMEDIATE")
                prior = db.execute("SELECT * FROM spare_registry_reservations WHERE request_key=?", (request_key,)).fetchone()
                if prior:
                    if prior["request_fingerprint"] != fingerprint:
                        db.rollback()
                        raise SpareRegistryError("SPARE_REQUEST_CONFLICT", "This idempotency key was already used for different spare details.", 409)
                    spare_key, spare_code = str(prior["spare_key"]), str(prior["spare_code"])
                    if remote_code and (remote_code != spare_code or remote_key != spare_key):
                        db.rollback()
                        raise SpareRegistryError("SPARE_RESERVATION_CONFLICT", "The Grist request ledger and local reserved number disagree.", 409)
                elif remote_code and remote_key:
                    other = db.execute("SELECT request_key FROM spare_registry_reservations WHERE spare_code=?", (remote_code,)).fetchone()
                    if other and other["request_key"] != request_key:
                        db.rollback()
                        raise SpareRegistryError("SPARE_RESERVATION_CONFLICT", "A different request already reserved the recovered spare number.", 409)
                    spare_key, spare_code = remote_key, remote_code
                    match = _SPARE_CODE.match(spare_code)
                    number = int(match.group(1)) if match else 0
                    db.execute("INSERT INTO spare_registry_reservations(request_key,request_fingerprint,spare_key,spare_code,status,updated_at) VALUES(?,?,?,?,?,?)", (request_key, fingerprint, spare_key, spare_code, "reserved", _now()))
                    db.execute("UPDATE spare_registry_settings SET next_spare_number=MAX(next_spare_number,?) WHERE singleton=1", (number + 1,))
                else:
                    row = db.execute("SELECT next_spare_number FROM spare_registry_settings WHERE singleton=1").fetchone()
                    number = max(int(row[0]), max(existing_codes, default=0) + 1)
                    spare_key = str(uuid4())
                    spare_code = f"SM-S-{number:06d}"
                    db.execute("INSERT INTO spare_registry_reservations(request_key,request_fingerprint,spare_key,spare_code,status,updated_at) VALUES(?,?,?,?,?,?)", (request_key, fingerprint, spare_key, spare_code, "reserved", _now()))
                    db.execute("UPDATE spare_registry_settings SET next_spare_number=? WHERE singleton=1", (number + 1,))
                for alias in sorted({normalize_alias(value) for value in aliases if normalize_alias(value)}):
                    prior_alias = db.execute("SELECT * FROM spare_registry_alias_reservations WHERE normalized_alias=?", (alias,)).fetchone()
                    if prior_alias:
                        if str(prior_alias["request_key"]) != request_key or str(prior_alias["spare_key"]) != spare_key or str(prior_alias["request_fingerprint"]) != fingerprint:
                            db.rollback()
                            raise SpareRegistryError("SPARE_ALIAS_CONFLICT", "This legacy code is reserved by another create attempt. Resume that exact request before assigning it elsewhere.", 409, "safe_to_edit")
                    else:
                        db.execute("INSERT INTO spare_registry_alias_reservations(normalized_alias,request_key,spare_key,request_fingerprint,updated_at) VALUES(?,?,?,?,?)", (alias, request_key, spare_key, fingerprint, _now()))
                db.commit()
            finally:
                db.close()
        return spare_key, spare_code

    def _finish_reservation(self, request_key: str, status: str, record_id: int | None = None) -> None:
        db = self._connect()
        try:
            db.execute("UPDATE spare_registry_reservations SET status=?,record_id=COALESCE(?,record_id),updated_at=? WHERE request_key=?", (status, str(record_id) if record_id else None, _now(), request_key))
        finally:
            db.close()

    def _ensure_request(self, *, request_key: str, request_type: str, fingerprint: str, spare_key: str = "", spare_code: str = "", actor: str = "", reason: str = "") -> dict[str, Any]:
        now = grist_datetime(_now())
        fields = {"RequestKey": request_key, "RequestType": request_type, "RequestFingerprint": fingerprint, "SpareKey": spare_key, "SpareCode": spare_code, "Status": "started", "Actor": actor, "Reason": reason, "StartedAt": now, "UpdatedAt": now, "CompletedAt": None, "Result": canonical_grist_json_cell({})}
        row = self._find(self._rows("SpareMasterRequest", refresh=True), "RequestKey", request_key)
        if row:
            existing = row.get("fields", {})
            if str(existing.get("RequestFingerprint") or "") != fingerprint or str(existing.get("RequestType") or "") != request_type:
                raise SpareRegistryError("SPARE_REQUEST_CONFLICT", "This request key was already used for different details.", 409)
            return row
        return self._ensure_keyed("SpareMasterRequest", "RequestKey", request_key, fields, "RequestFingerprint")

    def _complete_request(self, request_key: str, fingerprint: str, result: dict[str, Any]) -> None:
        row = self._find(self._rows("SpareMasterRequest", refresh=True), "RequestKey", request_key)
        if not row or str(row.get("fields", {}).get("RequestFingerprint") or "") != fingerprint:
            raise SpareRegistryError("SPARE_WRITE_UNCONFIRMED", "Grist did not retain the spare request journal record.", 503)
        completed_at = grist_datetime(_now())
        self.client.update_table_records("SpareMasterRequest", [{"id": row["id"], "fields": {"Status": "complete", "UpdatedAt": completed_at, "CompletedAt": completed_at, "Result": canonical_grist_json_cell(result)}}])
        confirmed = self._find(self._rows("SpareMasterRequest", refresh=True), "RequestKey", request_key)
        if not confirmed or str(confirmed.get("fields", {}).get("Status") or "") != "complete" or str(confirmed.get("fields", {}).get("RequestFingerprint") or "") != fingerprint:
            raise SpareRegistryError("SPARE_WRITE_UNCONFIRMED", "Grist did not confirm the completed spare request journal record.", 503)

    def create_request_record(self, request_key: str) -> dict[str, Any] | None:
        """Return the durable request row used to prove same-request create recovery."""
        return self._find(self._rows("SpareMasterRequest", refresh=True), "RequestKey", request_key)

    def _reject_request(self, request_key: str, fingerprint: str, result: dict[str, Any]) -> None:
        row = self._find(self._rows("SpareMasterRequest", refresh=True), "RequestKey", request_key)
        if not row or str(row.get("fields", {}).get("RequestFingerprint") or "") != fingerprint:
            raise SpareRegistryError("SPARE_WRITE_UNCONFIRMED", "Grist did not retain the rejected spare request audit record.", 503)
        if str(row.get("fields", {}).get("Status") or "") == "rejected":
            return
        now = grist_datetime(_now())
        self.client.update_table_records("SpareMasterRequest", [{"id": row["id"], "fields": {"Status": "rejected", "UpdatedAt": now, "CompletedAt": now, "Result": canonical_grist_json_cell(result)}}])
        confirmed = self._find(self._rows("SpareMasterRequest", refresh=True), "RequestKey", request_key)
        if not confirmed or str(confirmed.get("fields", {}).get("Status") or "") != "rejected":
            raise SpareRegistryError("SPARE_WRITE_UNCONFIRMED", "Grist did not confirm the rejected spare request.", 503)

    def _release_alias_reservations(self, request_key: str) -> None:
        db = self._connect()
        try:
            db.execute("DELETE FROM spare_registry_alias_reservations WHERE request_key=?", (request_key,))
        finally:
            db.close()

    def _retire_rejected_create(self, *, row: dict[str, Any], actor: str, reason: str, request_key: str, fingerprint: str) -> dict[str, Any]:
        fields = row.get("fields", {})
        spare_key = str(fields.get("SpareKey") or "")
        if not spare_key:
            raise SpareRegistryError("SPARE_WRITE_UNCONFIRMED", "The partial spare identity cannot be safely reconciled without its canonical key.", 503)
        current_version = int(fields.get("Version") or 1)
        existing_history = self.history(spare_key)
        if any(str(item.get("RequestKey") or "") == request_key and str(item.get("EventType") or "") == "creation_rejected" for item in existing_history):
            return row
        has_current_version = any(int(item.get("Version") or 0) == current_version for item in existing_history)
        status = str(fields.get("Status") or "active").casefold()
        next_version = current_version + 1 if status != "retired" or has_current_version else current_version
        if status != "retired" or next_version != current_version:
            self.client.update_table_records("SpareMaster", [{"id": row["id"], "fields": {"Status": "retired", "Version": next_version, "UpdatedAt": grist_datetime(_now()), "UpdatedBy": actor}}])
            row = self._spare_master_by_key(spare_key)
            if not row or str(row.get("fields", {}).get("Status") or "").casefold() != "retired":
                raise SpareRegistryError("SPARE_WRITE_UNCONFIRMED", "Grist did not confirm retirement of the rejected partial spare.", 503)
        self._ensure_history(row=row, event_type="creation_rejected", actor=actor, reason=reason, request_key=request_key, fingerprint=fingerprint, snapshot=self._public_master(row))
        return row

    def _spare_master_by_key(self, spare_key: str) -> dict[str, Any] | None:
        return self._find(self._rows("SpareMaster", refresh=True), "SpareKey", spare_key)

    def _check_alias_available(self, alias: str, spare_key: str) -> None:
        normalized = normalize_alias(alias)
        if not normalized:
            return
        index = self.identity_index()
        owners = index["by_alias"].get(normalized, [])
        if any(str(item["master"].get("fields", {}).get("SpareKey") or "") != spare_key for item in owners):
            raise SpareRegistryError("SPARE_ALIAS_CONFLICT", f"Legacy code {alias!r} is already linked to a different spare. Review the identity conflict; no merge was made.", 409)

    def ensure_alias(self, *, spare_key: str, spare_record_id: int, value: str, provenance: dict[str, Any] | None = None, evidence_fingerprint: str | None = None) -> dict[str, Any] | None:
        value = str(value or "").strip()
        normalized = normalize_alias(value)
        if not normalized:
            return None
        self._check_alias_available(value, spare_key)
        provenance = provenance or {}
        key_data = {"spareKey": spare_key, "normalized": normalized, "sourceHash": provenance.get("sourceHash", ""), "sourceKind": provenance.get("sourceKind", "manual"), "sheet": provenance.get("sheetName", ""), "row": provenance.get("sourceRow", 0), "cell": provenance.get("sourceCell", "")}
        source_file = str(provenance.get("sourceFile") or "")
        if source_file:
            key_data["sourceFile"] = source_file
        alias_key = "alias:" + hashlib.sha256(json.dumps(key_data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        fingerprint = evidence_fingerprint or request_fingerprint({**key_data, "value": value})
        fields = {"AliasKey": alias_key, "SpareMaster": spare_record_id, "Value": value, "NormalizedValue": normalized, "SourceFile": source_file, "SourceHash": str(provenance.get("sourceHash") or ""), "SourceKind": str(provenance.get("sourceKind") or "manual"), "SheetName": str(provenance.get("sheetName") or ""), "SourceRow": int(provenance.get("sourceRow") or 0), "SourceCell": str(provenance.get("sourceCell") or ""), "CreatedAt": grist_datetime(_now()), "IdentityStatus": str(provenance.get("identityStatus") or "resolved"), "EvidenceFingerprint": fingerprint}
        row = self._find(self._rows("SpareAlias", refresh=True), "AliasKey", alias_key)
        if row:
            if str(row.get("fields", {}).get("EvidenceFingerprint") or "") != fingerprint:
                raise SpareRegistryError("SPARE_REQUEST_CONFLICT", "The existing SpareAlias record has a different immutable fingerprint.", 409)
            return row
        if source_file:
            legacy_data = {key: value for key, value in key_data.items() if key != "sourceFile"}
            legacy_key = "alias:" + hashlib.sha256(json.dumps(legacy_data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            legacy = self._find(self._rows("SpareAlias", refresh=True), "AliasKey", legacy_key)
            if legacy:
                legacy_fields = legacy.get("fields", {})
                if (_ref_id(legacy_fields.get("SpareMaster")) == spare_record_id and str(legacy_fields.get("SourceFile") or "") == source_file
                        and str(legacy_fields.get("EvidenceFingerprint") or "") == fingerprint):
                    return legacy
        return self._ensure_keyed("SpareAlias", "AliasKey", alias_key, fields, "EvidenceFingerprint")

    def _ensure_history(self, *, row: dict[str, Any], event_type: str, actor: str, reason: str, request_key: str, fingerprint: str, snapshot: dict[str, Any]) -> None:
        fields = row.get("fields", {})
        spare_key = str(fields.get("SpareKey") or "")
        version = int(fields.get("Version") or 1)
        history_key = f"{spare_key}:v{version}"
        history_fp = request_fingerprint({"historyKey": history_key, "eventType": event_type, "requestKey": request_key, "requestFingerprint": fingerprint, "snapshot": snapshot})
        self._ensure_keyed("SpareHistory", "HistoryKey", history_key, {"HistoryKey": history_key, "SpareMaster": _row_id(row), "Version": version, "EventType": event_type, "Actor": actor, "Reason": reason, "OccurredAt": grist_datetime(_now()), "RequestKey": request_key, "RequestFingerprint": history_fp, "Snapshot": canonical_grist_json_cell(snapshot)}, "RequestFingerprint")

    def create_spare(self, *, description: str, category: str = "", unit: str = "", sourcing_type: str = "unclassified", actor: str, reason: str = "", request_key: str, aliases: Iterable[dict[str, Any]] = (), source_provenance: dict[str, Any] | None = None) -> dict[str, Any]:
        description = " ".join(str(description or "").split())
        if not description:
            raise SpareRegistryError("SPARE_DESCRIPTION_REQUIRED", "Description is required.", retry_disposition="safe_to_edit")
        if not str(request_key or "").strip():
            raise SpareRegistryError("SPARE_REQUEST_KEY_REQUIRED", "A unique idempotency key is required for spare creation.", retry_disposition="safe_to_edit")
        sourcing = normalize_sourcing_type(sourcing_type)
        alias_items = [dict(item) for item in aliases]
        normalized_aliases = [normalize_alias(item.get("value")) for item in alias_items if normalize_alias(item.get("value"))]
        if len(set(normalized_aliases)) != len(normalized_aliases):
            raise SpareRegistryError("SPARE_ALIAS_DUPLICATE_INPUT", "Enter each legacy code only once.", retry_disposition="safe_to_edit")
        fingerprint = request_fingerprint({"type": "create", "description": description, "category": category.strip(), "unit": unit.strip(), "sourcingType": sourcing, "aliases": [{"value": str(x.get("value") or "").strip(), "provenance": x.get("provenance") or {}, **({"evidenceFingerprint": str(x.get("evidenceFingerprint"))} if x.get("evidenceFingerprint") else {})} for x in alias_items], "sourceProvenance": source_provenance or {}})
        self._assert_schema(write=True)

        request_row = self._find(self._rows("SpareMasterRequest", refresh=True), "RequestKey", request_key)
        if request_row:
            request_fields = request_row.get("fields", {})
            if str(request_fields.get("RequestType") or "") != "create" or str(request_fields.get("RequestFingerprint") or "") != fingerprint:
                raise SpareRegistryError("SPARE_REQUEST_CONFLICT", "This idempotency key was already used for different spare details.", 409)
            if str(request_fields.get("Status") or "") == "complete":
                completed_key = str(request_fields.get("SpareKey") or "")
                completed = self._spare_master_by_key(completed_key)
                if not completed or completed.get("fields", {}).get("CreateRequestKey") != request_key or completed.get("fields", {}).get("CreateFingerprint") != fingerprint:
                    raise SpareRegistryError("SPARE_WRITE_UNCONFIRMED", "The completed request no longer matches its Grist spare identity.", 503)
                completed_status = str(completed.get("fields", {}).get("Status") or "").casefold()
                if completed_status in {"active", "retired"}:
                    record_id = _row_id(completed)
                    if not record_id:
                        raise SpareRegistryError("SPARE_WRITE_UNCONFIRMED", "The completed request has no confirmed Grist record ID.", 503)
                    for alias in alias_items:
                        self.ensure_alias(spare_key=completed_key, spare_record_id=record_id, value=str(alias.get("value") or ""), provenance=alias.get("provenance") or {}, evidence_fingerprint=str(alias.get("evidenceFingerprint") or "") or None)
                    snapshot_row = {**completed, "fields": {**completed.get("fields", {}), "Status": "active"}}
                    self._ensure_history(row=snapshot_row, event_type="created", actor=actor, reason=reason, request_key=request_key, fingerprint=fingerprint, snapshot=self._public_master(snapshot_row))
                    result = self.get_spare(completed_key)
                    result["idempotent"] = True
                    return result
            if str(request_fields.get("Status") or "") == "rejected":
                result = _object_cell(request_fields.get("Result"))
                raise SpareRegistryError(str(result.get("code") or "SPARE_ALIAS_CONFLICT"), str(result.get("message") or "This create request was already rejected; edit the legacy code and start a new request."), 409, "safe_to_edit")
        reservation = self._reservation_for_request(request_key)
        known_spare_key = str((request_row or {}).get("fields", {}).get("SpareKey") or (reservation or {}).get("spare_key") or "")

        def reject_alias_conflict(error: SpareRegistryError, *, spare_key: str, spare_code: str) -> None:
            nonlocal request_row
            existing_master = self._spare_master_by_key(spare_key) if spare_key else None
            if not request_row and reservation:
                request_row = self._ensure_request(request_key=request_key, request_type="create", fingerprint=fingerprint, spare_key=spare_key, spare_code=spare_code, actor=actor, reason=reason)
            if existing_master:
                actual = existing_master.get("fields", {})
                if actual.get("CreateRequestKey") != request_key or actual.get("CreateFingerprint") != fingerprint or str(actual.get("SpareCode") or "") != spare_code:
                    raise SpareRegistryError("SPARE_REQUEST_CONFLICT", "The reserved number is already attached to different Grist data.", 409)
                self._retire_rejected_create(row=existing_master, actor=actor, reason=f"Create rejected: {error}", request_key=request_key, fingerprint=fingerprint)
            if request_row or reservation:
                self._reject_request(request_key, fingerprint, {"code": error.code, "message": str(error), "spareKey": spare_key, "spareCode": spare_code})
                self._finish_reservation(request_key, "rejected", _row_id(existing_master))
                self._release_alias_reservations(request_key)
            raise SpareRegistryError(error.code, str(error), 409, "safe_to_edit")

        for alias in alias_items:
            try:
                self._check_alias_available(str(alias.get("value") or ""), known_spare_key)
            except SpareRegistryError as exc:
                if exc.code != "SPARE_ALIAS_CONFLICT":
                    raise
                reject_alias_conflict(exc, spare_key=known_spare_key, spare_code=str((request_row or {}).get("fields", {}).get("SpareCode") or (reservation or {}).get("spare_code") or ""))

        spare_key, spare_code = self._reserve_code(request_key, fingerprint, normalized_aliases)
        request_row = self._ensure_request(request_key=request_key, request_type="create", fingerprint=fingerprint, spare_key=spare_key, spare_code=spare_code, actor=actor, reason=reason)
        for alias in alias_items:
            try:
                self._check_alias_available(str(alias.get("value") or ""), spare_key)
            except SpareRegistryError as exc:
                if exc.code != "SPARE_ALIAS_CONFLICT":
                    raise
                reject_alias_conflict(exc, spare_key=spare_key, spare_code=spare_code)

        existing = self._spare_master_by_key(spare_key)
        now = grist_datetime(_now())
        fields = {"SpareKey": spare_key, "SpareCode": spare_code, "Description": description, "Category": str(category or "").strip(), "Unit": str(unit or "").strip(), "SourcingType": sourcing, "Status": "pending", "CreatedAt": now, "CreatedBy": actor, "UpdatedAt": now, "UpdatedBy": actor, "Version": 1, "SourceProvenance": canonical_grist_json_cell(_json_value(source_provenance or {})), "CreateRequestKey": request_key, "CreateFingerprint": fingerprint}
        if existing:
            actual = existing.get("fields", {})
            if actual.get("CreateRequestKey") != request_key or actual.get("CreateFingerprint") != fingerprint or actual.get("SpareCode") != spare_code:
                raise SpareRegistryError("SPARE_REQUEST_CONFLICT", "The reserved number is already attached to different Grist data.", 409)
            if str(actual.get("Status") or "active").casefold() == "retired" and str(request_row.get("fields", {}).get("Status") or "") != "complete":
                raise SpareRegistryError("SPARE_CREATE_RECOVERY_BLOCKED", "This incomplete create was already retired. Resume the same request with an administrator reviewing its history before changing it.", 409)
            if str(actual.get("Status") or "active").casefold() == "active" and str(request_row.get("fields", {}).get("Status") or "") != "complete":
                self.client.update_table_records("SpareMaster", [{"id": existing["id"], "fields": {"Status": "pending", "UpdatedAt": grist_datetime(_now()), "UpdatedBy": actor}}])
                existing = self._spare_master_by_key(spare_key)
                if not existing or str(existing.get("fields", {}).get("Status") or "").casefold() != "pending":
                    raise SpareRegistryError("SPARE_WRITE_UNCONFIRMED", "Grist did not hide the incomplete spare while recovery is running.", 503)
            row = existing
        else:
            row = self._ensure_keyed("SpareMaster", "SpareKey", spare_key, fields, "CreateFingerprint")
        record_id = _row_id(row)
        if not record_id:
            raise SpareRegistryError("SPARE_WRITE_UNCONFIRMED", "Grist did not return the created spare record ID.", 503)
        for alias in alias_items:
            self.ensure_alias(spare_key=spare_key, spare_record_id=record_id, value=str(alias.get("value") or ""), provenance=alias.get("provenance") or {}, evidence_fingerprint=str(alias.get("evidenceFingerprint") or "") or None)
        snapshot_row = {"id": row["id"], "fields": {**row.get("fields", {}), "Status": "active"}}
        snapshot = self._public_master(snapshot_row)
        self._ensure_history(row=row, event_type="created", actor=actor, reason=reason, request_key=request_key, fingerprint=fingerprint, snapshot=snapshot)
        self._complete_request(request_key, fingerprint, {"spareKey": spare_key, "spareCode": spare_code, "recordId": record_id})
        current = self._spare_master_by_key(spare_key)
        if not current:
            raise SpareRegistryError("SPARE_WRITE_UNCONFIRMED", "Grist did not confirm the created spare before activation.", 503)
        if str(current.get("fields", {}).get("Status") or "").casefold() != "active":
            self.client.update_table_records("SpareMaster", [{"id": current["id"], "fields": {"Status": "active"}}])
            current = self._spare_master_by_key(spare_key)
            if not current or str(current.get("fields", {}).get("Status") or "").casefold() != "active":
                raise SpareRegistryError("SPARE_WRITE_UNCONFIRMED", "Grist did not confirm the completed spare identity before activation.", 503)
        self._finish_reservation(request_key, "complete", record_id)
        result = self.get_spare(spare_key)
        result["idempotent"] = bool(existing)
        return result

    def update_spare(self, *, spare_key: str, expected_version: int, updates: dict[str, Any], actor: str, reason: str, request_key: str, retire: bool = False) -> dict[str, Any]:
        allowed = {"description", "category", "unit", "sourcingType"}
        if not retire and not set(updates).issubset(allowed):
            raise SpareRegistryError("SPARE_FIELDS_INVALID", "Only description, category, unit, and sourcing type can be maintained here.")
        if not str(reason or "").strip():
            raise SpareRegistryError("SPARE_REASON_REQUIRED", "Enter a short reason for the change.")
        request_type = "retire" if retire else "update"
        self._assert_schema(write=True)
        row = self._spare_master_by_key(spare_key)
        if not row:
            raise SpareRegistryError("SPARE_NOT_FOUND", "The spare identity was not found.", 404)
        fields = row.get("fields", {})
        current_updates = {
            "description": " ".join(str(updates.get("description", fields.get("Description")) or "").split()),
            "category": str(updates.get("category", fields.get("Category")) or "").strip(),
            "unit": str(updates.get("unit", fields.get("Unit")) or "").strip(),
            "sourcingType": normalize_sourcing_type(updates.get("sourcingType", fields.get("SourcingType") or "unclassified")),
        }
        if not retire and not current_updates["description"]:
            raise SpareRegistryError("SPARE_DESCRIPTION_REQUIRED", "Description is required.")
        if not str(request_key or "").strip():
            raise SpareRegistryError("SPARE_REQUEST_KEY_REQUIRED", "A unique idempotency key is required for this spare change.")
        if str(fields.get("Status") or "active").casefold() == "retired" and not retire:
            raise SpareRegistryError("SPARE_RETIRED", "Retired spares cannot be edited; create a new reviewed identity if needed.", 409)
        desired = {"Description": current_updates["description"], "Category": current_updates["category"], "Unit": current_updates["unit"], "SourcingType": current_updates["sourcingType"], "Status": "retired" if retire else str(fields.get("Status") or "active").casefold()}
        fingerprint = request_fingerprint({"type": request_type, "spareKey": spare_key, "expectedVersion": expected_version, "updates": desired, "reason": reason.strip()})
        prior_request = self._find(self._rows("SpareMasterRequest", refresh=True), "RequestKey", request_key)
        if prior_request:
            prior_fields = prior_request.get("fields", {})
            if prior_fields.get("RequestType") != request_type or prior_fields.get("RequestFingerprint") != fingerprint:
                raise SpareRegistryError("SPARE_REQUEST_CONFLICT", "This request key was already used for different spare details.", 409)
            if prior_fields.get("Status") == "complete":
                return self.get_spare(spare_key)
        else:
            if retire and str(fields.get("Status") or "active").casefold() == "retired":
                raise SpareRegistryError("SPARE_RETIRED", "This spare is already retired.", 409)
            self._ensure_request(request_key=request_key, request_type=request_type, fingerprint=fingerprint, spare_key=spare_key, actor=actor, reason=reason)
        next_version = expected_version + 1
        if retire:
            desired["Status"] = "retired"
        current_version = int(fields.get("Version") or 1)
        if current_version == expected_version:
            self.client.update_table_records("SpareMaster", [{"id": row["id"], "fields": {**desired, "Version": next_version, "UpdatedAt": grist_datetime(_now()), "UpdatedBy": actor}}])
            row = self._spare_master_by_key(spare_key)
            if not row:
                raise SpareRegistryError("SPARE_WRITE_UNCONFIRMED", "Grist did not confirm the spare edit.", 503)
            fields = row.get("fields", {})
        elif current_version != next_version or any(fields.get(key) != value for key, value in desired.items()):
            raise SpareRegistryError("SPARE_VERSION_CONFLICT", "The spare changed since it was loaded. Refresh before saving.", 409)
        if int(fields.get("Version") or 1) != next_version or any(fields.get(key) != value for key, value in desired.items()):
            raise SpareRegistryError("SPARE_WRITE_UNCONFIRMED", "Grist did not confirm the exact spare edit.", 503)
        self._ensure_history(row=row, event_type="retired" if retire else "updated", actor=actor, reason=reason.strip(), request_key=request_key, fingerprint=fingerprint, snapshot=self._public_master(row))
        self._complete_request(request_key, fingerprint, {"spareKey": spare_key, "recordId": _row_id(row), "version": next_version})
        return self.get_spare(spare_key)

    def ensure_rows(self, table: str, key_field: str, rows: list[dict[str, Any]], *, fingerprint_field: str = "EvidenceFingerprint", chunk_size: int = 100) -> dict[str, int]:
        """Idempotently create immutable evidence rows; a retry reconciles each key."""
        self._assert_schema(write=True)
        if not rows:
            return {"created": 0, "existing": 0}
        existing_rows = self._rows(table, refresh=True)
        keyed_rows: dict[str, list[dict[str, Any]]] = {}
        for row in existing_rows:
            key = str(row.get("fields", {}).get(key_field) or "")
            if key:
                keyed_rows.setdefault(key, []).append(row)
        duplicates = [key for key, matches in keyed_rows.items() if len(matches) > 1]
        if duplicates:
            raise SpareRegistryError("SPARE_DUPLICATE_KEY", f"Grist contains duplicate {key_field} rows; writes are paused until the duplicate is reviewed.", 409)
        existing = {key: matches[0] for key, matches in keyed_rows.items()}
        missing = []
        skipped = 0
        for fields in rows:
            key = str(fields.get(key_field) or "")
            if not key:
                raise SpareRegistryError("SPARE_EVIDENCE_KEY_REQUIRED", f"{table} evidence needs a stable key.")
            prior = existing.get(key)
            if not prior and table in {"SpareSourceEvidence", "SpareSourceCost", "SpareCompositionEvidence"}:
                matches = [row for row in existing_rows if self._same_source_occurrence(table, row.get("fields", {}), fields)]
                if len(matches) > 1:
                    raise SpareRegistryError("SPARE_DUPLICATE_KEY", f"Grist contains duplicate source occurrences for {key_field}; writes are paused until the duplicate is reviewed.", 409)
                prior = matches[0] if matches else None
            if prior:
                expected = str(fields.get(fingerprint_field) or "")
                actual = str(prior.get("fields", {}).get(fingerprint_field) or "")
                legacy = {str(fields.get("_legacyEvidenceFingerprint") or ""), str(fields.get("_legacyFingerprint") or "")}
                if expected and actual != expected and actual not in legacy:
                    raise SpareRegistryError("SPARE_EVIDENCE_CONFLICT", f"The existing {table} evidence key has different source facts.", 409)
                skipped += 1
            else:
                missing.append({key: value for key, value in fields.items() if not key.startswith("_")})
        for start in range(0, len(missing), chunk_size):
            self.client.create_table_records(table, [{"fields": fields} for fields in missing[start:start + chunk_size]])
        confirmed_rows = self._rows(table, refresh=True)
        confirmed = {str(r.get("fields", {}).get(key_field) or "") for r in confirmed_rows}
        absent = [str(row.get(key_field)) for row in rows if str(row.get(key_field)) not in confirmed and not any(self._same_source_occurrence(table, prior.get("fields", {}), row) for prior in confirmed_rows)]
        if absent:
            raise SpareRegistryError("SPARE_WRITE_UNCONFIRMED", f"Grist did not confirm {len(absent)} {table} evidence records after writing.", 503)
        return {"created": len(missing), "existing": skipped}

    @staticmethod
    def _same_source_occurrence(table: str, stored: dict[str, Any], incoming: dict[str, Any]) -> bool:
        fields = {
            "SpareSourceEvidence": ("SourceFile", "SourceHash", "SourceKind", "SheetName", "SourceRow", "SourceCell"),
            "SpareSourceCost": ("SourceReference", "SourceHash", "SheetName", "SourceRow", "SourceCell", "CostKind"),
            "SpareCompositionEvidence": ("SourceFile", "SourceHash", "SourceKind", "SheetName", "SourceRow"),
        }.get(table)
        if not fields:
            return False
        return all(str(stored.get(key) or "") == str(incoming.get(key) or "") for key in fields)

    def start_import(self, *, request_key: str, fingerprint: str, preview_fingerprint: str, source_hashes: dict[str, str], actor: str) -> dict[str, Any]:
        self._assert_schema(write=True)
        if not request_key.strip():
            raise SpareRegistryError("SPARE_REQUEST_KEY_REQUIRED", "A unique idempotency key is required for source import.")
        row = self._find(self._rows("SpareImportBatch", refresh=True), "RequestKey", request_key)
        if row:
            fields = row.get("fields", {})
            if fields.get("RequestFingerprint") != fingerprint or fields.get("PreviewFingerprint") != preview_fingerprint:
                raise SpareRegistryError("SPARE_IMPORT_REQUEST_CONFLICT", "This import retry key was already used for a different preview.", 409)
            if _object_cell(fields.get("SourceHashes")) != source_hashes:
                raise SpareRegistryError("SPARE_IMPORT_STALE", "The source revisions for this import retry key have changed.", 409)
            return row
        now = grist_datetime(_now())
        batch_key = "import:" + hashlib.sha256(request_key.encode()).hexdigest()
        fields = {"BatchKey": batch_key, "RequestKey": request_key, "RequestFingerprint": fingerprint, "PreviewFingerprint": preview_fingerprint, "SourceHashes": canonical_grist_json_cell(source_hashes), "Status": "applying", "Actor": actor, "StartedAt": now, "UpdatedAt": now, "CompletedAt": None, "Result": canonical_grist_json_cell({})}
        return self._ensure_keyed("SpareImportBatch", "RequestKey", request_key, fields, "RequestFingerprint")

    def update_import_progress(self, request_key: str, progress: dict[str, Any]) -> None:
        row = self._find(self._rows("SpareImportBatch", refresh=True), "RequestKey", request_key)
        if not row:
            raise SpareRegistryError("SPARE_IMPORT_UNCONFIRMED", "The import progress record is missing from Grist.", 503)
        fields = row.get("fields", {})
        if str(fields.get("Status") or "") == "complete":
            return
        previous = _object_cell(fields.get("Result"))
        recovery_history = previous.get("recoveryHistory")
        saved_progress = dict(progress)
        if isinstance(recovery_history, list) and recovery_history:
            saved_progress["recoveryHistory"] = recovery_history
        self.client.update_table_records("SpareImportBatch", [{"id": row["id"], "fields": {"Status": "applying", "UpdatedAt": grist_datetime(_now()), "CompletedAt": None, "Result": canonical_grist_json_cell(saved_progress)}}])

    def reopen_import_for_recovery(self, request_key: str, *, reason: str, details: dict[str, Any]) -> None:
        row = self._find(self._rows("SpareImportBatch", refresh=True), "RequestKey", request_key)
        if not row:
            raise SpareRegistryError("SPARE_IMPORT_UNCONFIRMED", "The import recovery record is missing from Grist.", 503)
        fields = row.get("fields", {})
        if str(fields.get("Status") or "") != "complete":
            return
        previous_result = _object_cell(fields.get("Result"))
        recovery_history = list(previous_result.get("recoveryHistory") or [])
        now = grist_datetime(_now())
        recovery_history.append({"event": "reopened_after_incomplete_identity", "at": now, "reason": reason, "details": _json_value(details), "previousStatus": "complete", "previousResult": previous_result})
        progress = {"stage": "identity-recovery", "details": _json_value(details), "recoveryHistory": recovery_history}
        self.client.update_table_records("SpareImportBatch", [{"id": row["id"], "fields": {"Status": "applying", "UpdatedAt": now, "CompletedAt": None, "Result": canonical_grist_json_cell(progress)}}])
        confirmed = self._find(self._rows("SpareImportBatch", refresh=True), "RequestKey", request_key)
        if not confirmed or str(confirmed.get("fields", {}).get("Status") or "") != "applying":
            raise SpareRegistryError("SPARE_IMPORT_UNCONFIRMED", "Grist did not confirm that the incomplete import batch was reopened for recovery.", 503)

    def require_import_recovery(self, request_key: str, *, details: dict[str, Any]) -> None:
        row = self._find(self._rows("SpareImportBatch", refresh=True), "RequestKey", request_key)
        if not row:
            raise SpareRegistryError("SPARE_IMPORT_UNCONFIRMED", "The import recovery record is missing from Grist.", 503)
        fields = row.get("fields", {})
        previous_result = _object_cell(fields.get("Result"))
        recovery_history = list(previous_result.get("recoveryHistory") or [])
        current_status = str(fields.get("Status") or "unknown")
        now = grist_datetime(_now())
        if current_status == "complete":
            recovery_history.append({"event": "invalid_completion_detected", "at": now, "previousStatus": "complete", "previousResult": previous_result})
        last_block = recovery_history[-1] if recovery_history else {}
        if last_block.get("event") != "recovery_required" or last_block.get("details") != _json_value(details):
            recovery_history.append({"event": "recovery_required", "at": now, "details": _json_value(details)})
        progress = {"stage": "recovery_required", "details": _json_value(details), "recoveryHistory": recovery_history}
        self.client.update_table_records("SpareImportBatch", [{"id": row["id"], "fields": {"Status": "recovery_required", "UpdatedAt": now, "CompletedAt": None, "Result": canonical_grist_json_cell(progress)}}])
        confirmed = self._find(self._rows("SpareImportBatch", refresh=True), "RequestKey", request_key)
        if not confirmed or str(confirmed.get("fields", {}).get("Status") or "") != "recovery_required":
            raise SpareRegistryError("SPARE_IMPORT_UNCONFIRMED", "Grist did not confirm the import recovery blocker.", 503)

    def import_status(self, request_key: str) -> dict[str, Any]:
        self._assert_schema()
        row = self._find(self._rows("SpareImportBatch"), "RequestKey", request_key)
        if not row:
            raise SpareRegistryError("SPARE_IMPORT_NOT_FOUND", "No Grist import attempt exists for that retry key.", 404)
        fields = row.get("fields", {})
        return {"requestKey": request_key, "status": str(fields.get("Status") or "unknown"), "progress": _object_cell(fields.get("Result")), "updatedAt": fields.get("UpdatedAt"), "previewFingerprint": str(fields.get("PreviewFingerprint") or ""), "sourceHashes": _object_cell(fields.get("SourceHashes"))}

    def finish_import(self, request_key: str, result: dict[str, Any]) -> None:
        row = self._find(self._rows("SpareImportBatch", refresh=True), "RequestKey", request_key)
        if not row:
            raise SpareRegistryError("SPARE_IMPORT_UNCONFIRMED", "Grist did not retain the import batch.", 503)
        previous_result = _object_cell(row.get("fields", {}).get("Result"))
        recovery_history = previous_result.get("recoveryHistory")
        final_result = dict(result)
        if isinstance(recovery_history, list) and recovery_history:
            final_result["recoveryHistory"] = recovery_history
        now = grist_datetime(_now())
        self.client.update_table_records("SpareImportBatch", [{"id": row["id"], "fields": {"Status": "complete", "UpdatedAt": now, "CompletedAt": now, "Result": canonical_grist_json_cell(final_result)}}])
        confirmed = self._find(self._rows("SpareImportBatch", refresh=True), "RequestKey", request_key)
        if not confirmed or str(confirmed.get("fields", {}).get("Status") or "") != "complete" or _object_cell(confirmed.get("fields", {}).get("Result")) != final_result:
            raise SpareRegistryError("SPARE_IMPORT_UNCONFIRMED", "Grist did not confirm import completion.", 503)

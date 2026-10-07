"""Grist-canonical Part registry and its local reservation/recovery journal.

The small local SQLite file coordinates one supported writer installation and
reserves numbers/request names. Business records are always read from and
published to Safari Manufacturing in Grist.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import socket
import sqlite3
from contextlib import contextmanager
from threading import RLock
from typing import Any
from uuid import uuid4
from urllib.parse import urlparse

from app.domain import utc_now
from app.exceptions import GristError, GristValidationError
from app.grist import GristClient
from app.grist_types import grist_datetime
from app.part_identity import PartIdentityError, generated_name, normalized_name, normalized_shortcode, request_fingerprint


def _serialized(method):
    """Serialize registry mutations across processes using the shared journal."""
    def wrapper(self, *args, **kwargs):
        registry = self.registry if hasattr(self, "registry") else self
        with registry.lock:
            return method(self, *args, **kwargs)
    return wrapper


COORDINATOR_TABLE = "PartRegistryCoordinator"
REQUEST_TABLE = "PartRegistryRequest"
MANAGED_TABLES = {
    "ProductPart", "PartMetadataVersion", "PartNameAlias", "PartScopeShortcode",
    "PartShortcodeHistory", "PartRevision", "PartMappingReview", "PartComponentRevision",
    "PartRevisionLine", "PartDrawing", "Vendor", "PartPurchaseSpecification",
    "VendorPartMapping", "PartPurchaseRecord", "PurchasedPartCostEvidence", "AuditEvent", "PurchaseItem",
}


def _ref_id(value: Any) -> int | None:
    if value in (None, "", 0):
        return None
    if isinstance(value, list) and len(value) > 2 and value[0] == "R":
        try:
            row_id = int(value[2])
            return row_id or None
        except (TypeError, ValueError):
            return None
    try:
        row_id = int(value)
        return row_id or None
    except (TypeError, ValueError):
        return None


def _ref(value: Any) -> int | None:
    return _ref_id(value)


def _public_scope(scope: str) -> str:
    return {"global": "Global", "product": "Product", "product_model": "Product Model", "model_code": "Model Code"}.get(scope, scope)


class GristPartRegistry:
    """Parts business API backed by Grist; SQLite is only an allocator journal."""

    def __init__(self, client: GristClient, path: str | Path | None = None):
        self.client = client
        configured = path if path is not None else os.getenv("SAFARI_PART_DATABASE_PATH")
        self.path = Path(configured).expanduser() if configured else Path(__file__).resolve().parents[1] / "state" / "safari_parts.sqlite3"
        if str(self.path).startswith("\\\\") or str(self.path) == ":memory:":
            raise PartIdentityError("PART_DATABASE_UNSUPPORTED", "Part coordination needs one durable local SQLite file shared by all application processes on the writer host.")
        self.path = self.path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.host_id = socket.gethostname().strip().casefold()
        self._thread_lock = RLock()
        self._initialize_journal()
        self.lock = _SQLiteJournalLock(self, self._thread_lock)

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=45, isolation_level=None)
        db.row_factory = sqlite3.Row
        return db

    @contextmanager
    def _database(self):
        db = self._connect()
        try:
            yield db
        finally:
            db.close()

    def _initialize_journal(self):
        with self._database() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS part_registry_settings (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1), coordinator_id TEXT NOT NULL,
                    next_part_number INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS part_registry_reservations (
                    request_key TEXT PRIMARY KEY, request_type TEXT NOT NULL, request_fingerprint TEXT NOT NULL,
                    stable_part_id TEXT NOT NULL, part_number TEXT NOT NULL UNIQUE, name_key TEXT NOT NULL UNIQUE,
                    payload_json TEXT NOT NULL, status TEXT NOT NULL, result_json TEXT, updated_at TEXT NOT NULL
                );
            """)
            db.execute("INSERT OR IGNORE INTO part_registry_settings(singleton,coordinator_id,next_part_number) VALUES(1,?,1)", (str(uuid4()),))

    @property
    def coordinator_id(self) -> str:
        with self._database() as db:
            return str(db.execute("SELECT coordinator_id FROM part_registry_settings WHERE singleton=1").fetchone()[0])

    def _ensure_schema(self):
        tables = {str(row.get("id")) for row in GristAdminClientShim(self.client).list_tables()}
        missing = sorted(MANAGED_TABLES - tables)
        if missing:
            raise PartIdentityError("PART_SCHEMA_UNAVAILABLE", "Safari Grist is missing required Parts tables: " + ", ".join(missing) + ". Apply the reviewed schema plan before enabling Part writes.")
        required = {"StablePartId", "PartNumber", "EngineeringRevision", "CurrentMetadataVersion", "CurrentPartRevision", "PublishStatus"}
        columns = {str(item.get("id")) for item in GristAdminClientShim(self.client).list_columns("ProductPart")}
        if required - columns:
            raise PartIdentityError("PART_SCHEMA_UNAVAILABLE", "Safari Grist ProductPart is missing required managed identity columns.")

    def sync_legacy_names(self, rows: list[dict[str, Any]]) -> None:
        """Compatibility hook; legacy names are checked directly in Grist."""
        return None

    def _remote_coordinator(self):
        rows = self.client.fetch_table_records_with_ids(COORDINATOR_TABLE)
        if len(rows) > 1:
            raise PartIdentityError("PART_COORDINATOR_CONFLICT", "More than one Part writer coordinator is registered in Grist.")
        return rows[0] if rows else None

    def bind_coordinator(self, *, actor: str, reason: str, request_key: str = "bind-part-writer") -> dict[str, Any]:
        """Explicit first-time binding; never silently rebind an installation."""
        self.client.validate_safari_write_target()
        self._ensure_schema()
        if not actor.strip() or not reason.strip():
            raise PartIdentityError("PART_INPUT_REQUIRED", "Writer binding requires an actor and reason.")
        current = self._remote_coordinator()
        if current:
            fields = current.get("fields", {})
            if fields.get("CoordinatorId") == self.coordinator_id and str(fields.get("WriterHostId", "")).casefold() == self.host_id:
                return {"bound": True, "idempotent": True, "writerHostId": self.host_id, "coordinatorId": self.coordinator_id}
            raise PartIdentityError("PART_COORDINATOR_CONFLICT", "Safari Grist is already bound to a different writer host or local coordinator database. No automatic takeover is allowed.")
        fingerprint = request_fingerprint([self.host_id, self.coordinator_id, actor.strip(), reason.strip()])
        self.client.create_table_records(COORDINATOR_TABLE, [{"fields": {
            "RegistryKey": "safari-parts-writer", "WriterHostId": self.host_id, "CoordinatorId": self.coordinator_id,
            "BoundAt": grist_datetime(utc_now()), "Actor": actor.strip(), "Reason": reason.strip(),
            "RequestKey": request_key, "Fingerprint": fingerprint,
        }}])
        confirmed = self._remote_coordinator()
        fields = confirmed.get("fields", {}) if confirmed else {}
        if fields.get("CoordinatorId") != self.coordinator_id or str(fields.get("WriterHostId", "")).casefold() != self.host_id:
            raise PartIdentityError("PART_COORDINATOR_UNCONFIRMED", "Grist did not confirm the writer binding; Part writes remain disabled.")
        return {"bound": True, "idempotent": False, "writerHostId": self.host_id, "coordinatorId": self.coordinator_id}

    def _verify_writer(self):
        self.client.validate_safari_write_target()
        self._ensure_schema()
        row = self._remote_coordinator()
        fields = row.get("fields", {}) if row else {}
        if fields.get("CoordinatorId") != self.coordinator_id or str(fields.get("WriterHostId", "")).casefold() != self.host_id:
            raise PartIdentityError("PART_COORDINATOR_UNBOUND", "Part writes are disabled until this shared local allocator is explicitly bound as Safari Grist's sole writer.")

    def _rows(self, table: str) -> list[dict[str, Any]]:
        return self.client.fetch_table_records_with_ids(table)

    @staticmethod
    def _find(rows: list[dict[str, Any]], field: str, value: str) -> dict[str, Any] | None:
        found = [row for row in rows if str(row.get("fields", {}).get(field) or "") == value]
        if len(found) > 1:
            raise PartIdentityError("PART_DUPLICATE_KEY", f"Grist contains duplicate {field}={value!r} rows; repair is required before continuing.")
        return found[0] if found else None

    def _ensure_keyed(self, table: str, key_field: str, key: str, fields: dict[str, Any]) -> dict[str, Any]:
        row = self._find(self._rows(table), key_field, key)
        if row:
            return row
        self.client.create_table_records(table, [{"fields": fields}])
        row = self._find(self._rows(table), key_field, key)
        if not row:
            raise PartIdentityError("PART_WRITE_UNCONFIRMED", f"Grist did not confirm {table} record {key!r} after Save.")
        return row

    def _begin_request(self, request_key: str, request_type: str, fingerprint: str, entity_uuid: str = ""):
        existing = self._find(self._rows(REQUEST_TABLE), "RequestKey", request_key)
        if existing:
            fields = existing.get("fields", {})
            if fields.get("RequestFingerprint") != fingerprint:
                raise PartIdentityError("PART_REQUEST_CONFLICT", "This request key was already used with different data.")
            return existing
        now = grist_datetime(utc_now())
        self.client.create_table_records(REQUEST_TABLE, [{"fields": {"RequestKey": request_key, "RequestType": request_type,
            "RequestFingerprint": fingerprint, "EntityUUID": entity_uuid, "Status": "publishing", "StartedAt": now,
            "UpdatedAt": now, "WriterHostId": self.host_id, "CoordinatorId": self.coordinator_id}}])
        existing = self._find(self._rows(REQUEST_TABLE), "RequestKey", request_key)
        if not existing or existing.get("fields", {}).get("RequestFingerprint") != fingerprint:
            raise PartIdentityError("PART_WRITE_UNCONFIRMED", "Grist did not confirm the operation request journal.")
        return existing

    def _complete_request(self, request: dict[str, Any], result: dict[str, Any]):
        self.client.update_table_records(REQUEST_TABLE, [{"id": int(request["id"]), "fields": {
            "Status": "published", "UpdatedAt": grist_datetime(utc_now()), "Result": json.dumps(result, sort_keys=True)}}])

    def _update(self, table: str, row: dict[str, Any], fields: dict[str, Any]):
        self.client.update_table_records(table, [{"id": int(row["id"]), "fields": fields}])
        refreshed = self._find(self._rows(table), "StablePartId", str(row.get("fields", {}).get("StablePartId") or "")) if table == "ProductPart" else None
        return refreshed or row

    def _check_name_available(self, key: str, exclude_part_id: str = "") -> list[dict[str, Any]]:
        collisions = []
        for row in self._rows("ProductPart"):
            fields = row.get("fields", {})
            stable_id = str(fields.get("StablePartId") or "")
            if stable_id == exclude_part_id:
                continue
            display = str(fields.get("DisplayName") or "")
            if normalized_name(str(fields.get("NameKey") or display)) == key:
                collisions.append({"source": "legacy" if not stable_id else "canonical", "id": stable_id or str(row.get("id")), "number": fields.get("PartNumber"), "status": fields.get("Status")})
        for row in self._rows("PartNameAlias"):
            fields = row.get("fields", {})
            if normalized_name(str(fields.get("NameKey") or fields.get("DisplayName") or "")) != key:
                continue
            pp_id = _ref(fields.get("ProductPart"))
            part = next((item for item in self._rows("ProductPart") if int(item.get("id", -1)) == pp_id), None)
            if part and str(part.get("fields", {}).get("StablePartId") or "") == exclude_part_id:
                continue
            collisions.append({"source": "canonical", "id": str((part or {}).get("fields", {}).get("StablePartId") or pp_id or ""), "number": (part or {}).get("fields", {}).get("PartNumber"), "status": (part or {}).get("fields", {}).get("Status")})
        return collisions

    def preview(self, *, scope_type: str, target_id: str, description: str, variant: str = "", exclude_part_id: str = "") -> dict[str, Any]:
        short = self._shortcode(scope_type, target_id)
        name = generated_name(short["Shortcode"], description, variant)
        key = normalized_name(name)
        collisions = self._check_name_available(key, exclude_part_id)
        return {"name": name, "available": not collisions, "collision": collisions, "validatedAt": utc_now()}

    def _shortcode(self, scope: str, target_id: str) -> dict[str, Any]:
        key = f"{scope}:{target_id}"
        row = self._find(self._rows("PartScopeShortcode"), "ScopeKey", key)
        if not row or not str(row.get("fields", {}).get("Shortcode") or "").strip():
            raise PartIdentityError("PART_SHORTCODE_REQUIRED", "Maintain a shortcode for this scope target before creating a Part.")
        return {"id": int(row["id"]), **row["fields"]}

    @_serialized
    def set_shortcode(self, *, scope_type: str, target_id: str, target_label: str, shortcode: str, actor: str, reason: str, request_key: str) -> dict[str, Any]:
        self._verify_writer()
        value = normalized_shortcode(shortcode)
        if not value or len(value) > 20 or not value.replace("-", "").isalnum() or not actor.strip() or not reason.strip() or not request_key.strip():
            raise PartIdentityError("PART_INPUT_INVALID", "Shortcode must contain up to 20 letters, numbers or hyphens; actor and reason are required.")
        scope_key = f"{scope_type}:{target_id}"
        fp = request_fingerprint([scope_type, target_id, value, actor.strip(), reason.strip()])
        request = self._begin_request(request_key, "set_scope_shortcode", fp, scope_key)
        existing = self._find(self._rows("PartScopeShortcode"), "ScopeKey", scope_key)
        old = str(existing.get("fields", {}).get("Shortcode") or "") if existing else ""
        history = self._find(self._rows("PartShortcodeHistory"), "HistoryKey", f"shortcode:{scope_key}:request:{request_key}")
        if request.get("fields", {}).get("Status") == "published" and history:
            return {"shortcode": value, "version": int(history.get("fields", {}).get("Version") or 1), "idempotent": True}
        already_applied = bool(existing and old == value and existing.get("fields", {}).get("RequestKey") == request_key)
        if old == value and not already_applied:
            result = {"shortcode": value, "version": int(existing["fields"].get("Version") or 1)}
            self._complete_request(request, result)
            return {**result, "idempotent": True}
        version = int(existing["fields"].get("Version") or 1) if already_applied else (int(existing["fields"].get("Version") or 0) + 1 if existing else 1)
        fields = {"ScopeKey": scope_key, "ScopeType": scope_type, "ScopeTargetId": target_id,
                  "ScopeTargetLabel": target_label.strip(), "Shortcode": value, "NormalizedShortcode": value,
                  "Version": version, "UpdatedAt": grist_datetime(utc_now()), "Actor": actor.strip(),
                  "Reason": reason.strip(), "RequestKey": request_key, "RequestFingerprint": fp}
        target_field = {"product": "ScopeProduct", "product_model": "ScopeProductModel", "model_code": "ScopeModelCode"}.get(scope_type)
        if target_field:
            fields[target_field] = int(target_id)
        if existing:
            self.client.update_table_records("PartScopeShortcode", [{"id": int(existing["id"]), "fields": fields}])
        else:
            self.client.create_table_records("PartScopeShortcode", [{"fields": fields}])
        history_key = f"shortcode:{scope_key}:request:{request_key}"
        self._ensure_keyed("PartShortcodeHistory", "HistoryKey", history_key, {
            "HistoryKey": history_key, "PartScopeShortcode": int(existing["id"]) if existing else self._find(self._rows("PartScopeShortcode"), "ScopeKey", scope_key)["id"],
            "Version": version, "OldShortcode": old, "NewShortcode": value, "Actor": actor.strip(), "Reason": reason.strip(),
            "OccurredAt": grist_datetime(utc_now()), "RequestKey": request_key, "RequestFingerprint": fp,
        })
        result = {"shortcode": value, "version": version}
        self._complete_request(request, result)
        return {**result, "idempotent": False}

    def _reserve_create(self, *, scope_type: str, target_id: str, target_label: str, description: str, variant: str,
                        expected_name: str, actor: str, reason: str, request_key: str) -> dict[str, Any]:
        now = utc_now()
        fingerprint = request_fingerprint([scope_type, target_id, target_label, description, variant, expected_name, actor.strip(), reason.strip()])
        payload = {"scopeType": scope_type, "targetId": target_id, "targetLabel": target_label, "description": description,
                   "variant": variant, "expectedName": expected_name, "actor": actor.strip(), "reason": reason.strip()}
        with self._database() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT * FROM part_registry_reservations WHERE request_key=?", (request_key,)).fetchone()
            if existing:
                if existing["request_fingerprint"] != fingerprint:
                    db.rollback()
                    raise PartIdentityError("PART_REQUEST_CONFLICT", "This request key was already used with different Part data.")
                db.commit()
                return dict(existing)
            collisions = self._check_name_available(normalized_name(expected_name))
            if collisions:
                db.rollback()
                raise PartIdentityError("PART_NAME_EXISTS", "This generated name is already reserved by a current or historical Part name.")
            setting = db.execute("SELECT * FROM part_registry_settings WHERE singleton=1").fetchone()
            remote_numbers = []
            for row in self._rows("ProductPart"):
                number = str(row.get("fields", {}).get("PartNumber") or "")
                if number.startswith("SM-P-") and number[5:].isdigit():
                    remote_numbers.append(int(number[5:]))
            number_value = max(int(setting["next_part_number"]), max(remote_numbers, default=0) + 1)
            number = f"SM-P-{number_value:06d}"
            stable_id = str(uuid4())
            try:
                db.execute("INSERT INTO part_registry_reservations(request_key,request_type,request_fingerprint,stable_part_id,part_number,name_key,payload_json,status,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                           (request_key, "create", fingerprint, stable_id, number, normalized_name(expected_name), json.dumps(payload, sort_keys=True), "reserved", now))
            except sqlite3.IntegrityError as exc:
                db.rollback()
                raise PartIdentityError("PART_NAME_EXISTS", "This Part name or number is already reserved by another request.") from exc
            db.execute("UPDATE part_registry_settings SET next_part_number=? WHERE singleton=1", (number_value + 1,))
            db.commit()
        return {"request_key": request_key, "request_type": "create", "request_fingerprint": fingerprint,
                "stable_part_id": stable_id, "part_number": number, "name_key": normalized_name(expected_name),
                "payload_json": json.dumps(payload, sort_keys=True), "status": "reserved", "result_json": None}

    def create_part(self, *, scope_type: str, target_id: str, target_label: str, description: str, variant: str = "",
                    expected_name: str, actor: str, reason: str, request_key: str, revision_assertion: Any = None) -> dict[str, Any]:
        self._verify_writer()
        if revision_assertion is not None and str(revision_assertion).casefold() != "a":
            raise PartIdentityError("PART_REVISION_LOCKED", "New Parts remain Rev A until approved CR control is available.")
        description = " ".join(description.split())
        variant = " ".join(variant.split())
        if scope_type not in {"global", "product", "product_model", "model_code"} or not target_id or not target_label.strip():
            raise PartIdentityError("PART_SCOPE_TARGET_INVALID", "Choose a valid scope and target.")
        if not description or len(description) > 120 or len(variant) > 120 or not reason.strip() or not actor.strip() or not request_key.strip():
            raise PartIdentityError("PART_INPUT_REQUIRED", "Description, actor, reason and idempotency key are required.")
        shortcode = self._shortcode(scope_type, target_id)
        actual_name = generated_name(str(shortcode["Shortcode"]), description, variant)
        if actual_name != expected_name:
            raise PartIdentityError("PART_NAME_PREVIEW_STALE", "The generated name changed after preview. Review it before saving.")
        reservation = self._reserve_create(scope_type=scope_type, target_id=target_id, target_label=target_label,
            description=description, variant=variant, expected_name=expected_name, actor=actor, reason=reason, request_key=request_key)
        if reservation.get("status") == "published" and reservation.get("result_json"):
            return {"part": json.loads(reservation["result_json"]), "idempotent": True}
        payload = json.loads(reservation["payload_json"])
        stable_id, number = reservation["stable_part_id"], reservation["part_number"]
        metadata_key, revision_key = f"{stable_id}:metadata:1", f"{stable_id}:revision:A"
        fingerprint = reservation["request_fingerprint"]
        now = grist_datetime(utc_now())
        name_key = normalized_name(expected_name)
        target_field = {"product": "ScopeProduct", "product_model": "ScopeProductModel", "model_code": "ScopeModelCode"}.get(payload["scopeType"])
        typed_target = {"ScopeProduct": None, "ScopeProductModel": None, "ScopeModelCode": None}
        if target_field:
            typed_target[target_field] = int(payload["targetId"])
        grist_request = self._find(self._rows(REQUEST_TABLE), "RequestKey", request_key)
        if grist_request:
            if grist_request.get("fields", {}).get("RequestFingerprint") != fingerprint:
                raise PartIdentityError("PART_REQUEST_CONFLICT", "Grist already contains this request key with different Part data.")
        else:
            self.client.create_table_records(REQUEST_TABLE, [{"fields": {"RequestKey": request_key, "RequestType": "create_part", "RequestFingerprint": fingerprint,
                "EntityUUID": stable_id, "ReservedPartNumber": number, "ReservedNameKey": name_key, "Status": "publishing",
                "StartedAt": now, "UpdatedAt": now, "WriterHostId": self.host_id, "CoordinatorId": self.coordinator_id}}])
        part = self._find(self._rows("ProductPart"), "StablePartId", stable_id)
        if not part:
            fields = {"PartKey": stable_id, "StablePartId": stable_id, "PartNumber": number, "EngineeringRevision": "A",
                "DisplayName": expected_name, "NameKey": name_key, "Status": "active", "PublishStatus": "publishing",
                "CreatedActor": payload["actor"], "CreatedReason": payload["reason"], "CreatedAt": now,
                "CreateRequestKey": request_key, "CreateFingerprint": fingerprint, "ScopeType": payload["scopeType"],
                "ScopeTargetId": payload["targetId"], "ScopeTargetLabel": payload["targetLabel"],
                "Description": payload["description"], "DesignVariant": payload["variant"], "MetadataVersion": 1, **typed_target}
            self.client.create_table_records("ProductPart", [{"fields": fields}])
            part = self._find(self._rows("ProductPart"), "StablePartId", stable_id)
        if not part:
            raise PartIdentityError("PART_WRITE_UNCONFIRMED", "The Grist ProductPart master was not readable after publication.")
        part_id = int(part["id"])
        scope_fields = {"ScopeType": payload["scopeType"], "ScopeTargetId": payload["targetId"], "ScopeTargetLabel": payload["targetLabel"]}
        if target_field:
            scope_fields[target_field] = int(payload["targetId"])
        metadata_fields = {"MetadataKey": metadata_key, "ProductPart": part_id, "Version": 1, **scope_fields,
            "Shortcode": shortcode["Shortcode"], "Description": payload["description"], "DesignVariant": payload["variant"],
            "DisplayName": expected_name, "NameKey": name_key, "Actor": payload["actor"], "Reason": payload["reason"],
            "OccurredAt": now, "RequestKey": request_key, "RequestFingerprint": fingerprint}
        metadata = self._ensure_keyed("PartMetadataVersion", "MetadataKey", metadata_key, metadata_fields)
        self._ensure_keyed("PartNameAlias", "AliasKey", f"{stable_id}:name:{name_key}", {
            "AliasKey": f"{stable_id}:name:{name_key}", "ProductPart": part_id, "MetadataVersion": int(metadata["id"]),
            "DisplayName": expected_name, "NameKey": name_key, "IsCurrent": True, "CreatedAt": now,
            "Actor": payload["actor"], "Reason": payload["reason"], "RequestKey": request_key})
        revision = self._ensure_keyed("PartRevision", "RevisionKey", revision_key, {
            "RevisionKey": revision_key, "ProductPart": part_id, "Revision": 1, "RevisionLabel": "A", "Status": "draft",
            "BaselineStatus": "draft", "RequestKey": request_key, "RequestFingerprint": fingerprint})
        self._ensure_keyed("AuditEvent", "RequestKey", request_key, {"EventType": "part_created", "Actor": payload["actor"],
            "OccurredAt": now, "EntityType": "ProductPart", "EntityId": stable_id, "Reason": payload["reason"],
            "Payload": json.dumps({"partNumber": number, "name": expected_name, "engineeringRevision": "A"}, sort_keys=True),
            "RequestKey": request_key, "RequestFingerprint": fingerprint})
        self.client.update_table_records("ProductPart", [{"id": part_id, "fields": {
            "CurrentMetadataVersion": int(metadata["id"]), "CurrentPartRevision": int(revision["id"]),
            "MetadataVersion": 1, "PublishStatus": "published"}}])
        result = self.get_part(stable_id)
        if not result or result.get("partNumber") != number or result.get("name") != expected_name or result.get("engineeringRevision") != "A" or result.get("publishStatus") != "published":
            raise PartIdentityError("PART_WRITE_UNCONFIRMED", "Grist did not confirm the complete Part, metadata, alias and Rev A publication.")
        req = self._find(self._rows(REQUEST_TABLE), "RequestKey", request_key)
        if req:
            self.client.update_table_records(REQUEST_TABLE, [{"id": int(req["id"]), "fields": {"Status": "published", "UpdatedAt": grist_datetime(utc_now()), "Result": json.dumps(result, sort_keys=True)}}])
        with self._database() as db:
            db.execute("UPDATE part_registry_reservations SET status='published',result_json=?,updated_at=? WHERE request_key=?", (json.dumps(result, sort_keys=True), utc_now(), request_key))
        return {"part": result, "idempotent": reservation.get("status") == "published"}

    def get_part(self, stable_id: str) -> dict[str, Any] | None:
        row = self._find(self._rows("ProductPart"), "StablePartId", stable_id)
        if not row:
            return None
        fields = row.get("fields", {})
        meta_id = _ref(fields.get("CurrentMetadataVersion"))
        rev_id = _ref(fields.get("CurrentPartRevision"))
        metadata = next((item for item in self._rows("PartMetadataVersion") if int(item.get("id", -1)) == meta_id), None)
        revision = next((item for item in self._rows("PartRevision") if int(item.get("id", -1)) == rev_id), None)
        meta = metadata.get("fields", {}) if metadata else {}
        rev = revision.get("fields", {}) if revision else {}
        aliases = [item.get("fields", {}).get("DisplayName", "") for item in self._rows("PartNameAlias") if _ref(item.get("fields", {}).get("ProductPart")) == int(row["id"]) and not item.get("fields", {}).get("IsCurrent")]
        scope_type = str(meta.get("ScopeType") or fields.get("ScopeType") or "")
        target_id = str(meta.get("ScopeTargetId") or fields.get("ScopeTargetId") or "")
        target_label = str(meta.get("ScopeTargetLabel") or fields.get("ScopeTargetLabel") or "")
        return {"id": stable_id, "gristRecordId": int(row["id"]), "partNumber": fields.get("PartNumber"),
            "name": meta.get("DisplayName") or fields.get("DisplayName") or "", "description": meta.get("Description") or fields.get("Description") or "",
            "variant": meta.get("DesignVariant") or fields.get("DesignVariant") or "", "scope": scope_type, "scopeTargetId": target_id,
            "scopeTarget": target_label, "shortcode": meta.get("Shortcode", ""), "engineeringRevision": fields.get("EngineeringRevision") or "A",
            "revisionRecordId": rev_id, "metadataRecordId": meta_id, "revisionStatus": rev.get("BaselineStatus") or rev.get("Status") or "draft",
            "status": fields.get("Status") or "active", "publishStatus": fields.get("PublishStatus") or "", "metadataVersion": meta.get("Version") or fields.get("MetadataVersion") or 1,
            "createdAt": fields.get("CreatedAt"), "actor": fields.get("CreatedActor"), "reason": fields.get("CreatedReason"),
            "aliases": aliases, "legacy": False}

    def list_parts(self, *, search: str = "", scope_type: str = "", target_id: str = "", include_retired: bool = True) -> list[dict[str, Any]]:
        query = normalized_name(search)
        result = []
        for row in self._rows("ProductPart"):
            stable_id = str(row.get("fields", {}).get("StablePartId") or "")
            if not stable_id:
                continue
            part = self.get_part(stable_id)
            if not part or (not include_retired and part["status"] == "retired"):
                continue
            if scope_type and part["scope"] != scope_type:
                continue
            if target_id and part["scopeTargetId"] != target_id:
                continue
            haystack = normalized_name(" ".join([str(part["partNumber"] or ""), part["name"], part["description"], part["variant"], *part["aliases"]]))
            if query and query not in haystack:
                continue
            result.append(part)
        return sorted(result, key=lambda item: (item["scope"], item["scopeTarget"].casefold(), item["name"].casefold()))

    @_serialized
    def update_metadata(self, *, part_id: str, scope_type: str, target_id: str, target_label: str, description: str, variant: str,
                        expected_version: int, expected_name: str, expected_usage_fingerprint: str, actor: str, reason: str, request_key: str):
        self._verify_writer()
        part = self.get_part(part_id)
        if not part:
            raise PartIdentityError("PART_NOT_FOUND", "The selected Part no longer exists.")
        if part["status"] == "retired":
            raise PartIdentityError("PART_RETIRED", "Retired Parts cannot be changed.")
        if variant.strip() != part["variant"].strip():
            raise PartIdentityError("PART_NEW_IDENTITY_REQUIRED", "A physical design variant needs a new Part number.")
        if not reason.strip() or not expected_usage_fingerprint:
            raise PartIdentityError("PART_USAGE_EVIDENCE_REQUIRED", "A reviewed usage preview and change reason are required.")
        fingerprint = request_fingerprint([part_id, scope_type, target_id, description, variant, expected_version, expected_name, expected_usage_fingerprint, actor, reason.strip()])
        request = self._begin_request(request_key, "update_part_metadata", fingerprint, part_id)
        if request.get("fields", {}).get("Status") == "published":
            return {"part": self.get_part(part_id), "idempotent": True}
        next_version = expected_version + 1
        pending_metadata = self._find(self._rows("PartMetadataVersion"), "MetadataKey", f"{part_id}:metadata:{next_version}")
        if int(part["metadataVersion"]) != expected_version and not pending_metadata:
            raise PartIdentityError("PART_METADATA_STALE", "Part metadata changed; reload before saving.")
        current_usage = self.usage_evidence(part_id)
        if current_usage["fingerprint"] != expected_usage_fingerprint:
            raise PartIdentityError("PART_USAGE_STALE", "Part source mappings changed after preview; review the current assignments again.")
        preview = self.preview(scope_type=scope_type, target_id=target_id, description=description, variant=variant, exclude_part_id=part_id)
        if preview["name"] != expected_name:
            raise PartIdentityError("PART_NAME_PREVIEW_STALE", "Generated name changed; preview again.")
        if not preview["available"]:
            raise PartIdentityError("PART_NAME_EXISTS", "This name has already been used.")
        shortcode = self._shortcode(scope_type, target_id)
        row = self._find(self._rows("ProductPart"), "StablePartId", part_id)
        now = grist_datetime(utc_now())
        version = expected_version + 1
        target_fields = {"ScopeType": scope_type, "ScopeTargetId": target_id, "ScopeTargetLabel": target_label.strip()}
        target_fields.update({"ScopeProduct": None, "ScopeProductModel": None, "ScopeModelCode": None})
        field = {"product": "ScopeProduct", "product_model": "ScopeProductModel", "model_code": "ScopeModelCode"}.get(scope_type)
        if field:
            target_fields[field] = int(target_id)
        meta = self._ensure_keyed("PartMetadataVersion", "MetadataKey", f"{part_id}:metadata:{version}", {
            "MetadataKey": f"{part_id}:metadata:{version}", "ProductPart": int(row["id"]), "Version": version, **target_fields,
            "Shortcode": shortcode["Shortcode"], "Description": description.strip(), "DesignVariant": variant.strip(), "DisplayName": expected_name,
            "NameKey": normalized_name(expected_name), "Actor": actor, "Reason": reason.strip(), "OccurredAt": now,
            "RequestKey": request_key, "RequestFingerprint": fingerprint})
        alias_key = f"{part_id}:name:{normalized_name(expected_name)}"
        previous_alias = self._find(self._rows("PartNameAlias"), "AliasKey", f"{part_id}:name:{normalized_name(part['name'])}")
        if previous_alias and previous_alias.get("fields", {}).get("AliasKey") != alias_key:
            self.client.update_table_records("PartNameAlias", [{"id": int(previous_alias["id"]), "fields": {"IsCurrent": False}}])
        current_alias = self._ensure_keyed("PartNameAlias", "AliasKey", alias_key, {
            "AliasKey": alias_key, "ProductPart": int(row["id"]), "MetadataVersion": int(meta["id"]),
            "DisplayName": expected_name, "NameKey": normalized_name(expected_name), "IsCurrent": True, "CreatedAt": now,
            "Actor": actor, "Reason": reason.strip(), "RequestKey": request_key})
        if current_alias.get("fields", {}).get("MetadataVersion") != int(meta["id"]) or not current_alias.get("fields", {}).get("IsCurrent"):
            self.client.update_table_records("PartNameAlias", [{"id": int(current_alias["id"]), "fields": {"MetadataVersion": int(meta["id"]), "IsCurrent": True}}])
        self.client.update_table_records("ProductPart", [{"id": int(row["id"]), "fields": {"DisplayName": expected_name,
            "NameKey": normalized_name(expected_name), "ScopeType": scope_type, "ScopeTargetId": target_id,
            "ScopeTargetLabel": target_label.strip(), "ScopeProduct": target_fields["ScopeProduct"], "ScopeProductModel": target_fields["ScopeProductModel"],
            "ScopeModelCode": target_fields["ScopeModelCode"], "Description": description.strip(), "CurrentMetadataVersion": int(meta["id"]), "MetadataVersion": version}}])
        self._ensure_keyed("AuditEvent", "RequestKey", request_key, {"EventType": "part_metadata_changed", "Actor": actor, "OccurredAt": now,
            "EntityType": "ProductPart", "EntityId": part_id, "Reason": reason.strip(), "Payload": json.dumps({"metadataVersion": version}, sort_keys=True),
            "RequestKey": request_key, "RequestFingerprint": fingerprint})
        updated = self.get_part(part_id)
        self._complete_request(request, {"part": updated})
        return {"part": updated, "idempotent": False}

    def usage_evidence(self, part_id: str):
        rows = [row.get("fields", {}) for row in self._rows("PartMappingReview") if str(row.get("fields", {}).get("StablePartId") or "") == part_id]
        rows.sort(key=lambda item: (str(item.get("FileKey")), str(item.get("SheetName")), int(item.get("SourceRow") or 0)))
        return {"items": rows, "fingerprint": request_fingerprint(rows)}

    def append_mapping_records(self, fields: list[dict[str, Any]]) -> list[dict[str, Any]]:
        self._verify_writer()
        if not fields:
            return []
        existing = self._rows("PartMappingReview")
        by_key = {str(row.get("fields", {}).get("ReviewKey") or ""): row for row in existing}
        missing = []
        for item in fields:
            key = str(item.get("ReviewKey") or "")
            if not key:
                raise PartIdentityError("PART_REVIEW_KEY_REQUIRED", "Every Grist mapping row requires a stable review key.")
            row = by_key.get(key)
            if row:
                if row.get("fields", {}) != item:
                    raise PartIdentityError("PART_REQUEST_CONFLICT", "A mapping review key already exists with different evidence.")
            else:
                missing.append({"fields": item})
        if missing:
            self.client.create_table_records("PartMappingReview", missing)
        confirmed = self._rows("PartMappingReview")
        by_key = {str(row.get("fields", {}).get("ReviewKey") or ""): row for row in confirmed}
        result = []
        for item in fields:
            row = by_key.get(str(item["ReviewKey"]))
            if not row or row.get("fields", {}) != item:
                raise PartIdentityError("PART_WRITE_UNCONFIRMED", "Grist did not confirm every Part mapping row after Save.")
            result.append(row)
        return result

    @_serialized
    def retire_part(self, *, part_id: str, expected_version: int, actor: str, reason: str, request_key: str):
        self._verify_writer()
        part = self.get_part(part_id)
        if not part:
            raise PartIdentityError("PART_NOT_FOUND", "The selected Part no longer exists.")
        if not actor.strip() or not reason.strip() or not request_key.strip():
            raise PartIdentityError("PART_INPUT_REQUIRED", "Retirement requires actor, reason and request key.")
        fingerprint = request_fingerprint([part_id, expected_version, actor.strip(), reason.strip()])
        request = self._begin_request(request_key, "retire_part", fingerprint, part_id)
        if request.get("fields", {}).get("Status") == "published":
            return {"part": self.get_part(part_id), "idempotent": True}
        if part["metadataVersion"] != expected_version:
            raise PartIdentityError("PART_METADATA_STALE", "Part metadata changed; reload before retiring.")
        row = self._find(self._rows("ProductPart"), "StablePartId", part_id)
        self.client.update_table_records("ProductPart", [{"id": int(row["id"]), "fields": {"Status": "retired"}}])
        now = grist_datetime(utc_now())
        self._ensure_keyed("AuditEvent", "RequestKey", request_key, {"EventType": "part_retired", "Actor": actor, "OccurredAt": now,
            "EntityType": "ProductPart", "EntityId": part_id, "Reason": reason, "Payload": "{}", "RequestKey": request_key,
            "RequestFingerprint": fingerprint})
        updated = self.get_part(part_id)
        self._complete_request(request, {"part": updated})
        return {"part": updated, "idempotent": False}

    def history(self, part_id: str):
        part = self.get_part(part_id)
        if not part:
            raise PartIdentityError("PART_NOT_FOUND", "The selected Part no longer exists.")
        pp_id = part["gristRecordId"]
        return {
            "metadata": [row.get("fields", {}) for row in self._rows("PartMetadataVersion") if _ref(row.get("fields", {}).get("ProductPart")) == pp_id],
            "aliases": [row.get("fields", {}) for row in self._rows("PartNameAlias") if _ref(row.get("fields", {}).get("ProductPart")) == pp_id],
            "revisions": [row.get("fields", {}) for row in self._rows("PartRevision") if _ref(row.get("fields", {}).get("ProductPart")) == pp_id],
            "lifecycle": [row.get("fields", {}) for row in self._rows("AuditEvent") if row.get("fields", {}).get("EntityId") == part_id],
            "mappings": [row.get("fields", {}) for row in self._rows("PartMappingReview") if str(row.get("fields", {}).get("StablePartId") or "") == part_id],
        }

    def current_revision(self, part_id: str) -> dict[str, Any]:
        part = self.get_part(part_id)
        if not part:
            raise PartIdentityError("PART_NOT_FOUND", "The selected Part no longer exists.")
        row = next((item for item in self._rows("PartRevision") if int(item.get("id", -1)) == part["revisionRecordId"]), None)
        if not row:
            raise PartIdentityError("PART_REVISION_UNAVAILABLE", "The Part has no readable engineering baseline.")
        return row


class GristPartFeatures:
    """Composition, drawing, vendor and actual-purchase operations in Grist."""
    def __init__(self, registry: GristPartRegistry):
        self.registry = registry
        self.client = registry.client

    def _part(self, part_id: str):
        part = self.registry.get_part(part_id)
        if not part:
            raise PartIdentityError("PART_NOT_FOUND", "The selected Part no longer exists.")
        return part

    def _revision_is_draft(self, revision_row: dict[str, Any]):
        fields = revision_row.get("fields", {})
        if str(fields.get("RevisionLabel") or "A") != "A":
            raise PartIdentityError("PART_REVISION_LOCKED", "Engineering changes beyond Rev A require the approved CR workflow.")
        if str(fields.get("BaselineStatus") or fields.get("Status") or "draft").casefold() != "draft":
            raise PartIdentityError("PART_REVISION_LOCKED", "The Part definition is finalized and physically locked until approved CR control exists.")

    def components(self, part_id: str) -> list[dict[str, Any]]:
        part = self._part(part_id)
        parent_revision = part["revisionRecordId"]
        revisions = {int(row["id"]): row.get("fields", {}) for row in self.registry._rows("PartRevision")}
        parts_by_record = {int(row["id"]): row.get("fields", {}) for row in self.registry._rows("ProductPart")}
        result = []
        for row in self.registry._rows("PartComponentRevision"):
            fields = row.get("fields", {})
            if _ref(fields.get("ParentRevision")) != parent_revision or str(fields.get("ComponentStatus") or fields.get("Status") or "active").casefold() in {"void", "removed", "inactive"}:
                continue
            child_revision_id = _ref(fields.get("ChildRevision"))
            child_revision = revisions.get(child_revision_id, {})
            child_part_id = _ref(child_revision.get("ProductPart"))
            child_fields = parts_by_record.get(child_part_id, {})
            child_stable_id = str(child_fields.get("StablePartId") or "")
            result.append({"id": row.get("id"), "componentKey": fields.get("ComponentKey"), "childPartId": child_stable_id,
                "childPartNumber": child_fields.get("PartNumber"), "childName": child_fields.get("DisplayName"),
                "childRevisionId": child_revision_id, "childRevisionLabel": child_revision.get("RevisionLabel") or child_revision.get("Revision"),
                "quantity": fields.get("Quantity"), "uom": fields.get("QuantityUOM"), "status": fields.get("ComponentStatus") or fields.get("Status"),
                "reason": fields.get("Reason"), "actor": fields.get("Actor")})
        return result

    @_serialized
    def add_component(self, *, parent_part_id: str, child_part_id: str, quantity: float, uom: str, actor: str, reason: str, request_key: str):
        self.registry._verify_writer()
        parent, child = self._part(parent_part_id), self._part(child_part_id)
        if parent_part_id == child_part_id:
            raise PartIdentityError("PART_COMPONENT_CYCLE", "A Part cannot contain itself.")
        try:
            value = float(quantity)
        except (TypeError, ValueError):
            value = 0
        if not math.isfinite(value) or value <= 0 or not uom.strip() or not actor.strip() or not reason.strip() or not request_key.strip():
            raise PartIdentityError("PART_COMPONENT_INPUT_INVALID", "Component quantity must be positive, with a unit, actor and reason.")
        parent_revision = self.registry.current_revision(parent_part_id)
        self._revision_is_draft(parent_revision)
        parent_revision_id, child_revision_id = int(parent_revision["id"]), int(child["revisionRecordId"])
        adjacency: dict[int, set[int]] = {}
        for row in self.registry._rows("PartComponentRevision"):
            fields = row.get("fields", {})
            if str(fields.get("ComponentStatus") or fields.get("Status") or "active").casefold() in {"void", "removed", "inactive"}:
                continue
            p, c = _ref(fields.get("ParentRevision")), _ref(fields.get("ChildRevision"))
            if p and c:
                adjacency.setdefault(p, set()).add(c)
        pending = [child_revision_id]
        visited: set[int] = set()
        while pending:
            current = pending.pop()
            if current == parent_revision_id:
                raise PartIdentityError("PART_COMPONENT_CYCLE", "This child would create a recursive Part composition cycle.")
            if current in visited:
                continue
            visited.add(current)
            pending.extend(adjacency.get(current, ()))
        key = f"{parent_revision_id}:{child_revision_id}:{request_key}"
        fingerprint = request_fingerprint([parent_part_id, child_part_id, value, uom.strip(), actor.strip(), reason.strip()])
        saved = self.registry._ensure_keyed("PartComponentRevision", "ComponentKey", key, {
            "ComponentKey": key, "ParentRevision": parent_revision_id, "ChildRevision": child_revision_id,
            "Quantity": value, "QuantityUOM": uom.strip(), "Status": "active", "ComponentStatus": "active",
            "Actor": actor.strip(), "Reason": reason.strip(), "OccurredAt": grist_datetime(utc_now()),
            "RequestKey": request_key, "RequestFingerprint": fingerprint})
        if saved.get("fields", {}).get("RequestFingerprint") != fingerprint:
            raise PartIdentityError("PART_REQUEST_CONFLICT", "This component request key was already used with different data.")
        return {"component": saved.get("fields", {}), "id": saved.get("id"), "components": self.components(parent_part_id)}

    @_serialized
    def finalize_revision(self, *, part_id: str, actor: str, reason: str, request_key: str):
        self.registry._verify_writer()
        part = self._part(part_id)
        revision = self.registry.current_revision(part_id)
        fields = revision.get("fields", {})
        if not actor.strip() or not reason.strip() or not request_key.strip():
            raise PartIdentityError("PART_INPUT_REQUIRED", "Finalization requires actor, reason and request key.")
        if fields.get("BaselineStatus") == "finalized":
            if fields.get("RequestKey") != request_key:
                raise PartIdentityError("PART_REVISION_FINALIZED", "Rev A is already finalized; this finalization request key does not match the recorded operation.")
            definition_hash = str(fields.get("DefinitionHash") or "")
            occurred = fields.get("FinalizedAt") or grist_datetime(utc_now())
            self.registry._ensure_keyed("AuditEvent", "RequestKey", request_key, {"EventType": "part_revision_finalized", "Actor": fields.get("FinalizedBy") or actor.strip(),
                "OccurredAt": occurred, "EntityType": "PartRevision", "EntityId": str(revision["id"]), "Reason": fields.get("FinalizationReason") or reason.strip(),
                "Payload": json.dumps({"revisionLabel": "A", "definitionHash": definition_hash}, sort_keys=True),
                "RequestKey": request_key, "RequestFingerprint": definition_hash})
            return {"part": part, "revision": fields, "idempotent": True}
        self._revision_is_draft(revision)
        physical = {"components": self.components(part_id), "lines": [row.get("fields", {}) for row in self.registry._rows("PartRevisionLine") if _ref(row.get("fields", {}).get("PartRevision")) == int(revision["id"])],
                    "drawings": [row.get("fields", {}) for row in self.registry._rows("PartDrawing") if _ref(row.get("fields", {}).get("PartRevision")) == int(revision["id"])]}
        definition_hash = request_fingerprint(physical)
        occurred = grist_datetime(utc_now())
        self.client.update_table_records("PartRevision", [{"id": int(revision["id"]), "fields": {"Status": "finalized", "BaselineStatus": "finalized",
            "DefinitionHash": definition_hash, "FinalizedAt": occurred, "FinalizedBy": actor.strip(), "FinalizationReason": reason.strip(),
            "RequestKey": request_key, "RequestFingerprint": definition_hash}}])
        self.registry._ensure_keyed("AuditEvent", "RequestKey", request_key, {"EventType": "part_revision_finalized", "Actor": actor.strip(),
            "OccurredAt": occurred, "EntityType": "PartRevision", "EntityId": str(revision["id"]), "Reason": reason.strip(),
            "Payload": json.dumps({"revisionLabel": "A", "definitionHash": definition_hash}, sort_keys=True),
            "RequestKey": request_key, "RequestFingerprint": definition_hash})
        check = self.registry.current_revision(part_id)
        if check.get("fields", {}).get("DefinitionHash") != definition_hash or check.get("fields", {}).get("BaselineStatus") != "finalized":
            raise PartIdentityError("PART_WRITE_UNCONFIRMED", "Grist did not confirm the finalized Rev A baseline.")
        return {"part": self._part(part_id), "revision": check.get("fields", {}), "idempotent": False}

    @_serialized
    def link_process_line(self, *, part_id: str, line_master_id: int, line_revision_id: int, quantity: float = 1,
                          uom: str = "each", actor: str, reason: str, request_key: str,
                          reassign_owner: bool = False, expected_owner_id: int | None = None):
        self.registry._verify_writer()
        part = self._part(part_id)
        revision = self.registry.current_revision(part_id)
        self._revision_is_draft(revision)
        master = next((row for row in self.registry._rows("LineMaster") if int(row.get("id", -1)) == int(line_master_id)), None)
        line_revision = next((row for row in self.registry._rows("LineRevision") if int(row.get("id", -1)) == int(line_revision_id)), None)
        if not master or not line_revision or _ref(line_revision.get("fields", {}).get("LineMaster")) != int(line_master_id):
            raise PartIdentityError("PART_LINE_UNAVAILABLE", "Choose an existing LineMaster and its exact LineRevision.")
        try:
            amount = float(quantity)
        except (TypeError, ValueError):
            amount = 0
        if not math.isfinite(amount) or amount <= 0 or not uom.strip():
            raise PartIdentityError("PART_LINE_INPUT_INVALID", "Line quantity must be positive and have a unit.")
        current_owner = _ref(master.get("fields", {}).get("ProductPart"))
        if current_owner != part["gristRecordId"]:
            if current_owner is not None and (not reassign_owner or expected_owner_id != current_owner or not reason.strip()):
                raise PartIdentityError("PART_LINE_OWNER_REVIEW_REQUIRED", "This source line still has a different legacy Part owner. Review its current owner and explicitly authorize reassignment.")
            self.client.update_table_records("LineMaster", [{"id": int(line_master_id), "fields": {"ProductPart": part["gristRecordId"]}}])
        line_fields = {"RevisionLineKey": request_key, "PartRevision": int(revision["id"]), "LineMaster": int(line_master_id),
            "LineRevision": int(line_revision_id), "ProcessType": str(line_revision.get("fields", {}).get("ProcessType") or master.get("fields", {}).get("ProcessType") or ""),
            "QuantityPerPart": amount, "QuantityUOM": uom.strip(), "Status": "active", "Actor": actor.strip(), "Reason": reason.strip(),
            "OccurredAt": grist_datetime(utc_now()), "RequestKey": request_key,
            "RequestFingerprint": request_fingerprint([part_id, line_master_id, line_revision_id, amount, uom, actor, reason])}
        saved = self.registry._ensure_keyed("PartRevisionLine", "RevisionLineKey", request_key, line_fields)
        if saved.get("fields", {}).get("RequestFingerprint") != line_fields["RequestFingerprint"]:
            raise PartIdentityError("PART_REQUEST_CONFLICT", "This line-link request key was already used with different data.")
        return {"line": saved.get("fields", {}), "id": saved.get("id")}

    @_serialized
    def add_drawing(self, *, part_id: str, identity: str, link_type: str, file_path: str = "", external_url: str = "",
                    file_version: str = "", actor: str, reason: str, request_key: str):
        self.registry._verify_writer()
        part = self._part(part_id)
        revision = self.registry.current_revision(part_id)
        self._revision_is_draft(revision)
        if link_type not in {"local_file", "external_url"} or not identity.strip() or not actor.strip() or not reason.strip() or not request_key.strip():
            raise PartIdentityError("PART_DRAWING_INPUT_INVALID", "Drawing identity, link type, actor and reason are required.")
        parsed_url = urlparse(external_url)
        if link_type == "local_file" and not file_path.strip():
            raise PartIdentityError("PART_DRAWING_PATH_REQUIRED", "Choose a local drawing file path.")
        if link_type == "external_url" and (parsed_url.scheme not in {"https", "http"} or not parsed_url.netloc):
            raise PartIdentityError("PART_DRAWING_URL_INVALID", "Drawing links must use a complete HTTP or HTTPS URL.")
        key = f"{part_id}:drawing:{request_key}"
        saved = self.registry._ensure_keyed("PartDrawing", "DrawingKey", key, {"DrawingKey": key, "PartRevision": int(revision["id"]),
            "DrawingIdentity": identity.strip(), "LinkType": link_type, "FilePath": file_path, "ExternalURL": external_url,
            "FileVersion": file_version.strip(), "Status": "active", "Actor": actor.strip(), "Reason": reason.strip(),
            "OccurredAt": grist_datetime(utc_now()), "RequestKey": request_key,
            "RequestFingerprint": request_fingerprint([part_id, identity, link_type, file_path, external_url, file_version, actor, reason])})
        return {"drawing": saved.get("fields", {}), "id": saved.get("id")}

    def drawings(self, part_id: str):
        part = self._part(part_id)
        revision_ids = {int(row["id"]) for row in self.registry._rows("PartRevision") if _ref(row.get("fields", {}).get("ProductPart")) == part["gristRecordId"]}
        return [row.get("fields", {}) for row in self.registry._rows("PartDrawing") if _ref(row.get("fields", {}).get("PartRevision")) in revision_ids]

    def process_lines(self, part_id: str):
        part = self._part(part_id)
        revision_id = part["revisionRecordId"]
        links = [row.get("fields", {}) for row in self.registry._rows("PartRevisionLine") if _ref(row.get("fields", {}).get("PartRevision")) == revision_id]
        line_revisions = {int(row["id"]): row.get("fields", {}) for row in self.registry._rows("LineRevision")}
        details: dict[int, list[dict[str, Any]]] = {}
        for row in self.registry._rows("LineDetail"):
            fields = row.get("fields", {})
            revision = _ref(fields.get("LineRevision"))
            if revision:
                details.setdefault(revision, []).append(fields)
        masters = {int(row["id"]): row.get("fields", {}) for row in self.registry._rows("LineMaster")}
        items = []
        for link in links:
            revision_key = _ref(link.get("LineRevision"))
            revision = line_revisions.get(revision_key, {})
            master_id = _ref(link.get("LineMaster"))
            master = masters.get(master_id, {})
            items.append({"partRevisionLine": link, "lineMasterId": master_id, "lineRevisionId": revision_key,
                "processType": link.get("ProcessType") or revision.get("ProcessType"), "lineStatus": revision.get("Status"),
                "physicalSignature": revision.get("PhysicalSignature"), "quantityPerPart": link.get("QuantityPerPart"),
                "quantityUOM": link.get("QuantityUOM"), "sourcePartName": master.get("SourcePartName"),
                "details": details.get(revision_key, [])})
        return {"status": "available", "items": items, "message": "No process lines are pinned to this Part revision." if not items else None}

    def line_candidates(self, query: str = ""):
        needle = normalized_name(query)
        masters = self.registry._rows("LineMaster")
        revisions = self.registry._rows("LineRevision")
        previous_ids = {_ref(row.get("fields", {}).get("PreviousRevision")) for row in revisions if _ref(row.get("fields", {}).get("PreviousRevision"))}
        detail_rows = self.registry._rows("LineDetail")
        parts = {int(row["id"]): row.get("fields", {}) for row in self.registry._rows("ProductPart")}
        result = []
        for master in masters:
            mid = int(master["id"])
            fields = master.get("fields", {})
            matching = [row for row in revisions if _ref(row.get("fields", {}).get("LineMaster")) == mid]
            active = [row for row in matching if int(row.get("id", -1)) not in previous_ids]
            if len(active) != 1:
                continue
            revision = active[0]
            rfields = revision.get("fields", {})
            details = [row.get("fields", {}) for row in detail_rows if _ref(row.get("fields", {}).get("LineRevision")) == int(revision["id"])]
            owner_id = _ref(fields.get("ProductPart"))
            owner = parts.get(owner_id, {})
            stable_owner = str(owner.get("StablePartId") or "")
            label = " ".join([str(fields.get("SourcePartName") or ""), str(rfields.get("ProcessType") or fields.get("ProcessType") or ""), str(fields.get("LineKey") or "")]).strip()
            if needle and needle not in normalized_name(label + " " + json.dumps(details, sort_keys=True, default=str)):
                continue
            result.append({"lineMasterId": mid, "lineRevisionId": int(revision["id"]), "label": label or f"Line {mid}",
                "processType": rfields.get("ProcessType") or fields.get("ProcessType"), "currentOwnerPartId": stable_owner or None,
                "currentOwnerLabel": owner.get("DisplayName") or ("Legacy / unallocated" if owner_id else "Unassigned"),
                "currentOwnerRecordId": owner_id, "status": rfields.get("Status"), "details": details})
        return sorted(result, key=lambda item: (str(item["processType"] or "").casefold(), item["label"].casefold()))

    @_serialized
    def create_vendor(self, *, name: str, actor: str, reason: str, request_key: str):
        self.registry._verify_writer()
        label = " ".join(name.split())
        key = normalized_name(label)
        if not label or not actor.strip() or not reason.strip() or not request_key.strip():
            raise PartIdentityError("PART_VENDOR_INPUT_INVALID", "Vendor name, actor, reason and request key are required.")
        existing = [row for row in self.registry._rows("Vendor") if normalized_name(str(row.get("fields", {}).get("NameKey") or row.get("fields", {}).get("DisplayName") or "")) == key]
        if existing:
            return {"vendor": existing[0].get("fields", {}), "id": existing[0].get("id"), "idempotent": True}
        vendor_key = f"vendor:{request_key}"
        fp = request_fingerprint([label, actor, reason])
        row = self.registry._ensure_keyed("Vendor", "VendorKey", vendor_key, {"VendorKey": vendor_key, "DisplayName": label, "NameKey": key,
            "Status": "active", "CreatedAt": grist_datetime(utc_now()), "Actor": actor.strip(), "Reason": reason.strip(),
            "RequestKey": request_key, "RequestFingerprint": fp})
        if row.get("fields", {}).get("RequestFingerprint") != fp:
            raise PartIdentityError("PART_REQUEST_CONFLICT", "This vendor request key was already used with different data.")
        return {"vendor": row.get("fields", {}), "id": row.get("id"), "idempotent": False}

    @_serialized
    def create_purchase_specification(self, *, part_id: str, code: str, manufacturer: str, manufacturer_part_number: str,
                                      description: str, costing_uom: str, currency: str, purchase_item_id: int | None,
                                      actor: str, reason: str, request_key: str):
        self.registry._verify_writer()
        part = self._part(part_id)
        if not code.strip() or not costing_uom.strip() or not currency.strip() or not actor.strip() or not reason.strip() or not request_key.strip():
            raise PartIdentityError("PART_PURCHASE_SPEC_INPUT_INVALID", "Specification code, costing unit/currency, actor and reason are required.")
        key = f"spec:{part_id}:{request_key}"
        fp = request_fingerprint([part_id, code, manufacturer, manufacturer_part_number, description, costing_uom, currency, purchase_item_id, actor, reason])
        existing_same = self.registry._find(self.registry._rows("PartPurchaseSpecification"), "SpecificationKey", key)
        if not existing_same:
            other_active = [row for row in self.registry._rows("PartPurchaseSpecification")
                if _ref(row.get("fields", {}).get("PartRevision")) == int(part["revisionRecordId"])
                and str(row.get("fields", {}).get("Status") or "active").casefold() == "active"]
            if other_active:
                raise PartIdentityError("PART_PURCHASE_SPEC_EXISTS", "This Rev A Part already has a purchase specification. Add alternate vendor mappings to the same specification; a different physical specification needs a separate Part.")
        row = self.registry._ensure_keyed("PartPurchaseSpecification", "SpecificationKey", key, {
            "SpecificationKey": key, "ProductPart": part["gristRecordId"], "PartRevision": part["revisionRecordId"],
            "PurchaseItem": purchase_item_id, "SpecificationCode": code.strip(), "Manufacturer": manufacturer.strip(),
            "ManufacturerPartNumber": manufacturer_part_number.strip(), "Description": description.strip(), "CostingUOM": costing_uom.strip(),
            "CostingCurrency": currency.strip().upper(), "Status": "active", "Actor": actor.strip(), "Reason": reason.strip(),
            "CreatedAt": grist_datetime(utc_now()), "RequestKey": request_key, "RequestFingerprint": fp})
        if row.get("fields", {}).get("RequestFingerprint") != fp:
            raise PartIdentityError("PART_REQUEST_CONFLICT", "This specification request key was already used with different data.")
        return {"specification": row.get("fields", {}), "id": row.get("id")}

    @_serialized
    def create_vendor_mapping(self, *, specification_id: int, vendor_id: int, sku: str, description: str,
                              actor: str, reason: str, request_key: str):
        self.registry._verify_writer()
        specification = next((row for row in self.registry._rows("PartPurchaseSpecification") if int(row.get("id", -1)) == int(specification_id)), None)
        vendor = next((row for row in self.registry._rows("Vendor") if int(row.get("id", -1)) == int(vendor_id)), None)
        if not specification or not vendor or not sku.strip() or not actor.strip() or not reason.strip():
            raise PartIdentityError("PART_VENDOR_MAPPING_INVALID", "Choose a saved specification and vendor, and enter SKU, actor and review reason.")
        normalized_sku = " ".join(sku.split()).casefold()
        for row in self.registry._rows("VendorPartMapping"):
            fields = row.get("fields", {})
            if _ref(fields.get("Vendor")) == int(vendor_id) and str(fields.get("NormalizedSKU") or "") == normalized_sku:
                if _ref(fields.get("PartPurchaseSpecification")) == int(specification_id):
                    return {"mapping": fields, "id": row.get("id"), "idempotent": True}
                raise PartIdentityError("PART_VENDOR_SKU_CONFLICT", "This vendor SKU is already reviewed as a different physical specification.")
        key = f"vendor-map:{request_key}"
        fp = request_fingerprint([specification_id, vendor_id, sku, description, actor, reason])
        row = self.registry._ensure_keyed("VendorPartMapping", "VendorPartMappingKey", key, {"VendorPartMappingKey": key,
            "Vendor": int(vendor_id), "PartPurchaseSpecification": int(specification_id), "VendorSKU": sku.strip(), "NormalizedSKU": normalized_sku,
            "VendorDescription": description.strip(), "Status": "reviewed", "ReviewedAt": grist_datetime(utc_now()), "ReviewedBy": actor.strip(),
            "ReviewReason": reason.strip(), "Actor": actor.strip(), "Reason": reason.strip(), "CreatedAt": grist_datetime(utc_now()),
            "RequestKey": request_key, "RequestFingerprint": fp})
        if row.get("fields", {}).get("RequestFingerprint") != fp:
            raise PartIdentityError("PART_REQUEST_CONFLICT", "This vendor mapping request key was already used with different data.")
        return {"mapping": row.get("fields", {}), "id": row.get("id"), "idempotent": False}

    @_serialized
    def add_unit_conversion(self, *, specification_id: int, from_uom: str, to_uom: str, factor: float,
                            evidence: str, actor: str, reason: str, request_key: str):
        self.registry._verify_writer()
        if not any(int(row.get("id", -1)) == int(specification_id) for row in self.registry._rows("PartPurchaseSpecification")):
            raise PartIdentityError("PART_PURCHASE_SPEC_NOT_FOUND", "Choose an existing purchase specification.")
        try:
            number = float(factor)
        except (TypeError, ValueError):
            number = 0
        if not math.isfinite(number) or number <= 0 or not from_uom.strip() or not to_uom.strip() or not evidence.strip() or not actor.strip() or not reason.strip():
            raise PartIdentityError("PART_CONVERSION_INPUT_INVALID", "A positive factor, units, evidence reference, actor and reason are required.")
        key = f"uom:{specification_id}:{request_key}"
        fp = request_fingerprint([specification_id, from_uom, to_uom, number, evidence, actor, reason])
        row = self.registry._ensure_keyed("PartPurchaseUnitConversion", "ConversionKey", key, {"ConversionKey": key,
            "PartPurchaseSpecification": int(specification_id), "FromUOM": from_uom.strip(), "ToUOM": to_uom.strip(),
            "Factor": number, "EvidenceReference": evidence.strip(), "Status": "approved", "Actor": actor.strip(),
            "Reason": reason.strip(), "OccurredAt": grist_datetime(utc_now()), "RequestKey": request_key, "RequestFingerprint": fp})
        if row.get("fields", {}).get("RequestFingerprint") != fp:
            raise PartIdentityError("PART_REQUEST_CONFLICT", "This conversion request key was already used with different data.")
        return {"conversion": row.get("fields", {}), "id": row.get("id")}

    @_serialized
    def add_currency_conversion(self, *, specification_id: int, from_currency: str, to_currency: str, rate: float,
                                rate_date: str, evidence: str, actor: str, reason: str, request_key: str):
        self.registry._verify_writer()
        if not any(int(row.get("id", -1)) == int(specification_id) for row in self.registry._rows("PartPurchaseSpecification")):
            raise PartIdentityError("PART_PURCHASE_SPEC_NOT_FOUND", "Choose an existing purchase specification.")
        try:
            value = float(rate)
            dt = datetime.fromisoformat(rate_date.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            raise PartIdentityError("PART_CONVERSION_INPUT_INVALID", "Exchange rate and effective date must be valid.")
        if not math.isfinite(value) or value <= 0 or not from_currency.strip() or not to_currency.strip() or not evidence.strip() or not actor.strip() or not reason.strip():
            raise PartIdentityError("PART_CONVERSION_INPUT_INVALID", "A positive rate, currencies, evidence, actor and reason are required.")
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        key = f"currency:{specification_id}:{request_key}"
        fp = request_fingerprint([specification_id, from_currency.upper(), to_currency.upper(), value, dt.isoformat(), evidence, actor, reason])
        row = self.registry._ensure_keyed("PartPurchaseCurrencyConversion", "ConversionKey", key, {"ConversionKey": key,
            "PartPurchaseSpecification": int(specification_id), "FromCurrency": from_currency.strip().upper(),
            "ToCurrency": to_currency.strip().upper(), "Rate": value, "RateDate": grist_datetime(dt),
            "EvidenceReference": evidence.strip(), "Status": "approved", "Actor": actor.strip(), "Reason": reason.strip(),
            "OccurredAt": grist_datetime(utc_now()), "RequestKey": request_key, "RequestFingerprint": fp})
        if row.get("fields", {}).get("RequestFingerprint") != fp:
            raise PartIdentityError("PART_REQUEST_CONFLICT", "This conversion request key was already used with different data.")
        return {"conversion": row.get("fields", {}), "id": row.get("id")}

    @_serialized
    def capture_purchase(self, *, part_id: str, specification_id: int, mapping_id: int, transaction_key: str,
                         transaction_line_key: str, record_type: str, status: str, transaction_at: str,
                         document_reference: str, quantity: float, quantity_uom: str, currency: str,
                         extended_amount: float, discount_amount: float, tax_amount: float, freight_amount: float,
                         other_charges: float, actor: str, reason: str, request_key: str,
                         reverses_record_id: int | None = None, supersedes_record_key: str = ""):
        self.registry._verify_writer()
        part = self._part(part_id)
        specification = next((row for row in self.registry._rows("PartPurchaseSpecification") if int(row.get("id", -1)) == int(specification_id)), None)
        mapping = next((row for row in self.registry._rows("VendorPartMapping") if int(row.get("id", -1)) == int(mapping_id)), None)
        if (not specification or not mapping
                or _ref(specification.get("fields", {}).get("ProductPart")) != part["gristRecordId"]
                or _ref(specification.get("fields", {}).get("PartRevision")) != part["revisionRecordId"]
                or _ref(mapping.get("fields", {}).get("PartPurchaseSpecification")) != int(specification_id)
                or str(mapping.get("fields", {}).get("Status") or "").casefold() != "reviewed"):
            raise PartIdentityError("PART_PURCHASE_LINK_INVALID", "Purchase record must link to this Part's specification and a reviewed vendor mapping.")
        try:
            at = datetime.fromisoformat(transaction_at.replace("Z", "+00:00"))
            numbers = [float(quantity), float(extended_amount), float(discount_amount), float(tax_amount), float(freight_amount), float(other_charges)]
        except (TypeError, ValueError):
            raise PartIdentityError("PART_PURCHASE_INPUT_INVALID", "Purchase date and monetary values must be valid.")
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        if not all(math.isfinite(value) for value in numbers) or numbers[0] <= 0 or min(numbers[1:]) < 0 or numbers[2] > numbers[1]:
            raise PartIdentityError("PART_PURCHASE_INPUT_INVALID", "Purchase quantity must be positive; charges/discount must be nonnegative and discount cannot exceed extended amount.")
        if not transaction_key.strip() or not transaction_line_key.strip() or not quantity_uom.strip() or not currency.strip() or not actor.strip() or not reason.strip() or not request_key.strip():
            raise PartIdentityError("PART_PURCHASE_INPUT_REQUIRED", "Transaction identity, quantity/UOM, currency, actor, reason and request key are required.")
        if status.casefold() not in {"draft", "posted", "completed", "void", "quote", "returned", "reversed"}:
            raise PartIdentityError("PART_PURCHASE_STATUS_INVALID", "Choose a supported purchase evidence status.")
        if record_type.casefold() not in {"actual_purchase", "invoice", "receipt", "purchase", "quote", "return", "reversal", "void", "correction"}:
            raise PartIdentityError("PART_PURCHASE_TYPE_INVALID", "Choose a supported purchase evidence type.")
        map_fields = mapping.get("fields", {})
        vendor_id = _ref(map_fields.get("Vendor"))
        transaction_identity = (vendor_id, transaction_key.strip().casefold(), transaction_line_key.strip().casefold())
        fingerprint = request_fingerprint([part_id, specification_id, mapping_id, *transaction_identity, record_type, status, at.isoformat(), document_reference, numbers, quantity_uom, currency.upper(), actor, reason, reverses_record_id, supersedes_record_key])
        if reverses_record_id is not None:
            original = next((row for row in self.registry._rows("PartPurchaseRecord") if int(row.get("id", -1)) == int(reverses_record_id)), None)
            if not original or _ref(original.get("fields", {}).get("PartPurchaseSpecification")) != int(specification_id):
                raise PartIdentityError("PART_PURCHASE_REVERSAL_INVALID", "A reversal must reference an existing purchase record for this specification.")
        if supersedes_record_key:
            original = self.registry._find(self.registry._rows("PartPurchaseRecord"), "PurchaseRecordKey", supersedes_record_key)
            if not original or _ref(original.get("fields", {}).get("PartPurchaseSpecification")) != int(specification_id):
                raise PartIdentityError("PART_PURCHASE_REVERSAL_INVALID", "A superseding record must identify an existing purchase record for this specification.")
        for old in self.registry._rows("PartPurchaseRecord"):
            f = old.get("fields", {})
            identity = (_ref(f.get("Vendor")), str(f.get("TransactionKey") or "").casefold(), str(f.get("TransactionLineKey") or "").casefold())
            if identity == transaction_identity:
                try:
                    old_at = datetime.fromisoformat(str(f.get("TransactionAt")).replace("Z", "+00:00"))
                    old_numbers = [float(f.get(key) or 0) for key in ("Quantity", "ExtendedAmount", "DiscountAmount", "TaxAmount", "FreightAmount", "OtherCharges")]
                except (TypeError, ValueError):
                    old_at, old_numbers = None, []
                same_evidence = bool(old_at and old_at == at and old_numbers == numbers
                    and _ref(f.get("ProductPart")) == part["gristRecordId"]
                    and _ref(f.get("PartPurchaseSpecification")) == int(specification_id)
                    and str(f.get("RecordType") or "").casefold() == record_type.casefold()
                    and str(f.get("Status") or "").casefold() == status.casefold()
                    and str(f.get("DocumentReference") or "").strip() == document_reference.strip()
                    and str(f.get("QuantityUOM") or "").casefold() == quantity_uom.strip().casefold()
                    and str(f.get("Currency") or "").upper() == currency.strip().upper()
                    and _ref(f.get("ReversesRecord")) == (int(reverses_record_id) if reverses_record_id is not None else None)
                    and str(f.get("SupersedesRecord") or "") == supersedes_record_key)
                if same_evidence:
                    return {"purchase": f, "id": old.get("id"), "idempotent": True}
                raise PartIdentityError("PART_PURCHASE_DUPLICATE_CONFLICT", "This actual vendor transaction line already exists with different evidence; use a correction/reversal record.")
        existing_request = self.registry._find(self.registry._rows("PartPurchaseRecord"), "RequestKey", request_key)
        if existing_request:
            if existing_request.get("fields", {}).get("RequestFingerprint") != fingerprint:
                raise PartIdentityError("PART_REQUEST_CONFLICT", "This purchase request key was already used with different evidence.")
            return {"purchase": existing_request.get("fields", {}), "id": existing_request.get("id"), "idempotent": True}
        record_key = f"purchase:{request_key}"
        fields = {"PurchaseRecordKey": record_key, "VendorPartMapping": int(mapping_id), "PartPurchaseSpecification": int(specification_id),
            "ProductPart": part["gristRecordId"], "PartRevision": part["revisionRecordId"], "Vendor": vendor_id,
            "TransactionKey": transaction_key.strip(), "TransactionLineKey": transaction_line_key.strip(), "RecordType": record_type,
            "Status": status.casefold(), "TransactionAt": grist_datetime(at), "DocumentReference": document_reference.strip(),
            "Quantity": numbers[0], "QuantityUOM": quantity_uom.strip(), "RateBasisUOM": quantity_uom.strip(), "Currency": currency.strip().upper(),
            "ExtendedAmount": numbers[1], "DiscountAmount": numbers[2], "TaxAmount": numbers[3], "FreightAmount": numbers[4],
            "OtherCharges": numbers[5], "RatePolicy": "net_merchandise_discounted_tax_freight_other_excluded_v1", "Actor": actor.strip(),
            "Reason": reason.strip(), "RecordedAt": grist_datetime(utc_now()), "RequestKey": request_key, "RequestFingerprint": fingerprint,
            "ReversesRecord": reverses_record_id, "SupersedesRecord": supersedes_record_key}
        row = self.registry._ensure_keyed("PartPurchaseRecord", "PurchaseRecordKey", record_key, fields)
        if row.get("fields", {}).get("RequestFingerprint") != fingerprint:
            raise PartIdentityError("PART_REQUEST_CONFLICT", "This purchase request key was already used with different evidence.")
        return {"purchase": row.get("fields", {}), "id": row.get("id"), "idempotent": False}

    @_serialized
    def purchase_detail(self, part_id: str, *, as_of: str | None = None, cost_run_key: str = "", configuration_selection_key: str = ""):
        from app.purchased_parts import resolve_purchased_part_rate
        part = self._part(part_id)
        specs = [row for row in self.registry._rows("PartPurchaseSpecification") if _ref(row.get("fields", {}).get("ProductPart")) == part["gristRecordId"]]
        vendors = {int(row["id"]): row.get("fields", {}) for row in self.registry._rows("Vendor")}
        mappings = [row for row in self.registry._rows("VendorPartMapping") if _ref(row.get("fields", {}).get("PartPurchaseSpecification")) in {int(spec["id"]) for spec in specs}]
        purchases = [row for row in self.registry._rows("PartPurchaseRecord") if _ref(row.get("fields", {}).get("ProductPart")) == part["gristRecordId"]]
        units = self.registry._rows("PartPurchaseUnitConversion")
        currencies = self.registry._rows("PartPurchaseCurrencyConversion")
        spec_results = []
        for source in specs:
            fields = source.get("fields", {})
            specification = {**fields, "recordId": str(source["id"])}
            for rows in (purchases, units, currencies):
                for row in rows:
                    row_fields = row.get("fields", {})
                    reference = row_fields.get("PartPurchaseSpecification")
                    if _ref(reference) == int(source["id"]):
                        row["fields"] = row_fields
            rate = resolve_purchased_part_rate(specification, purchases, unit_conversions=[row.get("fields", {}) for row in units if _ref(row.get("fields", {}).get("PartPurchaseSpecification")) == int(source["id"])],
                currency_conversions=[row.get("fields", {}) for row in currencies if _ref(row.get("fields", {}).get("PartPurchaseSpecification")) == int(source["id"])], as_of=as_of)
            if cost_run_key and configuration_selection_key:
                self.registry._verify_writer()
                evidence_key = request_fingerprint([cost_run_key, configuration_selection_key, as_of, rate.get("status"), rate.get("purchaseRecordId"), rate.get("transactionAt"), rate.get("rate")])
                purchase_row = next((row for row in purchases if str(row.get("id")) == str(rate.get("purchaseRecordId")) or str(row.get("fields", {}).get("PurchaseRecordKey")) == str(rate.get("purchaseRecordId"))), None)
                self.registry._ensure_keyed("PurchasedPartCostEvidence", "EvidenceKey", evidence_key, {
                        "EvidenceKey": evidence_key, "CostRunKey": cost_run_key, "ConfigurationSelectionKey": configuration_selection_key,
                        "ProductPart": part["gristRecordId"], "PartRevision": part["revisionRecordId"], "PartPurchaseSpecification": int(source["id"]),
                        "PurchaseRecord": int(purchase_row["id"]) if purchase_row else None,
                        "Vendor": _ref(purchase_row.get("fields", {}).get("Vendor")) if purchase_row else None,
                        "AsOfDate": grist_datetime(as_of) if as_of else None,
                        "TransactionAt": grist_datetime(rate["transactionAt"]) if rate.get("transactionAt") else None,
                        "PurchaseQuantity": rate.get("purchaseQuantity"), "PurchaseUOM": rate.get("purchaseUOM"),
                        "NormalizedQuantity": rate.get("normalizedQuantity"), "CostingUOM": rate.get("uom") or fields.get("CostingUOM"),
                        "Currency": rate.get("currency") or fields.get("CostingCurrency"), "BaseUnitPrice": rate.get("baseUnitPrice"),
                        "DiscountPerUnit": rate.get("discountPerUnit"), "AppliedUnitRate": rate.get("rate"), "RatePolicy": rate["ratePolicy"], "ResolutionStatus": rate["status"], "Reason": rate["reason"],
                        "Actor": "costing-resolver", "OccurredAt": grist_datetime(utc_now()), "RequestKey": evidence_key, "RequestFingerprint": evidence_key})
                rate["evidenceKey"] = evidence_key
            spec_mappings = [row for row in mappings if _ref(row.get("fields", {}).get("PartPurchaseSpecification")) == int(source["id"])]
            spec_purchases = [row.get("fields", {}) for row in purchases if _ref(row.get("fields", {}).get("PartPurchaseSpecification")) == int(source["id"])]
            spec_results.append({"specification": {"id": source.get("id"), **fields}, "rate": rate,
                "vendors": [{**vendors.get(_ref(row.get("fields", {}).get("Vendor")), {}), "id": _ref(row.get("fields", {}).get("Vendor")),
                    "mappingId": int(row["id"]), "sku": row.get("fields", {}).get("VendorSKU"), "status": row.get("fields", {}).get("Status")} for row in spec_mappings],
                "purchases": sorted(spec_purchases, key=lambda row: str(row.get("TransactionAt") or ""), reverse=True),
                "unitConversions": [row.get("fields", {}) for row in units if _ref(row.get("fields", {}).get("PartPurchaseSpecification")) == int(source["id"])],
                "currencyConversions": [row.get("fields", {}) for row in currencies if _ref(row.get("fields", {}).get("PartPurchaseSpecification")) == int(source["id"]) ]})
        vendor_catalog = [{"id": int(record_id), **fields} for record_id, fields in vendors.items()]
        vendor_catalog.sort(key=lambda item: str(item.get("DisplayName") or "").casefold())
        return {"partId": part_id, "specifications": spec_results, "vendorCatalog": vendor_catalog, "purchaseHistoryAvailable": bool(specs),
                "message": "No purchase specification has been captured." if not specs else None}


class GristAdminClientShim:
    """Use the existing administration API for read-only schema verification."""
    def __init__(self, client: GristClient):
        from app.grist_admin import GristAdminClient
        self.admin = GristAdminClient(client.api_key, client.base_url)
        self.doc_id = client.doc_id

    def list_tables(self):
        return self.admin.list_tables(self.doc_id)

    def list_columns(self, table: str):
        return self.admin.list_columns(self.doc_id, table)


class _SQLiteJournalLock:
    """Cross-process write reservation shared by Part/mapping request flows."""
    def __init__(self, registry: GristPartRegistry, thread_lock: RLock):
        self.registry = registry
        self.thread_lock = thread_lock
        self.db = None

    def __enter__(self):
        self.thread_lock.acquire()
        self.db = self.registry._connect()
        try:
            self.db.execute("BEGIN IMMEDIATE")
        except Exception:
            self.db.close()
            self.db = None
            self.thread_lock.release()
            raise
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if self.db:
                self.db.commit() if exc_type is None else self.db.rollback()
                self.db.close()
        finally:
            self.thread_lock.release()
        return False

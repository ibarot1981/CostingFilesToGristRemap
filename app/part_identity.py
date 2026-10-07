"""Durable Part identity, number allocation, and metadata history.

The Part registry is stored in SQLite because Grist's table API has no unique
constraint or cross-request transaction for a Part number.  A single local
SQLite file serializes writers across application processes on one host.
"""
from __future__ import annotations

from datetime import datetime, timezone
from contextlib import contextmanager
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sqlite3
from threading import local
import unicodedata
from typing import Any
from uuid import uuid4


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCOPE_TYPES = {"global", "product", "product_model", "model_code"}
PART_DB_ENV = "SAFARI_PART_DATABASE_PATH"


class PartIdentityError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class _SQLiteWriteLock:
    """Hold one cross-process SQLite write reservation around mapping review."""

    def __init__(self, store: "PartIdentityStore"):
        self.store = store

    def __enter__(self):
        active = getattr(self.store._thread_state, "connection", None)
        if active is not None:
            self.store._thread_state.depth += 1
            return self
        connection = self.store._connect()
        connection.execute("BEGIN IMMEDIATE")
        self.store._thread_state.connection = connection
        self.store._thread_state.depth = 1
        return self

    def __exit__(self, exception_type, exception, traceback):
        self.store._thread_state.depth -= 1
        if self.store._thread_state.depth:
            return False
        connection = self.store._thread_state.connection
        try:
            if exception_type is None:
                connection.commit()
            else:
                connection.rollback()
        finally:
            del self.store._thread_state.connection
            del self.store._thread_state.depth
            connection.close()
        return False


def normalized_name(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()


def normalized_shortcode(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip().upper()


def request_fingerprint(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return sha256(encoded.encode("utf-8")).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def generated_name(shortcode: str, description: str, variant: str = "") -> str:
    parts = [shortcode.strip(), " ".join(description.split())]
    if variant.strip():
        parts.append(" ".join(variant.split()))
    return " — ".join(parts)


class PartIdentityStore:
    """SQLite source of truth for new Parts and their metadata.

    SQLite's IMMEDIATE write transaction and UNIQUE constraints coordinate
    different app processes that share this database on a single host.
    """

    def __init__(self, path: str | Path | None = None):
        configured = path if path is not None else os.getenv(PART_DB_ENV)
        self.path = Path(configured).expanduser() if configured else PROJECT_ROOT / "state" / "safari_parts.sqlite3"
        if str(self.path).startswith("\\\\"):
            raise PartIdentityError("PART_DATABASE_UNSUPPORTED", "The Part registry must be on a local disk, not a network share.")
        if str(self.path) == ":memory:":
            raise PartIdentityError("PART_DATABASE_UNSUPPORTED", "The Part registry must use durable file storage.")
        self.path = self.path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._thread_state = local()
        self._initialize()
        self.lock = _SQLiteWriteLock(self)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA synchronous = FULL")
        return connection

    @contextmanager
    def _connection(self):
        """Close each short-lived connection even when an error is raised."""
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS part_registry_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS part_number_sequence (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    next_number INTEGER NOT NULL CHECK (next_number >= 1)
                );
                INSERT OR IGNORE INTO part_number_sequence(singleton, next_number) VALUES (1, 1);
                CREATE TABLE IF NOT EXISTS part_scope_shortcode (
                    scope_type TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    target_label TEXT NOT NULL,
                    shortcode TEXT NOT NULL,
                    normalized_shortcode TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    updated_at TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    PRIMARY KEY(scope_type, target_id),
                    UNIQUE(scope_type, normalized_shortcode)
                );
                CREATE TABLE IF NOT EXISTS part_shortcode_history (
                    history_id TEXT PRIMARY KEY,
                    scope_type TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    target_label TEXT NOT NULL,
                    old_shortcode TEXT,
                    new_shortcode TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    occurred_at TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    request_key TEXT NOT NULL UNIQUE
                );
                CREATE TABLE IF NOT EXISTS parts (
                    part_id TEXT PRIMARY KEY,
                    part_number TEXT NOT NULL UNIQUE,
                    engineering_revision TEXT NOT NULL DEFAULT 'A' CHECK (engineering_revision = 'A'),
                    status TEXT NOT NULL CHECK (status IN ('active', 'retired')),
                    metadata_version INTEGER NOT NULL CHECK (metadata_version >= 1),
                    created_at TEXT NOT NULL,
                    created_actor TEXT NOT NULL,
                    create_reason TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS part_metadata_version (
                    part_id TEXT NOT NULL REFERENCES parts(part_id),
                    version INTEGER NOT NULL CHECK (version >= 1),
                    scope_type TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    target_label TEXT NOT NULL,
                    shortcode TEXT NOT NULL,
                    description TEXT NOT NULL,
                    variant TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    name_key TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    request_key TEXT NOT NULL,
                    PRIMARY KEY(part_id, version)
                );
                CREATE TABLE IF NOT EXISTS part_name_index (
                    name_key TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    part_id TEXT NOT NULL REFERENCES parts(part_id),
                    metadata_version INTEGER NOT NULL,
                    is_current INTEGER NOT NULL CHECK (is_current IN (0, 1)),
                    added_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS part_name_index_part ON part_name_index(part_id);
                CREATE TABLE IF NOT EXISTS part_legacy_name_index (
                    external_id TEXT NOT NULL,
                    name_key TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    PRIMARY KEY(external_id, name_key)
                );
                CREATE INDEX IF NOT EXISTS part_legacy_name_index_name ON part_legacy_name_index(name_key);
                CREATE TABLE IF NOT EXISTS part_lifecycle_history (
                    event_id TEXT PRIMARY KEY,
                    part_id TEXT NOT NULL REFERENCES parts(part_id),
                    event_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    request_key TEXT NOT NULL UNIQUE
                );
                CREATE TABLE IF NOT EXISTS part_request_log (
                    request_key TEXT PRIMARY KEY,
                    request_type TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    recorded_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS part_mapping_review (
                    review_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ReviewKey TEXT NOT NULL UNIQUE,
                    FileKey TEXT NOT NULL,
                    SourceHash TEXT NOT NULL,
                    AssociationKey TEXT NOT NULL,
                    AssociationVersion INTEGER NOT NULL,
                    GroupKey TEXT NOT NULL,
                    SheetName TEXT NOT NULL,
                    SourceRow INTEGER NOT NULL,
                    SourceDescription TEXT NOT NULL,
                    ProductPartLegacyId INTEGER,
                    PartIdentity TEXT,
                    Version INTEGER NOT NULL,
                    Actor TEXT NOT NULL,
                    Reason TEXT NOT NULL,
                    OccurredAt TEXT NOT NULL,
                    RequestKey TEXT NOT NULL,
                    RequestFingerprint TEXT NOT NULL,
                    RequestRowCount INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS part_mapping_review_file_version ON part_mapping_review(FileKey, Version);
                CREATE INDEX IF NOT EXISTS part_mapping_review_request ON part_mapping_review(RequestKey);
                INSERT OR IGNORE INTO part_registry_meta(key, value) VALUES ('schema_version', '1');
            """)
            version = connection.execute("SELECT value FROM part_registry_meta WHERE key='schema_version'").fetchone()
            if not version or version["value"] != "1":
                raise PartIdentityError("PART_DATABASE_VERSION", "The Part registry database version is not supported.")

    @staticmethod
    def _row_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row else None

    def sync_legacy_names(self, rows: list[dict[str, Any]]) -> None:
        """Record observed legacy names locally for collision checks; never edits Grist."""
        now = utc_now()
        values = []
        for row in rows:
            fields = row.get("fields", {})
            name = " ".join(unicodedata.normalize("NFKC", str(fields.get("DisplayName") or "")).split())
            key = normalized_name(name)
            if not name or not key:
                continue
            values.append((str(row.get("id")), key, name, str(fields.get("Status") or "legacy"), now))
        if not values:
            return
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.executemany("""
                INSERT INTO part_legacy_name_index(external_id, name_key, display_name, status, last_seen_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(external_id, name_key) DO UPDATE SET
                    display_name=excluded.display_name, status=excluded.status, last_seen_at=excluded.last_seen_at
            """, values)
            connection.commit()

    def shortcode(self, scope_type: str, target_id: str) -> dict[str, Any] | None:
        if scope_type not in SCOPE_TYPES:
            return None
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM part_scope_shortcode WHERE scope_type=? AND target_id=?", (scope_type, target_id)).fetchone()
            return self._row_dict(row)

    def shortcodes(self) -> list[dict[str, Any]]:
        with self._connection() as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM part_scope_shortcode ORDER BY scope_type, target_label COLLATE NOCASE")]

    def canonical_part_records(self) -> list[dict[str, Any]]:
        result = []
        for part in self.list_parts():
            result.append({"id": part["id"], "fields": {"PartKey": f"part:id:{part['id']}", "StablePartId": part["id"],
                "DisplayName": part["name"], "Status": part["status"], "NameKey": normalized_name(part["name"]),
                "Aliases": part["aliases"],
                "PartNumber": part["partNumber"], "EngineeringRevision": "A", "ScopeType": part["scope"],
                "ScopeTargetId": part["scopeTargetId"], "ScopeTarget": part["scopeTarget"],
                "Description": part["description"], "DesignVariant": part["variant"]}})
        return result

    def mapping_records(self) -> list[dict[str, Any]]:
        active = getattr(self._thread_state, "connection", None)
        own_connection = active is None
        connection = active or self._connect()
        try:
            rows = connection.execute("SELECT * FROM part_mapping_review ORDER BY review_id").fetchall()
            result = []
            for row in rows:
                fields = {key: row[key] for key in row.keys() if key not in {"review_id", "ProductPartLegacyId"}}
                fields["ProductPart"] = row["ProductPartLegacyId"]
                result.append({"id": row["review_id"], "fields": fields})
            return result
        finally:
            if own_connection:
                connection.close()

    def append_mapping_records(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        active = getattr(self._thread_state, "connection", None)
        own_transaction = active is None
        connection = active or self._connect()
        if own_transaction:
            connection.execute("BEGIN IMMEDIATE")
        saved = []
        try:
            for item in rows:
                fields = dict(item)
                values = (fields.get("ReviewKey"), fields.get("FileKey"), fields.get("SourceHash"), fields.get("AssociationKey"),
                    int(fields.get("AssociationVersion") or 0), fields.get("GroupKey"), fields.get("SheetName"), int(fields.get("SourceRow") or 0),
                    fields.get("SourceDescription") or "", int(fields["ProductPart"]) if fields.get("ProductPart") not in (None, "") else None,
                    fields.get("PartIdentity") or None, int(fields.get("Version") or 0), fields.get("Actor") or "", fields.get("Reason") or "",
                    fields.get("OccurredAt") or utc_now(), fields.get("RequestKey") or "", fields.get("RequestFingerprint") or "",
                    int(fields.get("RequestRowCount") or len(rows)))
                cursor = connection.execute("""
                    INSERT INTO part_mapping_review(ReviewKey,FileKey,SourceHash,AssociationKey,AssociationVersion,GroupKey,SheetName,SourceRow,
                        SourceDescription,ProductPartLegacyId,PartIdentity,Version,Actor,Reason,OccurredAt,RequestKey,RequestFingerprint,RequestRowCount)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, values)
                saved_fields = {key: value for key, value in fields.items()}
                saved_fields.setdefault("ProductPart", values[9])
                saved.append({"id": cursor.lastrowid, "fields": saved_fields})
            if own_transaction:
                connection.commit()
            return saved
        except Exception:
            if own_transaction:
                connection.rollback()
            raise
        finally:
            if own_transaction:
                connection.close()

    def set_shortcode(self, *, scope_type: str, target_id: str, target_label: str, shortcode: str,
                      actor: str, reason: str, request_key: str) -> dict[str, Any]:
        code = normalized_shortcode(shortcode)
        if scope_type not in SCOPE_TYPES or not target_id or not target_label.strip():
            raise PartIdentityError("PART_SCOPE_TARGET_INVALID", "Choose a valid sharing scope and target.")
        if not re.fullmatch(r"[A-Z0-9]{1,20}", code):
            raise PartIdentityError("PART_SHORTCODE_INVALID", "Shortcodes must contain 1–20 letters or digits.")
        if not actor.strip() or not reason.strip() or not request_key.strip():
            raise PartIdentityError("PART_INPUT_REQUIRED", "A reason, actor and Idempotency-Key are required.")
        fingerprint = request_fingerprint([scope_type, target_id, code, actor, reason.strip()])
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            replay = self._request_replay(connection, request_key, "shortcode", fingerprint)
            if replay:
                connection.commit()
                return {**replay, "idempotent": True}
            old = connection.execute("SELECT * FROM part_scope_shortcode WHERE scope_type=? AND target_id=?", (scope_type, target_id)).fetchone()
            version = (old["version"] if old else 0) + 1
            try:
                connection.execute("""
                    INSERT INTO part_scope_shortcode(scope_type,target_id,target_label,shortcode,normalized_shortcode,version,updated_at,actor,reason)
                    VALUES(?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(scope_type,target_id) DO UPDATE SET target_label=excluded.target_label,
                        shortcode=excluded.shortcode, normalized_shortcode=excluded.normalized_shortcode,
                        version=excluded.version, updated_at=excluded.updated_at, actor=excluded.actor, reason=excluded.reason
                """, (scope_type, target_id, target_label.strip(), code, code, version, utc_now(), actor, reason.strip()))
            except sqlite3.IntegrityError as exc:
                connection.rollback()
                raise PartIdentityError("PART_SHORTCODE_CONFLICT", "That shortcode is already maintained for another target in this scope.") from exc
            connection.execute("""
                INSERT INTO part_shortcode_history(history_id,scope_type,target_id,target_label,old_shortcode,new_shortcode,version,occurred_at,actor,reason,request_key)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """, (str(uuid4()), scope_type, target_id, target_label.strip(), old["shortcode"] if old else None,
                  code, version, utc_now(), actor, reason.strip(), request_key))
            result = {"scope": scope_type, "targetId": target_id, "targetLabel": target_label.strip(), "shortcode": code, "version": version}
            self._write_request(connection, request_key, "shortcode", fingerprint, result)
            connection.commit()
            return {**result, "idempotent": False}

    @staticmethod
    def _request_replay(connection: sqlite3.Connection, key: str, request_type: str, fingerprint: str) -> dict[str, Any] | None:
        row = connection.execute("SELECT * FROM part_request_log WHERE request_key=?", (key,)).fetchone()
        if not row:
            return None
        if row["request_type"] != request_type or row["fingerprint"] != fingerprint:
            raise PartIdentityError("PART_REQUEST_CONFLICT", "This Idempotency-Key was already used with a different Part request.")
        return json.loads(row["result_json"])

    @staticmethod
    def _write_request(connection: sqlite3.Connection, key: str, request_type: str, fingerprint: str, result: dict[str, Any]) -> None:
        connection.execute("INSERT INTO part_request_log(request_key,request_type,fingerprint,result_json,recorded_at) VALUES(?,?,?,?,?)",
                           (key, request_type, fingerprint, json.dumps(result, ensure_ascii=False, separators=(",", ":")), utc_now()))

    def preview(self, *, scope_type: str, target_id: str, description: str, variant: str = "", exclude_part_id: str = "") -> dict[str, Any]:
        description = " ".join(unicodedata.normalize("NFKC", description).split())
        variant = " ".join(unicodedata.normalize("NFKC", variant).split())
        if scope_type not in SCOPE_TYPES or not target_id or not description or len(description) > 120 or len(variant) > 120:
            raise PartIdentityError("PART_INPUT_INVALID", "Choose a scope and target and enter a Part description of 1–120 characters; variant may be up to 120 characters.")
        shortcode = self.shortcode(scope_type, target_id)
        if not shortcode:
            raise PartIdentityError("PART_SHORTCODE_REQUIRED", "Maintain a shortcode for this scope target before previewing a Part name.")
        name = generated_name(shortcode["shortcode"], description, variant)
        key = normalized_name(name)
        with self._connection() as connection:
            existing = connection.execute("""
                SELECT 'part' AS source, p.part_id AS id, p.part_number AS number, p.status AS status
                FROM part_name_index n JOIN parts p ON p.part_id=n.part_id WHERE n.name_key=? AND p.part_id<>?
                UNION ALL
                SELECT 'legacy' AS source, external_id AS id, NULL AS number, status FROM part_legacy_name_index WHERE name_key=?
            """, (key, exclude_part_id, key)).fetchall()
        return {"name": name, "scope": scope_type, "targetId": target_id, "shortcode": shortcode["shortcode"],
                "available": not existing, "collision": [dict(row) for row in existing], "validatedAt": utc_now()}

    def create_part(self, *, scope_type: str, target_id: str, target_label: str, description: str,
                    variant: str = "", expected_name: str, actor: str, reason: str, request_key: str,
                    revision_assertion: Any = None) -> dict[str, Any]:
        if revision_assertion is not None and str(revision_assertion).casefold() != "a":
            raise PartIdentityError("PART_REVISION_LOCKED", "All new Parts are Rev A until the approved CR process is implemented.")
        if scope_type not in SCOPE_TYPES or not target_id or not target_label.strip():
            raise PartIdentityError("PART_SCOPE_TARGET_INVALID", "Choose a valid sharing scope and an existing scope target.")
        description = " ".join(unicodedata.normalize("NFKC", description).split())
        variant = " ".join(unicodedata.normalize("NFKC", variant).split())
        if not description or len(description) > 120 or len(variant) > 120 or len(reason.strip()) > 2000:
            raise PartIdentityError("PART_INPUT_INVALID", "A Part description, a variant up to 120 characters, and a reason up to 2,000 characters are required.")
        if not actor.strip() or not reason.strip() or not request_key.strip():
            raise PartIdentityError("PART_INPUT_REQUIRED", "A reason, actor and Idempotency-Key are required.")
        payload = [scope_type, target_id, description, variant, expected_name, actor, reason.strip()]
        fingerprint = request_fingerprint(payload)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            replay = self._request_replay(connection, request_key, "create", fingerprint)
            if replay:
                connection.commit()
                return {"part": replay, "idempotent": True}
            shortcode = connection.execute("SELECT * FROM part_scope_shortcode WHERE scope_type=? AND target_id=?", (scope_type, target_id)).fetchone()
            if not shortcode:
                connection.rollback()
                raise PartIdentityError("PART_SHORTCODE_REQUIRED", "Maintain a shortcode for this scope target before creating a Part.")
            actual_name = generated_name(shortcode["shortcode"], description, variant)
            if len(actual_name) > 200:
                connection.rollback()
                raise PartIdentityError("PART_INPUT_INVALID", "The generated Part name must be 200 characters or fewer.")
            if actual_name != expected_name:
                connection.rollback()
                raise PartIdentityError("PART_NAME_PREVIEW_STALE", "The generated name changed after preview. Review the current name before saving.")
            name_key = normalized_name(actual_name)
            conflict = connection.execute("""
                SELECT 1 FROM part_name_index WHERE name_key=?
                UNION ALL SELECT 1 FROM part_legacy_name_index WHERE name_key=? LIMIT 1
            """, (name_key, name_key)).fetchone()
            if conflict:
                connection.rollback()
                raise PartIdentityError("PART_NAME_EXISTS", "This generated name is already used by a current or historical Part name. Choose a meaningful distinction or review the existing Part.")
            sequence = connection.execute("SELECT next_number FROM part_number_sequence WHERE singleton=1").fetchone()["next_number"]
            connection.execute("UPDATE part_number_sequence SET next_number=? WHERE singleton=1", (sequence + 1,))
            part_id = str(uuid4())
            now = utc_now()
            number = f"SM-P-{sequence:06d}"
            connection.execute("INSERT INTO parts(part_id,part_number,engineering_revision,status,metadata_version,created_at,created_actor,create_reason) VALUES(?,?, 'A','active',1,?,?,?)",
                               (part_id, number, now, actor.strip(), reason.strip()))
            connection.execute("""
                INSERT INTO part_metadata_version(part_id,version,scope_type,target_id,target_label,shortcode,description,variant,display_name,name_key,occurred_at,actor,reason,request_key)
                VALUES(?,1,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (part_id, scope_type, target_id, target_label.strip(), shortcode["shortcode"], description, variant,
                  actual_name, name_key, now, actor.strip(), reason.strip(), request_key))
            connection.execute("INSERT INTO part_name_index(name_key,display_name,part_id,metadata_version,is_current,added_at) VALUES(?,?,?,?,1,?)",
                               (name_key, actual_name, part_id, 1, now))
            result = {"id": part_id, "partNumber": number, "name": actual_name, "description": description, "variant": variant,
                      "scope": scope_type, "scopeTargetId": target_id, "scopeTarget": target_label.strip(),
                      "engineeringRevision": "A", "status": "active", "metadataVersion": 1,
                      "createdAt": now, "actor": actor.strip(), "reason": reason.strip(), "legacy": False}
            self._write_request(connection, request_key, "create", fingerprint, result)
            connection.commit()
            return {"part": result, "idempotent": False}

    def list_parts(self, *, search: str = "", scope_type: str = "", target_id: str = "", include_retired: bool = True) -> list[dict[str, Any]]:
        clauses = []
        params: list[Any] = []
        if not include_retired:
            clauses.append("p.status='active'")
        if scope_type:
            clauses.append("m.scope_type=?")
            params.append(scope_type)
        if target_id:
            clauses.append("m.target_id=?")
            params.append(target_id)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._connection() as connection:
            rows = connection.execute(f"""
                SELECT p.*, m.scope_type, m.target_id, m.target_label, m.shortcode, m.description, m.variant,
                       m.display_name, m.occurred_at AS metadata_changed_at
                FROM parts p JOIN part_metadata_version m ON m.part_id=p.part_id AND m.version=p.metadata_version
                {where}
                ORDER BY m.scope_type, m.target_label COLLATE NOCASE, m.display_name COLLATE NOCASE
            """, params).fetchall()
            parts = [self._part_payload(connection, row) for row in rows]
        query = normalized_name(search)
        if query:
            parts = [part for part in parts if query in normalized_name(" ".join([part["partNumber"], part["name"], part["description"], part["variant"], *part["aliases"]]))]
        return parts

    def _part_payload(self, connection: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        aliases = [item["display_name"] for item in connection.execute("SELECT display_name FROM part_name_index WHERE part_id=? AND is_current=0 ORDER BY added_at", (row["part_id"],))]
        return {"id": row["part_id"], "partNumber": row["part_number"], "name": row["display_name"],
                "description": row["description"], "variant": row["variant"], "scope": row["scope_type"],
                "scopeTargetId": row["target_id"], "scopeTarget": row["target_label"], "shortcode": row["shortcode"],
                "engineeringRevision": "A", "status": row["status"], "metadataVersion": row["metadata_version"],
                "createdAt": row["created_at"], "actor": row["created_actor"], "reason": row["create_reason"],
                "aliases": aliases, "legacy": False}

    def get_part(self, part_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute("""
                SELECT p.*,m.scope_type,m.target_id,m.target_label,m.shortcode,m.description,m.variant,m.display_name
                FROM parts p JOIN part_metadata_version m ON m.part_id=p.part_id AND m.version=p.metadata_version
                WHERE p.part_id=?
            """, (part_id,)).fetchone()
            if not row:
                return None
            part = self._part_payload(connection, row)
            part["metadataHistory"] = [dict(item) for item in connection.execute("SELECT * FROM part_metadata_version WHERE part_id=? ORDER BY version", (part_id,))]
            part["shortcodeHistory"] = [dict(item) for item in connection.execute("SELECT * FROM part_shortcode_history WHERE scope_type=? AND target_id=? ORDER BY version", (row["scope_type"], row["target_id"]))]
            part["lifecycleHistory"] = [dict(item) for item in connection.execute("SELECT * FROM part_lifecycle_history WHERE part_id=? ORDER BY occurred_at", (part_id,))]
            part["mappingHistory"] = [dict(item) for item in connection.execute("""
                SELECT FileKey,SourceHash,AssociationKey,AssociationVersion,GroupKey,SheetName,SourceRow,SourceDescription,
                       Version,Actor,Reason,OccurredAt,RequestKey
                FROM part_mapping_review WHERE PartIdentity=? ORDER BY OccurredAt,review_id
            """, (part_id,))]
            return part

    def usage_evidence(self, part_id: str) -> dict[str, Any]:
        with self._connection() as connection:
            return self._usage_evidence(connection, part_id)

    @staticmethod
    def _usage_evidence(connection: sqlite3.Connection, part_id: str) -> dict[str, Any]:
        rows = [dict(item) for item in connection.execute("""
            SELECT FileKey,SourceHash,AssociationKey,AssociationVersion,GroupKey,SheetName,SourceRow,SourceDescription,
                   Version,Actor,Reason,OccurredAt,RequestKey
            FROM part_mapping_review WHERE PartIdentity=? ORDER BY FileKey,SourceHash,AssociationVersion,GroupKey,SheetName,SourceRow,Version
        """, (part_id,))]
        return {"items": rows, "fingerprint": request_fingerprint(rows)}

    def update_metadata(self, *, part_id: str, scope_type: str, target_id: str, target_label: str,
                        description: str, variant: str, expected_version: int, expected_name: str,
                        expected_usage_fingerprint: str, actor: str, reason: str, request_key: str) -> dict[str, Any]:
        description = " ".join(unicodedata.normalize("NFKC", description).split())
        variant = " ".join(unicodedata.normalize("NFKC", variant).split())
        if scope_type not in SCOPE_TYPES or not target_id or not target_label.strip():
            raise PartIdentityError("PART_SCOPE_TARGET_INVALID", "Choose a valid sharing scope and an existing scope target.")
        if not description or len(description) > 120 or len(variant) > 120 or not reason.strip() or not actor.strip() or not request_key.strip():
            raise PartIdentityError("PART_INPUT_INVALID", "A Part description, a reason, actor and request key are required.")
        if not expected_usage_fingerprint:
            raise PartIdentityError("PART_USAGE_EVIDENCE_REQUIRED", "Preview the current source mappings before changing Part scope or name.")
        fingerprint = request_fingerprint([part_id, scope_type, target_id, description, variant, expected_version, expected_name, expected_usage_fingerprint, actor, reason.strip()])
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            replay = self._request_replay(connection, request_key, "metadata", fingerprint)
            if replay:
                connection.commit()
                return {"part": replay, "idempotent": True}
            part = connection.execute("SELECT * FROM parts WHERE part_id=?", (part_id,)).fetchone()
            if not part:
                connection.rollback()
                raise PartIdentityError("PART_NOT_FOUND", "The selected Part no longer exists.")
            if part["status"] == "retired":
                connection.rollback()
                raise PartIdentityError("PART_RETIRED", "Retired Parts are searchable for history but cannot be changed.")
            if part["metadata_version"] != expected_version:
                connection.rollback()
                raise PartIdentityError("PART_METADATA_STALE", "The Part metadata changed. Reload the Part before saving.")
            current_metadata = connection.execute(
                "SELECT variant FROM part_metadata_version WHERE part_id=? AND version=?",
                (part_id, expected_version),
            ).fetchone()
            if not current_metadata or current_metadata["variant"] != variant:
                connection.rollback()
                raise PartIdentityError("PART_NEW_IDENTITY_REQUIRED", "A distinct design variant needs a new Part number at Rev A; metadata edits cannot change the physical design.")
            usage = self._usage_evidence(connection, part_id)
            if usage["fingerprint"] != expected_usage_fingerprint:
                connection.rollback()
                raise PartIdentityError("PART_USAGE_STALE", "Part source assignments changed after the metadata preview. Review the affected-use evidence again.")
            shortcode = connection.execute("SELECT * FROM part_scope_shortcode WHERE scope_type=? AND target_id=?", (scope_type, target_id)).fetchone()
            if not shortcode:
                connection.rollback()
                raise PartIdentityError("PART_SHORTCODE_REQUIRED", "Maintain a shortcode for this scope target before changing Part metadata.")
            name = generated_name(shortcode["shortcode"], description, variant)
            if len(name) > 200:
                connection.rollback()
                raise PartIdentityError("PART_INPUT_INVALID", "The generated Part name must be 200 characters or fewer.")
            if name != expected_name:
                connection.rollback()
                raise PartIdentityError("PART_NAME_PREVIEW_STALE", "The generated name changed after preview. Review the current name before saving.")
            key = normalized_name(name)
            conflict = connection.execute("SELECT part_id FROM part_name_index WHERE name_key=?", (key,)).fetchone()
            legacy_conflict = connection.execute("SELECT 1 FROM part_legacy_name_index WHERE name_key=? LIMIT 1", (key,)).fetchone()
            if (conflict and conflict["part_id"] != part_id) or legacy_conflict:
                connection.rollback()
                raise PartIdentityError("PART_NAME_EXISTS", "This name is already used by a current or historical Part name. Choose a meaningful distinction.")
            version = expected_version + 1
            now = utc_now()
            previous = connection.execute("SELECT name_key FROM part_metadata_version WHERE part_id=? AND version=?", (part_id, expected_version)).fetchone()
            if previous:
                connection.execute("UPDATE part_name_index SET is_current=0 WHERE name_key=? AND part_id=?", (previous["name_key"], part_id))
            if conflict and conflict["part_id"] == part_id:
                connection.execute("UPDATE part_name_index SET is_current=1,metadata_version=? WHERE name_key=? AND part_id=?", (version, key, part_id))
            else:
                connection.execute("INSERT INTO part_name_index(name_key,display_name,part_id,metadata_version,is_current,added_at) VALUES(?,?,?,?,1,?)",
                                   (key, name, part_id, version, now))
            connection.execute("""
                INSERT INTO part_metadata_version(part_id,version,scope_type,target_id,target_label,shortcode,description,variant,display_name,name_key,occurred_at,actor,reason,request_key)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (part_id, version, scope_type, target_id, target_label.strip(), shortcode["shortcode"], description, variant,
                  name, key, now, actor.strip(), reason.strip(), request_key))
            connection.execute("UPDATE parts SET metadata_version=? WHERE part_id=?", (version, part_id))
            result = {"id": part_id, "partNumber": part["part_number"], "name": name, "description": description, "variant": variant,
                      "scope": scope_type, "scopeTargetId": target_id, "scopeTarget": target_label.strip(),
                      "engineeringRevision": "A", "status": part["status"], "metadataVersion": version,
                      "createdAt": part["created_at"], "actor": part["created_actor"], "reason": part["create_reason"], "legacy": False}
            self._write_request(connection, request_key, "metadata", fingerprint, result)
            connection.commit()
            return {"part": result, "idempotent": False}

    def retire_part(self, *, part_id: str, expected_version: int, actor: str, reason: str, request_key: str) -> dict[str, Any]:
        if not reason.strip() or not actor.strip() or not request_key.strip():
            raise PartIdentityError("PART_INPUT_REQUIRED", "A reason, actor and Idempotency-Key are required.")
        fingerprint = request_fingerprint([part_id, expected_version, actor, reason.strip()])
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            replay = self._request_replay(connection, request_key, "retire", fingerprint)
            if replay:
                connection.commit()
                return {"part": replay, "idempotent": True}
            part = connection.execute("SELECT * FROM parts WHERE part_id=?", (part_id,)).fetchone()
            if not part:
                connection.rollback()
                raise PartIdentityError("PART_NOT_FOUND", "The selected Part no longer exists.")
            if part["metadata_version"] != expected_version:
                connection.rollback()
                raise PartIdentityError("PART_METADATA_STALE", "The Part metadata changed. Reload the Part before retiring it.")
            if part["status"] == "retired":
                connection.rollback()
                raise PartIdentityError("PART_ALREADY_RETIRED", "This Part is already retired.")
            connection.execute("UPDATE parts SET status='retired' WHERE part_id=?", (part_id,))
            connection.execute("INSERT INTO part_lifecycle_history(event_id,part_id,event_type,status,occurred_at,actor,reason,request_key) VALUES(?,?, 'retired','retired',?,?,?,?)",
                               (str(uuid4()), part_id, utc_now(), actor.strip(), reason.strip(), request_key))
            current = connection.execute("SELECT m.* FROM part_metadata_version m WHERE m.part_id=? AND m.version=?", (part_id, part["metadata_version"])).fetchone()
            result = {"id": part_id, "partNumber": part["part_number"], "name": current["display_name"], "description": current["description"],
                      "variant": current["variant"], "scope": current["scope_type"], "scopeTargetId": current["target_id"],
                      "scopeTarget": current["target_label"], "engineeringRevision": "A", "status": "retired",
                      "metadataVersion": part["metadata_version"], "legacy": False}
            self._write_request(connection, request_key, "retire", fingerprint, result)
            connection.commit()
            return {"part": result, "idempotent": False}


def default_store() -> PartIdentityStore:
    return PartIdentityStore()

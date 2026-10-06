"""Immutable processing lifecycle; costing authority is a separate concern."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from threading import RLock
from typing import Any

from app.domain import utc_now
from app.exceptions import GristError
from app.grist_types import datetime_text, grist_datetime


STATES = ("new", "associated", "extracted", "mapping_review", "reconciliation_review",
          "ready_to_store", "processed", "changes_pending")
TRANSITIONS = {
    "new": {"associated"},
    "associated": {"associated", "extracted", "changes_pending"},
    "extracted": {"mapping_review", "changes_pending"},
    "mapping_review": {"reconciliation_review", "changes_pending"},
    "reconciliation_review": {"mapping_review", "ready_to_store", "changes_pending"},
    "ready_to_store": {"reconciliation_review", "processed", "changes_pending"},
    "processed": {"changes_pending"},
    "changes_pending": {"changes_pending", "associated", "extracted"},
}


class ProcessingConflict(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ProcessingEvent:
    key: str
    file_id: str
    version: int
    from_state: str
    state: str
    source_hash: str
    association_id: str
    association_version: int
    actor: str
    reason: str
    occurred_at: str
    request_key: str
    fingerprint: str


class MemoryProcessingStore:
    available = True

    def __init__(self):
        self.events: dict[str, ProcessingEvent] = {}
        self.lock = RLock()

    def read(self, file_id: str) -> list[ProcessingEvent]:
        return sorted((e for e in self.events.values() if e.file_id == file_id), key=lambda e: e.version)

    def by_request(self, request_key: str) -> ProcessingEvent | None:
        return next((e for e in self.events.values() if e.request_key == request_key), None)

    def append(self, event: ProcessingEvent):
        self.events[event.key] = event


class GristProcessingStore:
    """One append-only row includes transition and audit, so retries are atomic."""
    lock = RLock()

    def __init__(self, client):
        self.client = client
        self.available = True

    def _records(self):
        try:
            result = self.client.fetch_table_records_with_ids("FileProcessingEvent")
            self.available = True
            return result
        except GristError as exc:
            if "404" in str(exc):
                self.available = False
                return []
            raise

    def read(self, file_id: str) -> list[ProcessingEvent]:
        events = [_decode(row["fields"]) for row in self._records() if row["fields"].get("FileKey") == file_id]
        versions = [e.version for e in events]
        if len(set(versions)) != len(versions):
            raise ProcessingConflict("PROCESSING_VERSION_CONFLICT", "Concurrent processing events need review.")
        return sorted(events, key=lambda e: e.version)

    def by_request(self, request_key):
        matches = [r for r in self._records() if r["fields"].get("RequestKey") == request_key]
        if len(matches) > 1:
            raise ProcessingConflict("PROCESSING_REQUEST_CONFLICT", "Duplicate processing request records need review.")
        return _decode(matches[0]["fields"]) if matches else None

    def append(self, event):
        self.client.validate_safari_write_target()
        self._records()
        if not self.available:
            raise ProcessingConflict("SCHEMA_MIGRATION_REQUIRED", "The processing-event schema must be applied before recording a state.")
        self.client.create_table_records("FileProcessingEvent", [{"fields": {
            "EventKey": event.key, "FileKey": event.file_id, "Version": event.version,
            "FromState": event.from_state, "State": event.state, "SourceHash": event.source_hash,
            "AssociationKey": event.association_id, "AssociationVersion": event.association_version,
            "Actor": event.actor, "Reason": event.reason, "OccurredAt": grist_datetime(event.occurred_at),
            "RequestKey": event.request_key, "RequestFingerprint": event.fingerprint,
        }}])


def _decode(f: dict[str, Any]) -> ProcessingEvent:
    return ProcessingEvent(str(f["EventKey"]), str(f["FileKey"]), int(f["Version"]),
        str(f["FromState"]), str(f["State"]), str(f["SourceHash"]), str(f["AssociationKey"]),
        int(f["AssociationVersion"]), str(f["Actor"]), str(f["Reason"]),
        datetime_text(f["OccurredAt"]), str(f["RequestKey"]), str(f["RequestFingerprint"]))


def processing_detail(store, file_id: str, association, source_hash: str) -> dict[str, Any]:
    events = store.read(file_id)
    latest = events[-1] if events else None
    changed = bool(latest and (latest.source_hash != source_hash or not association
        or latest.association_id != association.id or latest.association_version != association.version))
    state = latest.state if latest else "associated" if association else "new"
    return {"state": state, "effectiveState": "changes_pending" if changed else state,
        "version": latest.version if latest else 0, "sourceHash": source_hash,
        "recordedSourceHash": latest.source_hash if latest else None, "sourceChanged": changed,
        "schemaAvailable": store.available, "history": [asdict(e) for e in events],
        "stateBasis": "processing_event" if latest else "saved_association" if association else "unassociated_file",
        "associationKey": association.id if association else None,
        "associationVersion": association.version if association else None,
        "authorityChanged": False,
        "completionGate": {"available": False, "message": "Structural and Model Code configuration checks are not connected yet."}}


def transition(store, *, file_id: str, association, source_hash: str, expected_hash: str,
               expected_version: int, state: str, actor: str, reason: str, request_key: str,
               expected_association_key: str, expected_association_version: int,
               extracted_hash: str | None = None) -> dict[str, Any]:
    fingerprint = sha256(json.dumps([file_id, state, expected_hash, expected_version, expected_association_key, expected_association_version, actor, reason],
                                   ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    with store.lock:
        if not request_key or not reason.strip():
            raise ProcessingConflict("PROCESSING_REASON_REQUIRED", "A reason and request key are required.")
        previous = store.by_request(request_key)
        if previous:
            if previous.fingerprint != fingerprint:
                raise ProcessingConflict("IDEMPOTENCY_KEY_REUSED", "This request key belongs to a different processing change.")
            return {"event": asdict(previous), "idempotent": True}
        detail = processing_detail(store, file_id, association, source_hash)
        if detail["version"] != expected_version or source_hash != expected_hash:
            raise ProcessingConflict("STALE_PROCESSING_REVIEW", "The source or processing state changed; reload before saving.")
        if not association:
            raise ProcessingConflict("ASSOCIATION_REQUIRED", "Save the file association before processing.")
        if association.id != expected_association_key or association.version != expected_association_version:
            raise ProcessingConflict("STALE_PROCESSING_ASSOCIATION", "The association changed; reload processing review before saving.")
        if state not in TRANSITIONS.get(detail["effectiveState"], set()):
            raise ProcessingConflict("INVALID_PROCESSING_TRANSITION", "This transition is not available from the current state.")
        if state in {"ready_to_store", "processed"}:
            raise ProcessingConflict("PROCESSING_GATES_UNAVAILABLE", "Resolve structural and Model Code configuration checks before completing processing.")
        if state == "extracted" and extracted_hash != source_hash:
            raise ProcessingConflict("CURRENT_EXTRACTION_REQUIRED", "An accepted extraction for the selected source revision is required.")
        event = ProcessingEvent(sha256(request_key.encode()).hexdigest(), file_id, expected_version + 1,
            detail["effectiveState"], state, source_hash, association.id, association.version,
            actor, reason.strip(), utc_now(), request_key, fingerprint)
        store.append(event)
        # Detect cross-process duplicate versions; never choose a winner silently.
        store.read(file_id)
        return {"event": asdict(event), "idempotent": False}

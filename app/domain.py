"""Storage-neutral Safari Manufacturing domain records.

The web layer and the local test repository use these records.  Grist column
names deliberately do not appear here; that translation belongs in the Grist
adapter.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DomainRecord:
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Product(DomainRecord):
    id: str
    name: str
    source_row: int | None = None
    source_file: str | None = None
    active: bool = True


@dataclass(frozen=True)
class ProductModel(DomainRecord):
    id: str
    product_id: str
    model_number: str
    name: str = ""
    legacy_spares_only: bool = False
    source_row: int | None = None
    source_file: str | None = None
    active: bool = True


@dataclass(frozen=True)
class ProductModelCode(DomainRecord):
    id: str
    model_id: str
    code: str
    description: str = ""
    legacy_spares_only: bool = False
    source_values: tuple[str, ...] = ()
    source_row: int | None = None
    source_file: str | None = None
    active: bool = True


@dataclass(frozen=True)
class IdentityAlias(DomainRecord):
    id: str
    entity_type: str
    entity_id: str
    value: str
    normalized_value: str
    source: str = ""
    source_row: int | None = None


@dataclass(frozen=True)
class CostingFile(DomainRecord):
    id: str
    relative_path: str
    normalized_path: str
    name: str
    extension: str
    size_bytes: int
    modified_at: str
    file_hash: str | None = None
    product_id: str | None = None
    candidate_classification: str = "unknown"
    mapping_status: str = "unmapped"
    readable: bool | None = None
    parse_error: str | None = None
    source: str = "filesystem"


@dataclass(frozen=True)
class FileObservation(DomainRecord):
    id: str
    file_id: str
    observed_at: str
    relative_path: str
    normalized_path: str
    size_bytes: int
    modified_at: str
    file_hash: str | None = None
    readable: bool | None = None
    sheet_count: int | None = None
    external_reference_count: int | None = None
    parse_error: str | None = None
    source: str = "filesystem"


@dataclass(frozen=True)
class FileModelAssociation(DomainRecord):
    id: str
    file_id: str
    product_id: str
    model_id: str
    actor: str
    reason: str
    created_at: str
    superseded_at: str | None = None
    active: bool = True
    version: int = 1


@dataclass(frozen=True)
class FileCodeAssociation(DomainRecord):
    id: str
    association_id: str
    file_id: str
    code_id: str
    actor: str
    created_at: str
    superseded_at: str | None = None
    active: bool = True


@dataclass(frozen=True)
class ImportBatch(DomainRecord):
    id: str
    source_file: str
    source_hash: str | None
    parser_version: str
    started_at: str
    completed_at: str | None = None
    status: str = "planned"
    outcome: str = ""


@dataclass(frozen=True)
class ReconciliationIssue(DomainRecord):
    id: str
    issue_type: str
    severity: str
    message: str
    source_file: str = ""
    source_row: int | None = None
    entity_id: str | None = None
    status: str = "open"
    created_at: str = field(default_factory=utc_now)


@dataclass(frozen=True)
class AuditEvent(DomainRecord):
    id: str
    event_type: str
    actor: str
    occurred_at: str
    entity_type: str
    entity_id: str
    reason: str = ""
    payload: dict[str, Any] = field(default_factory=dict)

"""Repository contracts and the deterministic in-memory implementation."""

from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from app.domain import (
    AuditEvent,
    CostingFile,
    FileCodeAssociation,
    FileModelAssociation,
    FileObservation,
    ImportBatch,
    IdentityAlias,
    Product,
    ProductModel,
    ProductModelCode,
    ReconciliationIssue,
    utc_now,
)
from app.utils import normalize_text


@dataclass(frozen=True)
class AssociationProposal:
    file_id: str
    product_id: str
    model_id: str
    code_ids: tuple[str, ...]
    actor: str
    reason: str = ""
    expected_version: int | None = None
    expected_hash: str | None = None
    idempotency_key: str | None = None
    supersede: bool = False


@dataclass(frozen=True)
class ValidationError:
    code: str
    message: str
    field: str | None = None
    details: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "field": self.field, "details": self.details or {}}


@dataclass(frozen=True)
class AssociationValidation:
    valid: bool
    errors: tuple[ValidationError, ...] = ()
    warnings: tuple[ValidationError, ...] = ()
    proposal: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "errors": [item.to_dict() for item in self.errors],
            "warnings": [item.to_dict() for item in self.warnings],
            "proposal": self.proposal,
        }


@dataclass(frozen=True)
class AssociationSaveResult:
    association: FileModelAssociation
    codes: tuple[FileCodeAssociation, ...]
    audit_event: AuditEvent
    import_batch: ImportBatch
    idempotent: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "association": self.association.to_dict(),
            "codes": [item.to_dict() for item in self.codes],
            "auditEvent": self.audit_event.to_dict(),
            "importBatch": self.import_batch.to_dict(),
            "idempotent": self.idempotent,
        }


class SafariRepository(Protocol):
    def list_products(self) -> list[Product]: ...
    def list_models(self, product_id: str | None = None) -> list[ProductModel]: ...
    def list_codes(self, model_id: str, include_legacy: bool = False) -> list[ProductModelCode]: ...


class InMemorySafariRepository:
    """Small repository used by tests and local development.

    It intentionally keeps history as records instead of mutating/deleting it.
    The implementation mirrors the cardinality rules used by the Grist
    adapter and is safe to use as a contract test double.
    """

    adapter_name = "in-memory"

    def __init__(self) -> None:
        self.products: dict[str, Product] = {}
        self.models: dict[str, ProductModel] = {}
        self.codes: dict[str, ProductModelCode] = {}
        self.aliases: dict[str, IdentityAlias] = {}
        self.files: dict[str, CostingFile] = {}
        self.observations: list[FileObservation] = []
        self.associations: dict[str, FileModelAssociation] = {}
        self.code_associations: dict[str, FileCodeAssociation] = {}
        self.import_batches: dict[str, ImportBatch] = {}
        self.issues: dict[str, ReconciliationIssue] = {}
        self.audit_events: dict[str, AuditEvent] = {}
        self._idempotency: dict[str, AssociationSaveResult] = {}
        self._idempotency_fingerprints: dict[str, tuple[Any, ...]] = {}

    def add_product(self, item: Product) -> None:
        self.products[item.id] = item

    def add_model(self, item: ProductModel) -> None:
        if item.product_id not in self.products:
            raise ValueError(f"Unknown product {item.product_id}")
        self.models[item.id] = item

    def add_code(self, item: ProductModelCode) -> None:
        if item.model_id not in self.models:
            raise ValueError(f"Unknown model {item.model_id}")
        self.codes[item.id] = item

    def add_file(self, item: CostingFile) -> None:
        self.files[item.id] = item

    def add_observation(self, item: FileObservation) -> None:
        self.observations.append(item)

    def list_products(self) -> list[Product]:
        return sorted((item for item in self.products.values() if item.active), key=lambda item: item.name.casefold())

    def list_models(self, product_id: str | None = None) -> list[ProductModel]:
        items = (item for item in self.models.values() if item.active and (product_id is None or item.product_id == product_id))
        return sorted(items, key=lambda item: (item.model_number.casefold(), item.id))

    def list_codes(self, model_id: str, include_legacy: bool = False) -> list[ProductModelCode]:
        items = (item for item in self.codes.values() if item.active and item.model_id == model_id and (include_legacy or not item.legacy_spares_only))
        return sorted(items, key=lambda item: (item.code.casefold(), item.id))

    def get_file(self, file_id: str) -> CostingFile | None:
        return self.files.get(file_id)

    def current_association(self, file_id: str) -> FileModelAssociation | None:
        return next((item for item in self.associations.values() if item.file_id == file_id and item.active), None)

    def current_code_owner(self, code_id: str) -> FileCodeAssociation | None:
        return next((item for item in self.code_associations.values() if item.code_id == code_id and item.active), None)

    def codes_for_association(self, association_id: str, *, active_only: bool = True) -> list[ProductModelCode]:
        ids = [item.code_id for item in self.code_associations.values() if item.association_id == association_id and (not active_only or item.active)]
        return [self.codes[item] for item in ids if item in self.codes]

    def association_history(self, file_id: str) -> list[dict[str, Any]]:
        associations = sorted((item for item in self.associations.values() if item.file_id == file_id), key=lambda item: item.created_at)
        return [
            {
                "association": item.to_dict(),
                "codes": [code.to_dict() for code in self.codes_for_association(item.id, active_only=False)],
                "auditEvents": [
                    event.to_dict()
                    for event in sorted(
                        (event for event in self.audit_events.values() if event.entity_type == "FileModelAssociation" and event.entity_id == item.id),
                        key=lambda event: event.occurred_at,
                    )
                ],
            }
            for item in associations
        ]

    def validate_association(self, proposal: AssociationProposal, current_file_hash: str | None = None) -> AssociationValidation:
        errors: list[ValidationError] = []
        warnings: list[ValidationError] = []
        file = self.files.get(proposal.file_id)
        product = self.products.get(proposal.product_id)
        model = self.models.get(proposal.model_id)
        if file is None:
            errors.append(ValidationError("FILE_NOT_FOUND", "The costing file is not registered.", "fileId"))
        elif file.readable is False:
            errors.append(ValidationError("FILE_MISSING_OR_UNREADABLE", "The costing file cannot be parsed or read.", "fileId"))
        elif file.extension.casefold() != ".ods":
            errors.append(ValidationError("UNSUPPORTED_EXTENSION", "Only ODS files can be associated as costing workbooks.", "fileId"))
        elif file.candidate_classification in {"archive", "generated_output", "master_or_template", "unsupported"}:
            errors.append(ValidationError("FILE_NOT_ELIGIBLE", "This file is classified as an archive, generated output, master/template, or unsupported source.", "fileId"))
        if product is None:
            errors.append(ValidationError("PRODUCT_NOT_FOUND", "The selected Product does not exist.", "productId"))
        if model is None:
            errors.append(ValidationError("MODEL_NOT_FOUND", "The selected Product Model does not exist.", "modelId"))
        elif model.product_id != proposal.product_id:
            errors.append(ValidationError("MODEL_NOT_IN_PRODUCT", "The selected Model does not belong to the selected Product.", "modelId"))
        code_ids = list(proposal.code_ids)
        if len(set(code_ids)) != len(code_ids):
            errors.append(ValidationError("DUPLICATE_CODE_SELECTION", "A Model Code was selected more than once.", "codeIds"))
        for code_id in code_ids:
            code = self.codes.get(code_id)
            if code is None:
                errors.append(ValidationError("CODE_NOT_FOUND", f"Unknown Model Code {code_id}.", "codeIds"))
            elif code.model_id != proposal.model_id:
                errors.append(ValidationError("CODE_NOT_IN_MODEL", f"Model Code {code.code} does not belong to the selected Model.", "codeIds"))
            elif not code.active:
                errors.append(ValidationError("UNRESOLVED_CATALOG_CODE", f"Model Code {code.code} is unresolved or inactive in the canonical catalog.", "codeIds"))
            elif code.legacy_spares_only:
                errors.append(ValidationError("LEGACY_SPARES_ONLY", f"{code.code} is available only for legacy spares.", "codeIds"))
        if not code_ids:
            errors.append(ValidationError("NO_CODES_SELECTED", "Select at least one active Model Code.", "codeIds"))
        if file is not None and file.file_hash and current_file_hash is None:
            errors.append(ValidationError("FILE_MISSING_OR_UNREADABLE", "The costing file is missing or cannot be read.", "fileId"))
        elif file is not None and current_file_hash and file.file_hash and current_file_hash != file.file_hash:
            errors.append(ValidationError("FILE_CHANGED_SINCE_PREVIEW", "The costing file changed since it was inspected.", "fileId", {"expectedHash": file.file_hash, "actualHash": current_file_hash}))
        if proposal.expected_hash and current_file_hash != proposal.expected_hash:
            errors.append(ValidationError("FILE_CHANGED_SINCE_PREVIEW", "The costing file no longer matches the preview token.", "expectedHash", {"expectedHash": proposal.expected_hash, "actualHash": current_file_hash}))
        current = self.current_association(proposal.file_id)
        if current is not None:
            if proposal.expected_version is not None and proposal.expected_version != current.version:
                errors.append(ValidationError("STALE_ASSOCIATION", "The association changed since it was loaded.", "expectedVersion", {"currentVersion": current.version}))
            active_current_codes = {
                item.code_id for item in self.code_associations.values()
                if item.active and item.association_id == current.id
            }
            changes_existing = current.model_id != proposal.model_id or active_current_codes != set(code_ids)
            if changes_existing and not proposal.supersede:
                code = "FILE_ASSIGNED_TO_DIFFERENT_MODEL" if current.model_id != proposal.model_id else "ASSOCIATION_CHANGE_REQUIRES_SUPERSEDE"
                message = "This file is already assigned to another Model; supersede it explicitly with a reason." if current.model_id != proposal.model_id else "Changing the active code set requires explicit supersede approval and a reason."
                field = "modelId" if current.model_id != proposal.model_id else "codeIds"
                errors.append(ValidationError(code, message, field, {"modelId": current.model_id, "activeCodeIds": sorted(active_current_codes)}))
            if changes_existing and proposal.supersede:
                warnings.append(ValidationError("SUPERSEDES_FILE_MODEL", "Saving will supersede the current file-to-model/code assignment and preserve its history.", "modelId"))
        elif proposal.expected_version not in (None, 0):
            errors.append(ValidationError("STALE_ASSOCIATION", "The expected association version does not exist.", "expectedVersion"))
        for code_id in code_ids:
            owner = self.current_code_owner(code_id)
            if owner is not None and owner.file_id != proposal.file_id:
                if proposal.supersede and proposal.reason.strip():
                    warnings.append(ValidationError("SUPERSEDES_CODE_OWNER", "Saving will supersede the current owner of this code.", "codeIds", {"ownerFileId": owner.file_id}))
                else:
                    errors.append(ValidationError("CODE_ALREADY_ASSOCIATED", "This Model Code is already owned by another active costing file.", "codeIds", {"ownerFileId": owner.file_id, "ownerAssociationId": owner.association_id}))
        if proposal.supersede and not proposal.reason.strip():
            errors.append(ValidationError("REASON_REQUIRED", "A reason is required when superseding an association.", "reason"))
        return AssociationValidation(
            valid=not errors,
            errors=tuple(errors),
            warnings=tuple(warnings),
            proposal={
                "fileId": proposal.file_id,
                "productId": proposal.product_id,
                "modelId": proposal.model_id,
                "codeIds": code_ids,
                "supersede": proposal.supersede,
                "expectedVersion": current.version if current is not None else 0,
                "expectedHash": current_file_hash,
            },
        )

    def save_association(self, proposal: AssociationProposal, current_file_hash: str | None = None) -> AssociationSaveResult:
        if proposal.idempotency_key and proposal.idempotency_key in self._idempotency:
            fingerprint = _association_fingerprint(proposal)
            if self._idempotency_fingerprints.get(proposal.idempotency_key) != fingerprint:
                validation = AssociationValidation(valid=False, errors=(ValidationError("IDEMPOTENCY_KEY_REUSED", "The idempotency key was already used for a different association request.", "Idempotency-Key"),))
                raise AssociationConflict(validation)
            result = self._idempotency[proposal.idempotency_key]
            return replace(result, idempotent=True)
        validation = self.validate_association(proposal, current_file_hash=current_file_hash)
        if not validation.valid:
            raise AssociationConflict(validation)
        now = utc_now()
        previous = self.current_association(proposal.file_id)
        next_version = (previous.version + 1) if previous else 1
        if previous:
            self.associations[previous.id] = replace(previous, active=False, superseded_at=now)
        superseded_code_association_ids = [
            item.id for item in self.code_associations.values()
            if item.active and (item.file_id == proposal.file_id or item.code_id in proposal.code_ids)
        ]
        for code_assoc in list(self.code_associations.values()):
            if code_assoc.id in superseded_code_association_ids:
                self.code_associations[code_assoc.id] = replace(code_assoc, active=False, superseded_at=now)
        association = FileModelAssociation(
            id=f"fma-{uuid4().hex}", file_id=proposal.file_id, product_id=proposal.product_id,
            model_id=proposal.model_id, actor=proposal.actor, reason=proposal.reason,
            created_at=now, version=next_version,
        )
        self.associations[association.id] = association
        code_records = tuple(
            FileCodeAssociation(id=f"fca-{uuid4().hex}", association_id=association.id, file_id=proposal.file_id, code_id=code_id, actor=proposal.actor, created_at=now)
            for code_id in proposal.code_ids
        )
        for record in code_records:
            self.code_associations[record.id] = record
        file = self.files[proposal.file_id]
        self.files[proposal.file_id] = replace(file, mapping_status="mapped", file_hash=current_file_hash or file.file_hash)
        audit = AuditEvent(id=f"audit-{uuid4().hex}", event_type="file_association_saved", actor=proposal.actor, occurred_at=now, entity_type="FileModelAssociation", entity_id=association.id, reason=proposal.reason, payload={"codeIds": list(proposal.code_ids), "supersededAssociationId": previous.id if previous else None, "supersededCodeAssociationIds": superseded_code_association_ids})
        batch = ImportBatch(id=f"batch-{uuid4().hex}", source_file=file.relative_path, source_hash=current_file_hash or file.file_hash, parser_version="association-0.1", started_at=now, completed_at=now, status="queued", outcome="read-only processing queued")
        self.audit_events[audit.id] = audit
        self.import_batches[batch.id] = batch
        result = AssociationSaveResult(association=association, codes=code_records, audit_event=audit, import_batch=batch)
        if proposal.idempotency_key:
            self._idempotency[proposal.idempotency_key] = result
            self._idempotency_fingerprints[proposal.idempotency_key] = _association_fingerprint(proposal)
        return result

    def list_mapped_files(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for file in self.files.values():
            association = self.current_association(file.id)
            model = self.models.get(association.model_id) if association else None
            product = self.products.get(association.product_id) if association else None
            codes = self.codes_for_association(association.id) if association else []
            rows.append({"file": file.to_dict(), "product": product.to_dict() if product else None, "model": model.to_dict() if model else None, "codes": [item.to_dict() for item in codes], "association": association.to_dict() if association else None, "history": self.association_history(file.id)})
        return sorted(rows, key=lambda item: item["file"]["relative_path"].casefold())


class AssociationConflict(ValueError):
    def __init__(self, validation: AssociationValidation) -> None:
        super().__init__("Association proposal is invalid")
        self.validation = validation


def sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _association_fingerprint(proposal: AssociationProposal) -> tuple[Any, ...]:
    """Stable semantic request identity, excluding the idempotency key itself."""
    return (
        proposal.file_id,
        proposal.product_id,
        proposal.model_id,
        tuple(sorted(proposal.code_ids)),
        proposal.actor,
        proposal.reason,
        proposal.expected_version,
        proposal.expected_hash,
        proposal.supersede,
    )

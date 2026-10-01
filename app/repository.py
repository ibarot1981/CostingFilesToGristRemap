"""Repository contracts and the deterministic in-memory implementation."""

from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256
import hashlib
import json
from pathlib import Path
from typing import Any, Protocol, runtime_checkable
from uuid import uuid4

from app.domain import (
    AuditEvent,
    CostingChangeSetItem,
    CostingFile,
    CostingSnapshot,
    DirectoryProductMapping,
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


@runtime_checkable
class SafariRepository(Protocol):
    adapter_name: str
    products: dict[str, Product]
    models: dict[str, ProductModel]
    codes: dict[str, ProductModelCode]
    aliases: dict[str, IdentityAlias]
    files: dict[str, CostingFile]
    observations: list[FileObservation]
    associations: dict[str, FileModelAssociation]
    code_associations: dict[str, FileCodeAssociation]
    import_batches: dict[str, ImportBatch]
    issues: dict[str, ReconciliationIssue]
    audit_events: dict[str, AuditEvent]
    directory_mappings: dict[str, DirectoryProductMapping]
    costing_snapshots: dict[str, CostingSnapshot]
    costing_change_items: dict[str, CostingChangeSetItem]

    def list_products(self) -> list[Product]: ...
    def add_product(self, item: Product) -> None: ...
    def add_model(self, item: ProductModel) -> None: ...
    def add_code(self, item: ProductModelCode) -> None: ...
    def list_models(self, product_id: str | None = None) -> list[ProductModel]: ...
    def list_codes(self, model_id: str, include_legacy: bool = False) -> list[ProductModelCode]: ...
    def get_file(self, file_id: str) -> CostingFile | None: ...
    def add_file(self, item: CostingFile) -> None: ...
    def add_observation(self, item: FileObservation) -> None: ...
    def latest_accepted_costing_snapshot(self, costing_file_id: str) -> CostingSnapshot | None: ...
    def accept_costing_snapshot(self, *, costing_file_id: str, semantic_snapshot: dict[str, Any], changes: list[dict[str, Any]], actor: str, reason: str, expected_previous_snapshot_key: str | None, expected_semantic_hash: str, expected_source_hashes: dict[str, Any], idempotency_key: str) -> dict[str, Any]: ...
    def validate_association(self, proposal: AssociationProposal, current_file_hash: str | None = None) -> AssociationValidation: ...
    def save_association(self, proposal: AssociationProposal, current_file_hash: str | None = None) -> AssociationSaveResult: ...
    def current_association(self, file_id: str) -> FileModelAssociation | None: ...
    def current_code_owner(self, code_id: str) -> FileCodeAssociation | None: ...
    def codes_for_association(self, association_id: str, *, active_only: bool = True) -> list[ProductModelCode]: ...
    def association_history(self, file_id: str) -> list[dict[str, Any]]: ...
    def list_mapped_files(self) -> list[dict[str, Any]]: ...
    def upsert_issue(self, issue: ReconciliationIssue) -> ReconciliationIssue: ...
    def mutate_issue(self, issue_id: str, action: str, *, actor: str, reason: str = "", expected_version: int, assigned_owner: str | None = None, idempotency_key: str | None = None) -> ReconciliationIssue: ...
    def issue_details(self, issue_id: str) -> dict[str, Any] | None: ...
    def accept_file_revision(self, file_id: str, observation: FileObservation, *, issue_id: str, actor: str, reason: str, expected_issue_version: int, expected_stored_hash: str, expected_current_hash: str, idempotency_key: str | None = None) -> dict[str, Any]: ...
    def replay_source_revision(self, idempotency_key: str | None, *, file_id: str, issue_id: str, actor: str, reason: str, expected_issue_version: int, expected_stored_hash: str, expected_current_hash: str) -> dict[str, Any] | None: ...
    def plan_identity_cleanup(self, model_ids: list[str] | None = None) -> list[dict[str, Any]]: ...
    def apply_identity_cleanup(self, model_id: str, canonical_model_id: str, *, issue_id: str, actor: str, reason: str, expected_issue_version: int, idempotency_key: str | None = None) -> dict[str, Any]: ...
    def propose_directory_mapping(self, relative_path: str, product_id: str, *, inherit: bool, actor: str, reason: str = "", idempotency_key: str | None = None) -> DirectoryProductMapping: ...
    def transition_directory_mapping(self, mapping_id: str, action: str, *, actor: str, reason: str, expected_version: int, idempotency_key: str | None = None) -> DirectoryProductMapping: ...
    def effective_directory_mapping(self, relative_path: str) -> dict[str, Any] | None: ...
    def list_processing_queue(self, status: str = "", limit: int = 100) -> list[ImportBatch]: ...


class SafariRepositoryState:
    """Reusable domain behavior shared by memory and durable adapters."""

    adapter_name = "repository-base"

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
        self.directory_mappings: dict[str, DirectoryProductMapping] = {}
        self.costing_snapshots: dict[str, CostingSnapshot] = {}
        self.costing_change_items: dict[str, CostingChangeSetItem] = {}
        self._idempotency: dict[str, AssociationSaveResult] = {}
        self._idempotency_fingerprints: dict[str, tuple[Any, ...]] = {}
        self._issue_mutations: dict[str, tuple[str, ReconciliationIssue]] = {}
        self._identity_mutations: dict[str, tuple[str, dict[str, Any]]] = {}
        self._revision_mutations: dict[str, tuple[str, dict[str, Any]]] = {}
        self._directory_mutations: dict[str, tuple[str, DirectoryProductMapping]] = {}
        self._costing_mutations: dict[str, tuple[str, dict[str, Any]]] = {}

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


class SafariRepositoryBase(SafariRepositoryState):
    """Small repository used by tests and local development.

    It intentionally keeps history as records instead of mutating/deleting it.
    The implementation mirrors the cardinality rules used by the Grist
    adapter and is safe to use as a contract test double.
    """

    adapter_name = "repository-base"

    def __init__(self) -> None:
        super().__init__()

    def add_file(self, item: CostingFile) -> None:
        self.files[item.id] = item

    def add_observation(self, item: FileObservation) -> None:
        if not any(existing.id == item.id for existing in self.observations):
            self.observations.append(item)

    def latest_accepted_costing_snapshot(self, costing_file_id: str) -> CostingSnapshot | None:
        return max(
            (item for item in self.costing_snapshots.values() if item.costing_file_id == costing_file_id and item.status == "accepted"),
            key=lambda item: item.accepted_at,
            default=None,
        )

    def accept_costing_snapshot(
        self,
        *,
        costing_file_id: str,
        semantic_snapshot: dict[str, Any],
        changes: list[dict[str, Any]],
        actor: str,
        reason: str,
        expected_previous_snapshot_key: str | None,
        expected_semantic_hash: str,
        expected_source_hashes: dict[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        file = self.files.get(costing_file_id)
        if file is None:
            raise GovernanceConflict("COSTING_FILE_NOT_REGISTERED", "The selected costing workbook has no registered Safari identity.")
        if actor.strip().casefold() != "irshad":
            raise GovernanceConflict("APPROVER_NOT_AUTHORIZED", "Only Irshad may accept costing reconciliation changes during this milestone.")
        if not reason.strip():
            raise GovernanceConflict("REASON_REQUIRED", "A reason is required to accept a costing snapshot.")
        semantic_hash = str(semantic_snapshot.get("semantic_hash") or "")
        if not semantic_hash or semantic_hash != expected_semantic_hash:
            raise GovernanceConflict("STALE_COSTING_PREVIEW", "The semantic snapshot does not match the reviewed preview.")
        if dict(semantic_snapshot.get("source_hashes", {})) != expected_source_hashes:
            raise GovernanceConflict("STALE_COSTING_PREVIEW", "One or more ODS source revisions changed since the reviewed preview.")
        if any(item.get("classification") == "owner_review_required" for item in changes):
            raise GovernanceConflict("AMBIGUOUS_COSTING_CHANGES_REMAIN", "Resolve all ambiguous line identities before accepting this snapshot.")
        fingerprint = hashlib.sha256(json.dumps({
            "file": costing_file_id,
            "previous": expected_previous_snapshot_key,
            "semantic_hash": semantic_hash,
            "sources": semantic_snapshot.get("source_hashes", {}),
            "actor": actor,
            "reason": reason.strip(),
            "changes": [item.get("change_key") for item in changes],
        }, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
        if idempotency_key in self._costing_mutations:
            saved_fingerprint, saved_result = self._costing_mutations[idempotency_key]
            if saved_fingerprint != fingerprint:
                raise GovernanceConflict("IDEMPOTENCY_KEY_REUSED", "The idempotency key was already used for another costing acceptance.")
            return {**saved_result, "idempotent": True}
        previous = self.latest_accepted_costing_snapshot(costing_file_id)
        actual_previous_key = previous.snapshot_key if previous else None
        if actual_previous_key != expected_previous_snapshot_key:
            raise GovernanceConflict("STALE_COSTING_BASELINE", "The accepted costing baseline changed since this comparison was prepared.")
        source_hashes = dict(semantic_snapshot.get("source_hashes", {}))
        selected_source = semantic_snapshot.get("source_evidence", {}).get("selected_workbook_saved", {})
        if not source_hashes.get("selected_workbook_saved") or source_hashes.get("selected_workbook_saved") != selected_source.get("sha256"):
            raise GovernanceConflict("COSTING_SOURCE_UNVERIFIED", "The selected workbook source hash is missing or inconsistent with the reviewed evidence.")
        if not source_hashes.get("raw_steel") or not source_hashes.get("rate_log_dump"):
            raise GovernanceConflict("COSTING_SOURCE_UNVERIFIED", "The RawSteel and SteelRateLog source revisions must both be pinned before acceptance.")
        if previous and previous.semantic_hash == semantic_hash and previous.source_hashes == source_hashes:
            result = {"snapshot": previous.to_dict(), "changes": [], "auditEvents": [], "idempotent": True, "unchanged": True}
            self._costing_mutations[idempotency_key] = (fingerprint, result)
            return result
        accepted_at = utc_now()
        snapshot_key = "costing:" + hashlib.sha256(json.dumps({
            "file": costing_file_id,
            "previous": actual_previous_key,
            "semantic_hash": semantic_hash,
            "source_hashes": source_hashes,
        }, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
        snapshot = CostingSnapshot(
            id=snapshot_key,
            snapshot_key=snapshot_key,
            costing_file_id=costing_file_id,
            observed_at=str(semantic_snapshot.get("observed_at") or accepted_at),
            semantic_hash=semantic_hash,
            semantic_content=dict(semantic_snapshot.get("content", {})),
            source_hashes=source_hashes,
            previous_snapshot_key=actual_previous_key,
            accepted_at=accepted_at,
            accepted_by=actor.strip(),
            acceptance_reason=reason.strip(),
            request_key=idempotency_key,
        )
        if previous:
            self.costing_snapshots[previous.snapshot_key] = replace(previous, status="superseded")
        self.costing_snapshots[snapshot_key] = snapshot
        updated_file = replace(
            file,
            file_hash=source_hashes["selected_workbook_saved"],
            size_bytes=int(selected_source.get("size_bytes") or file.size_bytes),
            modified_at=str(selected_source.get("modified_at") or file.modified_at),
            readable=True,
            parse_error=None,
        )
        self.files[costing_file_id] = updated_file
        observation = FileObservation(
            id=f"costing-observation:{snapshot_key}",
            file_id=costing_file_id,
            observed_at=str(semantic_snapshot.get("observed_at") or accepted_at),
            relative_path=file.relative_path,
            normalized_path=file.normalized_path,
            size_bytes=updated_file.size_bytes,
            modified_at=updated_file.modified_at,
            file_hash=updated_file.file_hash,
            readable=True,
            sheet_count=int(selected_source.get("sheet_count") or 0) or None,
            external_reference_count=selected_source.get("external_reference_count"),
            parse_error=None,
            source="costing_reconciliation",
        )
        self.add_observation(observation)
        stored_changes: list[CostingChangeSetItem] = []
        audit_events: list[AuditEvent] = []
        for data in changes:
            change_key = str(data.get("change_key") or hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest())
            item_key = hashlib.sha256(f"{snapshot_key}|{change_key}".encode("utf-8")).hexdigest()
            item = CostingChangeSetItem(
                id=f"costing-change:{item_key}",
                change_key=change_key,
                snapshot_key=snapshot_key,
                previous_snapshot_key=actual_previous_key,
                costing_file_id=costing_file_id,
                change_type=str(data.get("change_type") or "semantic_change"),
                classification=str(data.get("classification") or "design_structure_change"),
                change_data=dict(data),
                accepted_at=accepted_at,
                accepted_by=actor.strip(),
                acceptance_reason=reason.strip(),
            )
            self.costing_change_items[item.id] = item
            stored_changes.append(item)
            audit_events.append(self._append_audit(
                "costing_change_accepted",
                actor.strip(),
                str(data.get("reason") or reason.strip()),
                "CostingChangeSetItem",
                item.id,
                {
                    "changeKey": change_key,
                    "changeType": item.change_type,
                    "classification": item.classification,
                    "previousSnapshotKey": actual_previous_key,
                    "snapshotKey": snapshot_key,
                    "previousState": data.get("previous_state"),
                    "currentState": data.get("current_state"),
                    "costImpact": data.get("cost_impact"),
                    "sourceEvidence": data.get("source_evidence"),
                    "crReference": data.get("cr_reference"),
                    "acceptanceReason": reason.strip(),
                },
            ))
        snapshot_event = self._append_audit(
            "costing_snapshot_accepted",
            actor.strip(),
            reason.strip(),
            "CostingSnapshot",
            snapshot_key,
            {
                "costingFileId": costing_file_id,
                "previousSnapshotKey": actual_previous_key,
                "semanticHash": semantic_hash,
                "sourceHashes": source_hashes,
                "changeCount": len(stored_changes),
            },
        )
        audit_events.append(snapshot_event)
        result = {
            "snapshot": snapshot.to_dict(),
            "changes": [item.to_dict() for item in stored_changes],
            "auditEvents": [item.to_dict() for item in audit_events],
            "observation": observation.to_dict(),
            "idempotent": False,
            "unchanged": False,
        }
        self._costing_mutations[idempotency_key] = (fingerprint, result)
        return result

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

    def upsert_issue(self, issue: ReconciliationIssue) -> ReconciliationIssue:
        fingerprint = issue.fingerprint or _issue_fingerprint(issue)
        existing = next((item for item in self.issues.values() if item.fingerprint == fingerprint), None)
        now = issue.last_seen_at or utc_now()
        if existing is None:
            saved = replace(issue, fingerprint=fingerprint, first_seen_at=issue.first_seen_at or now, last_seen_at=now)
            self.issues[saved.id] = saved
            return saved
        if existing.status in {"resolved", "deferred"}:
            now = issue.last_seen_at or utc_now()
            saved = replace(existing, status="reopened", last_seen_at=now, detected_facts=issue.detected_facts, proposed_resolution=issue.proposed_resolution, message=issue.message, version=existing.version + 1, reopened_actor="system:reconciliation", reopened_at=now, resolution_action="reopened", resolution_reason="Condition was detected again during a reconciliation scan.")
            self.issues[existing.id] = saved
            self._append_audit("reconciliation_issue_reopened", "system:reconciliation", "Condition was detected again during a reconciliation scan.", "ReconciliationIssue", saved.id, {"fingerprint": fingerprint})
            return saved
        changed = (existing.message, existing.severity, existing.detected_facts, existing.proposed_resolution) != (issue.message, issue.severity, issue.detected_facts, issue.proposed_resolution)
        saved = replace(existing, message=issue.message, severity=issue.severity, source_file=issue.source_file or existing.source_file, source_row=issue.source_row or existing.source_row, source_path=issue.source_path or existing.source_path, source_cell=issue.source_cell or existing.source_cell, detected_facts=issue.detected_facts, proposed_resolution=issue.proposed_resolution, last_seen_at=now, version=existing.version + (1 if changed else 0), costing_file_id=issue.costing_file_id or existing.costing_file_id, entity_type=issue.entity_type or existing.entity_type, entity_id=issue.entity_id or existing.entity_id)
        self.issues[existing.id] = saved
        return saved

    def mutate_issue(self, issue_id: str, action: str, *, actor: str, reason: str = "", expected_version: int, assigned_owner: str | None = None, idempotency_key: str | None = None) -> ReconciliationIssue:
        current = self.issues.get(issue_id)
        if current is None:
            raise GovernanceConflict("ISSUE_NOT_FOUND", "Reconciliation issue was not found.")
        fingerprint = _mutation_fingerprint(issue_id, action, actor, reason, assigned_owner, expected_version)
        if idempotency_key and idempotency_key in self._issue_mutations:
            prior_fingerprint, prior_issue = self._issue_mutations[idempotency_key]
            if prior_fingerprint != fingerprint:
                raise GovernanceConflict("IDEMPOTENCY_KEY_REUSED", "The idempotency key was already used for another issue mutation.", current)
            return prior_issue
        if current.version != expected_version:
            raise GovernanceConflict("STALE_ISSUE_VERSION", "The issue changed since it was loaded.", current)
        if action in {"resolve", "defer", "reopen", "keep_open"} and not reason.strip():
            raise GovernanceConflict("REASON_REQUIRED", f"A reason is required to {action.replace('_', ' ')} an issue.", current)
        allowed_statuses = {
            "resolve": {"open", "in_review", "reopened"},
            "defer": {"open", "in_review", "reopened"},
            "reopen": {"resolved", "deferred"},
            "keep_open": {"open", "in_review", "reopened"},
            "assign": {"open", "in_review", "deferred", "resolved", "reopened"},
            "unassign": {"open", "in_review", "deferred", "resolved", "reopened"},
        }
        if action in allowed_statuses and current.status not in allowed_statuses[action]:
            raise GovernanceConflict("INVALID_ISSUE_TRANSITION", f"Cannot {action.replace('_', ' ')} an issue in status {current.status!r}.", current)
        now = utc_now()
        if action == "assign":
            updated = replace(current, assigned_owner=(assigned_owner or "").strip() or None, version=current.version + 1)
        elif action == "unassign":
            updated = replace(current, assigned_owner=None, version=current.version + 1)
        elif action == "defer":
            updated = replace(current, status="deferred", resolution_action="defer", resolution_reason=reason.strip(), deferred_actor=actor, deferred_at=now, version=current.version + 1)
        elif action == "resolve":
            updated = replace(current, status="resolved", resolution_action="resolve", resolution_reason=reason.strip(), resolved_actor=actor, resolved_at=now, version=current.version + 1)
        elif action == "reopen":
            updated = replace(current, status="reopened", resolution_action="reopen", resolution_reason=reason.strip(), reopened_actor=actor, reopened_at=now, resolved_actor=None, resolved_at=None, deferred_actor=None, deferred_at=None, version=current.version + 1)
        elif action == "keep_open":
            updated = replace(current, status="in_review", resolution_action="keep_open", resolution_reason=reason.strip(), version=current.version + 1)
        else:
            raise GovernanceConflict("INVALID_ISSUE_ACTION", f"Unsupported issue action: {action}.", current)
        self.issues[issue_id] = updated
        self._append_audit(f"reconciliation_issue_{action}", actor, reason.strip(), "ReconciliationIssue", issue_id, {"previousStatus": current.status, "status": updated.status, "version": updated.version})
        if idempotency_key:
            self._issue_mutations[idempotency_key] = (fingerprint, updated)
        return updated

    def issue_details(self, issue_id: str) -> dict[str, Any] | None:
        issue = self.issues.get(issue_id)
        if issue is None:
            return None
        file_id = issue.costing_file_id or (issue.entity_id if issue.entity_type == "CostingFile" else None)
        related_file_ids = list(issue.detected_facts.get("ownerFileIds", []))
        if file_id and file_id not in related_file_ids:
            related_file_ids.insert(0, file_id)
        return {"issue": issue.to_dict(), "observations": [item.to_dict() for item in self.observations if item.file_id in related_file_ids], "associationHistory": self.association_history(file_id) if file_id else [], "relatedFileHistories": [{"fileId": related_id, "relativePath": self.files[related_id].relative_path if related_id in self.files else "", "history": self.association_history(related_id)} for related_id in related_file_ids], "auditTrail": [item.to_dict() for item in sorted(self.audit_events.values(), key=lambda row: row.occurred_at) if item.entity_id in {issue_id, issue.entity_id, *related_file_ids}]}

    def accept_file_revision(self, file_id: str, observation: FileObservation, *, issue_id: str, actor: str, reason: str, expected_issue_version: int, expected_stored_hash: str, expected_current_hash: str, idempotency_key: str | None = None) -> dict[str, Any]:
        request_fingerprint = _mutation_fingerprint(file_id, issue_id, actor, reason, expected_issue_version, expected_stored_hash, expected_current_hash)
        if idempotency_key and idempotency_key in self._revision_mutations:
            saved_fingerprint, saved_result = self._revision_mutations[idempotency_key]
            if saved_fingerprint != request_fingerprint:
                raise GovernanceConflict("IDEMPOTENCY_KEY_REUSED", "The idempotency key was already used for another source revision.")
            return saved_result
        issue = self.issues.get(issue_id)
        file = self.files.get(file_id)
        if issue is None or file is None:
            raise GovernanceConflict("REVISION_TARGET_MISSING", "The revision issue or costing file no longer exists.", issue)
        if issue.version != expected_issue_version:
            raise GovernanceConflict("STALE_ISSUE_VERSION", "The revision issue changed since preview.", issue)
        if not reason.strip():
            raise GovernanceConflict("REASON_REQUIRED", "A reason is required to accept a source revision.", issue)
        latest = max((item for item in self.observations if item.file_id == file_id), key=lambda item: item.observed_at, default=None)
        stored_hash = latest.file_hash if latest else file.file_hash
        if stored_hash != expected_stored_hash:
            raise GovernanceConflict("STALE_REVISION_FACTS", "The stored source fingerprint changed since preview.", issue)
        if observation.file_hash != expected_current_hash:
            raise GovernanceConflict("STALE_REVISION_FACTS", "The source file no longer matches the reviewed preview.", issue)
        if actor.strip().casefold() != "irshad":
            raise GovernanceConflict("APPROVER_NOT_AUTHORIZED", "Only Irshad may accept source revisions during Phase 0.", issue)
        if not observation.file_hash or observation.readable is False:
            raise GovernanceConflict("REVISION_NOT_READABLE", "An unreadable or unhashed workbook revision cannot be accepted.", issue)
        active_association = self.current_association(file_id)
        if active_association is None or not self._association_codes_still_valid(active_association):
            raise GovernanceConflict("ASSOCIATION_OWNERSHIP_CHANGED", "Current Model Code ownership no longer matches the reviewed association.", issue)
        self.add_observation(observation)
        self.files[file_id] = replace(file, file_hash=observation.file_hash, size_bytes=observation.size_bytes, modified_at=observation.modified_at, readable=observation.readable, parse_error=observation.parse_error)
        now = utc_now()
        resolved = replace(issue, status="resolved", resolution_action="accept_source_revision", resolution_reason=reason.strip(), resolved_actor=actor, resolved_at=now, version=issue.version + 1, detected_facts={**issue.detected_facts, "acceptedHash": observation.file_hash})
        self.issues[issue_id] = resolved
        event = self._append_audit("source_revision_accepted", actor, reason.strip(), "CostingFile", file_id, {"issueId": issue_id, "oldHash": stored_hash, "newHash": observation.file_hash, "associationId": active_association.id})
        result = {"file": self.files[file_id].to_dict(), "observation": observation.to_dict(), "issue": resolved.to_dict(), "auditEvent": event.to_dict()}
        if idempotency_key:
            self._revision_mutations[idempotency_key] = (request_fingerprint, result)
        return result

    def replay_source_revision(self, idempotency_key: str | None, *, file_id: str, issue_id: str, actor: str, reason: str, expected_issue_version: int, expected_stored_hash: str, expected_current_hash: str) -> dict[str, Any] | None:
        if not idempotency_key:
            return None
        fingerprint = _mutation_fingerprint(file_id, issue_id, actor, reason, expected_issue_version, expected_stored_hash, expected_current_hash)
        prior = self._revision_mutations.get(idempotency_key)
        if prior is None:
            return None
        if prior[0] != fingerprint:
            raise GovernanceConflict("IDEMPOTENCY_KEY_REUSED", "The idempotency key was already used for another source revision.")
        return prior[1]

    def _association_codes_still_valid(self, association: FileModelAssociation) -> bool:
        active_links = [item for item in self.code_associations.values() if item.active and item.association_id == association.id]
        if not active_links:
            return False
        for link in active_links:
            code = self.codes.get(link.code_id)
            if code is None or not code.active or code.model_id != association.model_id:
                return False
            owners = [item for item in self.code_associations.values() if item.active and item.code_id == link.code_id]
            if len(owners) != 1 or owners[0].file_id != association.file_id:
                return False
        return True

    def plan_identity_cleanup(self, model_ids: list[str] | None = None) -> list[dict[str, Any]]:
        selected = set(model_ids or [])
        plans = []
        for model in self.models.values():
            if selected and model.id not in selected:
                continue
            if not model.active or "\ufffd" not in f"{model.model_number} {model.name}":
                continue
            canonical_text = f"{model.model_number} {model.name}".replace("\ufffd", "–")
            canonical = next((item for item in self.models.values() if item.id != model.id and item.active and item.product_id == model.product_id and f"{item.model_number} {item.name}" == canonical_text), None)
            codes = [item for item in self.codes.values() if item.model_id == model.id]
            associations = [item for item in self.associations.values() if item.model_id == model.id]
            code_ids = {code.id for code in codes}
            aliases = [item.id for item in self.aliases.values() if item.entity_type.casefold() in {"productmodel", "model"} and item.entity_id == model.id]
            refs = {
                "codes": [item.id for item in codes],
                "associations": [item.id for item in associations],
                "activeCodeAssociations": [item.id for item in self.code_associations.values() if item.active and item.code_id in code_ids],
                "aliases": aliases,
                # Directory mappings refer to Products, not Product Models;
                # the explicit empty list proves there is no direct relation.
                "directoryMappings": [],
            }
            issue = next((item for item in self.issues.values() if item.issue_type == "invalid_identity_encoding" and item.entity_id == model.id), None)
            plans.append({"model": model.to_dict(), "canonicalReplacement": canonical.to_dict() if canonical else None, "issue": issue.to_dict() if issue else None, "references": refs, "canApply": canonical is not None and issue is not None and not any(refs.values())})
        return plans

    def apply_identity_cleanup(self, model_id: str, canonical_model_id: str, *, issue_id: str, actor: str, reason: str, expected_issue_version: int, idempotency_key: str | None = None) -> dict[str, Any]:
        if actor.strip().casefold() != "irshad":
            raise GovernanceConflict("APPROVER_NOT_AUTHORIZED", "Only Irshad may approve identity cleanup during Phase 0.")
        fingerprint = _mutation_fingerprint(model_id, canonical_model_id, issue_id, actor, reason, expected_issue_version)
        if idempotency_key and idempotency_key in self._identity_mutations:
            saved_fingerprint, result = self._identity_mutations[idempotency_key]
            if saved_fingerprint != fingerprint:
                raise GovernanceConflict("IDEMPOTENCY_KEY_REUSED", "The idempotency key was already used for a different cleanup action.")
            return result
        plan = next((item for item in self.plan_identity_cleanup([model_id]) if item["model"]["id"] == model_id), None)
        if plan is None or not plan["canApply"] or (plan["canonicalReplacement"] or {}).get("id") != canonical_model_id:
            raise GovernanceConflict("IDENTITY_CLEANUP_REFERENCED", "The corrupted model has governed references or no verified canonical replacement.")
        issue = self.issues.get(issue_id)
        if issue is None or issue.version != expected_issue_version:
            raise GovernanceConflict("STALE_ISSUE_VERSION", "The identity issue changed since preview.", issue)
        if not reason.strip():
            raise GovernanceConflict("REASON_REQUIRED", "A reason is required to supersede an identity.", issue)
        model = self.models[model_id]
        now = utc_now()
        self.models[model_id] = replace(model, active=False, superseded_by_id=canonical_model_id, superseded_at=now)
        self.issues[issue_id] = replace(issue, status="resolved", resolution_action="supersede_corrupted_identity", resolution_reason=reason.strip(), resolved_actor=actor, resolved_at=now, version=issue.version + 1, detected_facts={**issue.detected_facts, "previousValue": model.model_number, "canonicalReplacementId": canonical_model_id})
        event = self._append_audit("identity_superseded", actor, reason.strip(), "ProductModel", model_id, {"previousValue": model.model_number, "canonicalReplacementId": canonical_model_id, "sourceFile": model.source_file, "sourceRow": model.source_row, "issueId": issue_id})
        result = {"model": self.models[model_id].to_dict(), "canonicalReplacement": self.models[canonical_model_id].to_dict(), "issue": self.issues[issue_id].to_dict(), "auditEvent": event.to_dict()}
        if idempotency_key:
            self._identity_mutations[idempotency_key] = (fingerprint, result)
        return result

    def propose_directory_mapping(self, relative_path: str, product_id: str, *, inherit: bool, actor: str, reason: str = "", idempotency_key: str | None = None) -> DirectoryProductMapping:
        path, normalized = normalize_relative_directory(relative_path)
        fingerprint = _mutation_fingerprint("propose", normalized, product_id, inherit, actor, reason)
        if idempotency_key and idempotency_key in self._directory_mutations:
            prior_fingerprint, prior = self._directory_mutations[idempotency_key]
            if prior_fingerprint != fingerprint:
                raise GovernanceConflict("IDEMPOTENCY_KEY_REUSED", "The idempotency key was already used for another directory mapping proposal.", prior)
            return prior
        if product_id not in self.products or not self.products[product_id].active:
            raise GovernanceConflict("PRODUCT_NOT_FOUND", "The selected Product is unavailable.")
        current = [item for item in self.directory_mappings.values() if item.normalized_path == normalized and item.status in {"proposed", "approved"}]
        version = max((item.version for item in current), default=0) + 1
        now = utc_now()
        mapping = DirectoryProductMapping(f"directory-mapping:{uuid4().hex}", path, normalized, product_id, inherit, "proposed", actor, reason=reason.strip(), created_at=now, updated_at=now, version=version, supersedes_id=current[0].id if current else None)
        self.directory_mappings[mapping.id] = mapping
        self._append_audit("directory_product_mapping_proposed", actor, reason.strip(), "DirectoryProductMapping", mapping.id, {"path": path, "productId": product_id, "inherit": inherit})
        if idempotency_key:
            self._directory_mutations[idempotency_key] = (fingerprint, mapping)
        return mapping

    def transition_directory_mapping(self, mapping_id: str, action: str, *, actor: str, reason: str, expected_version: int, idempotency_key: str | None = None) -> DirectoryProductMapping:
        current = self.directory_mappings.get(mapping_id)
        if current is None:
            raise GovernanceConflict("DIRECTORY_MAPPING_NOT_FOUND", "Directory mapping was not found.")
        fingerprint = _mutation_fingerprint("transition", mapping_id, action, actor, reason, expected_version)
        if idempotency_key and idempotency_key in self._directory_mutations:
            prior_fingerprint, prior = self._directory_mutations[idempotency_key]
            if prior_fingerprint != fingerprint:
                raise GovernanceConflict("IDEMPOTENCY_KEY_REUSED", "The idempotency key was already used for another directory mapping action.", current)
            return self.directory_mappings.get(prior.id, prior)
        if current.version != expected_version:
            raise GovernanceConflict("STALE_MAPPING_VERSION", "The directory mapping changed since it was loaded.", current)
        if action in {"approve", "reject", "supersede"} and not reason.strip():
            raise GovernanceConflict("REASON_REQUIRED", f"A reason is required to {action} a directory mapping.", current)
        if action == "approve" and actor.strip().casefold() != "irshad":
            raise GovernanceConflict("APPROVER_NOT_AUTHORIZED", "Only Irshad may approve directory mappings during Phase 0.", current)
        statuses = {"approve": "approved", "reject": "rejected", "supersede": "superseded"}
        if action not in statuses or current.status != "proposed":
            raise GovernanceConflict("INVALID_MAPPING_TRANSITION", f"Cannot {action} a {current.status} directory mapping.", current)
        now = utc_now()
        updated = replace(current, status=statuses[action], approver=actor if action == "approve" else current.approver, reason=reason.strip(), updated_at=now, version=current.version + 1)
        self.directory_mappings[mapping_id] = updated
        if action == "approve":
            for previous in list(self.directory_mappings.values()):
                if previous.id != mapping_id and previous.normalized_path == current.normalized_path and previous.status == "approved":
                    self.directory_mappings[previous.id] = replace(previous, status="superseded", reason=f"Superseded by {mapping_id}: {reason.strip()}", updated_at=now, version=previous.version + 1)
        self._append_audit(f"directory_product_mapping_{action}d" if action != "reject" else "directory_product_mapping_rejected", actor, reason.strip(), "DirectoryProductMapping", mapping_id, {"status": updated.status, "version": updated.version})
        if idempotency_key:
            self._directory_mutations[idempotency_key] = (fingerprint, updated)
        return updated

    def effective_directory_mapping(self, relative_path: str) -> dict[str, Any] | None:
        _, path = normalize_relative_directory(relative_path)
        candidates: list[tuple[int, DirectoryProductMapping, bool]] = []
        for mapping in self.directory_mappings.values():
            if mapping.status != "approved":
                continue
            mp = mapping.normalized_path
            if path == mp:
                candidates.append((len(mp.split("/")) if mp else 0, mapping, False))
            elif mapping.inherit and (not mp or path.startswith(mp + "/")):
                candidates.append((len(mp.split("/")) if mp else 0, mapping, True))
        if not candidates:
            return None
        _, selected, inherited = max(candidates, key=lambda item: (item[0], item[1].updated_at))
        return {"mapping": selected.to_dict(), "product": self.products[selected.product_id].to_dict(), "inherited": inherited}

    def list_processing_queue(self, status: str = "", limit: int = 100) -> list[ImportBatch]:
        items = [item for item in self.import_batches.values() if item.parser_version.casefold().startswith("association-") and (not status or item.status.casefold() == status.casefold())]
        return sorted(items, key=lambda item: (item.started_at, item.id), reverse=True)[:limit]

    def _append_audit(self, event_type: str, actor: str, reason: str, entity_type: str, entity_id: str, payload: dict[str, Any]) -> AuditEvent:
        event = AuditEvent(f"audit-{uuid4().hex}", event_type, actor, utc_now(), entity_type, entity_id, reason, payload)
        self.audit_events[event.id] = event
        return event


class InMemorySafariRepository(SafariRepositoryBase):
    """Test and local-development adapter backed only by process memory."""

    adapter_name = "in-memory"


class AssociationConflict(ValueError):
    def __init__(self, validation: AssociationValidation) -> None:
        super().__init__("Association proposal is invalid")
        self.validation = validation


class GovernanceConflict(ValueError):
    def __init__(self, code: str, message: str, current: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.current = current


def normalize_relative_directory(value: str) -> tuple[str, str]:
    from urllib.parse import unquote
    raw = str(value or "")
    for _ in range(3):
        decoded = unquote(raw)
        if decoded == raw:
            break
        raw = decoded
    raw = raw.replace("\\", "/").strip()
    if "\x00" in raw:
        raise GovernanceConflict("PATH_OUTSIDE_ROOT", "Directory mappings cannot contain a NUL character.")
    if raw.startswith("/") or (len(raw) >= 2 and raw[1] == ":") or any(part == ".." for part in raw.split("/")):
        raise GovernanceConflict("PATH_OUTSIDE_ROOT", "Directory mappings must use a relative path inside the configured costing root.")
    parts = [part for part in raw.split("/") if part not in {"", "."}]
    normalized = "/".join(parts).casefold()
    return "/".join(parts), normalized


def _issue_fingerprint(issue: ReconciliationIssue) -> str:
    payload = "\0".join((issue.issue_type, issue.entity_type, issue.entity_id or "", issue.costing_file_id or "", issue.source_path.casefold(), str(issue.source_row or ""), issue.source_cell.casefold()))
    return sha256(payload.encode("utf-8")).hexdigest()


def _mutation_fingerprint(*values: Any) -> str:
    return sha256(repr(values).encode("utf-8")).hexdigest()


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

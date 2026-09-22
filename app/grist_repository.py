"""Safari Manufacturing Grist repository adapter.

This adapter is intentionally not selected by normal API startup.  It is
constructed only when the explicit Safari document settings are present.
"""

from __future__ import annotations

import hashlib
import json
from threading import RLock
from typing import Any

from app.domain import AuditEvent, CostingFile, FileCodeAssociation, FileModelAssociation, FileObservation, IdentityAlias, ImportBatch, Product, ProductModel, ProductModelCode, ReconciliationIssue, utc_now
from app.exceptions import GristValidationError
from app.grist import GristClient
from app.grist_types import datetime_text as _datetime_text, grist_datetime as _grist_datetime
from app.repository import AssociationConflict, AssociationProposal, AssociationSaveResult, AssociationValidation, InMemorySafariRepository, ValidationError


TABLE_PRODUCT = "Product"
TABLE_MODEL = "ProductModel"
TABLE_CODE = "ProductModelCode"


class GristSafariRepository(InMemorySafariRepository):
    adapter_name = "grist-safari"

    def __init__(self, client: GristClient | None = None) -> None:
        super().__init__()
        self.client = client or GristClient.from_safari_environment()
        self._grist_file_ids: dict[str, int] = {}
        self._grist_association_ids: dict[str, int] = {}
        self._grist_code_association_ids: dict[str, int] = {}
        self._association_write_lock = RLock()
        self.refresh_identity()

    def save_association(self, proposal: AssociationProposal, current_file_hash: str | None = None) -> AssociationSaveResult:
        with self._association_write_lock:
            return self._save_association(proposal, current_file_hash)

    def validate_association(self, proposal: AssociationProposal, current_file_hash: str | None = None) -> AssociationValidation:
        """Validate against current Grist ownership, not a startup snapshot."""
        with self._association_write_lock:
            self._refresh_preserving_file(proposal.file_id)
            return super().validate_association(proposal, current_file_hash=current_file_hash)

    def _save_association(self, proposal: AssociationProposal, current_file_hash: str | None = None) -> AssociationSaveResult:
        """Persist a validated association and its governance records in Grist.

        Identity IDs are Grist record IDs after ``refresh_identity``.  File and
        association IDs are created here and never sent to the browser as Grist
        table/column identifiers.
        """
        self.client.validate_safari_write_target()
        self._refresh_preserving_file(proposal.file_id)
        fingerprint = _request_fingerprint(proposal)
        if proposal.idempotency_key:
            existing = self._find_request_record("FileModelAssociation", proposal.idempotency_key)
            if existing is not None:
                self._assert_request_fingerprint(existing, fingerprint)
                result = self._result_for_existing_association(existing, proposal)
                self._persist_association(result, proposal, proposal.idempotency_key, fingerprint)
                return self._reload_result(proposal.idempotency_key, proposal, idempotent=True)
            # A prior in-process result is not authoritative if Grist has no
            # corresponding request row. Force a fresh validation after an
            # uncertain pre-association failure instead of bypassing it via the
            # in-memory idempotency cache.
            self._idempotency.pop(proposal.idempotency_key, None)
            self._idempotency_fingerprints.pop(proposal.idempotency_key, None)

        result = super().save_association(proposal, current_file_hash=current_file_hash)
        request_key = proposal.idempotency_key or result.association.id
        self._persist_association(result, proposal, request_key, fingerprint)
        return self._reload_result(request_key, proposal, idempotent=result.idempotent)

    def _refresh_preserving_file(self, file_id: str) -> None:
        """Reload shared Grist state while retaining the just-inspected file."""
        request_file = self.files.get(file_id)
        request_observations = [item for item in self.observations if item.file_id == file_id]
        self.refresh_identity()
        if request_file is not None:
            self.files[file_id] = request_file
        known_observation_ids = {item.id for item in self.observations}
        self.observations.extend(item for item in request_observations if item.id not in known_observation_ids)

    def _persist_association(self, result: AssociationSaveResult, proposal: AssociationProposal, request_key: str, fingerprint: str) -> None:
        file_record = self.files[proposal.file_id]
        grist_file_id = self._ensure_file_record(file_record)
        association_fields = {
            "CostingFile": grist_file_id,
            "Product": int(proposal.product_id),
            "ProductModel": int(proposal.model_id),
            "Actor": proposal.actor,
            "Reason": proposal.reason,
            "CreatedAt": _grist_datetime(result.association.created_at),
            "Active": True,
            "Version": result.association.version,
            "RequestKey": request_key,
            "RequestFingerprint": fingerprint,
        }
        association_record = self._find_request_record("FileModelAssociation", request_key)
        if association_record is not None:
            self._assert_request_fingerprint(association_record, fingerprint)
            grist_association_id = int(association_record["id"])
        else:
            created = self.client.create_table_records("FileModelAssociation", [{"fields": association_fields}])
            if not created or created[0].get("id") is None:
                raise RuntimeError("Grist did not return the created FileModelAssociation record ID")
            grist_association_id = int(created[0]["id"])
        self._grist_association_ids[result.association.id] = grist_association_id

        existing_links = self.client.fetch_table_records_with_ids("FileCodeAssociation")
        existing_pairs = {
            (_ref(item.get("fields", {}).get("Association")), _ref(item.get("fields", {}).get("ProductModelCode")))
            for item in existing_links
        }
        missing_code_records = [
            {"fields": {"Association": grist_association_id, "CostingFile": grist_file_id, "ProductModelCode": int(code_id), "Actor": proposal.actor, "CreatedAt": _grist_datetime(result.association.created_at), "Active": True}}
            for code_id in proposal.code_ids
            if (str(grist_association_id), str(code_id)) not in existing_pairs
        ]
        if missing_code_records:
            self.client.create_table_records("FileCodeAssociation", missing_code_records)

        latest_observation = max((item for item in self.observations if item.file_id == proposal.file_id), key=lambda item: item.observed_at, default=None)
        observation_fields: dict[str, Any] = {"CostingFile": grist_file_id, "ObservedAt": _grist_datetime(latest_observation.observed_at if latest_observation else result.association.created_at), "RelativePath": latest_observation.relative_path if latest_observation else file_record.relative_path, "NormalizedPath": latest_observation.normalized_path if latest_observation else file_record.normalized_path, "SizeBytes": latest_observation.size_bytes if latest_observation else file_record.size_bytes, "ModifiedAt": _grist_datetime(latest_observation.modified_at if latest_observation else file_record.modified_at), "FileHash": (latest_observation.file_hash if latest_observation else file_record.file_hash) or "", "ParseError": (latest_observation.parse_error if latest_observation else file_record.parse_error) or ""}
        if latest_observation is not None:
            if latest_observation.readable is not None:
                observation_fields["Readable"] = latest_observation.readable
            if latest_observation.sheet_count is not None:
                observation_fields["SheetCount"] = latest_observation.sheet_count
            if latest_observation.external_reference_count is not None:
                observation_fields["ExternalReferenceCount"] = latest_observation.external_reference_count
        elif file_record.readable is not None:
            observation_fields["Readable"] = file_record.readable
        observations = self.client.fetch_table_records_with_ids("FileObservation")
        observation_exists = any(
            _ref(item.get("fields", {}).get("CostingFile")) == str(grist_file_id)
            and _datetime_text(item.get("fields", {}).get("ObservedAt")) == _datetime_text(observation_fields.get("ObservedAt"))
            for item in observations
        )
        if not observation_exists:
            self.client.create_table_records("FileObservation", [{"fields": observation_fields}])

        superseded_associations, superseded_code_associations = self._deactivate_prior_records(
            grist_file_id, grist_association_id, proposal.code_ids, result.association.created_at
        )
        audit_payload = {
            "codeIds": list(proposal.code_ids),
            "supersededAssociationId": superseded_associations[0] if superseded_associations else None,
            "supersededCodeAssociationIds": superseded_code_associations,
        }
        self._ensure_request_record("AuditEvent", request_key, fingerprint, {
            "EventType": "file_association_saved", "Actor": proposal.actor,
            "OccurredAt": _grist_datetime(result.association.created_at), "EntityType": "FileModelAssociation",
            "EntityId": f"fma:{grist_association_id}", "Reason": proposal.reason,
            "Payload": json.dumps(audit_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
        })
        self._ensure_request_record("ImportBatch", request_key, fingerprint, {
            "SourceFile": result.import_batch.source_file,
            "SourceHash": result.import_batch.source_hash or "",
            "ParserVersion": result.import_batch.parser_version,
            "StartedAt": _grist_datetime(result.import_batch.started_at),
            "CompletedAt": _grist_datetime(result.import_batch.completed_at) if result.import_batch.completed_at else None,
            "Status": result.import_batch.status,
            "Outcome": result.import_batch.outcome,
        })

    def _ensure_file_record(self, file_record: CostingFile) -> int:
        existing = [
            item for item in self.client.fetch_table_records_with_ids("CostingFile")
            if _text(item.get("fields", {}).get("NormalizedPath")).casefold() == file_record.normalized_path.casefold()
        ]
        if len(existing) > 1:
            raise GristValidationError(f"Multiple CostingFile rows match normalized path {file_record.normalized_path!r}; refusing to choose one.")
        if existing:
            row = existing[0]
            record_id = int(row["id"])
            values = {"FileHash": file_record.file_hash or "", "SizeBytes": file_record.size_bytes, "ModifiedAt": _grist_datetime(file_record.modified_at), "MappingStatus": "mapped"}
            old_fields = row.get("fields", {})
            updates = {key: value for key, value in values.items() if old_fields.get(key) != value}
            if updates:
                self.client.update_table_records("CostingFile", [{"id": record_id, "fields": updates}])
            self._grist_file_ids[file_record.id] = record_id
            return record_id
        created = self.client.create_table_records("CostingFile", [{"fields": {"RelativePath": file_record.relative_path, "NormalizedPath": file_record.normalized_path, "Name": file_record.name, "Extension": file_record.extension, "SizeBytes": file_record.size_bytes, "ModifiedAt": _grist_datetime(file_record.modified_at), "FileHash": file_record.file_hash or "", "CandidateClassification": file_record.candidate_classification, "MappingStatus": "mapped"}}])
        if not created or created[0].get("id") is None:
            raise RuntimeError("Grist did not return the created CostingFile record ID")
        record_id = int(created[0]["id"])
        self._grist_file_ids[file_record.id] = record_id
        return record_id

    def _deactivate_prior_records(self, file_id: int, association_id: int, code_ids: tuple[str, ...], superseded_at: str) -> tuple[list[str], list[str]]:
        superseded_associations: list[str] = []
        association_updates: list[dict[str, Any]] = []
        for record in self.client.fetch_table_records_with_ids("FileModelAssociation"):
            fields = record.get("fields", {})
            if int(record.get("id", -1)) == association_id or _ref(fields.get("CostingFile")) != str(file_id):
                continue
            same_operation = _datetime_text(fields.get("SupersededAt")) == _datetime_text(superseded_at)
            if fields.get("Active") is not False or same_operation:
                superseded_associations.append(f"fma:{record['id']}")
            if fields.get("Active") is not False:
                association_updates.append({"id": record["id"], "fields": {"Active": False, "SupersededAt": _grist_datetime(superseded_at)}})
        if association_updates:
            self.client.update_table_records("FileModelAssociation", association_updates)

        superseded_code_associations: list[str] = []
        code_updates: list[dict[str, Any]] = []
        selected_codes = {str(item) for item in code_ids}
        for record in self.client.fetch_table_records_with_ids("FileCodeAssociation"):
            fields = record.get("fields", {})
            if _ref(fields.get("Association")) == str(association_id):
                continue
            if _ref(fields.get("CostingFile")) != str(file_id) and _ref(fields.get("ProductModelCode")) not in selected_codes:
                continue
            same_operation = _datetime_text(fields.get("SupersededAt")) == _datetime_text(superseded_at)
            if fields.get("Active") is not False or same_operation:
                superseded_code_associations.append(f"fca:{record['id']}")
            if fields.get("Active") is not False:
                code_updates.append({"id": record["id"], "fields": {"Active": False, "SupersededAt": _grist_datetime(superseded_at)}})
        if code_updates:
            self.client.update_table_records("FileCodeAssociation", code_updates)
        return superseded_associations, superseded_code_associations

    def _find_request_record(self, table_id: str, request_key: str) -> dict[str, Any] | None:
        matches = [
            item for item in self.client.fetch_table_records_with_ids(table_id)
            if _text(item.get("fields", {}).get("RequestKey")) == request_key
        ]
        if len(matches) > 1:
            raise GristValidationError(f"Multiple {table_id} rows use the same request key; refusing to choose one.")
        return matches[0] if matches else None

    def _assert_request_fingerprint(self, record: dict[str, Any], fingerprint: str) -> None:
        if _text(record.get("fields", {}).get("RequestFingerprint")) != fingerprint:
            validation = AssociationValidation(valid=False, errors=(ValidationError("IDEMPOTENCY_KEY_REUSED", "The request key already belongs to a different association request.", "Idempotency-Key"),))
            raise AssociationConflict(validation)

    def _ensure_request_record(self, table_id: str, request_key: str, fingerprint: str, fields: dict[str, Any]) -> None:
        record = self._find_request_record(table_id, request_key)
        if record is not None:
            self._assert_request_fingerprint(record, fingerprint)
            return
        self.client.create_table_records(table_id, [{"fields": {**fields, "RequestKey": request_key, "RequestFingerprint": fingerprint}}])

    def _result_for_existing_association(self, record: dict[str, Any], proposal: AssociationProposal) -> AssociationSaveResult:
        association_id = f"fma:{record['id']}"
        association = self.associations.get(association_id)
        if association is None:
            raise GristValidationError("The saved idempotent association could not be loaded from Grist; refusing to create a replacement.")
        codes = tuple(item for item in self.code_associations.values() if item.association_id == association_id)
        known = {item.code_id for item in codes}
        missing = tuple(
            FileCodeAssociation(f"pending:{association_id}:{code_id}", association_id, association.file_id, code_id, proposal.actor, association.created_at)
            for code_id in proposal.code_ids if code_id not in known
        )
        audit_record = self._find_request_record("AuditEvent", proposal.idempotency_key or association.id)
        batch_record = self._find_request_record("ImportBatch", proposal.idempotency_key or association.id)
        audit = _audit_from_record(audit_record) if audit_record else AuditEvent(f"audit:{association_id}", "file_association_saved", association.actor, association.created_at, "FileModelAssociation", association_id, association.reason, {"codeIds": list(proposal.code_ids)})
        file = self.files[association.file_id]
        batch = _batch_from_record(batch_record) if batch_record else ImportBatch(f"batch:{association_id}", file.relative_path, file.file_hash, "association-0.1", association.created_at, association.created_at, "queued", "read-only processing queued")
        return AssociationSaveResult(association, codes + missing, audit, batch, idempotent=True)

    def _reload_result(self, request_key: str, proposal: AssociationProposal, *, idempotent: bool) -> AssociationSaveResult:
        self.refresh_identity()
        record = self._find_request_record("FileModelAssociation", request_key)
        audit_record = self._find_request_record("AuditEvent", request_key)
        batch_record = self._find_request_record("ImportBatch", request_key)
        if record is None or audit_record is None or batch_record is None:
            raise RuntimeError("Grist association write did not produce all required association, audit, and queue records")
        association_id = f"fma:{record['id']}"
        association = self.associations[association_id]
        codes = tuple(item for item in self.code_associations.values() if item.association_id == association_id)
        return AssociationSaveResult(association, codes, _audit_from_record(audit_record), _batch_from_record(batch_record), idempotent=idempotent)

    def refresh_identity(self) -> None:
        self.products.clear()
        self.models.clear()
        self.codes.clear()
        self.aliases.clear()
        self.files.clear()
        self.observations.clear()
        self.associations.clear()
        self.code_associations.clear()
        self.import_batches.clear()
        self.issues.clear()
        self.audit_events.clear()
        self._grist_file_ids.clear()
        self._grist_association_ids.clear()
        self._grist_code_association_ids.clear()
        products = self.client.fetch_table_records_with_ids(TABLE_PRODUCT)
        for record in products:
            fields = record.get("fields", {})
            product = Product(id=str(record.get("id")), name=_text(fields.get("Name")), source_file=_text(fields.get("SourceFile")) or None, source_row=_number(fields.get("SourceRow")), active=bool(fields.get("Active", True)))
            self.products[product.id] = product
        models = self.client.fetch_table_records_with_ids(TABLE_MODEL)
        for record in models:
            fields = record.get("fields", {})
            product_id = _ref(fields.get("Product"))
            if product_id is None:
                continue
            model = ProductModel(id=str(record.get("id")), product_id=product_id, model_number=_text(fields.get("ModelNumber")), name=_text(fields.get("Name")), legacy_spares_only=bool(fields.get("LegacySparesOnly", False)), source_file=_text(fields.get("SourceFile")) or None, source_row=_number(fields.get("SourceRow")), active=bool(fields.get("Active", True)))
            self.models[model.id] = model
        codes = self.client.fetch_table_records_with_ids(TABLE_CODE)
        for record in codes:
            fields = record.get("fields", {})
            model_id = _ref(fields.get("ProductModel"))
            if model_id is None:
                continue
            code = ProductModelCode(id=str(record.get("id")), model_id=model_id, code=_text(fields.get("Code")), description=_text(fields.get("Description")), legacy_spares_only=bool(fields.get("LegacySparesOnly", False)), source_values=_text_values(fields.get("SourceValues")), source_file=_text(fields.get("SourceFile")) or None, source_row=_number(fields.get("SourceRow")), active=bool(fields.get("Active", True)))
            self.codes[code.id] = code
        for record in self.client.fetch_table_records_with_ids("IdentityAlias"):
            fields = record.get("fields", {})
            alias = IdentityAlias(
                id=f"alias:{record.get('id')}",
                entity_type=_text(fields.get("EntityType")),
                entity_id=_text(fields.get("EntityId")),
                value=_text(fields.get("Value")),
                normalized_value=_text(fields.get("NormalizedValue")),
                source=_text(fields.get("Source")),
                source_row=_number(fields.get("SourceRow")),
            )
            self.aliases[alias.id] = alias
        for record in self.client.fetch_table_records_with_ids("ReconciliationIssue"):
            fields = record.get("fields", {})
            issue = ReconciliationIssue(
                id=f"issue:{record.get('id')}",
                issue_type=_text(fields.get("IssueType")),
                severity=_text(fields.get("Severity")),
                message=_text(fields.get("Message")),
                source_file=_text(fields.get("SourceFile")),
                source_row=_number(fields.get("SourceRow")),
                entity_id=_text(fields.get("EntityId")) or None,
                status=_text(fields.get("Status")) or "open",
                created_at=_datetime_text(fields.get("CreatedAt")) or utc_now(),
            )
            self.issues[issue.id] = issue
        for record in self.client.fetch_table_records_with_ids("ImportBatch"):
            if record.get("id") is not None:
                batch = _batch_from_record(record)
                self.import_batches[batch.id] = batch
        for record in self.client.fetch_table_records_with_ids("AuditEvent"):
            if record.get("id") is not None:
                event = _audit_from_record(record)
                self.audit_events[event.id] = event
        self._refresh_files_and_associations()

    def _refresh_files_and_associations(self) -> None:
        for record in self.client.fetch_table_records_with_ids("CostingFile"):
            fields = record.get("fields", {})
            relative_path = _text(fields.get("RelativePath"))
            if not relative_path:
                continue
            file_id = f"file:{_text(fields.get('NormalizedPath')) or relative_path.casefold()}"
            self.files[file_id] = CostingFile(id=file_id, relative_path=relative_path, normalized_path=_text(fields.get("NormalizedPath")) or relative_path.casefold(), name=_text(fields.get("Name")) or relative_path.rsplit("/", 1)[-1], extension=_text(fields.get("Extension")), size_bytes=_number(fields.get("SizeBytes")) or 0, modified_at=_datetime_text(fields.get("ModifiedAt")), file_hash=_text(fields.get("FileHash")) or None, product_id=_ref(fields.get("Product")), candidate_classification=_text(fields.get("CandidateClassification")) or "unknown", mapping_status=_text(fields.get("MappingStatus")) or "unmapped", readable=_bool(fields.get("Readable")), parse_error=_text(fields.get("ParseError")) or None)
            if record.get("id") is not None:
                self._grist_file_ids[file_id] = int(record["id"])
        for record in self.client.fetch_table_records_with_ids("FileObservation"):
            fields = record.get("fields", {})
            file_id = self._file_id_for_grist_id(_ref(fields.get("CostingFile")))
            if file_id is None:
                continue
            self.observations.append(FileObservation(
                id=f"observation:{record.get('id', len(self.observations))}",
                file_id=file_id,
                observed_at=_datetime_text(fields.get("ObservedAt")) or utc_now(),
                relative_path=_text(fields.get("RelativePath")),
                normalized_path=_text(fields.get("NormalizedPath")),
                size_bytes=_number(fields.get("SizeBytes")) or 0,
                modified_at=_datetime_text(fields.get("ModifiedAt")),
                file_hash=_text(fields.get("FileHash")) or None,
                readable=_bool(fields.get("Readable")),
                sheet_count=_number(fields.get("SheetCount")),
                external_reference_count=_number(fields.get("ExternalReferenceCount")),
                parse_error=_text(fields.get("ParseError")) or None,
                source="grist",
            ))
        association_record_map: dict[int, str] = {}
        for record in self.client.fetch_table_records_with_ids("FileModelAssociation"):
            fields = record.get("fields", {})
            record_id = record.get("id")
            file_record_id = _ref(fields.get("CostingFile"))
            file_id = self._file_id_for_grist_id(file_record_id)
            if record_id is None or file_id is None:
                continue
            domain_id = f"fma:{record_id}"
            association = FileModelAssociation(id=domain_id, file_id=file_id, product_id=_ref(fields.get("Product")) or "", model_id=_ref(fields.get("ProductModel")) or "", actor=_text(fields.get("Actor")), reason=_text(fields.get("Reason")), created_at=_datetime_text(fields.get("CreatedAt")) or utc_now(), superseded_at=_datetime_text(fields.get("SupersededAt")) or None, active=_bool(fields.get("Active")) is not False, version=_number(fields.get("Version")) or 1)
            self.associations[domain_id] = association
            self._grist_association_ids[domain_id] = int(record_id)
            association_record_map[int(record_id)] = domain_id
        for record in self.client.fetch_table_records_with_ids("FileCodeAssociation"):
            fields = record.get("fields", {})
            record_id = record.get("id")
            association_ref = _ref(fields.get("Association"))
            association_id = association_record_map.get(int(association_ref)) if association_ref and association_ref.isdigit() else None
            file_id = self._file_id_for_grist_id(_ref(fields.get("CostingFile")))
            if record_id is None or association_id is None or file_id is None:
                continue
            domain_id = f"fca:{record_id}"
            code = FileCodeAssociation(id=domain_id, association_id=association_id, file_id=file_id, code_id=_ref(fields.get("ProductModelCode")) or "", actor=_text(fields.get("Actor")), created_at=_datetime_text(fields.get("CreatedAt")) or utc_now(), superseded_at=_datetime_text(fields.get("SupersededAt")) or None, active=_bool(fields.get("Active")) is not False)
            self.code_associations[domain_id] = code
            self._grist_code_association_ids[domain_id] = int(record_id)

    def _file_id_for_grist_id(self, grist_id: str | None) -> str | None:
        if grist_id is None:
            return None
        return next((file_id for file_id, record_id in self._grist_file_ids.items() if str(record_id) == str(grist_id)), None)


def _ref(value: Any) -> str | None:
    if isinstance(value, int):
        return str(value)
    if isinstance(value, list) and len(value) > 2 and value[0] == "R":
        return str(value[2])
    if isinstance(value, list) and len(value) > 1 and value[0] == "L":
        return str(value[1])
    return None


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _text_values(value: Any) -> tuple[str, ...]:
    """Decode a Grist Any-list cell while preserving each source spelling."""
    if isinstance(value, (list, tuple)):
        values = value[1:] if value and value[0] == "L" else value
        return tuple(str(item) for item in values if item is not None)
    if value in (None, ""):
        return ()
    return (str(value),)


def _number(value: Any) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _bool(value: Any) -> bool | None:
    if value is None or value == "":
        return None
    return bool(value)


def _request_fingerprint(proposal: AssociationProposal) -> str:
    payload = {
        "fileId": proposal.file_id,
        "productId": proposal.product_id,
        "modelId": proposal.model_id,
        "codeIds": sorted(proposal.code_ids),
        "actor": proposal.actor,
        "reason": proposal.reason,
        "expectedVersion": proposal.expected_version,
        "expectedHash": proposal.expected_hash,
        "supersede": proposal.supersede,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _audit_from_record(record: dict[str, Any]) -> AuditEvent:
    fields = record.get("fields", {})
    payload = fields.get("Payload") or {}
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            payload = {"raw": payload}
    if not isinstance(payload, dict):
        payload = {"value": payload}
    return AuditEvent(
        id=f"audit:{record.get('id')}",
        event_type=_text(fields.get("EventType")),
        actor=_text(fields.get("Actor")),
        occurred_at=_datetime_text(fields.get("OccurredAt")),
        entity_type=_text(fields.get("EntityType")),
        entity_id=_text(fields.get("EntityId")),
        reason=_text(fields.get("Reason")),
        payload=payload,
    )


def _batch_from_record(record: dict[str, Any]) -> ImportBatch:
    fields = record.get("fields", {})
    return ImportBatch(
        id=f"batch:{record.get('id')}",
        source_file=_text(fields.get("SourceFile")),
        source_hash=_text(fields.get("SourceHash")) or None,
        parser_version=_text(fields.get("ParserVersion")),
        started_at=_datetime_text(fields.get("StartedAt")),
        completed_at=_datetime_text(fields.get("CompletedAt")) or None,
        status=_text(fields.get("Status")),
        outcome=_text(fields.get("Outcome")),
    )

"""Safari Manufacturing Grist repository adapter.

This adapter is intentionally not selected by normal API startup.  It is
constructed only when the explicit Safari document settings are present.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from threading import RLock
from typing import Any

from app.domain import AuditEvent, CostingChangeSetItem, CostingFile, CostingSnapshot, DirectoryProductMapping, FileCodeAssociation, FileModelAssociation, FileObservation, IdentityAlias, ImportBatch, Product, ProductModel, ProductModelCode, ReconciliationIssue, utc_now
from app.exceptions import GristError, GristValidationError
from app.grist import GristClient
from app.grist_admin import GristAdminClient
from app.grist_types import datetime_text as _datetime_text, grist_datetime as _grist_datetime
from app.repository import AssociationConflict, AssociationProposal, AssociationSaveResult, AssociationValidation, GovernanceConflict, SafariRepositoryBase, ValidationError, normalize_relative_directory
from app.schema import plan_schema


TABLE_PRODUCT = "Product"
TABLE_MODEL = "ProductModel"
TABLE_CODE = "ProductModelCode"


class GristSafariRepository(SafariRepositoryBase):
    adapter_name = "grist-safari"

    def __init__(self, client: GristClient | None = None) -> None:
        super().__init__()
        self.client = client or GristClient.from_safari_environment()
        self._grist_file_ids: dict[str, int] = {}
        self._grist_association_ids: dict[str, int] = {}
        self._grist_code_association_ids: dict[str, int] = {}
        self._grist_issue_ids: dict[str, int] = {}
        self._grist_directory_mapping_ids: dict[str, int] = {}
        self._grist_costing_snapshot_ids: dict[str, int] = {}
        self._grist_costing_change_ids: dict[str, int] = {}
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
        self.directory_mappings.clear()
        self.costing_snapshots.clear()
        self.costing_change_items.clear()
        self._grist_file_ids.clear()
        self._grist_association_ids.clear()
        self._grist_code_association_ids.clear()
        self._grist_issue_ids.clear()
        self._grist_directory_mapping_ids.clear()
        self._grist_costing_snapshot_ids.clear()
        self._grist_costing_change_ids.clear()
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
            model = ProductModel(id=str(record.get("id")), product_id=product_id, model_number=_text(fields.get("ModelNumber")), name=_text(fields.get("Name")), legacy_spares_only=bool(fields.get("LegacySparesOnly", False)), source_file=_text(fields.get("SourceFile")) or None, source_row=_number(fields.get("SourceRow")), active=bool(fields.get("Active", True)), superseded_by_id=_text(fields.get("SupersededById")) or None, superseded_at=_datetime_text(fields.get("SupersededAt")) or None)
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
                fingerprint=_text(fields.get("Fingerprint")),
                entity_type=_text(fields.get("EntityType")),
                source_path=_text(fields.get("SourcePath")),
                source_cell=_text(fields.get("SourceCell")),
                detected_facts=_dict_value(fields.get("DetectedFacts")),
                proposed_resolution=_dict_value(fields.get("ProposedResolution")),
                assigned_owner=_text(fields.get("AssignedOwner")) or None,
                first_seen_at=_datetime_text(fields.get("FirstSeenAt")) or _datetime_text(fields.get("CreatedAt")) or utc_now(),
                last_seen_at=_datetime_text(fields.get("LastSeenAt")) or _datetime_text(fields.get("CreatedAt")) or utc_now(),
                version=_number(fields.get("Version")) or 1,
                resolution_action=_text(fields.get("ResolutionAction")),
                resolution_reason=_text(fields.get("ResolutionReason")),
                resolved_actor=_text(fields.get("ResolvedActor")) or None,
                resolved_at=_datetime_text(fields.get("ResolvedAt")) or None,
                deferred_actor=_text(fields.get("DeferredActor")) or None,
                deferred_at=_datetime_text(fields.get("DeferredAt")) or None,
                reopened_actor=_text(fields.get("ReopenedActor")) or None,
                reopened_at=_datetime_text(fields.get("ReopenedAt")) or None,
            )
            self._grist_issue_ids[issue.id] = int(record["id"])
            self.issues[issue.id] = issue
        for record in self._fetch_directory_mapping_records():
            fields = record.get("fields", {})
            product_id = _ref(fields.get("Product"))
            if record.get("id") is None or product_id is None:
                continue
            mapping = DirectoryProductMapping(id=f"directory-mapping:{record['id']}", relative_path=_text(fields.get("RelativePath")), normalized_path=_text(fields.get("NormalizedPath")), product_id=product_id, inherit=bool(fields.get("Inherit", False)), status=_text(fields.get("Status")) or "proposed", proposer=_text(fields.get("Proposer")), approver=_text(fields.get("Approver")) or None, reason=_text(fields.get("Reason")), created_at=_datetime_text(fields.get("CreatedAt")) or utc_now(), updated_at=_datetime_text(fields.get("UpdatedAt")) or utc_now(), version=_number(fields.get("Version")) or 1, supersedes_id=_text(fields.get("SupersedesId")) or None)
            self.directory_mappings[mapping.id] = mapping
            self._grist_directory_mapping_ids[mapping.id] = int(record["id"])
        for record in self.client.fetch_table_records_with_ids("ImportBatch"):
            if record.get("id") is not None:
                batch = _batch_from_record(record)
                self.import_batches[batch.id] = batch
        for record in self.client.fetch_table_records_with_ids("AuditEvent"):
            if record.get("id") is not None:
                event = _audit_from_record(record)
                self.audit_events[event.id] = event
        self._refresh_files_and_associations()
        self._refresh_costing_records()

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
        for record in self.client.fetch_table_records_with_ids("ReconciliationIssue"):
            grist_file_id = _ref(record.get("fields", {}).get("CostingFile"))
            file_id = self._file_id_for_grist_id(grist_file_id)
            issue = self.issues.get(f"issue:{record.get('id')}")
            if issue is not None and file_id:
                self.issues[issue.id] = replace(issue, costing_file_id=file_id)

    def _write_guard(self) -> None:
        self.client.validate_safari_write_target()

    def _fetch_optional_records(self, table_id: str) -> list[dict[str, Any]]:
        try:
            return self.client.fetch_table_records_with_ids(table_id)
        except GristError as exc:
            if "404" in str(exc):
                return []
            raise

    def latest_accepted_costing_snapshot(self, costing_file_id: str) -> CostingSnapshot | None:
        self.refresh_identity()
        return super().latest_accepted_costing_snapshot(costing_file_id)

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
        if not idempotency_key.strip():
            raise GovernanceConflict("IDEMPOTENCY_KEY_REQUIRED", "Costing snapshot acceptance requires an idempotency key.")
        file_before_refresh = self.files.get(costing_file_id)
        self._governance_write_guard()
        self.refresh_identity()
        if file_before_refresh is not None and costing_file_id not in self.files:
            self.files[costing_file_id] = file_before_refresh
        fingerprint_payload = {
            "file": costing_file_id,
            "previous": expected_previous_snapshot_key,
            "semantic_hash": semantic_snapshot.get("semantic_hash"),
            "sources": semantic_snapshot.get("source_hashes", {}),
            "actor": actor,
            "reason": reason.strip(),
            "changes": [item.get("change_key") for item in changes],
        }
        fingerprint = hashlib.sha256(json.dumps(fingerprint_payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
        existing_request = self._find_request_record("AuditEvent", idempotency_key)
        if existing_request is not None:
            self._assert_request_fingerprint(existing_request, fingerprint)
            self.refresh_identity()
            prior_payload = _dict_value(existing_request.get("fields", {}).get("Payload"))
            snapshot_key = _text(prior_payload.get("snapshotKey"))
            snapshot = self.costing_snapshots.get(snapshot_key)
            if snapshot is None:
                raise GovernanceConflict("COSTING_ACCEPTANCE_INCOMPLETE", "The prior request audit exists but its accepted snapshot is missing; inspect Safari change-set records before retrying.")
            if snapshot.status == "accepted":
                saved_changes = [item for item in self.costing_change_items.values() if item.snapshot_key == snapshot_key]
                return {"snapshot": snapshot.to_dict(), "changes": [item.to_dict() for item in saved_changes], "auditEvents": [_audit_from_record(existing_request).to_dict()], "idempotent": True, "unchanged": False}

        result = super().accept_costing_snapshot(
            costing_file_id=costing_file_id,
            semantic_snapshot=semantic_snapshot,
            changes=changes,
            actor=actor,
            reason=reason,
            expected_previous_snapshot_key=expected_previous_snapshot_key,
            expected_semantic_hash=expected_semantic_hash,
            expected_source_hashes=expected_source_hashes,
            idempotency_key=idempotency_key,
        )
        if result.get("unchanged"):
            return result
        snapshot = self.costing_snapshots[result["snapshot"]["snapshot_key"]]
        file_record_id = self._ensure_file_record(self.files[costing_file_id])
        snapshot_records = self.client.fetch_table_records_with_ids("CostingSnapshot")
        snapshot_matches = [row for row in snapshot_records if _text(row.get("fields", {}).get("SnapshotKey")) == snapshot.snapshot_key]
        if len(snapshot_matches) > 1:
            raise GristValidationError("Multiple CostingSnapshot records use the same snapshot key; refusing to choose one.")
        snapshot_fields = _costing_snapshot_fields(snapshot, file_record_id, status="staging")
        if snapshot_matches:
            snapshot_record_id = int(snapshot_matches[0]["id"])
            prior_status = _text(snapshot_matches[0].get("fields", {}).get("Status"))
            if prior_status == "accepted":
                snapshot_fields["Status"] = "accepted"
            self.client.update_table_records("CostingSnapshot", [{"id": snapshot_record_id, "fields": snapshot_fields}])
        else:
            created = self.client.create_table_records("CostingSnapshot", [{"fields": snapshot_fields}])
            if not created or created[0].get("id") is None:
                raise RuntimeError("Grist did not return the created CostingSnapshot record ID")
            snapshot_record_id = int(created[0]["id"])
        self._grist_costing_snapshot_ids[snapshot.snapshot_key] = snapshot_record_id

        remote_change_rows = self.client.fetch_table_records_with_ids("CostingChangeSetItem")
        by_item_key: dict[str, dict[str, Any]] = {}
        for row in remote_change_rows:
            item_key = _text(row.get("fields", {}).get("ItemKey"))
            if item_key:
                if item_key in by_item_key:
                    raise GristValidationError("Multiple CostingChangeSetItem records use the same item key; refusing to choose one.")
                by_item_key[item_key] = row
        change_records: list[dict[str, Any]] = []
        for item_data in result["changes"]:
            item_key = hashlib.sha256(f"{snapshot.snapshot_key}|{item_data['change_key']}".encode("utf-8")).hexdigest()
            fields = _costing_change_item_fields(item_data, item_key=item_key, snapshot_record_id=snapshot_record_id, file_record_id=file_record_id, request_key=idempotency_key, status="staging")
            existing = by_item_key.get(item_key)
            if existing is not None:
                record_id = int(existing["id"])
                if _text(existing.get("fields", {}).get("Status")) == "accepted":
                    fields["Status"] = "accepted"
                self.client.update_table_records("CostingChangeSetItem", [{"id": record_id, "fields": fields}])
            else:
                created = self.client.create_table_records("CostingChangeSetItem", [{"fields": fields}])
                if not created or created[0].get("id") is None:
                    raise RuntimeError("Grist did not return the created CostingChangeSetItem record ID")
                record_id = int(created[0]["id"])
            change_records.append({"id": record_id, "fields": fields})
            self._grist_costing_change_ids[item_data["id"]] = record_id

        # Snapshot and change items stay staging until source observation and
        # audit writes also succeed. Retries can resume without exposing a
        # partially committed accepted baseline or change set.
        file = self.files[costing_file_id]
        source = semantic_snapshot.get("source_evidence", {}).get("selected_workbook_saved", {})
        self.client.update_table_records("CostingFile", [{"id": file_record_id, "fields": {
            "FileHash": file.file_hash or "",
            "SizeBytes": file.size_bytes,
            "ModifiedAt": _grist_datetime(file.modified_at) if file.modified_at else None,
        }}])
        observation = result.get("observation", {})
        observation_records = self.client.fetch_table_records_with_ids("FileObservation")
        observation_exists = any(
            _ref(row.get("fields", {}).get("CostingFile")) == str(file_record_id)
            and _text(row.get("fields", {}).get("FileHash")) == _text(source.get("sha256"))
            and _datetime_text(row.get("fields", {}).get("ObservedAt")) == _datetime_text(observation.get("observed_at"))
            for row in observation_records
        )
        if observation and not observation_exists:
            self.client.create_table_records("FileObservation", [{"fields": _observation_fields(
                FileObservation(**observation), file_record_id,
            )}])

        for event in result.get("auditEvents", []):
            entity_type = event.get("entity_type")
            audit_event = self.audit_events.get(event.get("id")) or AuditEvent(**event)
            if entity_type == "CostingChangeSetItem":
                change_key = event.get("payload", {}).get("changeKey", "")
                event_key = f"{idempotency_key}:change:{change_key}"
                self._persist_governance_audit(audit_event, idempotency_key=event_key)
            else:
                payload = {**event.get("payload", {}), "snapshotKey": snapshot.snapshot_key}
                self._persist_governance_audit(audit_event, idempotency_key=idempotency_key, fingerprint=fingerprint, payload_override=payload)
        if change_records:
            self.client.update_table_records("CostingChangeSetItem", [
                {"id": row["id"], "fields": {"Status": "accepted"}}
                for row in change_records
            ])
        self.client.update_table_records("CostingSnapshot", [{"id": snapshot_record_id, "fields": {"Status": "accepted"}}])
        previous_key = snapshot.previous_snapshot_key
        if previous_key and previous_key in self._grist_costing_snapshot_ids:
            previous_record_id = self._grist_costing_snapshot_ids[previous_key]
            self.client.update_table_records("CostingSnapshot", [{"id": previous_record_id, "fields": {"Status": "superseded"}}])
        self.refresh_identity()
        saved_snapshot = self.costing_snapshots.get(snapshot.snapshot_key)
        saved_changes = [item for item in self.costing_change_items.values() if item.snapshot_key == snapshot.snapshot_key]
        result["snapshot"] = saved_snapshot.to_dict() if saved_snapshot else snapshot.to_dict()
        result["changes"] = [item.to_dict() for item in saved_changes]
        return result

    def _refresh_costing_records(self) -> None:
        snapshot_records = self._fetch_optional_records("CostingSnapshot")
        snapshot_keys_by_id: dict[str, str] = {}
        for record in snapshot_records:
            fields = record.get("fields", {})
            snapshot_key = _text(fields.get("SnapshotKey"))
            file_id = self._file_id_for_grist_id(_ref(fields.get("CostingFile")))
            if not snapshot_key or file_id is None or record.get("id") is None:
                continue
            snapshot = CostingSnapshot(
                id=snapshot_key,
                snapshot_key=snapshot_key,
                costing_file_id=file_id,
                observed_at=_datetime_text(fields.get("ObservedAt")) or utc_now(),
                semantic_hash=_text(fields.get("SemanticHash")),
                semantic_content=_dict_value(fields.get("SemanticContent")),
                source_hashes=_dict_value(fields.get("SourceHashes")),
                status=_text(fields.get("Status")) or "staging",
                previous_snapshot_key=_text(fields.get("PreviousSnapshotKey")) or None,
                accepted_at=_datetime_text(fields.get("AcceptedAt")) or utc_now(),
                accepted_by=_text(fields.get("AcceptedBy")),
                acceptance_reason=_text(fields.get("AcceptanceReason")),
                request_key=_text(fields.get("RequestKey")),
            )
            self.costing_snapshots[snapshot_key] = snapshot
            self._grist_costing_snapshot_ids[snapshot_key] = int(record["id"])
            snapshot_keys_by_id[str(record["id"])] = snapshot_key
        for record in self._fetch_optional_records("CostingChangeSetItem"):
            fields = record.get("fields", {})
            file_id = self._file_id_for_grist_id(_ref(fields.get("CostingFile")))
            snapshot_ref = _ref(fields.get("Snapshot"))
            snapshot_key = snapshot_keys_by_id.get(snapshot_ref or "")
            if file_id is None or snapshot_key is None or record.get("id") is None:
                continue
            item = CostingChangeSetItem(
                id=f"costing-change:{record['id']}",
                change_key=_text(fields.get("ChangeKey")),
                snapshot_key=snapshot_key,
                previous_snapshot_key=_text(fields.get("PreviousSnapshotKey")) or None,
                costing_file_id=file_id,
                change_type=_text(fields.get("ChangeType")),
                classification=_text(fields.get("Classification")),
                change_data=_dict_value(fields.get("ChangeData")),
                status=_text(fields.get("Status")) or "staging",
                accepted_at=_datetime_text(fields.get("AcceptedAt")) or utc_now(),
                accepted_by=_text(fields.get("AcceptedBy")),
                acceptance_reason=_text(fields.get("AcceptanceReason")),
            )
            self.costing_change_items[item.id] = item
            self._grist_costing_change_ids[item.id] = int(record["id"])

    def _governance_write_guard(self) -> None:
        """Refuse governance writes until the reviewed durable schema is present.

        The legacy v2 document can still be opened for read-only reconciliation,
        but source-revision acceptance writes both a current-file fingerprint
        and an immutable observation.  Those writes must not start unless all
        v3 fields/tables are available, or a later issue/audit write could fail
        after the source record had already changed.
        """
        self._write_guard()
        # Contract tests use a fake client that accepts all tables/columns.
        # The production adapter always uses the concrete GristClient.
        if not isinstance(self.client, GristClient):
            return
        import os

        workspace_id = getattr(self.client, "safari_workspace_id", "")
        plan = plan_schema(
            GristAdminClient(self.client.api_key, self.client.base_url),
            self.client.doc_id,
            workspace_id=workspace_id,
            legacy_doc_id=os.getenv("GRIST_DOC_ID", "").strip(),
        )
        if plan.create_tables or plan.add_columns or plan.update_columns:
            missing = []
            missing.extend(f"table:{item['id']}" for item in plan.create_tables)
            missing.extend(f"columns:{item['tableId']}" for item in plan.add_columns)
            missing.extend(f"column-types:{item['tableId']}" for item in plan.update_columns)
            raise GovernanceConflict(
                "SCHEMA_MIGRATION_REQUIRED",
                "The reviewed governance schema is not applied; no governed write was attempted (" + ", ".join(missing) + ").",
            )

    def _fetch_directory_mapping_records(self) -> list[dict[str, Any]]:
        """Treat the not-yet-applied v3 mapping table as empty for read-only use."""
        try:
            return self.client.fetch_table_records_with_ids("DirectoryProductMapping")
        except GristError as exc:
            # The target has already been validated, so a 404 here means the
            # optional v3 table has not been created. All other failures stay
            # visible rather than being mistaken for an empty mapping list.
            if "404" in str(exc):
                return []
            raise

    def upsert_issue(self, issue: ReconciliationIssue) -> ReconciliationIssue:
        self._governance_write_guard()
        file_id = issue.costing_file_id or (issue.entity_id if issue.entity_type == "CostingFile" else None)
        if file_id:
            self._refresh_preserving_file(file_id)
        else:
            self.refresh_identity()
        known_events = set(self.audit_events)
        saved = super().upsert_issue(issue)
        durable = self._persist_reconciliation_issue(saved)
        for event in self.audit_events.values():
            if event.id not in known_events:
                self._persist_governance_audit(event)
        return durable

    def _persist_reconciliation_issue(self, issue: ReconciliationIssue) -> ReconciliationIssue:
        self._governance_write_guard()
        fields = _issue_fields(issue, self._grist_file_ids.get(issue.costing_file_id or ""))
        matches = [item for item in self.client.fetch_table_records_with_ids("ReconciliationIssue") if issue.fingerprint and _text(item.get("fields", {}).get("Fingerprint")) == issue.fingerprint]
        if len(matches) > 1:
            raise GristValidationError("Multiple ReconciliationIssue records use the same fingerprint; refusing to choose one.")
        if matches:
            record = matches[0]
            self.client.update_table_records("ReconciliationIssue", [{"id": record["id"], "fields": fields}])
            record_id = int(record["id"])
        else:
            created = self.client.create_table_records("ReconciliationIssue", [{"fields": fields}])
            if not created or created[0].get("id") is None:
                raise RuntimeError("Grist did not return the created ReconciliationIssue record ID")
            record_id = int(created[0]["id"])
        durable = replace(issue, id=f"issue:{record_id}")
        if issue.id != durable.id:
            self.issues.pop(issue.id, None)
        self.issues[durable.id] = durable
        self._grist_issue_ids[durable.id] = record_id
        return durable

    def mutate_issue(self, issue_id: str, action: str, *, actor: str, reason: str = "", expected_version: int, assigned_owner: str | None = None, idempotency_key: str | None = None) -> ReconciliationIssue:
        self._governance_write_guard()
        self.refresh_identity()
        if idempotency_key:
            existing = self._find_request_record("AuditEvent", idempotency_key)
            if existing is not None:
                expected = _governance_fingerprint(issue_id, action, actor, reason, assigned_owner, expected_version)
                if _text(existing.get("fields", {}).get("RequestFingerprint")) != expected:
                    raise GovernanceConflict("IDEMPOTENCY_KEY_REUSED", "The idempotency key belongs to another reconciliation mutation.")
                current = self.issues.get(issue_id)
                if current is not None:
                    return current
        issue = super().mutate_issue(issue_id, action, actor=actor, reason=reason, expected_version=expected_version, assigned_owner=assigned_owner, idempotency_key=idempotency_key)
        issue = self._persist_reconciliation_issue(issue)
        event = max(self.audit_events.values(), key=lambda item: item.occurred_at)
        self._persist_governance_audit(event, idempotency_key=idempotency_key, fingerprint=_governance_fingerprint(issue_id, action, actor, reason, assigned_owner, expected_version))
        return issue

    def accept_file_revision(self, file_id: str, observation: FileObservation, *, issue_id: str, actor: str, reason: str, expected_issue_version: int, expected_stored_hash: str, expected_current_hash: str, idempotency_key: str | None = None) -> dict[str, Any]:
        replay = self.replay_source_revision(idempotency_key, file_id=file_id, issue_id=issue_id, actor=actor, reason=reason, expected_issue_version=expected_issue_version, expected_stored_hash=expected_stored_hash, expected_current_hash=expected_current_hash)
        if replay is not None:
            return replay
        self._governance_write_guard()
        self._refresh_preserving_file(file_id)
        result = super().accept_file_revision(file_id, observation, issue_id=issue_id, actor=actor, reason=reason, expected_issue_version=expected_issue_version, expected_stored_hash=expected_stored_hash, expected_current_hash=expected_current_hash, idempotency_key=idempotency_key)
        file_record_id = self._ensure_file_record(self.files[file_id])
        self.client.update_table_records("CostingFile", [{"id": file_record_id, "fields": {"FileHash": observation.file_hash or "", "SizeBytes": observation.size_bytes, "ModifiedAt": _grist_datetime(observation.modified_at)}}])
        existing = [item for item in self.client.fetch_table_records_with_ids("FileObservation") if _ref(item.get("fields", {}).get("CostingFile")) == str(file_record_id) and _text(item.get("fields", {}).get("FileHash")) == (observation.file_hash or "")]
        if not existing:
            self.client.create_table_records("FileObservation", [{"fields": _observation_fields(observation, file_record_id)}])
        persisted_issue = self._persist_reconciliation_issue(self.issues[issue_id])
        result["issue"] = persisted_issue.to_dict()
        fingerprint = _governance_fingerprint(file_id, issue_id, actor, reason, expected_issue_version, expected_stored_hash, expected_current_hash)
        self._persist_governance_audit(self.audit_events[result["auditEvent"]["id"]], idempotency_key=idempotency_key, fingerprint=fingerprint, payload_override={"issueId": issue_id, "oldHash": expected_stored_hash, "newHash": observation.file_hash, "observationId": observation.id})
        return result

    def replay_source_revision(self, idempotency_key: str | None, *, file_id: str, issue_id: str, actor: str, reason: str, expected_issue_version: int, expected_stored_hash: str, expected_current_hash: str) -> dict[str, Any] | None:
        if not idempotency_key:
            return None
        prior = self._find_request_record("AuditEvent", idempotency_key)
        if prior is None:
            # A process-local cache cannot prove that a multi-table Grist write
            # reached its durable audit record. Force normal revalidation.
            self._revision_mutations.pop(idempotency_key, None)
            return None
        expected = _governance_fingerprint(file_id, issue_id, actor, reason, expected_issue_version, expected_stored_hash, expected_current_hash)
        if _text(prior.get("fields", {}).get("RequestFingerprint")) != expected:
            raise GovernanceConflict("IDEMPOTENCY_KEY_REUSED", "The idempotency key was already used for another source revision.")
        self.refresh_identity()
        fields = prior.get("fields", {})
        payload = _dict_value(fields.get("Payload"))
        saved_hash = _text(payload.get("newHash"))
        issue = self.issues.get(issue_id)
        file = self.files.get(file_id)
        observation = max((item for item in self.observations if item.file_id == file_id and item.file_hash == saved_hash), key=lambda item: item.observed_at, default=None)
        audit = _audit_from_record(prior)
        return {
            "file": file.to_dict() if file else None,
            "observation": observation.to_dict() if observation else None,
            "issue": issue.to_dict() if issue else None,
            "auditEvent": audit.to_dict(),
            "idempotent": True,
        }

    def apply_identity_cleanup(self, model_id: str, canonical_model_id: str, *, issue_id: str, actor: str, reason: str, expected_issue_version: int, idempotency_key: str | None = None) -> dict[str, Any]:
        self._governance_write_guard()
        self.refresh_identity()
        if idempotency_key:
            prior = self._find_request_record("AuditEvent", idempotency_key)
            if prior is not None:
                expected = _governance_fingerprint(model_id, canonical_model_id, issue_id, actor, reason, expected_issue_version)
                if _text(prior.get("fields", {}).get("RequestFingerprint")) != expected:
                    raise GovernanceConflict("IDEMPOTENCY_KEY_REUSED", "The idempotency key was already used for another identity cleanup.")
                model = self.models.get(model_id)
                canonical = self.models.get(canonical_model_id)
                issue = self.issues.get(issue_id)
                event = _audit_from_record(prior)
                if model is not None and canonical is not None and issue is not None:
                    return {"model": model.to_dict(), "canonicalReplacement": canonical.to_dict(), "issue": issue.to_dict(), "auditEvent": event.to_dict(), "idempotent": True}
        result = super().apply_identity_cleanup(model_id, canonical_model_id, issue_id=issue_id, actor=actor, reason=reason, expected_issue_version=expected_issue_version, idempotency_key=idempotency_key)
        model_record_id = int(model_id)
        self.client.update_table_records("ProductModel", [{"id": model_record_id, "fields": {"Active": False, "SupersededById": canonical_model_id, "SupersededAt": _grist_datetime(result["model"]["superseded_at"])}}])
        result["issue"] = self._persist_reconciliation_issue(self.issues[issue_id]).to_dict()
        event = next(item for item in self.audit_events.values() if item.entity_type == "ProductModel" and item.entity_id == model_id and item.event_type == "identity_superseded")
        fingerprint = _governance_fingerprint(model_id, canonical_model_id, issue_id, actor, reason, expected_issue_version)
        self._persist_governance_audit(event, idempotency_key=idempotency_key, fingerprint=fingerprint)
        audit_record = self._find_request_record("AuditEvent", idempotency_key or event.id)
        if audit_record is not None:
            durable_event = _audit_from_record(audit_record)
            self.audit_events.pop(event.id, None)
            self.audit_events[durable_event.id] = durable_event
            result["auditEvent"] = durable_event.to_dict()
        return result

    def propose_directory_mapping(self, relative_path: str, product_id: str, *, inherit: bool, actor: str, reason: str = "", idempotency_key: str | None = None) -> DirectoryProductMapping:
        self._governance_write_guard()
        self.refresh_identity()
        path, normalized = normalize_relative_directory(relative_path)
        fingerprint = _mutation_fingerprint("propose", normalized, product_id, inherit, actor, reason)
        if idempotency_key:
            prior_event = self._find_request_record("AuditEvent", idempotency_key)
            prior = self._find_request_record("DirectoryProductMapping", idempotency_key)
            if prior_event is not None:
                if _text(prior_event.get("fields", {}).get("RequestFingerprint")) != fingerprint:
                    raise GovernanceConflict("IDEMPOTENCY_KEY_REUSED", "The idempotency key already belongs to another directory mapping proposal.")
                entity_id = _text(prior_event.get("fields", {}).get("EntityId"))
                if entity_id in self.directory_mappings:
                    return self.directory_mappings[entity_id]
            if prior is not None:
                if _text(prior.get("fields", {}).get("RequestFingerprint")) != fingerprint:
                    raise GovernanceConflict("IDEMPOTENCY_KEY_REUSED", "The idempotency key already belongs to another directory mapping proposal.")
                return self.directory_mappings[f"directory-mapping:{prior['id']}"]
        mapping = super().propose_directory_mapping(path, product_id, inherit=inherit, actor=actor, reason=reason, idempotency_key=idempotency_key)
        fields = _directory_mapping_fields(mapping, request_key=idempotency_key, request_fingerprint=fingerprint)
        created = self.client.create_table_records("DirectoryProductMapping", [{"fields": fields}])
        if not created or created[0].get("id") is None:
            raise RuntimeError("Grist did not return a created DirectoryProductMapping record ID")
        durable = replace(mapping, id=f"directory-mapping:{created[0]['id']}")
        self.directory_mappings.pop(mapping.id, None)
        self.directory_mappings[durable.id] = durable
        self._grist_directory_mapping_ids[durable.id] = int(created[0]["id"])
        event = next(item for item in self.audit_events.values() if item.entity_id == mapping.id and item.event_type == "directory_product_mapping_proposed")
        if event.entity_id != durable.id:
            event = replace(event, entity_id=durable.id)
            self.audit_events[event.id] = event
        self._persist_governance_audit(event, idempotency_key=idempotency_key, fingerprint=fingerprint if idempotency_key else None)
        return durable

    def transition_directory_mapping(self, mapping_id: str, action: str, *, actor: str, reason: str, expected_version: int, idempotency_key: str | None = None) -> DirectoryProductMapping:
        self._governance_write_guard()
        self.refresh_identity()
        fingerprint = _mutation_fingerprint("transition", mapping_id, action, actor, reason, expected_version)
        if idempotency_key:
            prior_event = self._find_request_record("AuditEvent", idempotency_key)
            prior = self._find_request_record("DirectoryProductMapping", idempotency_key)
            if prior_event is not None:
                if _text(prior_event.get("fields", {}).get("RequestFingerprint")) != fingerprint:
                    raise GovernanceConflict("IDEMPOTENCY_KEY_REUSED", "The idempotency key already belongs to another directory mapping action.")
                entity_id = _text(prior_event.get("fields", {}).get("EntityId"))
                if entity_id in self.directory_mappings:
                    return self.directory_mappings[entity_id]
            if prior is not None:
                if _text(prior.get("fields", {}).get("RequestFingerprint")) != fingerprint:
                    raise GovernanceConflict("IDEMPOTENCY_KEY_REUSED", "The idempotency key already belongs to another directory mapping action.")
                return self.directory_mappings[f"directory-mapping:{prior['id']}"]
        item = super().transition_directory_mapping(mapping_id, action, actor=actor, reason=reason, expected_version=expected_version, idempotency_key=idempotency_key)
        record_id = self._grist_directory_mapping_ids.get(mapping_id)
        if record_id is None:
            raise GovernanceConflict("DIRECTORY_MAPPING_NOT_FOUND", "The directory mapping has no durable Grist record.")
        self.client.update_table_records("DirectoryProductMapping", [{"id": record_id, "fields": _directory_mapping_fields(item, request_key=idempotency_key, request_fingerprint=fingerprint if idempotency_key else None)}])
        for prior in self.directory_mappings.values():
            if prior.id != mapping_id and prior.status == "superseded" and prior.normalized_path == item.normalized_path:
                prior_record_id = self._grist_directory_mapping_ids.get(prior.id)
                if prior_record_id:
                    self.client.update_table_records("DirectoryProductMapping", [{"id": prior_record_id, "fields": _directory_mapping_fields(prior)}])
        event = max(self.audit_events.values(), key=lambda candidate: candidate.occurred_at)
        self._persist_governance_audit(event, idempotency_key=idempotency_key, fingerprint=fingerprint if idempotency_key else None)
        return item

    def _persist_governance_audit(self, event: AuditEvent, *, idempotency_key: str | None = None, fingerprint: str | None = None, payload_override: dict[str, Any] | None = None) -> None:
        self._write_guard()
        request_key = idempotency_key or event.id
        payload = payload_override or event.payload
        body_fingerprint = fingerprint or hashlib.sha256(json.dumps({"eventType": event.event_type, "actor": event.actor, "reason": event.reason, "entityType": event.entity_type, "entityId": event.entity_id, "payload": payload}, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
        self._ensure_request_record("AuditEvent", request_key, body_fingerprint, {"EventType": event.event_type, "Actor": event.actor, "OccurredAt": _grist_datetime(event.occurred_at), "EntityType": event.entity_type, "EntityId": event.entity_id, "Reason": event.reason, "Payload": json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))})

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


def _dict_value(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            decoded = json.loads(value)
            return decoded if isinstance(decoded, dict) else {"value": decoded}
        except json.JSONDecodeError:
            return {"raw": value}
    if isinstance(value, list) and value and value[0] == "L":
        return {"values": value[1:]}
    return {}


def _issue_fields(issue: ReconciliationIssue, grist_file_id: int | None) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "IssueType": issue.issue_type,
        "Severity": issue.severity,
        "Message": issue.message,
        "SourceFile": issue.source_file,
        "SourceRow": issue.source_row,
        "EntityId": issue.entity_id or "",
        "Status": issue.status,
        "CreatedAt": _grist_datetime(issue.first_seen_at or issue.created_at),
        "Fingerprint": issue.fingerprint,
        "EntityType": issue.entity_type,
        "SourcePath": issue.source_path,
        "SourceCell": issue.source_cell,
        "DetectedFacts": issue.detected_facts,
        "ProposedResolution": issue.proposed_resolution,
        "AssignedOwner": issue.assigned_owner or "",
        "FirstSeenAt": _grist_datetime(issue.first_seen_at or issue.created_at),
        "LastSeenAt": _grist_datetime(issue.last_seen_at or issue.created_at),
        "Version": issue.version,
        "ResolutionAction": issue.resolution_action,
        "ResolutionReason": issue.resolution_reason,
        "ResolvedActor": issue.resolved_actor or "",
        "ResolvedAt": _grist_datetime(issue.resolved_at) if issue.resolved_at else None,
        "DeferredActor": issue.deferred_actor or "",
        "DeferredAt": _grist_datetime(issue.deferred_at) if issue.deferred_at else None,
        "ReopenedActor": issue.reopened_actor or "",
        "ReopenedAt": _grist_datetime(issue.reopened_at) if issue.reopened_at else None,
    }
    if grist_file_id is not None:
        fields["CostingFile"] = grist_file_id
    return fields


def _observation_fields(observation: FileObservation, grist_file_id: int) -> dict[str, Any]:
    return {"CostingFile": grist_file_id, "ObservedAt": _grist_datetime(observation.observed_at), "RelativePath": observation.relative_path, "NormalizedPath": observation.normalized_path, "SizeBytes": observation.size_bytes, "ModifiedAt": _grist_datetime(observation.modified_at) if observation.modified_at else None, "FileHash": observation.file_hash or "", "Readable": observation.readable, "SheetCount": observation.sheet_count, "ExternalReferenceCount": observation.external_reference_count, "ParseError": observation.parse_error or ""}


def _directory_mapping_fields(mapping: DirectoryProductMapping, *, request_key: str | None = None, request_fingerprint: str | None = None) -> dict[str, Any]:
    fields = {"RelativePath": mapping.relative_path, "NormalizedPath": mapping.normalized_path, "Product": int(mapping.product_id), "Inherit": mapping.inherit, "Status": mapping.status, "Proposer": mapping.proposer, "Approver": mapping.approver or "", "Reason": mapping.reason, "CreatedAt": _grist_datetime(mapping.created_at), "UpdatedAt": _grist_datetime(mapping.updated_at), "Version": mapping.version, "SupersedesId": mapping.supersedes_id or ""}
    if request_key is not None:
        fields["RequestKey"] = request_key
    if request_fingerprint is not None:
        fields["RequestFingerprint"] = request_fingerprint
    return fields


def _costing_snapshot_fields(snapshot: CostingSnapshot, file_record_id: int, *, status: str) -> dict[str, Any]:
    return {
        "SnapshotKey": snapshot.snapshot_key,
        "CostingFile": file_record_id,
        "ObservedAt": _grist_datetime(snapshot.observed_at),
        "SemanticHash": snapshot.semantic_hash,
        # Grist cell values are scalar/encoded references, even for Any
        # columns. Store structured evidence as compact JSON text and decode it
        # through _dict_value when refreshing the repository.
        "SemanticContent": json.dumps(snapshot.semantic_content, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False),
        "SourceHashes": json.dumps(snapshot.source_hashes, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False),
        "Status": status,
        "PreviousSnapshotKey": snapshot.previous_snapshot_key or "",
        "AcceptedAt": _grist_datetime(snapshot.accepted_at),
        "AcceptedBy": snapshot.accepted_by,
        "AcceptanceReason": snapshot.acceptance_reason,
        "RequestKey": snapshot.request_key,
    }


def _numeric_value(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _costing_change_item_fields(
    item: dict[str, Any],
    *,
    item_key: str,
    snapshot_record_id: int,
    file_record_id: int,
    request_key: str,
    status: str,
) -> dict[str, Any]:
    data = item.get("change_data", {})
    cr_reference = data.get("cr_reference")
    if cr_reference is None:
        cr_reference = data.get("current_state", {}).get("cr_reference") if isinstance(data.get("current_state"), dict) else None
    if isinstance(cr_reference, (dict, list)):
        cr_reference = json.dumps(cr_reference, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    source_evidence = data.get("source_evidence") or {
        "previous": (data.get("previous_state") or {}).get("source_evidence") if isinstance(data.get("previous_state"), dict) else None,
        "current": (data.get("current_state") or {}).get("source_evidence") if isinstance(data.get("current_state"), dict) else None,
    }
    return {
        "ItemKey": item_key,
        "ChangeKey": item.get("change_key", ""),
        "Snapshot": snapshot_record_id,
        "PreviousSnapshotKey": item.get("previous_snapshot_key") or "",
        "CostingFile": file_record_id,
        "ChangeType": item.get("change_type", "semantic_change"),
        "Classification": item.get("classification", "design_structure_change"),
        "ChangeData": json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False),
        "PreviousState": json.dumps(data.get("previous_state"), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False),
        "CurrentState": json.dumps(data.get("current_state"), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False),
        "SourceEvidence": json.dumps(source_evidence, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False),
        "CostImpact": _numeric_value(data.get("cost_impact")),
        "CRReference": str(cr_reference or ""),
        "Status": status,
        "AcceptedAt": _grist_datetime(item.get("accepted_at")),
        "AcceptedBy": item.get("accepted_by", ""),
        "AcceptanceReason": item.get("acceptance_reason", ""),
        "RequestKey": request_key,
    }


def _governance_fingerprint(*values: Any) -> str:
    return hashlib.sha256(json.dumps(values, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":")).encode("utf-8")).hexdigest()


def _mutation_fingerprint(*values: Any) -> str:
    return hashlib.sha256(repr(values).encode("utf-8")).hexdigest()


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

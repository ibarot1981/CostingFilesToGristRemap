"""Audited, idempotent canonical-catalog upsert into Safari Manufacturing Grist."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.catalog_import import CatalogImportResult
from app.domain import ImportBatch, utc_now
from app.exceptions import GristDuplicateNameError, GristValidationError
from app.grist_types import grist_datetime, grist_list
from app.repository import sha256_file
from app.utils import normalize_text


@dataclass(frozen=True)
class CatalogGristPlan:
    document_id: str
    source_file: str
    source_hash: str | None
    parser_version: str
    idempotent: bool
    creates: dict[str, int]
    updates: dict[str, int]
    changes: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "documentId": self.document_id,
            "sourceFile": self.source_file,
            "sourceHash": self.source_hash,
            "parserVersion": self.parser_version,
            "idempotent": self.idempotent,
            "creates": dict(self.creates),
            "updates": dict(self.updates),
            "changes": list(self.changes),
        }


def sync_catalog_to_grist(
    client: Any,
    result: CatalogImportResult,
    *,
    apply: bool = False,
    parser_version: str = "catalog-0.2",
    expected_plan: CatalogGristPlan | None = None,
) -> CatalogGristPlan:
    """Plan by default; on apply, upsert identities without deleting records.

    A failed/uncertain attempt can be retried: identities are matched by their
    parent relationship and normalized display value before any create, while
    row-level aliases/issues are similarly deduplicated.
    """
    client.validate_safari_write_target()
    source_path = Path(result.source_file)
    source_hash = sha256_file(source_path) if source_path.is_file() else None
    table_ids = ("Product", "ProductModel", "ProductModelCode", "IdentityAlias", "ReconciliationIssue", "ImportBatch")
    tables = {table_id: client.fetch_table_records_with_ids(table_id) for table_id in table_ids}

    if source_hash:
        applied_batch = next((
            record for record in tables["ImportBatch"]
            if _text(record.get("fields", {}).get("SourceFile")) == result.source_file
            and _text(record.get("fields", {}).get("SourceHash")) == source_hash
            and _text(record.get("fields", {}).get("ParserVersion")) == parser_version
            and _text(record.get("fields", {}).get("Status")).casefold() == "applied"
        ), None)
        if applied_batch is not None:
            return CatalogGristPlan(client.doc_id, result.source_file, source_hash, parser_version, True, {}, {}, ())

    creates: dict[str, list[tuple[str, dict[str, Any], dict[str, str]]]] = {key: [] for key in table_ids}
    updates: dict[str, list[dict[str, Any]]] = {key: [] for key in table_ids}
    changes: list[dict[str, Any]] = []
    product_record_ids: dict[str, int] = {}
    product_domain_by_record: dict[str, str] = {}

    existing_products = _unique_index(tables["Product"], lambda fields: normalize_text(fields.get("Name")), "Product")
    for product in result.products:
        key = normalize_text(product.name)
        fields = {"Name": product.name, "SourceFile": product.source_file or "", "SourceRow": product.source_row or 0, "Active": product.active}
        existing = existing_products.get(key)
        if existing is None:
            creates["Product"].append((product.id, fields, {}))
            changes.append({"table": "Product", "action": "create", "identity": product.name})
        else:
            product_record_ids[product.id] = _record_id(existing)
            product_domain_by_record[str(existing["id"])] = product.id
            patch = _changed_fields(existing.get("fields", {}), fields)
            if patch:
                updates["Product"].append({"id": _record_id(existing), "fields": patch})
                changes.append({"table": "Product", "action": "update", "identity": product.name, "fields": sorted(patch)})

    existing_models = _unique_index(tables["ProductModel"], lambda fields: _parent_key(fields, "Product", product_domain_by_record, "ProductModel"), "Product Model")
    model_record_ids: dict[str, int] = {}
    model_domain_by_record: dict[str, str] = {}
    for model in result.models:
        key = (model.product_id, normalize_text(model.model_number))
        fields = {"ModelNumber": model.model_number, "Name": model.name, "LegacySparesOnly": model.legacy_spares_only, "SourceFile": model.source_file or "", "SourceRow": model.source_row or 0, "Active": model.active}
        existing = existing_models.get(key)
        if existing is None:
            creates["ProductModel"].append((model.id, fields, {"Product": model.product_id}))
            changes.append({"table": "ProductModel", "action": "create", "identity": model.model_number})
        else:
            model_record_ids[model.id] = _record_id(existing)
            model_domain_by_record[str(existing["id"])] = model.id
            patch = _changed_fields(existing.get("fields", {}), fields)
            if patch:
                updates["ProductModel"].append({"id": _record_id(existing), "fields": patch})
                changes.append({"table": "ProductModel", "action": "update", "identity": model.model_number, "fields": sorted(patch)})

    existing_codes = _unique_index(tables["ProductModelCode"], lambda fields: _parent_key(fields, "ProductModel", model_domain_by_record, "Product Model Code"), "Product Model Code")
    for code in result.codes:
        key = (code.model_id, normalize_text(code.code))
        fields = {"Code": code.code, "Description": code.description, "LegacySparesOnly": code.legacy_spares_only, "SourceValues": grist_list(code.source_values), "SourceFile": code.source_file or "", "SourceRow": code.source_row or 0, "Active": code.active}
        existing = existing_codes.get(key)
        if existing is None:
            creates["ProductModelCode"].append((code.id, fields, {"ProductModel": code.model_id}))
            changes.append({"table": "ProductModelCode", "action": "create", "identity": code.code})
        else:
            patch = _changed_fields(existing.get("fields", {}), fields)
            if patch:
                updates["ProductModelCode"].append({"id": _record_id(existing), "fields": patch})
                changes.append({"table": "ProductModelCode", "action": "update", "identity": code.code, "fields": sorted(patch)})

    existing_aliases = {_alias_signature(item.get("fields", {})) for item in tables["IdentityAlias"]}
    for alias in result.aliases:
        fields = {"EntityType": alias.entity_type, "EntityId": alias.entity_id, "Value": alias.value, "NormalizedValue": alias.normalized_value, "Source": alias.source, "SourceRow": alias.source_row or 0}
        if _alias_signature(fields) not in existing_aliases:
            creates["IdentityAlias"].append((alias.id, fields, {}))
            changes.append({"table": "IdentityAlias", "action": "create", "identity": alias.value})

    existing_issues = {_issue_signature(item.get("fields", {})) for item in tables["ReconciliationIssue"]}
    for issue in result.issues:
        fields = {"IssueType": issue.issue_type, "Severity": issue.severity, "Message": issue.message, "SourceFile": issue.source_file, "SourceRow": issue.source_row or 0, "EntityId": issue.entity_id or "", "Status": "open", "CreatedAt": grist_datetime(issue.created_at)}
        if _issue_signature(fields) not in existing_issues:
            creates["ReconciliationIssue"].append((issue.id, fields, {}))
            changes.append({"table": "ReconciliationIssue", "action": "create", "identity": f"row {issue.source_row}: {issue.issue_type}"})

    now = utc_now()
    batch = ImportBatch(id=f"catalog-grist:{source_hash or result.source_file}:{parser_version}", source_file=result.source_file, source_hash=source_hash, parser_version=parser_version, started_at=now, completed_at=now if apply else None, status="applied" if apply else "planned", outcome="canonical catalog upsert" if apply else "canonical catalog plan")
    creates["ImportBatch"].append((batch.id, {"SourceFile": batch.source_file, "SourceHash": batch.source_hash or "", "ParserVersion": batch.parser_version, "StartedAt": grist_datetime(batch.started_at), "CompletedAt": grist_datetime(batch.completed_at), "Status": batch.status, "Outcome": batch.outcome}, {}))

    plan = CatalogGristPlan(
        client.doc_id,
        result.source_file,
        source_hash,
        parser_version,
        False,
        {table_id: len(records) for table_id, records in creates.items() if records},
        {table_id: len(records) for table_id, records in updates.items() if records},
        tuple(changes),
    )
    if not apply:
        return plan
    if expected_plan is not None and plan.to_dict() != expected_plan.to_dict():
        raise GristValidationError("The Safari catalog changed after review; inspect the refreshed plan before applying.")

    client.validate_safari_write_target()
    for table_id in ("Product", "ProductModel", "ProductModelCode", "IdentityAlias", "ReconciliationIssue"):
        _apply_table(client, table_id, creates[table_id], updates[table_id], product_record_ids, model_record_ids)
    _apply_table(client, "ImportBatch", creates["ImportBatch"], [], product_record_ids, model_record_ids)
    return plan


def _apply_table(client: Any, table_id: str, rows: list[tuple[str, dict[str, Any], dict[str, str]]], updates: list[dict[str, Any]], product_ids: dict[str, int], model_ids: dict[str, int]) -> None:
    for domain_id, fields, references in rows:
        for column, target_id in references.items():
            target_map = product_ids if column == "Product" else model_ids
            if target_id not in target_map:
                raise GristValidationError(f"Cannot import {table_id}: parent identity {target_id!r} was not resolved.")
            fields[column] = target_map[target_id]
    if rows:
        created = client.create_table_records(table_id, [{"fields": fields} for _, fields, _ in rows])
        if len(created) != len(rows):
            raise GristValidationError(f"Grist did not confirm every {table_id} record created.")
        for (domain_id, _, _), record in zip(rows, created):
            record_id = record.get("id")
            if record_id is None:
                raise GristValidationError(f"Grist did not return a record ID for {table_id}.")
            if table_id == "Product":
                product_ids[domain_id] = int(record_id)
            elif table_id == "ProductModel":
                model_ids[domain_id] = int(record_id)
    if updates:
        client.update_table_records(table_id, updates)


def _unique_index(records: list[dict[str, Any]], key_for_fields: Any, label: str) -> dict[Any, dict[str, Any]]:
    output: dict[Any, dict[str, Any]] = {}
    for record in records:
        key = key_for_fields(record.get("fields", {}))
        if key is None or key == "" or (isinstance(key, tuple) and any(part in (None, "") for part in key)):
            continue
        if key in output:
            raise GristDuplicateNameError(f"Multiple existing {label} records match the same canonical identity; refusing to choose one.")
        output[key] = record
    return output


def _parent_key(fields: dict[str, Any], column: str, domain_by_record: dict[str, str], label: str) -> tuple[str, str] | None:
    parent_id = _reference_id(fields.get(column))
    parent_domain_id = domain_by_record.get(str(parent_id)) if parent_id is not None else None
    value = normalize_text(fields.get("ModelNumber" if column == "Product" else "Code"))
    if not parent_domain_id or not value:
        return None
    return parent_domain_id, value


def _reference_id(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, list) and len(value) > 1 and value[0] == "L":
        try:
            return int(value[1])
        except (TypeError, ValueError):
            return None
    return None


def _record_id(record: dict[str, Any]) -> int:
    try:
        return int(record["id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise GristValidationError("Existing canonical Grist record did not include a numeric ID.") from exc


def _changed_fields(current: dict[str, Any], expected: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in expected.items() if current.get(key) != value}


def _alias_signature(fields: dict[str, Any]) -> tuple[str, str, str, str, int]:
    return (normalize_text(fields.get("EntityType")), normalize_text(fields.get("EntityId")), normalize_text(fields.get("Value")), normalize_text(fields.get("Source")), _int(fields.get("SourceRow")))


def _issue_signature(fields: dict[str, Any]) -> tuple[str, str, int, str, str]:
    return (normalize_text(fields.get("IssueType")), normalize_text(fields.get("SourceFile")), _int(fields.get("SourceRow")), normalize_text(fields.get("EntityId")), normalize_text(fields.get("Message")))


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0

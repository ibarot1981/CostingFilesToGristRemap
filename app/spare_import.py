"""Read-only ODS preview and idempotent application for spare source evidence."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from app.milestone2 import OdsCell, OdsDocument, OdsSheet, read_ods
from app.grist_types import canonical_grist_json_cell, grist_datetime
from app.part_identity import request_fingerprint
from app.spares import GristSpareRegistry, SpareRegistryError, _json_value, normalize_alias, normalize_sourcing_type, parse_source_date, source_cost_age


PRODUCT_SHEETS = ("SparesDB", "Spares Detail", "Spares Summary")
TEMPLATE_SHEET = "Master"
PARSER_VERSION = "spares-ods-preview-v1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _cell_text(cell: OdsCell) -> str:
    value = cell.value
    if value is not None:
        return str(_json_value(value)).strip()
    return str(cell.display_text or "").strip()


def _cell_json(cell: OdsCell) -> dict[str, Any]:
    return {"cell": cell.coordinate, "formula": cell.formula, "cached_value": _json_value(cell.value), "cached_display": cell.display_text, "value_type": cell.value_type}


def _row_cells(sheet: OdsSheet, row_number: int, max_column: int) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for column in range(1, max_column + 1):
        cell = sheet.cell(row_number, column)
        if cell.value is not None or cell.formula or cell.display_text:
            result[cell.coordinate] = _cell_json(cell)
    return result


def _file_label(path: Path, root: Path | None) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix() if root else path.name
    except (OSError, ValueError):
        return path.name


def _numeric(value: Any) -> float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
        return float(parsed) if parsed.is_finite() else None
    except (InvalidOperation, ValueError, TypeError, OverflowError):
        return None


def _stable_key(*parts: Any) -> str:
    return hashlib.sha256(json.dumps(_json_value(parts), sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def _source_type_candidate(value: str) -> str | None:
    normalized = normalize_alias(value)
    if normalized in {"store", "stores", "purchased", "purchase", "external"}:
        return "purchased"
    if normalized in {"in house", "in-house", "inhouse", "manufactured", "internal"}:
        return "in_house"
    return None


def _resolve_alias(index: dict[str, Any], code: str) -> tuple[str, int | None, str]:
    normalized = normalize_alias(code)
    matches = index.get("by_alias", {}).get(normalized, []) if normalized else []
    masters: dict[str, dict[str, Any]] = {}
    for item in matches:
        fields = item["master"].get("fields", {})
        spare_key = str(fields.get("SpareKey") or "")
        if spare_key:
            masters[spare_key] = item["master"]
    if len(masters) == 1:
        spare_key, row = next(iter(masters.items()))
        return spare_key, int(row["id"]), "resolved_existing"
    if len(masters) > 1:
        return "", None, "conflict_multiple_canonical_spares"
    return "", None, "unresolved_no_canonical_spare"


def _record_ref(value: Any) -> int | None:
    if isinstance(value, (list, tuple)) and len(value) > 2 and value[0] == "R":
        value = value[2]
    try:
        number = int(value)
        return number if number > 0 else None
    except (TypeError, ValueError):
        return None


def _extract_rows(sheet: OdsSheet, header_row: int) -> list[tuple[int, dict[int, OdsCell]]]:
    return [(row, cells) for row, cells in sheet.nonempty_rows() if row > header_row and cells]


def _require_sheets(document: OdsDocument, names: tuple[str, ...], label: str) -> None:
    missing = sorted(set(names) - set(document.sheets))
    if missing:
        raise SpareRegistryError("SPARE_SOURCE_SHEET_MISSING", f"{label} is missing required sheet(s): {', '.join(missing)}.", 422)


def build_import_plan(*, product_path: Path, template_path: Path, registry: GristSpareRegistry, costing_root: Path | None = None, association_key: str = "") -> dict[str, Any]:
    """Read current workbook revisions and prepare a deterministic, read-only import plan."""
    product_hash_before = _sha256(product_path)
    template_hash_before = _sha256(template_path)
    product = read_ods(product_path, set(PRODUCT_SHEETS))
    template = read_ods(template_path, {TEMPLATE_SHEET})
    _require_sheets(product, PRODUCT_SHEETS, "The associated costing workbook")
    _require_sheets(template, (TEMPLATE_SHEET,), "The configured spare master lineage workbook")
    index = registry.identity_index()

    product_spares = product.sheets["SparesDB"]
    product_details = product.sheets["Spares Detail"]
    product_summary = product.sheets["Spares Summary"]
    template_master = template.sheets[TEMPLATE_SHEET]
    spares_rows = _extract_rows(product_spares, 7)
    template_rows = _extract_rows(template_master, 1)

    source_by_code: dict[str, list[dict[str, Any]]] = {}
    for row_number, cells in spares_rows:
        code = _cell_text(product_spares.cell(row_number, 1))
        if code:
            source_by_code.setdefault(normalize_alias(code), []).append({"row": row_number, "cells": cells, "code": code})
    template_by_code: dict[str, list[tuple[int, dict[int, OdsCell]]]] = {}
    for row_number, cells in template_rows:
        code = _cell_text(template_master.cell(row_number, 1))
        if code:
            template_by_code.setdefault(normalize_alias(code), []).append((row_number, cells))

    identities: list[dict[str, Any]] = []
    create_by_code: dict[str, dict[str, Any]] = {}
    for normalized_code, sources in sorted(source_by_code.items()):
        source = sources[0]
        row = source["row"]
        code = source["code"]
        description = _cell_text(product_spares.cell(row, 3))
        category = _cell_text(product_spares.cell(row, 2))
        unit = _cell_text(product_spares.cell(row, 4))
        template_matches = template_by_code.get(normalized_code, [])
        candidate = _source_type_candidate(_cell_text(template_master.cell(template_matches[0][0], 8))) if len(template_matches) == 1 else None
        spare_key, record_id, resolution = _resolve_alias(index, code)
        existing_master = index.get("masters_by_id", {}).get(record_id) if record_id else None
        existing_fields = existing_master.get("fields", {}) if existing_master else {}
        if len(sources) > 1:
            action, resolution = "conflict", "duplicate_legacy_code_in_sparesdb"
        elif resolution == "conflict_multiple_canonical_spares":
            action = "conflict"
        elif not description:
            action, resolution = "unresolved", "missing_source_description"
        elif resolution == "resolved_existing":
            action = "noop"
        else:
            action = "create"
        row_item = {"legacyCode": code, "normalizedLegacyCode": normalized_code, "sourceRow": row, "description": description, "category": category, "unit": unit, "action": action, "resolution": resolution, "sourcingTypeCandidate": candidate, "sourcingTypeSourceValue": _cell_text(template_master.cell(template_matches[0][0], 8)) if len(template_matches) == 1 else "", "templateSourceRow": template_matches[0][0] if len(template_matches) == 1 else None, "templateMatchCount": len(template_matches), "spareKey": spare_key, "recordId": record_id, "canonicalSpareCode": str(existing_fields.get("SpareCode") or ""), "maintainedDescription": str(existing_fields.get("Description") or ""), "canonicalStatus": str(existing_fields.get("Status") or "")}
        identities.append(row_item)
        if action == "create":
            create_by_code[normalized_code] = row_item

    for row_number, _ in spares_rows:
        code_cell = product_spares.cell(row_number, 1)
        if not _cell_text(code_cell):
            identities.append({"legacyCode": "", "normalizedLegacyCode": "", "sourceRow": row_number, "description": _cell_text(product_spares.cell(row_number, 3)), "category": _cell_text(product_spares.cell(row_number, 2)), "unit": _cell_text(product_spares.cell(row_number, 4)), "action": "unresolved", "resolution": "unresolved_missing_legacy_code", "sourcingTypeCandidate": None, "sourcingTypeSourceValue": "", "templateSourceRow": None, "templateMatchCount": 0, "spareKey": "", "recordId": None, "canonicalSpareCode": "", "maintainedDescription": "", "canonicalStatus": ""})

    identity_by_code = {item["normalizedLegacyCode"]: item for item in identities}
    product_label = _file_label(product_path, costing_root)
    template_label = _file_label(template_path, costing_root)
    source_evidence: list[dict[str, Any]] = []
    cost_evidence: list[dict[str, Any]] = []
    compositions: list[dict[str, Any]] = []
    unresolved_source_rows = 0

    def attach_evidence(*, source_kind: str, path_label: str, source_hash: str, sheet_name: str, row_number: int, source_code: str, code_cell: OdsCell, cells: dict[str, dict[str, Any]], row_action: str, classification_candidate: str | None = None, date_cell: OdsCell | None = None, date_meaning: str = "") -> None:
        nonlocal unresolved_source_rows
        normalized_code = normalize_alias(source_code)
        identity = identity_by_code.get(normalized_code)
        resolution = row_action
        if not source_code:
            resolution = "unresolved_missing_legacy_code"
        elif identity:
            resolution = identity["resolution"] if identity["action"] in {"conflict", "unresolved"} else "resolved_unique_source_identity"
        spare_key = identity.get("spareKey", "") if identity and resolution == "resolved_unique_source_identity" else ""
        source_date_raw = _cell_text(date_cell) if date_cell else ""
        source_date, date_parse_status = parse_source_date(date_cell.value if date_cell and date_cell.value is not None else source_date_raw) if date_cell else (None, "missing")
        evidence_body = {"sourceKind": source_kind, "sourceFile": path_label, "sourceHash": source_hash, "sheetName": sheet_name, "sourceRow": row_number, "sourceCell": code_cell.coordinate if code_cell else "", "legacyCode": source_code, "formula": code_cell.formula if code_cell else None, "cachedValue": _json_value(code_cell.value) if code_cell else None, "dateRaw": source_date_raw, "dateISO": source_date, "dateParseStatus": date_parse_status, "classificationCandidate": classification_candidate, "resolution": resolution, "cells": cells}
        evidence_key = "src:" + _stable_key(source_hash, path_label, sheet_name, row_number, source_kind)
        source_evidence.append({"key": evidence_key, "sourceKind": source_kind, "sourceFile": path_label, "sourceHash": source_hash, "sheetName": sheet_name, "sourceRow": row_number, "sourceCell": code_cell.coordinate if code_cell else "", "legacyCode": source_code, "formula": code_cell.formula if code_cell else None, "cachedValue": _json_value(code_cell.value) if code_cell else None, "sourceDateRaw": source_date_raw, "sourceDateISO": source_date or "", "sourceDateMeaning": date_meaning, "classificationCandidate": classification_candidate or "", "resolutionStatus": resolution, "cells": cells, "spareKey": spare_key, "fingerprint": request_fingerprint(evidence_body)})
        if resolution.startswith("unresolved") or resolution.startswith("conflict"):
            unresolved_source_rows += 1

    for row_number, cells in spares_rows:
        code_cell = product_spares.cell(row_number, 1)
        code = _cell_text(code_cell)
        normalized = normalize_alias(code)
        identity = identity_by_code.get(normalized)
        row_action = identity["resolution"] if identity else "unresolved_missing_legacy_code"
        row_cells = _row_cells(product_spares, row_number, 7)
        template_matches = template_by_code.get(normalized, [])
        candidate = _source_type_candidate(_cell_text(template_master.cell(template_matches[0][0], 8))) if len(template_matches) == 1 else None
        attach_evidence(source_kind="SparesDB", path_label=product_label, source_hash=product_hash_before, sheet_name="SparesDB", row_number=row_number, source_code=code, code_cell=code_cell, cells=row_cells, row_action=row_action, classification_candidate=candidate, date_cell=product_spares.cell(row_number, 7), date_meaning="SparesDB column G is labelled Date; source semantics are not specified.")
        price = product_spares.cell(row_number, 5)
        if price.value is not None or price.formula or price.display_text:
            date_cell = product_spares.cell(row_number, 7)
            parsed_date, parse_status = parse_source_date(date_cell.value if date_cell.value is not None else date_cell.display_text)
            cost_body = {"sourceFile": product_label, "sourceHash": product_hash_before, "sheet": "SparesDB", "row": row_number, "cell": price.coordinate, "kind": "source_purchase_price", "value": _json_value(price.value), "formula": price.formula, "date": parsed_date, "dateParseStatus": parse_status, "code": code}
            legacy_cost_body = {key: value for key, value in cost_body.items() if key != "sourceFile"}
            cost_evidence.append({"key": "cost:" + _stable_key(product_hash_before, product_label, "SparesDB", row_number, price.coordinate, "source_purchase_price"), "_legacyKey": "cost:" + _stable_key(product_hash_before, "SparesDB", row_number, price.coordinate, "source_purchase_price"), "_legacyFingerprint": request_fingerprint(legacy_cost_body), "spareKey": identity.get("spareKey", "") if identity and identity["action"] in {"noop", "create"} else "", "legacyCode": code, "costKind": "source_purchase_price", "value": _numeric(price.value), "cachedValue": _json_value(price.value), "unit": _cell_text(product_spares.cell(row_number, 4)), "currency": "", "sourceFile": product_label, "sourceHash": product_hash_before, "sheetName": "SparesDB", "sourceRow": row_number, "sourceCell": price.coordinate, "formula": price.formula or "", "sourceDateRaw": _cell_text(date_cell), "sourceDateISO": parsed_date or "", "sourceDateMeaning": "SparesDB column G is labelled Date; source semantics are not specified.", "dateBasis": "formula_cache" if date_cell.formula else "source_value" if parsed_date else parse_status, "entryISO": "", "fingerprint": request_fingerprint(cost_body)})

    # Preserve every template lineage row, including unmatched records; only an
    # exact unique legacy-code match is linked to a canonical spare.
    for row_number, cells in template_rows:
        code_cell = template_master.cell(row_number, 1)
        code = _cell_text(code_cell)
        normalized = normalize_alias(code)
        identity = identity_by_code.get(normalized)
        matches = template_by_code.get(normalized, [])
        template_type_cell = template_master.cell(row_number, 8)
        candidate = _source_type_candidate(_cell_text(template_type_cell)) if code and len(matches) == 1 else None
        row_cells = _row_cells(template_master, row_number, 22)
        if not identity:
            row_action = "unresolved_template_identity_not_in_associated_sparesdb"
        else:
            row_action = identity["resolution"]
        attach_evidence(source_kind="TemplateMaster", path_label=template_label, source_hash=template_hash_before, sheet_name=TEMPLATE_SHEET, row_number=row_number, source_code=code, code_cell=code_cell, cells=row_cells, row_action=row_action, classification_candidate=candidate, date_cell=template_master.cell(row_number, 12), date_meaning="Template Master column L is labelled New Price Date Changed.")
        for price_column, date_column, cost_kind, meaning in (
            (5, 0, "source_purchase_price", "Template Master purchase-price source; no explicit date is attached."),
            (6, 0, "source_selling_price", "Template Master selling-price source; retained separately from cost."),
            (11, 12, "template_new_purchase_price", "Template Master L is labelled New Price Date Changed; this is source update-date evidence."),
            (16, 22, "template_new_selling_price", "Template Master V is labelled sales price date; date semantics are retained as labelled."),
        ):
            price = template_master.cell(row_number, price_column)
            if price.value is None and not price.formula and not price.display_text:
                continue
            date_cell = template_master.cell(row_number, date_column) if date_column else None
            parsed_date, parse_status = parse_source_date(date_cell.value if date_cell and date_cell.value is not None else date_cell.display_text if date_cell else None)
            body = {"sourceFile": template_label, "sourceHash": template_hash_before, "sheet": TEMPLATE_SHEET, "row": row_number, "cell": price.coordinate, "kind": cost_kind, "value": _json_value(price.value), "formula": price.formula, "date": parsed_date, "legacyCode": code}
            legacy_body = {key: value for key, value in body.items() if key != "sourceFile"}
            cost_evidence.append({"key": "cost:" + _stable_key(template_hash_before, template_label, TEMPLATE_SHEET, row_number, price.coordinate, cost_kind), "_legacyKey": "cost:" + _stable_key(template_hash_before, TEMPLATE_SHEET, row_number, price.coordinate, cost_kind), "_legacyFingerprint": request_fingerprint(legacy_body), "spareKey": identity.get("spareKey", "") if identity and identity["action"] in {"noop", "create"} else "", "legacyCode": code, "costKind": cost_kind, "value": _numeric(price.value), "cachedValue": _json_value(price.value), "unit": _cell_text(template_master.cell(row_number, 4)), "currency": "", "sourceFile": template_label, "sourceHash": template_hash_before, "sheetName": TEMPLATE_SHEET, "sourceRow": row_number, "sourceCell": price.coordinate, "formula": price.formula or "", "sourceDateRaw": _cell_text(date_cell) if date_cell else "", "sourceDateISO": parsed_date or "", "sourceDateMeaning": meaning if date_cell else "", "dateBasis": "formula_cache" if date_cell and date_cell.formula else "source_value" if parsed_date else parse_status, "entryISO": parsed_date if cost_kind == "template_new_purchase_price" else "", "fingerprint": request_fingerprint(body)})

    all_known_codes = set(source_by_code) | set(index.get("by_alias", {}))
    def parent_resolution(code: str) -> tuple[str, str]:
        norm = normalize_alias(code)
        identity = identity_by_code.get(norm)
        if not norm:
            return "", "unresolved_missing_parent_code"
        if identity and identity["action"] == "conflict":
            return "", "conflicting_parent_identity"
        if identity and identity["action"] == "unresolved":
            return "", "unresolved_parent_identity"
        if identity and identity["action"] == "create":
            return "", "planned_new_parent_identity"
        key, record_id, resolution = _resolve_alias(index, code)
        return key, "resolved_parent" if resolution == "resolved_existing" else "unresolved_parent_identity"

    def add_composition(sheet: OdsSheet, row_number: int, source_kind: str, source_hash: str, path_label: str, max_column: int) -> None:
        nonlocal unresolved_source_rows
        code_column = 2
        code_cell = sheet.cell(row_number, code_column)
        code = _cell_text(code_cell)
        parent_key, resolution = parent_resolution(code)
        line_type = _cell_text(sheet.cell(row_number, 4)) if source_kind == "SparesDetail" else ""
        line_code = _cell_text(sheet.cell(row_number, 5)) if source_kind == "SparesDetail" else ""
        line_kind = normalize_alias(line_type)
        manufacturing_code = line_code if any(token in line_kind for token in ("manufact", "mcl", "tool shop")) else ""
        stores_code = line_code if "store" in line_kind else ""
        nested_code = ""
        nested_status = "not_identified"
        if source_kind == "SparesDetail":
            for column in (5, 6, 7):
                candidate = _cell_text(sheet.cell(row_number, column))
                if normalize_alias(candidate) in all_known_codes and normalize_alias(candidate) != normalize_alias(code):
                    nested_code = candidate
                    nested_status = "source_value_matches_legacy_code_pending_review"
                    break
        cells = _row_cells(sheet, row_number, max_column)
        quantity = _numeric(sheet.cell(row_number, 8).value) if source_kind == "SparesDetail" else None
        override = _numeric(sheet.cell(row_number, 10).value) if source_kind == "SparesDetail" else None
        body = {"sourceFile": path_label, "sourceHash": source_hash, "sheet": sheet.name, "row": row_number, "kind": source_kind, "cells": cells, "nestedCode": nested_code}
        legacy_body = {key: value for key, value in body.items() if key != "sourceFile"}
        compositions.append({"key": "composition:" + _stable_key(source_hash, path_label, sheet.name, row_number, source_kind), "_legacyKey": "composition:" + _stable_key(source_hash, sheet.name, row_number, source_kind), "_legacyFingerprint": request_fingerprint(legacy_body), "sourceKind": source_kind, "sourceFile": path_label, "sourceHash": source_hash, "sheetName": sheet.name, "sourceRow": row_number, "sourceIdentity": code, "sourceLineType": line_type, "sourceLineCode": line_code, "manufacturingLineCode": manufacturing_code, "storesLineCode": stores_code, "nestedLegacySpareCode": nested_code, "nestedReferenceStatus": nested_status, "quantity": quantity, "quantityUnit": "", "quantityOverride": override, "overrideUnit": "", "resolutionStatus": resolution, "cells": cells, "spareKey": parent_key, "fingerprint": request_fingerprint(body)})
        if resolution != "resolved_parent":
            unresolved_source_rows += 1

    for row_number, _ in _extract_rows(product_details, 7):
        add_composition(product_details, row_number, "SparesDetail", product_hash_before, product_label, 11)
    for row_number, _ in _extract_rows(product_summary, 4):
        add_composition(product_summary, row_number, "SparesSummary", product_hash_before, product_label, 9)

    product_hash_after = _sha256(product_path)
    template_hash_after = _sha256(template_path)
    if product_hash_before != product_hash_after or template_hash_before != template_hash_after:
        raise SpareRegistryError("SPARE_IMPORT_STALE", "A source workbook changed while the read-only preview was being prepared. Reload the preview.", 409)

    masters_fingerprint = request_fingerprint({"masters": sorted((str(r.get("fields", {}).get("SpareKey") or ""), str(r.get("fields", {}).get("SpareCode") or ""), str(r.get("fields", {}).get("Description") or ""), str(r.get("fields", {}).get("Category") or ""), str(r.get("fields", {}).get("Unit") or ""), str(r.get("fields", {}).get("SourcingType") or ""), str(r.get("fields", {}).get("Status") or ""), int(r.get("fields", {}).get("Version") or 1)) for r in index["masters"]), "aliases": sorted((normalize_alias(r.get("fields", {}).get("Value")), str(index["masters_by_id"].get(_record_ref(r.get("fields", {}).get("SpareMaster")), {}).get("fields", {}).get("SpareKey") or ""), str(r.get("fields", {}).get("SourceHash") or ""), str(r.get("fields", {}).get("AliasKey") or "")) for r in index["aliases"])})
    source_hashes = {"associatedWorkbook": product_hash_before, "templateMaster": template_hash_before, "associationContext": request_fingerprint(association_key)}
    counts = {"creates": sum(item["action"] == "create" for item in identities), "updates": 0, "noops": sum(item["action"] == "noop" for item in identities), "conflicts": sum(item["action"] == "conflict" for item in identities), "unresolvedIdentities": sum(item["action"] == "unresolved" for item in identities), "unclassifiedCandidates": sum(not item.get("sourcingTypeCandidate") for item in identities if item["action"] in {"create", "noop"}), "sourceEvidence": len(source_evidence), "costEvidence": len(cost_evidence), "missingCostDates": sum(not item.get("sourceDateISO") for item in cost_evidence), "futureCostDates": sum(bool(item.get("sourceDateISO")) and source_cost_age(item["sourceDateISO"]).get("status") == "future_date" for item in cost_evidence), "compositionEvidence": len(compositions), "unresolvedEvidenceRows": unresolved_source_rows, "templateRows": len(template_rows), "associatedSpareRows": len(spares_rows)}
    fingerprint_body = {"parserVersion": PARSER_VERSION, "associationKey": association_key, "sourceHashes": source_hashes, "identityState": masters_fingerprint, "identities": [{key: item.get(key) for key in ("legacyCode", "action", "resolution", "description", "category", "unit", "sourcingTypeCandidate", "spareKey", "canonicalSpareCode", "maintainedDescription", "canonicalStatus")} for item in identities], "counts": counts}
    fingerprint = request_fingerprint(fingerprint_body)
    return {"parserVersion": PARSER_VERSION, "associationKey": association_key, "sourceHashes": source_hashes, "sourceLabels": {"associatedWorkbook": product_label, "templateMaster": template_label}, "previewFingerprint": fingerprint, "counts": counts, "identities": identities, "sourceEvidence": source_evidence, "costEvidence": cost_evidence, "compositions": compositions, "conflicts": [item for item in identities if item["action"] == "conflict"], "unresolved": [item for item in identities if item["action"] == "unresolved"]}


def preview_payload(plan: dict[str, Any], *, offset: int = 0, limit: int = 100, evidence_offset: int = 0, evidence_limit: int = 50) -> dict[str, Any]:
    identities = plan["identities"]
    source_evidence = plan["sourceEvidence"]
    cost_evidence = plan["costEvidence"]
    compositions = plan["compositions"]
    public_rows = lambda rows: [{key: value for key, value in item.items() if not key.startswith("_")} for item in rows]
    return {"parserVersion": plan["parserVersion"], "associationKey": plan["associationKey"], "sourceHashes": plan["sourceHashes"], "sourceLabels": plan["sourceLabels"], "previewFingerprint": plan["previewFingerprint"], "counts": plan["counts"], "identityRows": identities[offset:offset + limit], "identityOffset": offset, "identityLimit": limit, "identityTotal": len(identities), "sourceEvidenceRows": public_rows(source_evidence[evidence_offset:evidence_offset + evidence_limit]), "costEvidenceRows": public_rows(cost_evidence[evidence_offset:evidence_offset + evidence_limit]), "compositionEvidenceRows": public_rows(compositions[evidence_offset:evidence_offset + evidence_limit]), "evidenceOffset": evidence_offset, "evidenceLimit": evidence_limit, "evidenceTotals": {"sourceEvidence": len(source_evidence), "costEvidence": len(cost_evidence), "compositionEvidence": len(compositions)}, "conflicts": plan["conflicts"][offset:offset + limit], "unresolved": plan["unresolved"][offset:offset + limit], "reviewRequired": True, "readOnly": True}


def _grist_ref(spare_key: str, resolved: dict[str, dict[str, Any]]) -> int | None:
    item = resolved.get(normalize_alias(spare_key))
    return int(item["recordId"]) if item and item.get("recordId") else None


def apply_import_plan(*, plan: dict[str, Any], registry: GristSpareRegistry, actor: str, request_key: str, expected_preview_fingerprint: str | None = None) -> dict[str, Any]:
    preview_fingerprint = expected_preview_fingerprint or str(plan.get("previewFingerprint") or "")
    if not preview_fingerprint or not request_key.strip():
        raise SpareRegistryError("SPARE_IMPORT_REQUEST_INVALID", "A reviewed preview and unique retry key are required.")
    request_fingerprint_value = request_fingerprint({"previewFingerprint": preview_fingerprint, "sourceHashes": plan["sourceHashes"]})
    batch = registry.start_import(request_key=request_key, fingerprint=request_fingerprint_value, preview_fingerprint=preview_fingerprint, source_hashes=plan["sourceHashes"], actor=actor)
    batch_fields = batch.get("fields", {})
    if str(batch_fields.get("Status") or "") == "complete":
        result = batch_fields.get("Result")
        try:
            result = json.loads(result) if isinstance(result, str) else result
        except (TypeError, ValueError):
            result = {}
        if isinstance(result, dict):
            return {**result, "idempotent": True}

    resolved: dict[str, dict[str, Any]] = {}
    index = registry.identity_index()
    for normalized_code, owners in index["by_alias"].items():
        unique = {str(item["master"].get("fields", {}).get("SpareKey") or ""): item["master"] for item in owners}
        if len(unique) == 1:
            spare_key, master = next(iter(unique.items()))
            resolved[normalized_code] = {"spareKey": spare_key, "recordId": int(master["id"])}

    for identity in plan["identities"]:
        norm = identity["normalizedLegacyCode"]
        if identity["action"] == "create":
            operation_key = "import-create:" + _stable_key(request_key, norm)
            provenance = {"sourceKind": "SparesDB", "sourceFile": plan["sourceLabels"]["associatedWorkbook"], "sourceHash": plan["sourceHashes"]["associatedWorkbook"], "sheetName": "SparesDB", "sourceRow": identity["sourceRow"], "sourceCell": f"A{identity['sourceRow']}"}
            alias_evidence = next((item for item in plan["sourceEvidence"] if item["sourceKind"] == "SparesDB" and normalize_alias(item["legacyCode"]) == norm), None)
            aliases = [{"value": identity["legacyCode"], "provenance": {**provenance, "identityStatus": alias_evidence["resolutionStatus"]}, "evidenceFingerprint": alias_evidence["fingerprint"]}] if alias_evidence else []
            result = registry.create_spare(description=identity["description"], category=identity["category"], unit=identity["unit"], sourcing_type=identity["sourcingTypeCandidate"] or "unclassified", actor=actor, reason="Reviewed import from associated costing workbook.", request_key=operation_key, aliases=aliases, source_provenance={"importRequestKey": request_key, **provenance})
            resolved[norm] = {"spareKey": result["spareKey"], "recordId": int(result["recordId"])}
        elif identity["action"] == "noop":
            resolved[norm] = {"spareKey": identity["spareKey"], "recordId": int(identity["recordId"])}
            # Alias rows are ensured with their exact source-evidence fingerprint
            # below. Re-ensuring here with a weaker derived fingerprint conflicts
            # with the immutable provenance already stored for this same alias key.
    registry.update_import_progress(request_key, {"stage": "identities", "completed": len(resolved), "total": len(plan["identities"]), "creates": plan["counts"]["creates"], "noops": plan["counts"]["noops"]})

    source_rows = []
    now = grist_datetime(datetime.now(timezone.utc))
    for item in plan["sourceEvidence"]:
        ref = resolved.get(normalize_alias(item["legacyCode"])) if item["legacyCode"] else None
        provenance = {"sourceKind": item["sourceKind"], "sourceFile": item["sourceFile"], "sourceHash": item["sourceHash"], "sheetName": item["sheetName"], "sourceRow": item["sourceRow"], "sourceCell": item["sourceCell"], "identityStatus": item["resolutionStatus"]}
        if ref and item["legacyCode"]:
            registry.ensure_alias(spare_key=ref["spareKey"], spare_record_id=ref["recordId"], value=item["legacyCode"], provenance=provenance, evidence_fingerprint=item["fingerprint"])
        source_rows.append({"EvidenceKey": item["key"], "SpareMaster": ref["recordId"] if ref else None, "SourceKind": item["sourceKind"], "SourceFile": item["sourceFile"], "SourceHash": item["sourceHash"], "SheetName": item["sheetName"], "SourceRow": item["sourceRow"], "SourceCell": item["sourceCell"], "LegacyItemCode": item["legacyCode"], "Formula": item["formula"] or "", "CachedValue": canonical_grist_json_cell({"value": item["cachedValue"]}), "SourceDateRaw": item["sourceDateRaw"], "SourceDateISO": item["sourceDateISO"], "SourceDateMeaning": item["sourceDateMeaning"], "ClassificationCandidate": item["classificationCandidate"], "ResolutionStatus": item["resolutionStatus"], "ObservedAt": now, "RawCells": canonical_grist_json_cell(item["cells"]), "EvidenceFingerprint": item["fingerprint"]})
    source_result = registry.ensure_rows("SpareSourceEvidence", "EvidenceKey", source_rows)
    registry.update_import_progress(request_key, {"stage": "source-evidence", "created": source_result["created"], "existing": source_result["existing"], "total": len(source_rows)})

    cost_rows = []
    for item in plan["costEvidence"]:
        ref = resolved.get(normalize_alias(item["legacyCode"])) if item["legacyCode"] else None
        cost_body = {key: item[key] for key in ("key", "costKind", "value", "cachedValue", "unit", "currency", "sourceFile", "sourceHash", "sheetName", "sourceRow", "sourceCell", "formula", "sourceDateRaw", "sourceDateISO", "sourceDateMeaning", "dateBasis", "fingerprint")}
        legacy_cost_body = {**cost_body, "key": item.get("_legacyKey", cost_body["key"]), "fingerprint": item.get("_legacyFingerprint", cost_body["fingerprint"])}
        cost_rows.append({"CostEvidenceKey": item["key"], "SpareMaster": ref["recordId"] if ref else None, "CostKind": item["costKind"], "SourceValue": item["value"], "Unit": item["unit"], "Currency": item["currency"], "SourceReference": f"{item['sourceFile']}#{item['sheetName']}.{item['sourceCell']}", "SourceHash": item["sourceHash"], "SheetName": item["sheetName"], "SourceRow": item["sourceRow"], "SourceCell": item["sourceCell"], "Formula": item["formula"], "CachedValue": canonical_grist_json_cell({"value": item["cachedValue"]}), "SourceDateRaw": item["sourceDateRaw"], "SourceDateISO": item["sourceDateISO"], "SourceDateMeaning": item["sourceDateMeaning"], "EntryAt": None, "UpdatedAt": grist_datetime(item["entryISO"] + "T00:00:00+05:30") if item["entryISO"] else None, "EffectiveAt": None, "TransactionAt": None, "ObservedAt": now, "ImportedAt": now, "RecalculatedAt": None, "DateBasis": item["dateBasis"], "ApprovalStatus": "unapproved_source_evidence", "EvidenceFingerprint": request_fingerprint(cost_body)})
        cost_rows[-1]["_legacyEvidenceFingerprint"] = request_fingerprint(legacy_cost_body)
    cost_result = registry.ensure_rows("SpareSourceCost", "CostEvidenceKey", cost_rows)
    registry.update_import_progress(request_key, {"stage": "cost-evidence", "created": cost_result["created"], "existing": cost_result["existing"], "total": len(cost_rows)})

    composition_rows = []
    for item in plan["compositions"]:
        ref = resolved.get(normalize_alias(item["sourceIdentity"])) if item["sourceIdentity"] else None
        composition_rows.append({"CompositionKey": item["key"], "_legacyFingerprint": item.get("_legacyFingerprint", ""), "ParentSpare": ref["recordId"] if ref else None, "SourceKind": item["sourceKind"], "SourceFile": item["sourceFile"], "SourceHash": item["sourceHash"], "SheetName": item["sheetName"], "SourceRow": item["sourceRow"], "SourceIdentity": item["sourceIdentity"], "SourceLineType": item["sourceLineType"], "SourceLineCode": item["sourceLineCode"], "ManufacturingLineCode": item["manufacturingLineCode"], "StoresLineCode": item["storesLineCode"], "NestedLegacySpareCode": item["nestedLegacySpareCode"], "NestedReferenceStatus": item["nestedReferenceStatus"], "Quantity": item["quantity"], "QuantityUnit": item["quantityUnit"], "QuantityOverride": item["quantityOverride"], "OverrideUnit": item["overrideUnit"], "ResolutionStatus": "resolved_parent" if ref else item["resolutionStatus"], "SourceCells": canonical_grist_json_cell(item["cells"]), "ObservedAt": now, "EvidenceFingerprint": item["fingerprint"]})
    composition_result = registry.ensure_rows("SpareCompositionEvidence", "CompositionKey", composition_rows)
    result = {"requestKey": request_key, "previewFingerprint": preview_fingerprint, "status": "complete", "counts": plan["counts"], "created": {"sourceEvidence": source_result["created"], "costEvidence": cost_result["created"], "compositionEvidence": composition_result["created"]}, "existing": {"sourceEvidence": source_result["existing"], "costEvidence": cost_result["existing"], "compositionEvidence": composition_result["existing"]}, "resolvedSpareCount": len(resolved), "unresolvedEvidenceCount": plan["counts"]["unresolvedEvidenceRows"], "idempotent": False}
    registry.update_import_progress(request_key, {"stage": "composition-evidence", "created": composition_result["created"], "existing": composition_result["existing"], "total": len(composition_rows)})
    registry.finish_import(request_key, result)
    return result

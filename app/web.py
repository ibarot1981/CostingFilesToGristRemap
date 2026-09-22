"""FastAPI application for the Safari Manufacturing Phase 0 workbench."""

from __future__ import annotations

import json
from datetime import datetime
import os
from dataclasses import replace
from pathlib import Path
from typing import Any
from zipfile import ZipFile
import xml.etree.ElementTree as ET

from fastapi import Body, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware

from app.catalog import scan_costing_file
from app.catalog_import import import_catalog
from app.domain import CostingFile as DomainCostingFile, FileObservation, utc_now
from app.filesystem_catalog import FilesystemCatalog, PathSafetyError
from app.repository import AssociationConflict, AssociationProposal, InMemorySafariRepository, sha256_file
from app.workbook import OdsWorkbook


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_COSTING_ROOT = Path(r"C:\Irshad\Safari\DRWGD\Products Costing")
DEFAULT_CATALOG = PROJECT_ROOT / "reports" / "first_pass_costing_catalog.json"
DEFAULT_CATALOG_NAME = "Product-ProductModelNo-ModelCode.ods"

app = FastAPI(title="Safari Manufacturing ERP", version="0.2.0")
app.add_middleware(CORSMiddleware, allow_origins=["http://127.0.0.1:4320", "http://localhost:4320"], allow_credentials=True, allow_methods=["GET", "POST"], allow_headers=["*"])
_repository: InMemorySafariRepository | None = None


@app.get("/api/health")
def health(request: Request) -> dict[str, Any]:
    repository = _get_repository()
    grist_writes_enabled = repository.adapter_name == "grist-safari"
    return {"status": "ok", "mode": "local" if repository.adapter_name == "in-memory" else "grist", "adapter": repository.adapter_name, "user": _request_user(request), "catalogAvailable": _catalog_path().exists(), "costingRoot": str(_costing_root()), "writeEnabled": grist_writes_enabled}


@app.get("/api/catalog/summary")
def catalog_summary() -> dict[str, Any]:
    path = _catalog_path()
    if path.exists() and path.suffix.casefold() == ".json":
        return _load_catalog()["summary"]
    try:
        files = _filesystem().walk_files()
    except (FileNotFoundError, PathSafetyError) as exc:
        raise _error(503, "COSTING_ROOT_UNAVAILABLE", str(exc))
    return {"root": str(_costing_root()), "generated_at": None, "file_count": len(files), "readable_count": None, "unreadable_count": None, "formula_count": None, "files_with_external_references": None, "external_reference_count": None}


@app.get("/api/catalog/files")
def catalog_files(query: str = "", directory: str = "", status: str = "", classification: str = "", extension: str = "", offset: int = Query(0, ge=0), limit: int = Query(250, ge=1, le=1000)) -> dict[str, Any]:
    try:
        files = _filesystem().walk_files(query=query, extension=extension or None)
        repository = _get_repository()
        conflicting_file_ids = _conflicting_file_ids(repository)
        if directory:
            files = [item for item in files if Path(item.relative_path).parent.as_posix().casefold() == directory.casefold()]
        if classification:
            files = [item for item in files if item.candidate_classification == classification]
        rows = [_registry_status(item, conflicting_file_ids=conflicting_file_ids).to_dict() for item in files]
        if status == "mapped":
            mapped = {item["file"]["relative_path"] for item in repository.list_mapped_files() if item["association"]}
            rows = [item for item in rows if item["relative_path"] in mapped]
        elif status == "unmapped":
            rows = [item for item in rows if (item.get("mapping_status") or item.get("mappingStatus") or "unmapped") == "unmapped"]
        elif status == "conflict":
            rows = [item for item in rows if (item.get("mapping_status") or item.get("mappingStatus")) == "conflict"]
        return {"total": len(rows), "offset": offset, "items": rows[offset : offset + limit], "source": "filesystem"}
    except (FileNotFoundError, PathSafetyError) as exc:
        cached = _load_cached_files(query, directory, offset, limit)
        if cached is not None:
            return cached
        raise _error(503, "COSTING_ROOT_UNAVAILABLE", str(exc))


@app.get("/api/catalog/tree")
@app.get("/api/explorer/tree")
def catalog_tree(path: str = "") -> dict[str, Any]:
    try:
        children = _filesystem().list_children(path)
    except PathSafetyError as exc:
        raise _error(400, exc.code, str(exc))
    except (FileNotFoundError, NotADirectoryError) as exc:
        raise _error(404, "DIRECTORY_NOT_FOUND", str(exc))
    conflicting_file_ids = _conflicting_file_ids(_get_repository())
    return {"root": str(_costing_root()), "path": path, "items": [_registry_status(item, conflicting_file_ids=conflicting_file_ids).to_dict() for item in children]}


@app.get("/api/catalog/inspect")
@app.get("/api/explorer/inspect")
def inspect_file(path: str) -> dict[str, Any]:
    try:
        node = _filesystem().inspect(path)
    except PathSafetyError as exc:
        raise _error(400, exc.code, str(exc))
    except FileNotFoundError as exc:
        raise _error(404, "FILE_NOT_FOUND", str(exc))
    return _registry_status(node, conflicting_file_ids=_conflicting_file_ids(_get_repository())).to_dict()


@app.get("/api/products")
def products() -> list[dict[str, Any]]:
    return [item.to_dict() for item in _get_repository().list_products()]


@app.get("/api/products/{product_id}/models")
def product_models_for_product(product_id: str) -> list[dict[str, Any]]:
    return [_model_payload(item) for item in _get_repository().list_models(product_id)]


@app.get("/api/products/models")
def product_models(product_id: str | None = None) -> list[dict[str, Any]]:
    return [_model_payload(item) for item in _get_repository().list_models(product_id)]


@app.get("/api/models/{model_id}/codes")
def model_codes(model_id: str, include_legacy: bool = False) -> dict[str, Any]:
    repository = _get_repository()
    codes = repository.list_codes(model_id, include_legacy=include_legacy)
    owners = {item.code_id: item for item in repository.code_associations.values() if item.active}
    active = [{**item.to_dict(), "owner": _owner_payload(owners.get(item.id))} for item in codes if not item.legacy_spares_only]
    available = [item for item in active if item.get("owner") is None]
    conflicts = [item for item in active if item.get("owner")]
    return {
        "active": active,
        "available": available,
        "legacy": [item.to_dict() for item in repository.list_codes(model_id, include_legacy=True) if item.legacy_spares_only],
        "conflicts": conflicts,
    }


@app.get("/api/associations")
def associations() -> dict[str, Any]:
    repository = _get_repository()
    grist_writes_enabled = repository.adapter_name == "grist-safari"
    return {"schemaAvailable": grist_writes_enabled, "writeEnabled": grist_writes_enabled, "adapter": repository.adapter_name, "items": repository.list_mapped_files(), "message": "Using the in-memory adapter; no Grist mutation will occur." if repository.adapter_name == "in-memory" else None}


@app.post("/api/associations/validate")
def validate_association(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        proposal = _proposal_from_payload(payload)
    except PathSafetyError as exc:
        raise _error(400, exc.code, str(exc))
    except FileNotFoundError as exc:
        raise _error(404, "FILE_NOT_FOUND", str(exc))
    return _get_repository().validate_association(proposal, current_file_hash=_current_hash(proposal.file_id)).to_dict()


@app.post("/api/associations")
def save_association(request: Request, payload: dict[str, Any] = Body(...), idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> dict[str, Any]:
    if idempotency_key:
        payload = {**payload, "idempotencyKey": idempotency_key}
    user = _request_user(request)
    payload = {**payload, "actor": user["username"] if user["username"] != "Local user" else user["id"]}
    try:
        proposal = _proposal_from_payload(payload)
    except PathSafetyError as exc:
        raise _error(400, exc.code, str(exc))
    except FileNotFoundError as exc:
        raise _error(404, "FILE_NOT_FOUND", str(exc))
    try:
        return _get_repository().save_association(proposal, current_file_hash=_current_hash(proposal.file_id)).to_dict()
    except AssociationConflict as exc:
        raise _error(409, "ASSOCIATION_CONFLICT", "The association proposal was rejected.", {"validation": exc.validation.to_dict()})


@app.get("/api/associations/{file_id}/history")
def association_history(file_id: str) -> list[dict[str, Any]]:
    return _get_repository().association_history(file_id)


@app.get("/api/associations/{file_id}")
def association_detail(file_id: str) -> dict[str, Any]:
    repository = _get_repository()
    return {"current": repository.current_association(file_id).to_dict() if repository.current_association(file_id) else None, "history": repository.association_history(file_id)}


@app.get("/api/processing-queue")
def processing_queue(status: str = "", limit: int = Query(100, ge=1, le=500)) -> dict[str, Any]:
    """List durable association batches; processing remains a visible safe stub."""
    repository = _get_repository()
    batches = [item for item in repository.import_batches.values() if item.parser_version.casefold().startswith("association-")]
    if status:
        batches = [item for item in batches if item.status.casefold() == status.casefold()]
    batches.sort(key=lambda item: (item.started_at, item.id), reverse=True)
    return {"items": [item.to_dict() for item in batches[:limit]], "total": len(batches), "adapter": repository.adapter_name}


@app.get("/api/mapped-files")
def mapped_files(status: str = "", product_id: str = "", model_id: str = "") -> dict[str, Any]:
    repository = _get_repository()
    existing = repository.list_mapped_files()
    by_path = {str(item["file"].get("relative_path", "")).casefold(): item for item in existing}
    filesystem = _filesystem()
    try:
        filesystem_files = filesystem.walk_files()
    except (FileNotFoundError, PathSafetyError):
        filesystem_files = []
    current_by_path = {node.relative_path.casefold(): node for node in filesystem_files}
    review_indexes = _mapped_file_review_indexes(repository)
    conflicting_file_ids = set(review_indexes["conflict_codes_by_file"])
    for node in filesystem_files:
        key = node.relative_path.casefold()
        if key not in by_path:
            by_path[key] = {"file": _registry_status(node, conflicting_file_ids=conflicting_file_ids).to_dict(), "product": None, "model": None, "codes": [], "association": None, "history": []}
    rows = [_mapped_file_review_row(item, current_by_path, repository, filesystem, review_indexes) for item in by_path.values()]
    if status:
        rows = [item for item in rows if item["reviewStatus"] == status or (status == "needs-review" and item["issues"])]
    if product_id:
        rows = [item for item in rows if item["association"] and item["association"]["product_id"] == product_id]
    if model_id:
        rows = [item for item in rows if item["association"] and item["association"]["model_id"] == model_id]
    return {"items": rows, "total": len(rows), "adapter": _get_repository().adapter_name}


def _mapped_file_review_row(item: dict[str, Any], current_by_path: dict[str, Any], repository: Any, filesystem: FilesystemCatalog, review_indexes: dict[str, Any] | None = None) -> dict[str, Any]:
    """Add review facts without hashing or opening every workbook in the tree."""
    row = dict(item)
    file_info = dict(row.get("file") or {})
    path = str(file_info.get("relative_path") or file_info.get("relativePath") or "")
    stored_mtime = file_info.get("modified_at")
    stored_size = file_info.get("size_bytes")
    current = current_by_path.get(path.casefold())
    file_id = str(file_info.get("id") or f"file:{path.casefold()}")
    if current is not None:
        current_info = current.to_dict()
        # Tree metadata is cheap and current; preserve repository mapping state
        # and inspection facts, which are not recomputed during a list request.
        for key in ("name", "relative_path", "extension", "size_bytes", "modified_at", "candidate_classification"):
            file_info[key] = current_info.get(key)
    row["file"] = file_info

    path_key = path.replace("\\", "/").casefold()
    processing_batches = [
        batch for batch in repository.import_batches.values()
        if batch.parser_version.casefold().startswith("association-")
        and batch.source_file.replace("\\", "/").casefold() == path_key
    ]
    processing_batch = max(processing_batches, key=lambda batch: (batch.started_at, batch.id), default=None)
    row["processingBatch"] = processing_batch.to_dict() if processing_batch else None

    if review_indexes is None:
        review_indexes = _mapped_file_review_indexes(repository)
    latest = review_indexes["latest_observation_by_file"].get(file_id)
    association = row.get("association")
    active_links = review_indexes["active_links_by_association"].get(association.get("id"), []) if association else []
    conflict_codes = sorted(review_indexes["conflict_codes_by_file"].get(file_id, set()))

    issues: list[str] = []
    if conflict_codes:
        issues.append("duplicate_code_ownership")
    if association:
        product = row.get("product")
        model = row.get("model")
        if not product or not model or model.get("product_id") != association.get("product_id"):
            issues.append("catalog_mismatch")
        for link in active_links:
            code = repository.codes.get(link.code_id)
            if code is None or not code.active or code.legacy_spares_only or code.model_id != association.get("model_id"):
                if "catalog_mismatch" not in issues:
                    issues.append("catalog_mismatch")

    known_readable = latest.readable if latest is not None else file_info.get("readable")
    known_parse_error = latest.parse_error if latest is not None else file_info.get("parse_error")
    if known_readable is False or known_parse_error:
        issues.append("parse_error")
    external_count = latest.external_reference_count if latest is not None else file_info.get("external_reference_count")
    if isinstance(external_count, int) and external_count > 0:
        issues.append("external_links")
    if current is None and (association or file_info.get("id")):
        baseline_mtime = latest.modified_at if latest is not None else stored_mtime
        baseline_size = latest.size_bytes if latest is not None else stored_size
        baseline_hash = (latest.file_hash if latest is not None else None) or file_info.get("file_hash") or file_info.get("fileHash")
        moved_to = _find_moved_file(path, baseline_mtime, baseline_size, baseline_hash, current_by_path, filesystem)
        if moved_to:
            issues.append("moved_file")
            row["movedTo"] = moved_to
        else:
            issues.append("missing_file")
    elif current is not None:
        baseline_mtime = latest.modified_at if latest is not None else stored_mtime
        baseline_size = latest.size_bytes if latest is not None else stored_size
        if (baseline_mtime and current.modified_at and str(baseline_mtime) != str(current.modified_at)) or (baseline_size is not None and current.size_bytes is not None and baseline_size != current.size_bytes):
            issues.append("changed_file")

    row["reviewStatus"] = "conflict" if conflict_codes else ("mapped" if association else "unmapped")
    row["issues"] = issues
    row["conflictCodes"] = conflict_codes
    row["latestObservation"] = latest.to_dict() if latest is not None else None
    row["lastObservedAt"] = latest.observed_at if latest is not None else None
    row["lastObservedChange"] = {
        "modifiedAt": latest.modified_at if latest is not None else (current.modified_at if current is not None else file_info.get("modified_at")),
        "observedAt": latest.observed_at if latest is not None else None,
        "sizeBytes": latest.size_bytes if latest is not None else (current.size_bytes if current is not None else file_info.get("size_bytes")),
        "source": "observation" if latest is not None else "filesystem-mtime",
    }
    return row


def _find_moved_file(path: str, modified_at: Any, size_bytes: Any, file_hash: Any, current_by_path: dict[str, Any], filesystem: FilesystemCatalog) -> str | None:
    """Verify likely move candidates only for missing, hashed repository files."""
    if not modified_at or size_bytes is None or not file_hash:
        return None
    candidates = [
        node for node in current_by_path.values()
        if node.relative_path.casefold() != path.casefold()
        and node.size_bytes == size_bytes
        and _same_timestamp(node.modified_at, modified_at)
    ]
    if len(candidates) != 1:
        return None
    candidate = candidates[0]
    try:
        return candidate.relative_path if sha256_file(filesystem.resolve(candidate.relative_path)) == file_hash else None
    except (OSError, PathSafetyError):
        return None


def _same_timestamp(left: Any, right: Any) -> bool:
    try:
        left_value = datetime.fromisoformat(str(left).replace("Z", "+00:00"))
        right_value = datetime.fromisoformat(str(right).replace("Z", "+00:00"))
        return left_value == right_value
    except (TypeError, ValueError):
        return str(left) == str(right)


@app.get("/api/catalog/preview")
def preview(path: str, sheet: str = "", start_row: int = Query(1, ge=1), row_count: int = Query(40, ge=1, le=200), start_col: int = Query(1, ge=1), column_count: int = Query(40, ge=1, le=80)) -> dict[str, Any]:
    # Defaults are Query objects when this function is exercised directly in
    # a unit test; FastAPI supplies integers in normal requests.
    start_row = start_row if isinstance(start_row, int) else 1
    row_count = row_count if isinstance(row_count, int) else 40
    start_col = start_col if isinstance(start_col, int) else 1
    column_count = column_count if isinstance(column_count, int) else 40
    try:
        workbook_path = _filesystem().resolve(path)
    except PathSafetyError as exc:
        raise _error(400, exc.code, str(exc))
    except FileNotFoundError:
        raise _error(404, "FILE_NOT_FOUND", "ODS file not found below the configured root.")
    if workbook_path.suffix.casefold() != ".ods":
        raise _error(415, "UNSUPPORTED_EXTENSION", "Workbook preview supports ODS files only.")
    scan = scan_costing_file(workbook_path, _costing_root())
    if not scan.readable:
        error_text = (scan.error or "").casefold()
        code = "ODS_ENCRYPTED" if "encrypted" in error_text else "ODS_PARSE_ERROR"
        raise _error(422, code, scan.error or "The ODS package could not be read.")
    try:
        workbook = OdsWorkbook.load(workbook_path)
    except Exception as exc:
        raise _error(422, "ODS_PARSE_ERROR", str(exc))
    sheet_names = [name for name in workbook.sheets if not _is_external_cache_sheet(name)]
    selected_sheet = sheet or (sheet_names[0] if sheet_names else "")
    if selected_sheet not in workbook.sheets:
        raise _error(404, "SHEET_NOT_FOUND", f"Sheet not found: {selected_sheet}")
    rows = workbook.sheets[selected_sheet]
    row_start, col_start = start_row - 1, start_col - 1
    selected_rows = rows[row_start : row_start + row_count]
    selected = [row[col_start : col_start + column_count] for row in selected_rows]
    width = min(max((len(row) for row in selected), default=0), column_count)
    formulas = _formula_cells(workbook_path, selected_sheet, row_start, col_start, row_count, width)
    cells = []
    for row_index, row in enumerate(selected):
        cells.append([{"value": _json_cell(value), "kind": "formula" if (row_index, column_index) in formulas else "value", "formula": formulas.get((row_index, column_index))} for column_index, value in enumerate(row[:width])])
    return {"path": path, "sheets": sheet_names, "sheet": selected_sheet, "startRow": start_row, "startColumn": start_col, "totalRows": len(rows), "totalColumns": max((len(row) for row in rows), default=0), "truncatedRows": row_start + row_count < len(rows), "truncatedColumns": any(len(row) > col_start + width for row in selected_rows), "readOnly": True, "formulaCount": scan.formula_count, "externalReferenceCount": scan.external_reference_count, "externalLinkWarning": scan.external_reference_count > 0, "rows": [[_json_cell(value) for value in row[:width]] for row in selected], "cells": cells}


def _get_repository() -> InMemorySafariRepository:
    global _repository
    if _repository is not None:
        return _repository
    if os.getenv("SAFARI_REPOSITORY", "memory").casefold() == "grist":
        from app.grist_repository import GristSafariRepository
        repository = GristSafariRepository()
    else:
        repository = InMemorySafariRepository()
    # The Grist adapter has already loaded canonical identities from the
    # validated Safari document. Seeding it again from ODS would add a second
    # set with different IDs and make API references ambiguous. ODS seeding is
    # only for the explicitly local in-memory development adapter.
    catalog_path = os.getenv("CATALOG_ODS_PATH", "").strip()
    if not catalog_path:
        candidate = _costing_root() / DEFAULT_CATALOG_NAME
        catalog_path = str(candidate) if candidate.exists() else ""
    if repository.adapter_name == "in-memory" and catalog_path and Path(catalog_path).exists():
        try:
            result = import_catalog(Path(catalog_path))
            for item in result.products:
                repository.add_product(item)
            for item in result.models:
                repository.add_model(item)
            for item in result.codes:
                repository.add_code(item)
            repository.aliases.update({item.id: item for item in result.aliases})
            repository.issues.update({item.id: item for item in result.issues})
        except Exception:
            pass
    _repository = repository
    return repository


def _filesystem() -> FilesystemCatalog:
    return FilesystemCatalog(_costing_root())


def _costing_root() -> Path:
    return Path(os.getenv("COSTING_ROOT", str(DEFAULT_COSTING_ROOT))).expanduser().resolve()


def _catalog_path() -> Path:
    return Path(os.getenv("COSTING_CATALOG_PATH", str(DEFAULT_CATALOG)))


def _load_catalog() -> dict[str, Any]:
    path = _catalog_path()
    if not path.exists():
        raise HTTPException(status_code=503, detail={"code": "CATALOG_NOT_GENERATED", "message": f"Catalog has not been generated: {path}"})
    return json.loads(path.read_text(encoding="utf-8"))


def _load_cached_files(query: str, directory: str, offset: int, limit: int) -> dict[str, Any] | None:
    try:
        rows = _load_catalog()["files"]
    except (HTTPException, OSError, json.JSONDecodeError):
        return None
    needle = query.strip().casefold()
    selected = [row for row in rows if (not needle or needle in str(row["relative_path"]).casefold()) and (not directory or str(row["directory"]).casefold() == directory.casefold())]
    selected.sort(key=lambda row: str(row["modified_at"]), reverse=True)
    return {"total": len(selected), "offset": offset, "items": selected[offset : offset + limit], "source": "cached-report"}


def _proposal_from_payload(payload: dict[str, Any]) -> AssociationProposal:
    file_id = str(payload.get("fileId") or payload.get("file_id") or "")
    if not file_id and payload.get("path"):
        file_id = _register_file(str(payload["path"]))
    if not file_id:
        raise _error(422, "FILE_REQUIRED", "fileId or path is required.")
    return AssociationProposal(file_id=file_id, product_id=str(payload.get("productId") or payload.get("product_id") or ""), model_id=str(payload.get("modelId") or payload.get("model_id") or ""), code_ids=tuple(str(item) for item in (payload.get("codeIds") or payload.get("code_ids") or [])), actor=str(payload.get("actor") or "local-development"), reason=str(payload.get("reason") or ""), expected_version=payload.get("expectedVersion", payload.get("expected_version")), expected_hash=payload.get("expectedHash", payload.get("expected_hash")), idempotency_key=payload.get("idempotencyKey", payload.get("idempotency_key")), supersede=bool(payload.get("supersede", False)))


def _register_file(relative_path: str) -> str:
    node = _filesystem().inspect(relative_path)
    file_id = f"file:{node.relative_path.casefold()}"
    repository = _get_repository()
    existing = repository.get_file(file_id)
    repository.add_file(DomainCostingFile(id=file_id, relative_path=node.relative_path, normalized_path=node.relative_path.casefold(), name=node.name, extension=node.extension, size_bytes=node.size_bytes or 0, modified_at=node.modified_at or "", file_hash=node.content_hash, product_id=existing.product_id if existing else None, candidate_classification=node.candidate_classification, mapping_status=existing.mapping_status if existing else node.mapping_status, readable=node.readable, parse_error=node.parse_error, source="filesystem-explorer"))
    observation_id = f"observation:{file_id}:{node.modified_at}:{node.content_hash or 'unhashed'}"
    if not any(item.id == observation_id for item in repository.observations):
        repository.add_observation(FileObservation(id=observation_id, file_id=file_id, observed_at=utc_now(), relative_path=node.relative_path, normalized_path=node.relative_path.casefold(), size_bytes=node.size_bytes or 0, modified_at=node.modified_at or "", file_hash=node.content_hash, readable=node.readable, sheet_count=node.sheet_count, external_reference_count=node.external_reference_count, parse_error=node.parse_error))
    return file_id


def _current_hash(file_id: str) -> str | None:
    file = _get_repository().get_file(file_id)
    if file is None:
        return None
    try:
        return sha256_file(_filesystem().resolve(file.relative_path))
    except (OSError, FileNotFoundError, PathSafetyError):
        return None


def _mapped_file_review_indexes(repository: Any) -> dict[str, Any]:
    latest_observation_by_file: dict[str, Any] = {}
    for observation in repository.observations:
        current = latest_observation_by_file.get(observation.file_id)
        if current is None or observation.observed_at > current.observed_at:
            latest_observation_by_file[observation.file_id] = observation

    active_links_by_association: dict[str, list[Any]] = {}
    owners_by_code: dict[str, list[Any]] = {}
    for link in repository.code_associations.values():
        if not link.active:
            continue
        active_links_by_association.setdefault(link.association_id, []).append(link)
        owners_by_code.setdefault(link.code_id, []).append(link)

    conflict_codes_by_file: dict[str, set[str]] = {}
    for code_id, owners in owners_by_code.items():
        if len(owners) > 1:
            for owner in owners:
                conflict_codes_by_file.setdefault(owner.file_id, set()).add(code_id)

    return {
        "latest_observation_by_file": latest_observation_by_file,
        "active_links_by_association": active_links_by_association,
        "conflict_codes_by_file": conflict_codes_by_file,
    }


def _conflicting_file_ids(repository: Any) -> set[str]:
    owners_by_code: dict[str, list[str]] = {}
    for link in repository.code_associations.values():
        if link.active:
            owners_by_code.setdefault(link.code_id, []).append(link.file_id)
    return {file_id for owners in owners_by_code.values() if len(owners) > 1 for file_id in owners}


def _registry_status(node: Any, *, conflicting_file_ids: set[str] | None = None) -> Any:
    repository = _get_repository()
    file_id = f"file:{node.relative_path.casefold()}"
    record = repository.get_file(file_id)
    if conflicting_file_ids is None:
        conflicting_file_ids = _conflicting_file_ids(repository)
    if file_id in conflicting_file_ids:
        return replace(node, mapping_status="conflict")
    return replace(node, mapping_status=record.mapping_status) if record else node


def _owner_payload(owner: Any) -> dict[str, Any] | None:
    if owner is None:
        return None
    file = _get_repository().get_file(owner.file_id)
    return {"fileId": owner.file_id, "associationId": owner.association_id, "name": file.name if file else owner.file_id, "relativePath": file.relative_path if file else None}


def _model_payload(item: Any) -> dict[str, Any]:
    repository = _get_repository()
    payload = item.to_dict()
    payload.update({"modelNumber": item.model_number, "modelName": item.name, "productId": item.product_id, "codes": [code.to_dict() for code in repository.list_codes(item.id)]})
    return payload


def _json_cell(value: Any) -> Any:
    return value if value is None or isinstance(value, (str, int, float, bool)) else str(value)


def _is_external_cache_sheet(name: str) -> bool:
    return name.strip().lstrip("'").casefold().startswith(("file://", "http://", "https://"))


def _formula_cells(path: Path, sheet_name: str, start_row: int, start_col: int, row_count: int, column_count: int) -> dict[tuple[int, int], str]:
    """Read formula attributes only; cached values still come from pyexcel."""
    formulas: dict[tuple[int, int], str] = {}
    table_ns = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"
    try:
        with ZipFile(path) as package:
            with package.open("content.xml") as stream:
                root = ET.parse(stream).getroot()
        for table in root.iter(f"{table_ns}table"):
            if table.attrib.get(f"{table_ns}name") != sheet_name:
                continue
            visible_row = 0
            for row in table.findall(f"{table_ns}table-row"):
                row_repeat = int(row.attrib.get(f"{table_ns}number-rows-repeated", "1"))
                for repeated_row in range(row_repeat):
                    if visible_row >= start_row + row_count:
                        return formulas
                    visible_col = 0
                    for cell in row.findall(f"{table_ns}table-cell"):
                        col_repeat = int(cell.attrib.get(f"{table_ns}number-columns-repeated", "1"))
                        formula = cell.attrib.get(f"{table_ns}formula") or cell.attrib.get("{urn:oasis:names:tc:opendocument:xmlns:of:1.2}formula")
                        if formula:
                            for repeated_col in range(col_repeat):
                                if visible_row >= start_row and start_col <= visible_col + repeated_col < start_col + column_count:
                                    formulas[(visible_row - start_row, visible_col + repeated_col - start_col)] = formula
                        visible_col += col_repeat
                    visible_row += 1
                
    except (OSError, KeyError, ValueError, ET.ParseError):
        return {}
    return formulas


def _request_user(request: Request) -> dict[str, str]:
    return {"id": request.headers.get("x-authentik-uid", "local-development"), "username": request.headers.get("x-authentik-username", "Local user"), "email": request.headers.get("x-authentik-email", "")}


def _error(status: int, code: str, message: str, extra: dict[str, Any] | None = None) -> HTTPException:
    detail = {"code": code, "message": message}
    if extra:
        detail.update(extra)
    return HTTPException(status_code=status, detail=detail)

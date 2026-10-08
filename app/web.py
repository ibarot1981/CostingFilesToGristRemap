"""FastAPI application for the Safari Manufacturing Phase 0 workbench."""

from __future__ import annotations

import json
import csv
import hashlib
import io
from datetime import datetime
import os
from dataclasses import replace
from pathlib import Path
from typing import Any
from zipfile import BadZipFile, ZipFile
import xml.etree.ElementTree as ET

from fastapi import Body, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response

from app.catalog import scan_costing_file
from app.catalog_import import import_catalog
from app.domain import CostingFile as DomainCostingFile, FileObservation, ReconciliationIssue, utc_now
from app.filesystem_catalog import FilesystemCatalog, PathSafetyError
from app.exceptions import CostingAppError, GristError
from app.repository import AssociationConflict, AssociationProposal, GovernanceConflict, InMemorySafariRepository, SafariRepository, sha256_file
from app.workbook import OdsWorkbook
from app.milestone2 import build_current_costing_review, build_mcl_rate_warning_index, extract_rate_log, read_ods, resolve_semantic_ambiguities
from app.libreoffice_refresh import LibreOfficeRefreshError, linked_ods_sources, refreshed_ods_copy
from app.part_identity import PartIdentityError


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_COSTING_ROOT = Path(r"C:\Irshad\Safari\DRWGD\Products Costing")
DEFAULT_CATALOG = PROJECT_ROOT / "reports" / "first_pass_costing_catalog.json"
DEFAULT_CATALOG_NAME = "Product-ProductModelNo-ModelCode.ods"

app = FastAPI(title="Safari Manufacturing ERP", version="0.2.0")
app.add_middleware(CORSMiddleware, allow_origins=["http://127.0.0.1:4320", "http://localhost:4320"], allow_credentials=True, allow_methods=["GET", "POST"], allow_headers=["*"])
_repository: SafariRepository | None = None
_part_identity_registry = None


@app.exception_handler(PartIdentityError)
async def _part_identity_error_handler(request: Request, exc: PartIdentityError):
    error = _part_http_error(exc)
    return JSONResponse(status_code=error.status_code, content={"detail": error.detail})


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
        rows = [_explorer_node_payload(item, conflicting_file_ids=conflicting_file_ids) for item in files]
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
    return {"root": str(_costing_root()), "path": path, "items": [_explorer_node_payload(item, conflicting_file_ids=conflicting_file_ids) for item in children]}


@app.get("/api/catalog/inspect")
@app.get("/api/explorer/inspect")
def inspect_file(path: str) -> dict[str, Any]:
    try:
        node = _filesystem().inspect(path)
    except PathSafetyError as exc:
        raise _error(400, exc.code, str(exc))
    except FileNotFoundError as exc:
        raise _error(404, "FILE_NOT_FOUND", str(exc))
    return _explorer_node_payload(node, conflicting_file_ids=_conflicting_file_ids(_get_repository()))


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
    return _get_repository().association_detail(file_id)


@app.get("/api/explorer/association")
def file_association(path: str) -> dict[str, Any]:
    workbook_path = _resolve_preview_workbook(path)
    relative_path = workbook_path.relative_to(_costing_root()).as_posix()
    return _get_repository().association_detail(f"file:{relative_path.casefold()}")


@app.get("/api/processing-queue")
def processing_queue(status: str = "", limit: int = Query(100, ge=1, le=500)) -> dict[str, Any]:
    """List durable association batches; processing remains a visible safe stub."""
    repository = _get_repository()
    batches = [item for item in repository.import_batches.values() if item.parser_version.casefold().startswith("association-")]
    if status:
        batches = [item for item in batches if item.status.casefold() == status.casefold()]
    batches.sort(key=lambda item: (item.started_at, item.id), reverse=True)
    return {"items": [item.to_dict() for item in batches[:limit]], "total": len(batches), "adapter": repository.adapter_name}


def _processing_context(path: str):
    # Retrieving the association refreshes the durable adapter without mutations.
    detail = file_association(path)
    repository = _get_repository()
    return repository, detail["fileId"], repository.current_association(detail["fileId"]), sha256_file(_resolve_preview_workbook(path))


def _part_context(path: str):
    from app.part_mapping import PartConflict, source_groups
    from app.milestone2 import _configured_process_lines
    repository, file_id, association, source_hash = _processing_context(path)
    try:
        groups = source_groups(_configured_process_lines(read_ods(_resolve_preview_workbook(path))))
    except (OSError, KeyError, ValueError, BadZipFile, ET.ParseError) as exc:
        raise _error(422, "PART_SOURCE_UNAVAILABLE", str(exc))
    if sha256_file(_resolve_preview_workbook(path)) != source_hash:
        raise PartConflict("PART_REVIEW_STALE", "The workbook changed while reading; reload the review")
    return repository, file_id, association, source_hash, groups


def _part_registry():
    global _part_identity_registry
    if _part_identity_registry is None:
        try:
            repository = _get_repository()
            if repository.adapter_name != "grist-safari":
                raise _error(503, "PART_GRIST_REQUIRED", "Canonical Part writes require the configured Safari Manufacturing Grist adapter; local SQLite is not used as a business-data fallback.")
            from app.grist_parts import GristPartRegistry
            _part_identity_registry = GristPartRegistry(repository.client)
        except HTTPException:
            raise
        except Exception as exc:
            raise _error(503, "PART_DATABASE_UNAVAILABLE", "The durable Part registry could not be opened.") from exc
    return _part_identity_registry


def _part_features():
    from app.grist_parts import GristPartFeatures, GristPartRegistry
    registry = _part_registry()
    if not isinstance(registry, GristPartRegistry):
        raise _error(503, "PART_GRIST_REQUIRED", "Composition and purchased-Part records require the configured Safari Manufacturing Grist adapter.")
    return GristPartFeatures(registry)


def _live_cost_service():
    from app.live_cost import GristLiveCostService
    return GristLiveCostService(_part_registry())


def _legacy_part_rows(repository: SafariRepository) -> list[dict[str, Any]]:
    if repository.adapter_name != "grist-safari":
        return []
    try:
        rows = repository.part_store.legacy_part_records() if hasattr(repository.part_store, "legacy_part_records") else repository.part_store.records("ProductPart")
        rows = [row for row in rows if not row.get("fields", {}).get("StablePartId")]
    except Exception as exc:
        from app.part_identity import PartIdentityError
        raise PartIdentityError("PART_LEGACY_UNAVAILABLE", "Existing Safari Parts could not be read; creation is paused to protect name uniqueness.") from exc
    if not getattr(repository.part_store, "legacy_parts_available", getattr(repository.part_store, "available", True)):
        from app.part_identity import PartIdentityError
        raise PartIdentityError("PART_LEGACY_UNAVAILABLE", "The Safari ProductPart table is unavailable; creation is paused to protect name uniqueness.")
    return rows


def _part_mapping_store(repository: SafariRepository):
    if hasattr(repository.part_store, "identity_store"):
        return repository.part_store
    from app.part_mapping import PartRegistryMappingStore
    repository.part_store = PartRegistryMappingStore(repository.part_store, _part_registry())
    return repository.part_store


def _part_scope_warnings(repository: SafariRepository, association, part: dict[str, Any]) -> list[dict[str, str]]:
    if not association or part.get("legacy") or not part.get("scope"):
        return []
    if part.get("scope") == "global":
        return []
    model = repository.models.get(getattr(association, "model_id", None))
    if not model:
        return []
    code_ids = [item.code_id for item in repository.code_associations.values()
                if item.active and item.association_id == association.id and item.code_id in repository.codes]
    out_of_scope = []
    if part["scope"] == "product":
        product_id = model.product_id
        if product_id != part.get("scopeTargetId"):
            out_of_scope = code_ids
    elif part["scope"] == "product_model":
        if model.id != part.get("scopeTargetId"):
            out_of_scope = code_ids
    elif part["scope"] == "model_code":
        out_of_scope = [code_id for code_id in code_ids if code_id != part.get("scopeTargetId")]
    return [{"id": code_id, "code": repository.codes[code_id].code} for code_id in out_of_scope]


def _scope_target(repository: SafariRepository, scope: str, target_id: str) -> dict[str, str]:
    if scope not in {"global", "product", "product_model", "model_code"}:
        raise _error(422, "PART_SCOPE_INVALID", "Choose Global, Product, Product Model or Model Code sharing scope.")
    if scope == "global":
        if target_id != "global":
            raise _error(422, "PART_SCOPE_TARGET_INVALID", "Global scope must use its maintained global target.")
        return {"id": "global", "label": "Safari Manufacturing"}
    if scope == "product":
        item = repository.products.get(str(target_id))
        if not item or not item.active:
            raise _error(422, "PART_SCOPE_TARGET_INVALID", "Choose an active Product from Safari Manufacturing.")
        return {"id": item.id, "label": item.name}
    if scope == "product_model":
        item = repository.models.get(str(target_id))
        product = repository.products.get(item.product_id) if item else None
        if not item or not item.active or not product or not product.active:
            raise _error(422, "PART_SCOPE_TARGET_INVALID", "Choose an active Product Model and its active Product.")
        return {"id": item.id, "label": " ".join(value for value in [item.model_number, item.name] if value).strip()}
    item = repository.codes.get(str(target_id))
    model = repository.models.get(item.model_id) if item else None
    product = repository.products.get(model.product_id) if model else None
    if not item or not item.active or item.legacy_spares_only or not model or not model.active or not product or not product.active:
        raise _error(422, "PART_SCOPE_TARGET_INVALID", "Choose an active Model Code under an active Product Model and Product.")
    return {"id": item.id, "label": item.code}


def _part_scope_targets() -> dict[str, Any]:
    repository = _get_repository()
    registry = _part_registry()
    stored = {(item["scope_type"], item["target_id"]): item["shortcode"] for item in registry.shortcodes()}
    products = [{"id": item.id, "label": item.name, "shortcode": stored.get(("product", item.id)), "active": item.active}
                for item in repository.products.values() if item.active]
    models = [{"id": item.id, "parentId": item.product_id,
               "label": " ".join(value for value in [item.model_number, item.name] if value).strip(),
               "shortcode": stored.get(("product_model", item.id)), "active": item.active}
              for item in repository.models.values() if item.active and item.product_id in repository.products and repository.products[item.product_id].active]
    codes = [{"id": item.id, "parentId": item.model_id, "label": item.code,
              "shortcode": stored.get(("model_code", item.id)), "active": item.active}
             for item in repository.codes.values() if item.active and not item.legacy_spares_only and item.model_id in repository.models
             and repository.models[item.model_id].active and repository.models[item.model_id].product_id in repository.products
             and repository.products[repository.models[item.model_id].product_id].active]
    return {"scopes": [
        {"id": "global", "label": "Global", "target": {"id": "global", "label": "Safari Manufacturing", "shortcode": stored.get(("global", "global"))}},
        {"id": "product", "label": "Product", "targets": products},
        {"id": "product_model", "label": "Product Model", "targets": models},
        {"id": "model_code", "label": "Model Code", "targets": codes},
    ]}


def _legacy_part_payloads(repository: SafariRepository, rows: list[dict[str, Any]], query: str = "") -> list[dict[str, Any]]:
    from app.part_identity import normalized_name
    revisions: dict[str, set[str]] = {}
    if repository.adapter_name == "grist-safari":
        try:
            for revision in repository.client.fetch_table_records_with_ids("PartRevision"):
                fields = revision.get("fields", {})
                reference = fields.get("ProductPart")
                if isinstance(reference, list) and len(reference) > 2 and reference[0] == "R":
                    part_id = str(reference[2])
                elif isinstance(reference, int):
                    part_id = str(reference)
                else:
                    continue
                value = fields.get("Revision")
                revisions.setdefault(part_id, set()).add(str(value))
        except Exception:
            revisions = {}
    search_key = normalized_name(query)
    result = []
    for row in rows:
        fields = row.get("fields", {})
        name = str(fields.get("DisplayName") or "")
        name_key = normalized_name(name)
        search_text = normalized_name(str(name) + " " + str(row.get("id")) + " " + str(fields.get("PartKey") or ""))
        if search_key and search_key not in search_text:
            continue
        result.append({"id": f"legacy:{row.get('id')}", "legacyRecordId": str(row.get("id")),
            "partNumber": None, "name": name, "description": name, "variant": "", "scope": "legacy",
            "scopeTargetId": None, "scopeTarget": "Legacy record", "engineeringRevision": None,
            "legacyRevisionValues": sorted(revisions.get(str(row.get("id")), set())),
            "status": str(fields.get("Status") or "legacy"), "metadataVersion": None,
            "aliases": [], "partKey": fields.get("PartKey"), "legacy": True,
            "ambiguousName": sum(1 for other in rows if normalized_name(str(other.get("fields", {}).get("DisplayName") or "")) == name_key) > 1})
    return result


def _part_http_error(exc) -> HTTPException:
    status = 404 if exc.code == "PART_NOT_FOUND" else 503 if exc.code in {"PART_DATABASE_UNAVAILABLE", "PART_DATABASE_UNSUPPORTED", "PART_LEGACY_UNAVAILABLE", "PART_SCHEMA_UNAVAILABLE", "PART_COORDINATOR_UNBOUND", "PART_WRITE_UNCONFIRMED", "PART_COORDINATOR_UNCONFIRMED", "PART_GRIST_REQUIRED", "PART_CREATED_SHARING_PENDING"} else 422 if exc.code.endswith(("INVALID", "REQUIRED", "LOCKED", "INACTIVE")) else 409
    return _error(status, exc.code, str(exc))


@app.get("/api/parts")
def parts_register(search: str = "", scope: str = "", target_id: str = "", include_retired: bool = True) -> dict[str, Any]:
    from app.part_identity import PartIdentityError
    repository = _get_repository()
    registry = _part_registry()
    try:
        legacy = _legacy_part_rows(repository)
        registry.sync_legacy_names(legacy)
        return {"items": registry.list_parts(search=search, scope_type=scope, target_id=target_id, include_retired=include_retired),
                "legacyItems": _legacy_part_payloads(repository, legacy, search),
                "storage": "Grist Safari Manufacturing" if repository.adapter_name == "grist-safari" else "in-memory local preview",
                "schemaAvailable": True}
    except PartIdentityError as exc:
        raise _part_http_error(exc)


@app.get("/api/parts/scope-targets")
def part_scope_targets() -> dict[str, Any]:
    return _part_scope_targets()


@app.get("/api/parts/preview")
def part_name_preview(scope: str, target_id: str, description: str, variant: str = "", exclude_id: str = "") -> dict[str, Any]:
    from app.part_identity import PartIdentityError
    repository = _get_repository()
    target = _scope_target(repository, scope, target_id)
    legacy = _legacy_part_rows(repository)
    registry = _part_registry()
    registry.sync_legacy_names(legacy)
    try:
        result = registry.preview(scope_type=scope, target_id=target_id, description=description, variant=variant, exclude_part_id=exclude_id)
        result["targetLabel"] = target["label"]
        return result
    except PartIdentityError as exc:
        raise _part_http_error(exc)


@app.post("/api/parts/shortcodes")
def maintain_part_shortcode(request: Request, payload: dict[str, Any] = Body(...), idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> dict[str, Any]:
    from app.part_identity import PartIdentityError
    scope = str(payload.get("scope") or "")
    target_id = str(payload.get("targetId") or "")
    target = _scope_target(_get_repository(), scope, target_id)
    try:
        return _part_registry().set_shortcode(scope_type=scope, target_id=target_id, target_label=target["label"],
            shortcode=str(payload.get("shortcode") or ""), actor=_request_actor(request), reason=str(payload.get("reason") or ""),
            request_key=idempotency_key or "")
    except PartIdentityError as exc:
        raise _part_http_error(exc)


@app.post("/api/parts")
def create_canonical_part(request: Request, payload: dict[str, Any] = Body(...), idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    from app.part_identity import PartIdentityError
    repository = _get_repository()
    scope = str(payload.get("scope") or "")
    target_id = str(payload.get("targetId") or "")
    target = _scope_target(repository, scope, target_id)
    try:
        legacy = _legacy_part_rows(repository)
        registry = _part_registry()
        registry.sync_legacy_names(legacy)
        actor = _request_actor(request)
        reason = str(payload.get("reason") or "")
        code_ids = payload.get("intendedModelCodeIds", [])
        product_id = payload.get("selectedProductId")
        model_id = payload.get("selectedProductModelId")
        if not isinstance(code_ids, list):
            raise PartIdentityError("PART_INTENDED_CODES_INVALID", "Intended Model Codes must be submitted as a list.")
        if hasattr(registry, "validate_intended_model_codes"):
            registry.validate_intended_model_codes(code_ids, product_id=product_id, model_id=model_id)
        elif code_ids:
            raise PartIdentityError("PART_INTENDED_SHARING_REQUIRES_GRIST", "Intended sharing can be saved only when Safari Manufacturing Grist is the active Part store.")
        result = registry.create_part(scope_type=scope, target_id=target_id, target_label=target["label"],
            description=str(payload.get("description") or ""), variant=str(payload.get("variant") or ""),
            expected_name=str(payload.get("expectedName") or ""), actor=actor,
            reason=reason, request_key=idempotency_key or "",
            revision_assertion=payload.get("engineeringRevision", payload.get("revision")))
        if code_ids:
            sharing_key = "part-create-intended:" + hashlib.sha256(str(idempotency_key or "").encode()).hexdigest()
            try:
                result["intendedSharing"] = registry.save_intended_sharing(part_id=result["part"]["id"], code_ids=code_ids,
                    expected_version=0, actor=actor, reason=reason, request_key=sharing_key,
                    product_id=product_id, model_id=model_id)
            except PartIdentityError as exc:
                if exc.code == "PART_REQUEST_CONFLICT":
                    raise
                raise PartIdentityError("PART_CREATED_SHARING_PENDING",
                    f"Part {result['part']['partNumber']} ({result['part']['id']}) is saved. Intended sharing is still pending; retry this same Save so it can resume without creating another Part.") from exc
            except Exception as exc:
                raise PartIdentityError("PART_CREATED_SHARING_PENDING",
                    f"Part {result['part']['partNumber']} ({result['part']['id']}) is saved. Intended sharing is still pending; retry this same Save so it can resume without creating another Part.") from exc
        return result
    except PartIdentityError as exc:
        raise _part_http_error(exc)


@app.post("/api/parts/{part_id}/metadata")
def update_part_metadata(part_id: str, request: Request, payload: dict[str, Any] = Body(...), idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> dict[str, Any]:
    from app.part_identity import PartIdentityError
    scope = str(payload.get("scope") or "")
    target_id = str(payload.get("targetId") or "")
    target = _scope_target(_get_repository(), scope, target_id)
    expected_version = payload.get("expectedVersion")
    if type(expected_version) is not int:
        raise _error(422, "PART_METADATA_VERSION_REQUIRED", "Reload the Part and include its current metadata version.")
    try:
        return _part_registry().update_metadata(part_id=part_id, scope_type=scope, target_id=target_id,
            target_label=target["label"], description=str(payload.get("description") or ""), variant=str(payload.get("variant") or ""),
            expected_version=expected_version, expected_name=str(payload.get("expectedName") or ""), actor=_request_actor(request),
            expected_usage_fingerprint=str(payload.get("expectedUsageFingerprint") or ""),
            reason=str(payload.get("reason") or ""), request_key=idempotency_key or "")
    except PartIdentityError as exc:
        raise _part_http_error(exc)


@app.get("/api/parts/{part_id}/metadata-preview")
def part_metadata_preview(part_id: str, scope: str, target_id: str, description: str, variant: str = "") -> dict[str, Any]:
    from app.part_identity import PartIdentityError
    repository = _get_repository()
    target = _scope_target(repository, scope, target_id)
    legacy = _legacy_part_rows(repository)
    registry = _part_registry()
    registry.sync_legacy_names(legacy)
    part = registry.get_part(part_id)
    if not part:
        raise _error(404, "PART_NOT_FOUND", "The selected Part identity was not found.")
    try:
        name = registry.preview(scope_type=scope, target_id=target_id, description=description, variant=variant, exclude_part_id=part_id)
        usage = registry.usage_evidence(part_id)
        return {"before": {"scope": part["scope"], "targetId": part["scopeTargetId"], "target": part["scopeTarget"], "name": part["name"]},
                "after": {"scope": scope, "targetId": target_id, "target": target["label"], "name": name["name"]},
                "available": name["available"], "collision": name["collision"],
                "affectedSourceAssignments": usage["items"], "usageFingerprint": usage["fingerprint"],
                "partNumber": part["partNumber"], "engineeringRevision": "A"}
    except PartIdentityError as exc:
        raise _part_http_error(exc)


@app.post("/api/parts/{part_id}/retire")
def retire_part(part_id: str, request: Request, payload: dict[str, Any] = Body(...), idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> dict[str, Any]:
    from app.part_identity import PartIdentityError
    expected_version = payload.get("expectedVersion")
    if type(expected_version) is not int:
        raise _error(422, "PART_METADATA_VERSION_REQUIRED", "Reload the Part and include its current metadata version.")
    try:
        return _part_registry().retire_part(part_id=part_id, expected_version=expected_version, actor=_request_actor(request),
            reason=str(payload.get("reason") or ""), request_key=idempotency_key or "")
    except PartIdentityError as exc:
        raise _part_http_error(exc)


@app.get("/api/parts/mappings")
def part_mappings(path: str):
    from app.part_mapping import PartConflict, mapping_detail
    try:
        repository, file_id, association, source_hash, groups = _part_context(path)
        detail = mapping_detail(_part_mapping_store(repository), file_id=file_id, source_hash=source_hash, association=association, groups=groups)
        warnings = {part["id"]: _part_scope_warnings(repository, association, part) for part in detail["parts"]}
        for part in detail["parts"]:
            part["outOfScopeCodes"] = warnings[part["id"]]
        for group in detail["groups"]:
            if group["part"]:
                group["part"]["outOfScopeCodes"] = warnings.get(group["part"]["id"], [])
        detail["scopeAdvisoryOnly"] = True
        return detail
    except PartConflict as exc:
        raise _error(409, exc.code, str(exc))


@app.get("/api/parts/{part_id}")
def part_details(part_id: str) -> dict[str, Any]:
    repository = _get_repository()
    part = _part_registry().get_part(part_id)
    if part:
        registry = _part_registry()
        if hasattr(registry, "history"):
            history = registry.history(part_id)
            part["metadataHistory"] = [{"part_id": part_id, "version": item.get("Version"), "display_name": item.get("DisplayName"),
                "scope_type": item.get("ScopeType"), "target_label": item.get("ScopeTargetLabel"), "shortcode": item.get("Shortcode"),
                "occurred_at": item.get("OccurredAt"), "description": item.get("Description"), "variant": item.get("DesignVariant"),
                "actor": item.get("Actor"), "reason": item.get("Reason")} for item in history["metadata"]]
            part["aliasHistory"] = history["aliases"]
            part["revisionHistory"] = history["revisions"]
            part["lifecycleHistory"] = [{"event_id": item.get("RequestKey"), "event_type": item.get("EventType"), "status": "recorded",
                "occurred_at": item.get("OccurredAt"), "actor": item.get("Actor"), "reason": item.get("Reason")} for item in history["lifecycle"]]
            part["mappingHistory"] = history["mappings"]
        else:
            history = {}
        part.setdefault("mappingHistory", [])
        mapped_sources = [{"name": f"{item['SheetName']} · row {item['SourceRow']}", "sheet": item["SheetName"],
            "row": item["SourceRow"], "description": item["SourceDescription"], "sourceHash": item["SourceHash"],
            "associationVersion": item["AssociationVersion"], "mappingVersion": item["Version"],
            "actor": item.get("Actor"), "reason": item.get("Reason"), "occurredAt": item.get("OccurredAt")} for item in part["mappingHistory"]]
        if registry.__class__.__name__ == "GristPartRegistry":
            features = _part_features()
            process_lines = features.process_lines(part_id)
            components = features.components(part_id)
            drawings = features.drawings(part_id)
            purchases = features.purchase_detail(part_id)
            intended_sharing = registry.intended_sharing(part_id)
            used_in = _live_cost_service().part_usage(part_id)
            part["compositionStatus"] = part.get("revisionStatus") or "draft"
        else:
            process_lines = {"status": "partial" if mapped_sources else "unavailable", "items": mapped_sources,
                "message": "Process line requirements are not pinned to stable Parts in this local preview."}
            components, drawings, purchases = [], [], {"specifications": [], "purchaseHistoryAvailable": False, "message": "Purchase capture requires Safari Grist."}
            intended_sharing = {"status": "unavailable", "version": 0, "items": [], "history": []}
            used_in = {"status": "unavailable", "coverage": "none", "items": [], "message": "Current configuration usage requires Safari Grist."}
        return {"part": part, "mappingHistory": part["mappingHistory"],
                "processLines": process_lines,
                "components": {"status": "available", "items": components, "message": "No component Parts are linked." if not components else None},
                "purchases": purchases,
                "intendedSharing": intended_sharing,
                "drawings": {"status": "available" if drawings else "empty", "items": drawings, "message": "No drawings are linked to this revision." if not drawings else None},
                "usedIn": used_in}
    if part_id.startswith("legacy:") and repository.adapter_name == "grist-safari":
        record_id = part_id.split(":", 1)[1]
        rows = _legacy_part_rows(repository)
        row = next((item for item in rows if str(item.get("id")) == record_id), None)
        if row:
            legacy = _legacy_part_payloads(repository, [row])
            return {"part": legacy[0], "processLines": {"status": "unavailable", "items": [], "message": "Legacy line references are retained in Grist and have not been migrated."},
                    "drawings": {"status": "unavailable", "items": [], "message": "Legacy drawing references have not been reconciled."},
                    "usedIn": {"status": "unavailable", "items": [], "message": "Legacy configurations have not been reconciled."}}
    raise _error(404, "PART_NOT_FOUND", "The selected Part identity was not found.")


@app.get("/api/parts/{part_id}/composition")
def part_composition(part_id: str):
    return {"items": _part_features().components(part_id)}


@app.put("/api/parts/{part_id}/intended-sharing")
def save_part_intended_sharing(part_id: str, request: Request, payload: dict[str, Any] = Body(...), idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    from app.part_identity import PartIdentityError
    registry = _part_registry()
    if not hasattr(registry, "save_intended_sharing"):
        raise _error(503, "PART_INTENDED_SHARING_REQUIRES_GRIST", "Intended sharing can be saved only when Safari Manufacturing Grist is the active Part store.")
    try:
        return registry.save_intended_sharing(part_id=part_id, code_ids=payload.get("intendedModelCodeIds", []),
            expected_version=payload.get("expectedVersion"), actor=_request_actor(request), reason=str(payload.get("reason") or ""),
            request_key=idempotency_key or "", product_id=payload.get("selectedProductId"), model_id=payload.get("selectedProductModelId"))
    except PartIdentityError as exc:
        raise _part_http_error(exc)


@app.post("/api/parts/{part_id}/components")
def add_part_component(part_id: str, request: Request, payload: dict[str, Any] = Body(...), idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    return _part_features().add_component(parent_part_id=part_id, child_part_id=str(payload.get("childPartId") or ""),
        quantity=payload.get("quantity"), uom=str(payload.get("uom") or ""), actor=_request_actor(request), reason=str(payload.get("reason") or ""),
        request_key=idempotency_key or "", sourcing_route=str(payload.get("sourcingRoute") or "auto"))


@app.post("/api/parts/{part_id}/finalize-revision")
def finalize_part_revision(part_id: str, request: Request, payload: dict[str, Any] = Body(...), idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    return _part_features().finalize_revision(part_id=part_id, actor=_request_actor(request), reason=str(payload.get("reason") or ""), request_key=idempotency_key or "")


@app.post("/api/parts/{part_id}/process-lines")
def link_part_process_line(part_id: str, request: Request, payload: dict[str, Any] = Body(...), idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    return _part_features().link_process_line(part_id=part_id, line_master_id=int(payload.get("lineMasterId") or 0),
        line_revision_id=int(payload.get("lineRevisionId") or 0), quantity=payload.get("quantity", 1), uom=str(payload.get("uom") or "each"),
        actor=_request_actor(request), reason=str(payload.get("reason") or ""), request_key=idempotency_key or "",
        reassign_owner=bool(payload.get("reassignOwner")), expected_owner_id=payload.get("expectedOwnerId"))


@app.get("/api/part-line-candidates")
def part_line_candidates(search: str = ""):
    return {"items": _part_features().line_candidates(search)}


@app.post("/api/parts/{part_id}/drawings")
def add_part_drawing(part_id: str, request: Request, payload: dict[str, Any] = Body(...), idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    return _part_features().add_drawing(part_id=part_id, identity=str(payload.get("identity") or ""), link_type=str(payload.get("linkType") or ""),
        file_path=str(payload.get("filePath") or ""), external_url=str(payload.get("externalUrl") or ""), file_version=str(payload.get("fileVersion") or ""),
        actor=_request_actor(request), reason=str(payload.get("reason") or ""), request_key=idempotency_key or "")


@app.get("/api/parts/drawings/{drawing_key}/open")
def open_part_drawing(drawing_key: str):
    features = _part_features()
    rows = features.registry._rows("PartDrawing")
    row = next((item for item in rows if str(item.get("fields", {}).get("DrawingKey") or "") == drawing_key), None)
    if not row or row.get("fields", {}).get("LinkType") != "local_file":
        raise _error(404, "PART_DRAWING_NOT_FOUND", "The local drawing link was not found.")
    root_value = os.getenv("SAFARI_DRAWINGS_ROOT", "").strip()
    if not root_value:
        raise _error(503, "PART_DRAWINGS_ROOT_UNCONFIGURED", "Local drawing preview is unavailable because the approved drawings root is not configured.")
    try:
        root = Path(root_value).resolve(strict=True)
        raw_path = Path(str(row.get("fields", {}).get("FilePath") or ""))
        resolved = (root / raw_path).resolve(strict=True) if not raw_path.is_absolute() else raw_path.resolve(strict=True)
        resolved.relative_to(root)
        if not resolved.is_file() or resolved.suffix.casefold() not in {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}:
            raise ValueError("Unsupported preview file")
    except (OSError, ValueError):
        raise _error(404, "PART_DRAWING_NOT_FOUND", "The drawing file is missing or outside the configured drawings root.")
    return FileResponse(resolved, filename=resolved.name, content_disposition_type="inline")


@app.post("/api/parts/{part_id}/vendors")
def create_part_vendor(part_id: str, request: Request, payload: dict[str, Any] = Body(...), idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if not _part_registry().get_part(part_id):
        raise _error(404, "PART_NOT_FOUND", "The selected Part identity was not found.")
    return _part_features().create_vendor(name=str(payload.get("name") or ""), actor=_request_actor(request), reason=str(payload.get("reason") or ""), request_key=idempotency_key or "")


@app.post("/api/parts/{part_id}/purchase-specifications")
def create_purchase_specification(part_id: str, request: Request, payload: dict[str, Any] = Body(...), idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    return _part_features().create_purchase_specification(part_id=part_id, code=str(payload.get("code") or ""), manufacturer=str(payload.get("manufacturer") or ""),
        manufacturer_part_number=str(payload.get("manufacturerPartNumber") or ""), description=str(payload.get("description") or ""),
        costing_uom=str(payload.get("costingUOM") or ""), currency=str(payload.get("currency") or ""),
        purchase_item_id=int(payload["purchaseItemId"]) if payload.get("purchaseItemId") not in (None, "") else None,
        actor=_request_actor(request), reason=str(payload.get("reason") or ""), request_key=idempotency_key or "")


@app.post("/api/parts/{part_id}/vendor-mappings")
def create_vendor_part_mapping(part_id: str, request: Request, payload: dict[str, Any] = Body(...), idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if not _part_registry().get_part(part_id):
        raise _error(404, "PART_NOT_FOUND", "The selected Part identity was not found.")
    return _part_features().create_vendor_mapping(specification_id=int(payload.get("specificationId") or 0), vendor_id=int(payload.get("vendorId") or 0),
        sku=str(payload.get("sku") or ""), description=str(payload.get("description") or ""), actor=_request_actor(request), reason=str(payload.get("reason") or ""), request_key=idempotency_key or "")


@app.post("/api/parts/{part_id}/purchases")
def record_part_purchase(part_id: str, request: Request, payload: dict[str, Any] = Body(...), idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    return _part_features().capture_purchase(part_id=part_id, specification_id=int(payload.get("specificationId") or 0), mapping_id=int(payload.get("vendorMappingId") or 0),
        transaction_key=str(payload.get("transactionKey") or ""), transaction_line_key=str(payload.get("transactionLineKey") or ""),
        record_type=str(payload.get("recordType") or "actual_purchase"), status=str(payload.get("status") or "posted"),
        transaction_at=str(payload.get("transactionAt") or ""), document_reference=str(payload.get("documentReference") or ""),
        quantity=payload.get("quantity"), quantity_uom=str(payload.get("quantityUOM") or ""), currency=str(payload.get("currency") or ""),
        extended_amount=payload.get("extendedAmount"), discount_amount=payload.get("discountAmount", 0), tax_amount=payload.get("taxAmount", 0),
        freight_amount=payload.get("freightAmount", 0), other_charges=payload.get("otherCharges", 0), actor=_request_actor(request), reason=str(payload.get("reason") or ""),
        request_key=idempotency_key or "", reverses_record_id=int(payload["reversesRecordId"]) if payload.get("reversesRecordId") else None,
        supersedes_record_key=str(payload.get("supersedesRecordKey") or ""))


@app.post("/api/parts/{part_id}/purchase-specifications/{specification_id}/unit-conversions")
def add_purchase_unit_conversion(part_id: str, specification_id: int, request: Request, payload: dict[str, Any] = Body(...), idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    return _part_features().add_unit_conversion(specification_id=specification_id, from_uom=str(payload.get("fromUOM") or ""), to_uom=str(payload.get("toUOM") or ""),
        factor=payload.get("factor"), evidence=str(payload.get("evidence") or ""), actor=_request_actor(request), reason=str(payload.get("reason") or ""), request_key=idempotency_key or "")


@app.post("/api/parts/{part_id}/purchase-specifications/{specification_id}/currency-conversions")
def add_purchase_currency_conversion(part_id: str, specification_id: int, request: Request, payload: dict[str, Any] = Body(...), idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    return _part_features().add_currency_conversion(specification_id=specification_id, from_currency=str(payload.get("fromCurrency") or ""), to_currency=str(payload.get("toCurrency") or ""),
        rate=payload.get("rate"), rate_date=str(payload.get("rateDate") or ""), evidence=str(payload.get("evidence") or ""), actor=_request_actor(request),
        reason=str(payload.get("reason") or ""), request_key=idempotency_key or "")


@app.get("/api/parts/{part_id}/purchase-rate")
def part_purchase_rate(part_id: str, as_of: str = ""):
    return _part_features().purchase_detail(part_id, as_of=as_of or None)


@app.get("/api/model-codes/{code_id}/costing-configuration")
def model_code_costing_configuration(code_id: str):
    return _live_cost_service().get_configuration(code_id)


@app.put("/api/model-codes/{code_id}/costing-configuration")
def save_model_code_costing_configuration(code_id: str, request: Request, payload: dict[str, Any] = Body(...), idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    return _live_cost_service().save_configuration(code_id, payload, actor=_request_actor(request), request_key=idempotency_key or "")


@app.get("/api/model-codes/{code_id}/live-cost")
def model_code_live_cost(code_id: str):
    return _live_cost_service().live_cost(code_id)


@app.get("/api/model-codes/{code_id}/cost-snapshots")
def model_code_cost_snapshots(code_id: str, offset: int = 0, limit: int = 25):
    return _live_cost_service().snapshot_history(code_id, offset=offset, limit=limit)


@app.post("/api/model-codes/{code_id}/cost-snapshots")
def save_model_code_cost_snapshot(code_id: str, request: Request, payload: dict[str, Any] = Body(...), idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    return _live_cost_service().save_snapshot(code_id, payload, actor=_request_actor(request), request_key=idempotency_key or "")


@app.post("/api/model-codes/{code_id}/cost-comparisons")
def compare_model_code_cost(code_id: str, payload: dict[str, Any] = Body(...)):
    return _live_cost_service().compare(code_id, payload)


@app.get("/api/model-codes/{code_id}/cost-policy")
def model_code_cost_policy(code_id: str):
    return _live_cost_service().policy_state(code_id)


@app.put("/api/cost-snapshot-policies/{scope_type}")
def save_cost_snapshot_policy(scope_type: str, request: Request, payload: dict[str, Any] = Body(...), idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    return _live_cost_service().set_policy(scope_type, payload.get("scopeId"), payload, actor=_request_actor(request), request_key=idempotency_key or "")


@app.post("/api/line-masters/{line_master_id}/process-rates")
def record_process_rate(line_master_id: int, request: Request, payload: dict[str, Any] = Body(...), idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    return _live_cost_service().record_process_rate(line_master_id, payload, actor=_request_actor(request), request_key=idempotency_key or "")


@app.get("/api/cost-snapshots/{snapshot_key}")
def get_cost_snapshot(snapshot_key: str, offset: int = 0, limit: int = 100):
    return _live_cost_service().get_snapshot(snapshot_key, offset=offset, limit=limit)


@app.post("/api/parts/{part_id}/purchase-rate-evidence")
def save_part_purchase_rate_evidence(part_id: str, payload: dict[str, Any] = Body(...)):
    raise _error(410, "SNAPSHOT_REQUIRED", "Purchase rate evidence is no longer written from a cost-run selection. Resolve the rate through the read-only purchase-rate endpoint, then use Save Cost Snapshot to persist frozen evidence.")


@app.post("/api/parts/mappings")
def save_part_mappings(request: Request, payload: dict[str, Any] = Body(...), idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    from app.part_mapping import PartConflict, save_mapping
    from app.processing import processing_detail
    version = payload.get("expectedVersion")
    association_version = payload.get("expectedAssociationVersion")
    decisions = payload.get("decisions")
    if (type(version) is not int or type(association_version) is not int or not isinstance(decisions, dict)
            or any(not isinstance(key, str) or not isinstance(value, str) for key, value in decisions.items())):
        raise _error(422, "PART_REVIEW_INPUT_INVALID", "Mapping versions and selected Parts are required")
    path = str(payload.get("path") or "")
    try:
        repository, file_id, association, source_hash, groups = _part_context(path)
        def verify_current():
            fresh_repo, fresh_id, fresh_association, fresh_hash = _processing_context(path)
            if (fresh_id != file_id or fresh_hash != source_hash or not fresh_association or not association
                    or (fresh_association.id, fresh_association.version) != (association.id, association.version)):
                raise PartConflict("PART_REVIEW_STALE", "The workbook or association changed; reload the review")
            state = processing_detail(fresh_repo.processing_store, file_id, fresh_association, fresh_hash)
            if state["state"] == "processed":
                raise PartConflict("PART_REOPEN_REQUIRED", "Record Changes Pending before revising Parts for a processed file")
        store = _part_mapping_store(repository) if repository.adapter_name == "grist-safari" or any(not value.isdigit() for value in decisions.values()) else repository.part_store
        return save_mapping(store, file_id=file_id, source_hash=source_hash, association=association,
            groups=groups, decisions=decisions, expected_hash=str(payload.get("expectedHash") or ""),
            expected_version=version, expected_association=str(payload.get("expectedAssociationKey") or ""),
            expected_association_version=association_version, actor=_request_actor(request),
            reason=str(payload.get("reason") or ""), request_key=idempotency_key or "", before_write=verify_current)
    except PartConflict as exc:
        raise _error(409, exc.code, str(exc))


@app.get("/api/processing/state")
def file_processing_state(path: str) -> dict[str, Any]:
    from app.processing import ProcessingConflict, processing_detail
    try:
        repository, file_id, association, source_hash = _processing_context(path)
        return processing_detail(repository.processing_store, file_id, association, source_hash)
    except ProcessingConflict as exc:
        raise _error(409, exc.code, str(exc))


@app.post("/api/processing/state")
def change_processing_state(request: Request, payload: dict[str, Any] = Body(...), idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> dict[str, Any]:
    from app.processing import ProcessingConflict, transition
    path = str(payload.get("path") or "")
    version = payload.get("expectedVersion")
    if not isinstance(version, int) or isinstance(version, bool):
        raise _error(422, "PROCESSING_VERSION_REQUIRED", "A processing version is required.")
    try:
        repository, file_id, association, source_hash = _processing_context(path)
        snapshot = repository.latest_accepted_costing_snapshot(file_id)
        hashes = snapshot.source_hashes if snapshot else {}
        return transition(repository.processing_store, file_id=file_id, association=association,
            source_hash=source_hash, expected_hash=str(payload.get("expectedHash") or ""),
            expected_version=version, state=str(payload.get("state") or ""),
            expected_association_key=str(payload.get("expectedAssociationKey") or ""),
            expected_association_version=payload.get("expectedAssociationVersion"),
            actor=_request_actor(request), reason=str(payload.get("reason") or ""),
            request_key=idempotency_key or "", extracted_hash=hashes.get("selected_workbook_saved"))
    except ProcessingConflict as exc:
        raise _error(409, exc.code, str(exc))


@app.get("/api/mapped-files")
def mapped_files(status: str = "", product_id: str = "", model_id: str = "", query: str = "") -> dict[str, Any]:
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
    if query.strip():
        needle = query.strip().casefold()
        rows = [item for item in rows if needle in json.dumps(item, ensure_ascii=False).casefold()]
    return {"items": rows, "total": len(rows), "adapter": _get_repository().adapter_name}


@app.get("/api/reconciliation/issues")
def reconciliation_issues(status: str = "", issue_type: str = "", severity: str = "", owner: str = "", product_id: str = "", model_id: str = "", file_id: str = "", query: str = "", offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500)) -> dict[str, Any]:
    repository = _get_repository()
    issues = list(repository.issues.values())
    if status:
        issues = [item for item in issues if item.status.casefold() == status.casefold()]
    if issue_type:
        issues = [item for item in issues if item.issue_type.casefold() == issue_type.casefold()]
    if severity:
        issues = [item for item in issues if item.severity.casefold() == severity.casefold()]
    if owner:
        issues = [item for item in issues if (item.assigned_owner or "").casefold() == owner.casefold()]
    if file_id:
        issues = [item for item in issues if _issue_affects_file(item, file_id, repository)]
    if product_id or model_id:
        issues = [item for item in issues if _issue_matches_identity(item, product_id, model_id, repository)]
    if query.strip():
        needle = query.strip().casefold()
        issues = [item for item in issues if needle in json.dumps(item.to_dict(), ensure_ascii=False).casefold()]
    issues.sort(key=lambda item: (item.last_seen_at, item.id), reverse=True)
    return {"total": len(issues), "offset": offset, "limit": limit, "items": [item.to_dict() for item in issues[offset:offset + limit]], "adapter": repository.adapter_name}


@app.get("/api/reconciliation/issues/{issue_id}")
def reconciliation_issue_detail(issue_id: str) -> dict[str, Any]:
    detail = _get_repository().issue_details(issue_id)
    if detail is None:
        raise _error(404, "ISSUE_NOT_FOUND", "Reconciliation issue was not found.")
    return detail


@app.post("/api/reconciliation/issues/{issue_id}/actions/{action}")
def reconciliation_issue_action(issue_id: str, action: str, request: Request, payload: dict[str, Any] = Body(...), idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> dict[str, Any]:
    repository = _get_repository()
    allowed = {"assign", "unassign", "defer", "resolve", "reopen", "keep-open"}
    if action not in allowed:
        raise _error(400, "INVALID_ISSUE_ACTION", f"Unsupported issue action: {action}.")
    current_issue = repository.issues.get(issue_id)
    if action == "resolve" and current_issue and current_issue.issue_type == "invalid_rate_entry":
        try:
            rate_path = _filesystem().resolve(current_issue.source_path)
        except (PathSafetyError, FileNotFoundError):
            raise _error(409, "ISSUE_SOURCE_UNAVAILABLE", "The SteelRateLog source is unavailable; the invalid-rate issue cannot be verified for resolution.")
        try:
            rate_doc = read_ods(rate_path, {"SteelRateLog"})
            _rows, current_findings = extract_rate_log(rate_doc)
        except (OSError, KeyError, ValueError) as exc:
            raise _error(409, "ISSUE_SOURCE_UNAVAILABLE", f"The SteelRateLog source could not be checked: {exc}")
        original_codes = set(current_issue.detected_facts.get("issueCodes", []))
        still_invalid = any(
            finding.get("source_row") == current_issue.source_row
            and finding.get("material") == current_issue.entity_id
            and (not original_codes or finding.get("code") in original_codes)
            for finding in current_findings
        )
        if still_invalid:
            raise _error(409, "ISSUE_CONDITION_STILL_PRESENT", "Correct the SteelRateLog source entry and refresh the costing workbook before resolving this issue.")
    try:
        issue = repository.mutate_issue(issue_id, action.replace("-", "_"), actor=_request_actor(request), reason=str(payload.get("reason") or ""), expected_version=int(payload.get("expectedVersion", 0)), assigned_owner=payload.get("owner"), idempotency_key=idempotency_key)
    except GovernanceConflict as exc:
        status_code = 404 if exc.code.endswith("NOT_FOUND") else 409 if exc.code.startswith(("STALE", "IDEMPOTENCY", "INVALID_ISSUE", "SCHEMA_MIGRATION")) else 422
        raise _error(status_code, exc.code, str(exc), {"current": exc.current.to_dict() if hasattr(exc.current, "to_dict") else None})
    return {"issue": issue.to_dict()}


@app.post("/api/reconciliation/scan")
def reconciliation_scan(payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    limit = min(100, max(1, int(payload.get("limit", 50))))
    dry_run = bool(payload.get("dryRun", True))
    candidates = _build_reconciliation_candidates(limit)
    repository = _get_repository()
    materialized = []
    if not dry_run:
        try:
            materialized = [repository.upsert_issue(issue) for issue in candidates]
        except GovernanceConflict as exc:
            raise _error(409, exc.code, str(exc))
    items = materialized if not dry_run else candidates
    return {"dryRun": dry_run, "scanned": min(limit, len([item for item in repository.files.values() if item.extension.casefold() == ".ods"])), "detected": len(candidates), "materialized": len(materialized), "countsByType": _count_by(items, lambda item: item.issue_type), "countsByStatus": _count_by(items, lambda item: item.status), "items": [item.to_dict() for item in items]}


@app.get("/api/reconciliation/export")
def reconciliation_export(format: str = "json", status: str = "", issue_type: str = "", severity: str = "", owner: str = "", product_id: str = "", model_id: str = "", file_id: str = "", query: str = "") -> Response:
    result = reconciliation_issues(status, issue_type, severity, owner, product_id, model_id, file_id, query, 0, 5000)
    if result["total"] > 5000:
        raise _error(413, "EXPORT_LIMIT_EXCEEDED", "The filtered register exceeds 5,000 issues; narrow the filters before exporting.")
    items = result["items"]
    if format.casefold() == "json":
        content = json.dumps({"total": result["total"], "items": items}, ensure_ascii=False, indent=2)
        return Response(content, media_type="application/json; charset=utf-8", headers={"Content-Disposition": 'attachment; filename="reconciliation-register.json"'})
    if format.casefold() != "csv":
        raise _error(400, "INVALID_EXPORT_FORMAT", "format must be csv or json.")
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\r\n")
    writer.writerow(("id", "fingerprint", "type", "severity", "status", "owner", "entityType", "entityId", "fileId", "sourcePath", "sourceRow", "message", "firstSeenAt", "lastSeenAt", "version", "resolutionAction", "resolutionReason", "detectedFacts"))
    for item in items:
        writer.writerow((item.get("id"), item.get("fingerprint"), item.get("issue_type"), item.get("severity"), item.get("status"), item.get("assigned_owner"), item.get("entity_type"), item.get("entity_id"), item.get("costing_file_id"), item.get("source_path"), item.get("source_row"), item.get("message"), item.get("first_seen_at"), item.get("last_seen_at"), item.get("version"), item.get("resolution_action"), item.get("resolution_reason"), json.dumps(item.get("detected_facts") or {}, ensure_ascii=False, sort_keys=True)))
    return Response(stream.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": 'attachment; filename="reconciliation-register.csv"'})


@app.post("/api/reconciliation/issues/{issue_id}/source-revision/preview")
def source_revision_preview(issue_id: str) -> dict[str, Any]:
    repository = _get_repository()
    issue = repository.issues.get(issue_id)
    if issue is None:
        raise _error(404, "ISSUE_NOT_FOUND", "Reconciliation issue was not found.")
    if issue.issue_type != "changed_file" or not issue.costing_file_id:
        raise _error(422, "ISSUE_NOT_SOURCE_REVISION", "This issue does not describe a changed registered source file.")
    return _source_revision_plan(issue, repository)


@app.post("/api/reconciliation/issues/{issue_id}/source-revision/apply")
def source_revision_apply(issue_id: str, request: Request, payload: dict[str, Any] = Body(...), idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> dict[str, Any]:
    repository = _get_repository()
    issue = repository.issues.get(issue_id)
    if issue is None or not issue.costing_file_id:
        raise _error(404, "ISSUE_NOT_FOUND", "Source revision issue was not found.")
    actor = _request_actor(request)
    if actor.strip().casefold() != "irshad":
        raise _error(403, "APPROVER_NOT_AUTHORIZED", "Only Irshad may accept source revisions during Phase 0.")
    try:
        expected_issue_version = int(payload.get("expectedIssueVersion", 0))
        expected_stored_hash = str(payload.get("expectedStoredHash") or "")
        expected_current_hash = str(payload.get("expectedCurrentHash") or "")
        replay = repository.replay_source_revision(
            idempotency_key,
            file_id=issue.costing_file_id,
            issue_id=issue_id,
            actor=actor,
            reason=str(payload.get("reason") or ""),
            expected_issue_version=expected_issue_version,
            expected_stored_hash=expected_stored_hash,
            expected_current_hash=expected_current_hash,
        )
        if replay is not None:
            return replay
        plan = _source_revision_plan(issue, repository)
        if plan["current"]["sha256"] != expected_current_hash:
            raise GovernanceConflict("STALE_REVISION_FACTS", "The source file changed after preview.", issue)
        final_hash = _current_hash(issue.costing_file_id)
        if final_hash != plan["current"]["sha256"]:
            raise GovernanceConflict("STALE_REVISION_FACTS", "The source file changed while apply was being prepared.", issue)
        observation = _observation_from_revision(issue.costing_file_id, plan["current"])
        return repository.accept_file_revision(issue.costing_file_id, observation, issue_id=issue_id, actor=actor, reason=str(payload.get("reason") or ""), expected_issue_version=expected_issue_version, expected_stored_hash=expected_stored_hash, expected_current_hash=plan["current"]["sha256"], idempotency_key=idempotency_key)
    except GovernanceConflict as exc:
        status_code = 403 if exc.code == "APPROVER_NOT_AUTHORIZED" else 409 if exc.code.startswith(("STALE", "IDEMPOTENCY", "ASSOCIATION", "SCHEMA_MIGRATION")) else 422
        raise _error(status_code, exc.code, str(exc), {"current": exc.current.to_dict() if hasattr(exc.current, "to_dict") else None})


@app.post("/api/reconciliation/identity-cleanup/preview")
def identity_cleanup_preview(payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    plans = _get_repository().plan_identity_cleanup(payload.get("modelIds"))
    for plan in plans:
        model_id = plan["model"]["id"]
        issue = next((item for item in _get_repository().issues.values() if item.issue_type == "invalid_identity_encoding" and item.entity_id == model_id), None)
        plan["issue"] = issue.to_dict() if issue else None
    return {"dryRun": True, "items": plans, "canApplyCount": sum(bool(item["canApply"]) for item in plans)}


@app.post("/api/reconciliation/identity-cleanup/apply")
def identity_cleanup_apply(request: Request, payload: dict[str, Any] = Body(...), idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> dict[str, Any]:
    actor = _request_actor(request)
    if actor.strip().casefold() != "irshad":
        raise _error(403, "APPROVER_NOT_AUTHORIZED", "Only Irshad may approve identity cleanup during Phase 0.")
    try:
        result = _get_repository().apply_identity_cleanup(str(payload.get("modelId") or ""), str(payload.get("canonicalModelId") or ""), issue_id=str(payload.get("issueId") or ""), actor=actor, reason=str(payload.get("reason") or ""), expected_issue_version=int(payload.get("expectedIssueVersion", 0)), idempotency_key=idempotency_key)
        return result
    except GovernanceConflict as exc:
        code = exc.code
        status_code = 409 if code.startswith(("STALE", "IDEMPOTENCY", "IDENTITY_CLEANUP", "SCHEMA_MIGRATION")) else 403 if code == "APPROVER_NOT_AUTHORIZED" else 422
        raise _error(status_code, code, str(exc), {"current": exc.current.to_dict() if hasattr(exc.current, "to_dict") else None})


@app.get("/api/directory-mappings")
def directory_mappings(status: str = "") -> dict[str, Any]:
    items = list(_get_repository().directory_mappings.values())
    if status:
        items = [item for item in items if item.status == status]
    items.sort(key=lambda item: (item.normalized_path, item.version))
    return {"total": len(items), "items": [item.to_dict() for item in items]}


@app.post("/api/directory-mappings")
def create_directory_mapping(request: Request, payload: dict[str, Any] = Body(...), idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> dict[str, Any]:
    try:
        item = _get_repository().propose_directory_mapping(str(payload.get("relativePath") or ""), str(payload.get("productId") or ""), inherit=bool(payload.get("inherit", False)), actor=_request_actor(request), reason=str(payload.get("reason") or ""), idempotency_key=idempotency_key)
        return {"mapping": item.to_dict()}
    except GovernanceConflict as exc:
        raise _error(409 if exc.code == "SCHEMA_MIGRATION_REQUIRED" else 422, exc.code, str(exc))


@app.post("/api/directory-mappings/{mapping_id}/{action}")
def directory_mapping_action(mapping_id: str, action: str, request: Request, payload: dict[str, Any] = Body(...), idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> dict[str, Any]:
    if action not in {"approve", "reject", "supersede"}:
        raise _error(400, "INVALID_MAPPING_ACTION", f"Unsupported directory mapping action: {action}.")
    try:
        item = _get_repository().transition_directory_mapping(mapping_id, action, actor=_request_actor(request), reason=str(payload.get("reason") or ""), expected_version=int(payload.get("expectedVersion", 0)), idempotency_key=idempotency_key)
        return {"mapping": item.to_dict()}
    except GovernanceConflict as exc:
        status_code = 403 if exc.code == "APPROVER_NOT_AUTHORIZED" else 409 if exc.code.startswith(("STALE", "INVALID_MAPPING", "IDEMPOTENCY", "SCHEMA_MIGRATION")) else 422
        raise _error(status_code, exc.code, str(exc), {"current": exc.current.to_dict() if hasattr(exc.current, "to_dict") else None})


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
    directory_path = Path(path).parent.as_posix()
    row["directoryProductMapping"] = repository.effective_directory_mapping(directory_path) if hasattr(repository, "effective_directory_mapping") else None
    row["directoryMappingProposals"] = [item.to_dict() for item in repository.directory_mappings.values() if item.normalized_path == directory_path.casefold() and item.status == "proposed"] if hasattr(repository, "directory_mappings") else []

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
    row["observationHistory"] = [item.to_dict() for item in repository.observations if item.file_id == file_id]
    row["durableIssues"] = [item.to_dict() for item in repository.issues.values() if item.costing_file_id == file_id or (item.entity_type == "CostingFile" and item.entity_id == file_id)]
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


def _build_reconciliation_candidates(limit: int) -> list[ReconciliationIssue]:
    repository = _get_repository()
    candidates: list[ReconciliationIssue] = []
    try:
        filesystem = _filesystem()
        nodes = filesystem.walk_files(extension=".ods")
    except (FileNotFoundError, PathSafetyError):
        filesystem = None
        nodes = []
    node_by_path = {item.relative_path.casefold(): item for item in nodes}
    registered = [item for item in repository.files.values() if item.extension.casefold() == ".ods"]
    registered.sort(key=lambda item: item.relative_path.casefold())
    checked = 0
    for file in registered:
        if checked >= limit:
            break
        checked += 1
        latest = max((item for item in repository.observations if item.file_id == file.id), key=lambda item: item.observed_at, default=None)
        current_node = node_by_path.get(file.relative_path.casefold())
        if filesystem is None:
            continue
        if current_node is None:
            moved_to = _find_moved_file(file.relative_path, latest.modified_at if latest else file.modified_at, latest.size_bytes if latest else file.size_bytes, latest.file_hash if latest else file.file_hash, node_by_path, filesystem)
            issue_type = "moved_file" if moved_to else "missing_file"
            candidates.append(_make_issue(issue_type, "warning" if moved_to else "error", f"Registered costing file is {'possibly moved to ' + moved_to if moved_to else 'missing from the configured root'}.", file=file, facts={"storedPath": file.relative_path, "movedTo": moved_to, "storedHash": latest.file_hash if latest else file.file_hash}, resolution={"action": "verify_moved_file" if moved_to else "restore_or_retire_mapping"}))
            continue
        try:
            current = filesystem.inspect(file.relative_path)
        except (OSError, PathSafetyError) as exc:
            candidates.append(_make_issue("unreadable_file", "error", f"Registered costing file could not be inspected: {exc}", file=file, facts={"path": file.relative_path, "error": str(exc)}))
            continue
        baseline_hash = latest.file_hash if latest else file.file_hash
        baseline_size = latest.size_bytes if latest else file.size_bytes
        baseline_mtime = latest.modified_at if latest else file.modified_at
        if baseline_hash and current.content_hash and baseline_hash != current.content_hash:
            candidates.append(_make_issue("changed_file", "high", "The registered costing workbook differs from its last accepted source observation.", file=file, facts={"stored": _observation_facts(file.relative_path, baseline_size, baseline_mtime, baseline_hash, latest), "current": _node_facts(current)}, resolution={"action": "accept_source_revision", "requiresOwnerApproval": True}))
        if current.readable is False or current.parse_error:
            candidates.append(_make_issue("parse_error", "error", current.parse_error or "The costing workbook could not be read.", file=file, facts=_node_facts(current), resolution={"action": "review_unreadable_source"}))
        if (current.external_reference_count or 0) > 0:
            candidates.append(_make_issue("external_link_review", "warning", f"Workbook contains {current.external_reference_count} external references requiring review.", file=file, facts=_node_facts(current), resolution={"action": "review_external_links"}))

    # Rate warnings are derived for previews, then become durable issues only
    # when the operator explicitly materializes a reconciliation scan.
    master_root = _costing_root() / "Template DB"
    rate_dump = master_root / "Spares List - Master.ods"
    rate_findings: dict[tuple[str, int], dict[str, Any]] = {}
    if filesystem is not None and rate_dump.is_file():
        for file in registered[:checked]:
            current_node = node_by_path.get(file.relative_path.casefold())
            if current_node is None or current_node.readable is False or current_node.parse_error:
                continue
            try:
                selected_path = filesystem.resolve(file.relative_path)
                rate_index = build_mcl_rate_warning_index(
                    selected_path,
                    master_root / "MaterialCostDB.ods",
                    rate_dump,
                )
            except (OSError, PathSafetyError, KeyError, ValueError):
                continue
            if rate_index.get("status") == "unavailable":
                continue
            for mcl_row, warning in rate_index.get("rows", {}).items():
                for source_row in warning.get("rateLogRows", []):
                    key = (str(warning.get("material", "")), int(source_row))
                    if not key[0]:
                        continue
                    finding = rate_findings.setdefault(key, {
                        "material": key[0],
                        "rateLogRow": key[1],
                        "issueCodes": set(),
                        "rateLogCells": set(),
                        "affectedMclRows": [],
                    })
                    finding["issueCodes"].update(warning.get("issueCodes", []))
                    finding["rateLogCells"].update(warning.get("rateLogCells", []))
                    finding["affectedMclRows"].append({"path": file.relative_path, "row": int(mcl_row)})
    rate_dump_relative = str(rate_dump.relative_to(_costing_root())).replace("\\", "/")
    for (material, source_row), finding in sorted(rate_findings.items()):
        affected = sorted(finding["affectedMclRows"], key=lambda item: (item["path"].casefold(), item["row"]))
        candidates.append(_make_issue(
            "invalid_rate_entry",
            "high",
            f"A confirmed invalid SteelRateLog entry for {material!r} at source row {source_row} blocks its effective rate and dependent cost in {len(affected)} active Material Cut List row(s).",
            entity_type="Material",
            entity_id=material,
            source_file=rate_dump_relative,
            source_row=source_row,
            facts={
                "material": material,
                "sourcePath": rate_dump_relative,
                "sourceRow": source_row,
                "sourceCells": sorted(finding["rateLogCells"]),
                "issueCodes": sorted(finding["issueCodes"]),
                "affectedMclRows": affected,
            },
            resolution={"action": "correct_rate_log_source_then_review", "requiresUserReview": True},
        ))

    owners_by_code: dict[str, list[Any]] = {}
    for link in repository.code_associations.values():
        if link.active:
            owners_by_code.setdefault(link.code_id, []).append(link)
    for code_id, owners in owners_by_code.items():
        if len(owners) > 1:
            code = repository.codes.get(code_id)
            candidates.append(_make_issue("duplicate_code_ownership", "error", f"Model Code {code.code if code else code_id} has multiple active file owners.", entity_type="ProductModelCode", entity_id=code_id, facts={"ownerFileIds": sorted(item.file_id for item in owners)}, resolution={"action": "guided_supersede_review"}))

    for model in repository.models.values():
        if not model.active:
            continue
        codes = [item for item in repository.codes.values() if item.model_id == model.id and item.active]
        canonical = None
        if "\ufffd" in f"{model.model_number} {model.name}":
            expected = f"{model.model_number} {model.name}".replace("\ufffd", "–")
            canonical = next((item for item in repository.models.values() if item.id != model.id and item.active and item.product_id == model.product_id and f"{item.model_number} {item.name}" == expected), None)
            candidates.append(_make_issue("invalid_identity_encoding", "error", f"Active Product Model {model.model_number!r} contains a Unicode replacement character.", entity_type="ProductModel", entity_id=model.id, source_file=model.source_file or "", source_row=model.source_row, facts={"previousValue": model.model_number, "sourceFile": model.source_file, "sourceRow": model.source_row, "canonicalReplacementId": canonical.id if canonical else None, "canonicalReplacement": canonical.model_number if canonical else None}, resolution={"action": "supersede_corrupted_identity", "requiresOwnerApproval": True}))
        if not codes:
            candidates.append(_make_issue("unmatched_active_identity", "warning", f"Active Product Model {model.model_number!r} has no active Model Codes.", entity_type="ProductModel", entity_id=model.id, source_file=model.source_file or "", source_row=model.source_row, facts={"modelNumber": model.model_number, "sourceFile": model.source_file, "sourceRow": model.source_row}, resolution={"action": "review_catalog_identity"}))

    for code in repository.codes.values():
        model = repository.models.get(code.model_id)
        if model is None or model.product_id not in repository.products:
            candidates.append(_make_issue("catalog_reference_mismatch", "error", f"Model Code {code.code!r} points to a missing Model or Product.", entity_type="ProductModelCode", entity_id=code.id, facts={"modelId": code.model_id, "productId": model.product_id if model else None}, resolution={"action": "repair_catalog_reference"}))
    return candidates


def _make_issue(issue_type: str, severity: str, message: str, *, file: Any = None, entity_type: str = "", entity_id: str | None = None, source_file: str = "", source_row: int | None = None, facts: dict[str, Any] | None = None, resolution: dict[str, Any] | None = None) -> ReconciliationIssue:
    from hashlib import sha256
    file_id = file.id if file is not None else None
    path = file.relative_path if file is not None else source_file
    fingerprint_source = "\0".join((issue_type, entity_type or ("CostingFile" if file is not None else ""), entity_id or file_id or path, path.casefold(), str(source_row or "")))
    fingerprint = sha256(fingerprint_source.encode("utf-8")).hexdigest()
    return ReconciliationIssue(id=f"issue:auto:{fingerprint[:24]}", issue_type=issue_type, severity=severity, message=message, source_file=source_file or path, source_row=source_row, entity_id=entity_id or file_id, fingerprint=fingerprint, entity_type=entity_type or ("CostingFile" if file is not None else ""), costing_file_id=file_id, source_path=path, detected_facts=facts or {}, proposed_resolution=resolution or {})


def _node_facts(node: Any) -> dict[str, Any]:
    return {"path": node.relative_path, "sizeBytes": node.size_bytes, "modifiedAt": node.modified_at, "sha256": node.content_hash, "readable": node.readable, "sheetCount": node.sheet_count, "externalReferenceCount": node.external_reference_count, "parseError": node.parse_error}


def _observation_facts(path: str, size_bytes: int, modified_at: str, file_hash: str | None, observation: Any) -> dict[str, Any]:
    return {"path": path, "sizeBytes": size_bytes, "modifiedAt": modified_at, "sha256": file_hash, "readable": observation.readable if observation else None, "sheetCount": observation.sheet_count if observation else None, "externalReferenceCount": observation.external_reference_count if observation else None, "parseError": observation.parse_error if observation else None, "observationId": observation.id if observation else None}


def _source_revision_plan(issue: ReconciliationIssue, repository: SafariRepository) -> dict[str, Any]:
    file = repository.get_file(issue.costing_file_id or "")
    if file is None:
        raise _error(409, "REVISION_FILE_MISSING", "The registered costing file no longer exists.")
    latest = max((item for item in repository.observations if item.file_id == file.id), key=lambda item: item.observed_at, default=None)
    try:
        current = _filesystem().inspect(file.relative_path)
    except (OSError, PathSafetyError) as exc:
        raise _error(409, "REVISION_SOURCE_UNAVAILABLE", str(exc))
    if not current.content_hash:
        raise _error(422, "REVISION_HASH_UNAVAILABLE", "The current workbook hash could not be computed.")
    association = repository.current_association(file.id)
    active_codes = [item for item in repository.code_associations.values() if association and item.active and item.association_id == association.id]
    ownership_valid = bool(association and active_codes) and all(repository.codes.get(item.code_id) and repository.codes[item.code_id].active and repository.codes[item.code_id].model_id == association.model_id and len([owner for owner in repository.code_associations.values() if owner.active and owner.code_id == item.code_id]) == 1 for item in active_codes)
    return {"issue": issue.to_dict(), "file": file.to_dict(), "stored": _observation_facts(file.relative_path, latest.size_bytes if latest else file.size_bytes, latest.modified_at if latest else file.modified_at, latest.file_hash if latest else file.file_hash, latest), "current": _node_facts(current), "association": association.to_dict() if association else None, "ownedCodes": [repository.codes[item.code_id].to_dict() for item in active_codes if item.code_id in repository.codes], "canApply": bool(ownership_valid and current.readable and current.content_hash != (latest.file_hash if latest else file.file_hash)), "issueVersion": issue.version}


def _observation_from_revision(file_id: str, facts: dict[str, Any]) -> FileObservation:
    return FileObservation(id=f"observation:{file_id}:{facts['sha256']}", file_id=file_id, observed_at=utc_now(), relative_path=str(facts["path"]), normalized_path=str(facts["path"]).casefold(), size_bytes=int(facts["sizeBytes"] or 0), modified_at=str(facts["modifiedAt"] or ""), file_hash=str(facts["sha256"]), readable=bool(facts["readable"]), sheet_count=facts.get("sheetCount"), external_reference_count=facts.get("externalReferenceCount"), parse_error=facts.get("parseError"))


def _issue_matches_identity(issue: ReconciliationIssue, product_id: str, model_id: str, repository: SafariRepository) -> bool:
    candidate_models: dict[str, Any] = {}
    if issue.entity_type.casefold() == "productmodel":
        direct_model = repository.models.get(issue.entity_id or "")
        if direct_model:
            candidate_models[direct_model.id] = direct_model
    if issue.entity_type.casefold() == "productmodelcode":
        code = repository.codes.get(issue.entity_id or "")
        if code and code.model_id in repository.models:
            candidate_models[code.model_id] = repository.models[code.model_id]
    fact_model = repository.models.get(str(issue.detected_facts.get("modelId") or ""))
    if fact_model:
        candidate_models[fact_model.id] = fact_model
    if issue.costing_file_id:
        association = repository.current_association(issue.costing_file_id)
        if association and association.model_id in repository.models:
            candidate_models[association.model_id] = repository.models[association.model_id]
    affected_paths = {
        str(row.get("path", "")).casefold()
        for row in issue.detected_facts.get("affectedMclRows", [])
        if isinstance(row, dict) and row.get("path")
    }
    for file in repository.files.values():
        if file.relative_path.casefold() in affected_paths:
            association = repository.current_association(file.id)
            if association and association.model_id in repository.models:
                candidate_models[association.model_id] = repository.models[association.model_id]
    if model_id and not any(model.id == model_id for model in candidate_models.values()):
        return False
    if product_id and not any(model.product_id == product_id for model in candidate_models.values()):
        return issue.detected_facts.get("productId") == product_id and not model_id
    return True


def _issue_affects_file(issue: ReconciliationIssue, file_id: str, repository: SafariRepository) -> bool:
    if issue.costing_file_id == file_id or issue.entity_id == file_id:
        return True
    target = repository.files.get(file_id)
    if target is None:
        return False
    return any(
        isinstance(row, dict)
        and str(row.get("path", "")).casefold() == target.relative_path.casefold()
        for row in issue.detected_facts.get("affectedMclRows", [])
    )


def _count_by(items: list[Any], selector: Any) -> dict[str, int]:
    result: dict[str, int] = {}
    for item in items:
        key = str(selector(item))
        result[key] = result.get(key, 0) + 1
    return result


def _preview_workbook(path: str, workbook_path: Path, sheet: str, start_row: int, row_count: int, start_col: int, column_count: int, refresh_metadata: dict[str, Any] | None = None) -> dict[str, Any]:
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
    rate_warnings: dict[str, Any] = {"status": "not_applicable", "rows": {}, "globalWarnings": []}
    if selected_sheet == "5. Material Cut List Price":
        master_root = _costing_root() / "Template DB"
        try:
            rate_warnings = build_mcl_rate_warning_index(
                workbook_path,
                master_root / "MaterialCostDB.ods",
                master_root / "Spares List - Master.ods",
            )
        except (OSError, KeyError, ValueError) as exc:
            rate_warnings = {
                "status": "unavailable",
                "message": f"Rate validation could not be completed: {exc}",
                "missingSources": [],
                "rows": {},
                "globalWarnings": [],
            }
    if rate_warnings.get("rows"):
        try:
            durable_rate_issues = [
                item for item in _get_repository().issues.values()
                if item.issue_type == "invalid_rate_entry"
            ]
            for warning in rate_warnings["rows"].values():
                source_rows = set(warning.get("rateLogRows", []))
                warning["durableIssues"] = [
                    {"id": issue.id, "status": issue.status, "version": issue.version, "sourceRow": issue.source_row}
                    for issue in durable_rate_issues
                    if issue.entity_id == warning.get("material") and issue.source_row in source_rows
                ]
        except (AttributeError, KeyError, TypeError):
            # Derived preview warnings remain available before issue materialization.
            pass
    rows_payload = [[_json_cell(value) for value in row[:width]] for row in selected]
    warning_rows = rate_warnings.get("rows", {})
    if selected_sheet == "5. Material Cut List Price" and warning_rows:
        field_numbers = {
            name: field_number - 1
            for name, field_number in zip(
                ("rate_per_kg", "material_cost", "grand_total"),
                rate_warnings.get("maskedColumns", []),
            )
        }
        rate_column = field_numbers.get("rate_per_kg")
        for row_index, physical_row in enumerate(range(start_row, start_row + len(selected))):
            warning = warning_rows.get(str(physical_row))
            if not warning:
                continue
            for field_name, column_index in field_numbers.items():
                local_column = column_index - col_start
                if not 0 <= local_column < width:
                    continue
                replacement = "RATE BLOCKED" if field_name == "rate_per_kg" else None
                rows_payload[row_index][local_column] = replacement
                cells[row_index][local_column] = {
                    "value": replacement,
                    "kind": "warning",
                    "formula": cells[row_index][local_column].get("formula"),
                }
            material_column = rate_warnings.get("materialColumn")
            local_material_column = (material_column - 1 - col_start) if material_column else -1
            for local_column in range(width):
                cells[row_index][local_column]["kind"] = "warning"
            if 0 <= local_material_column < width:
                cells[row_index][local_material_column]["warning"] = warning
    result = {"path": path, "sheets": sheet_names, "sheet": selected_sheet, "startRow": start_row, "startColumn": start_col, "totalRows": len(rows), "totalColumns": max((len(row) for row in rows), default=0), "truncatedRows": row_start + row_count < len(rows), "truncatedColumns": any(len(row) > col_start + width for row in selected_rows), "readOnly": True, "formulaCount": scan.formula_count, "externalReferenceCount": scan.external_reference_count, "externalLinkWarning": scan.external_reference_count > 0, "rows": rows_payload, "cells": cells, "rateWarnings": rate_warnings}
    if refresh_metadata:
        result["refreshMetadata"] = refresh_metadata
    return result


def _preview_arguments(start_row: Any, row_count: Any, start_col: Any, column_count: Any) -> tuple[int, int, int, int]:
    # Defaults are Query objects when endpoints are exercised directly.
    return (
        start_row if isinstance(start_row, int) else 1,
        row_count if isinstance(row_count, int) else 40,
        start_col if isinstance(start_col, int) else 1,
        column_count if isinstance(column_count, int) else 40,
    )


def _resolve_preview_workbook(path: str) -> Path:
    try:
        workbook_path = _filesystem().resolve(path)
    except PathSafetyError as exc:
        raise _error(400, exc.code, str(exc))
    except FileNotFoundError:
        raise _error(404, "FILE_NOT_FOUND", "ODS file not found below the configured root.")
    if workbook_path.suffix.casefold() != ".ods":
        raise _error(415, "UNSUPPORTED_EXTENSION", "Workbook preview supports ODS files only.")
    return workbook_path


def _costing_file_id_for_path(workbook_path: Path, repository: SafariRepository) -> str | None:
    """Resolve one registered Safari file by its path, never by basename alone."""
    target = workbook_path.resolve()
    root = _costing_root()
    matches: list[str] = []
    for item in repository.files.values():
        for value in (item.relative_path, item.normalized_path):
            if not value:
                continue
            candidate = Path(value)
            if not candidate.is_absolute():
                candidate = root / candidate
            try:
                if candidate.resolve() == target:
                    matches.append(item.id)
                    break
            except (OSError, RuntimeError):
                continue
    return matches[0] if len(set(matches)) == 1 else None


@app.get("/api/catalog/preview")
def preview(path: str, sheet: str = "", start_row: int = Query(1, ge=1), row_count: int = Query(40, ge=1, le=200), start_col: int = Query(1, ge=1), column_count: int = Query(40, ge=1, le=80)) -> dict[str, Any]:
    start_row, row_count, start_col, column_count = _preview_arguments(start_row, row_count, start_col, column_count)
    workbook_path = _resolve_preview_workbook(path)
    return _preview_workbook(path, workbook_path, sheet, start_row, row_count, start_col, column_count)


@app.post("/api/catalog/preview/refresh")
def refresh_preview(path: str, sheet: str = "", start_row: int = Query(1, ge=1), row_count: int = Query(40, ge=1, le=200), start_col: int = Query(1, ge=1), column_count: int = Query(40, ge=1, le=80)) -> dict[str, Any]:
    start_row, row_count, start_col, column_count = _preview_arguments(start_row, row_count, start_col, column_count)
    workbook_path = _resolve_preview_workbook(path)
    try:
        if not linked_ods_sources(workbook_path, _costing_root(), allow_empty=True):
            original_hash = sha256_file(workbook_path)
            result = _preview_workbook(path, workbook_path, sheet, start_row, row_count, start_col, column_count, {
                "status": "no_external_links", "checkedAt": utc_now(),
                "originalSha256": original_hash, "refreshedCopySha256": None,
                "linkedSources": [], "sourceWorkbookUnchanged": True, "linkedSourcesUnchanged": True,
            })
            if sha256_file(workbook_path) != original_hash:
                raise LibreOfficeRefreshError("The source workbook changed while previewing.")
            return result
        with refreshed_ods_copy(workbook_path, _costing_root()) as evidence:
            metadata = {
                "status": "refreshed_temporary_copy",
                "checkedAt": evidence.checked_at,
                "originalSha256": evidence.original_sha256,
                "refreshedCopySha256": evidence.refreshed_copy_sha256,
                "linkedSources": evidence.linked_sources,
                "sourceWorkbookUnchanged": True,
                "linkedSourcesUnchanged": True,
            }
            return _preview_workbook(
                path, evidence.temporary_path, sheet, start_row, row_count,
                start_col, column_count, metadata,
            )
    except LibreOfficeRefreshError as exc:
        raise _error(422, "EXTERNAL_REFRESH_FAILED", str(exc))


@app.post("/api/catalog/costing-review")
def costing_review(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Refresh and calculate without writing; compare with the accepted Safari state."""
    relative_path = str(payload.get("path") or "").strip()
    if not relative_path:
        raise _error(400, "COSTING_FILE_REQUIRED", "Select a product costing workbook first.")
    workbook_path = _resolve_preview_workbook(relative_path)
    master_root = _costing_root() / "Template DB"
    raw_steel_path = master_root / "MaterialCostDB.ods"
    rate_dump_path = master_root / "Spares List - Master.ods"
    missing = [str(path.relative_to(_costing_root())) for path in (raw_steel_path, rate_dump_path) if not path.is_file()]
    if missing:
        raise _error(
            422,
            "COSTING_SOURCE_UNAVAILABLE",
            "Current costing review needs the configured RawSteel and SteelRateLog source workbooks.",
            {"missingSources": missing},
        )
    try:
        repository = _get_repository()
        file_id = _costing_file_id_for_path(workbook_path, repository)
        accepted = repository.latest_accepted_costing_snapshot(file_id) if file_id else None
        accepted_payload = ({
            "snapshot_key": accepted.snapshot_key,
            "semantic_hash": accepted.semantic_hash,
            "content": accepted.semantic_content,
        } if accepted else None)
        with refreshed_ods_copy(workbook_path, _costing_root()) as evidence:
            review = build_current_costing_review(
                workbook_path,
                evidence.temporary_path,
                raw_steel_path,
                rate_dump_path,
                accepted_payload,
            )
            review["refresh"] = {
                "required": True,
                "verified": True,
                "status": "refreshed_temporary_copy",
                "checked_at": evidence.checked_at,
                "original_sha256": evidence.original_sha256,
                "refreshed_copy_sha256": evidence.refreshed_copy_sha256,
                "linked_sources": evidence.linked_sources,
                "source_workbook_unchanged": True,
                "linked_sources_unchanged": True,
            }
            review["costing_file_id"] = file_id
            review["accepted_baseline"] = ({
                "snapshot_key": accepted.snapshot_key,
                "semantic_hash": accepted.semantic_hash,
                "accepted_at": accepted.accepted_at,
                "accepted_by": accepted.accepted_by,
            } if accepted else None)
            review["persistence_adapter"] = repository.adapter_name
            review["can_accept"] = bool(
                file_id
                and review["status"] == "ready_for_owner_review"
                and not review["semantic_comparison"]["owner_review_required"]
            )
            from app.interim_rates import load_interim_rates
            rate_state = review.get("semantic_snapshot", {}).get("content", {}).get("rate_state", {})
            try:
                review["interim_rates"] = load_interim_rates({name: value.get("effective_rate_per_kg") for name, value in rate_state.items()}) if rate_state else {
                    "status": "not_applicable", "rows": [], "readOnly": True, "processingBlocker": False}
            except (CostingAppError, ValueError, OSError) as exc:
                review["interim_rates"] = {"status": "unavailable", "message": str(exc), "rows": [],
                                           "readOnly": True, "processingBlocker": False}
            if file_id and review.get("processing_evidence"):
                from app.part_mapping import source_groups, mapping_detail, attach_mapping_evidence
                detail = mapping_detail(_part_mapping_store(repository), file_id=file_id,
                    source_hash=evidence.original_sha256, association=repository.current_association(file_id),
                    groups=source_groups(review["semantic_snapshot"]["content"]["process_lists"]))
                attach_mapping_evidence(review["processing_evidence"], detail)
            return review
    except LibreOfficeRefreshError as exc:
        raise _error(422, "EXTERNAL_REFRESH_FAILED", str(exc))
    except (OSError, KeyError, ValueError, BadZipFile, ET.ParseError) as exc:
        raise _error(422, "COSTING_REVIEW_UNAVAILABLE", str(exc))


@app.get("/api/model-codes/{code_id}/records")
def model_code_records(
    code_id: str,
    sheet: str = "", process: str = "", master: str = "",
    part: str = "", material: str = "", status: str = "",
    offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500),
) -> dict[str, Any]:
    """Inspect stored file-baseline evidence without opening or hashing an ODS."""
    repository = _get_repository()
    code = repository.codes.get(code_id)
    if code is None or not code.active:
        raise _error(404, "MODEL_CODE_NOT_FOUND", "Active Model Code not found")
    owners = [link for link in repository.code_associations.values() if link.active and link.code_id == code_id]
    if len(owners) > 1:
        raise _error(409, "CODE_OWNERSHIP_CONFLICT", "Multiple active source files own this Model Code")
    owner = owners[0] if owners else None
    association = repository.associations.get(owner.association_id) if owner else None
    file = repository.files.get(association.file_id) if association else None
    if owner and (not association or not association.active or association.model_id != code.model_id or not file or owner.file_id != association.file_id):
        raise _error(409, "CODE_OWNERSHIP_CONFLICT", "The source association is missing or inconsistent")
    snapshot = repository.latest_accepted_costing_snapshot(file.id) if file else None
    result: dict[str, Any] = {
        "code": code.to_dict(), "source": file.to_dict() if file else None,
        "association": association.to_dict() if association else None,
        "readAt": utc_now(), "sourceRead": False, "authorityChanged": False,
        "scope": "shared_file_baseline", "configurationStatus": "review_required",
        "baseline": {"snapshotKey": snapshot.snapshot_key, "sourceHash": snapshot.source_hashes.get("selected_workbook_saved"),
                     "acceptedAt": snapshot.accepted_at, "observedAt": snapshot.observed_at} if snapshot else None,
        "persistence": repository.adapter_name, "total": 0, "items": [],
        "status": "unassociated" if not file else "no_accepted_snapshot" if not snapshot else "storage_unavailable",
    }
    if snapshot and repository.adapter_name == "grist-safari":
        from app.normalized_store import GristNormalizedStore
        try:
            result.update(GristNormalizedStore(repository.client).query(
                snapshot_key=snapshot.snapshot_key,
                filters={"sheet": sheet, "process": process, "master": master, "part": part, "material": material, "status": status},
                offset=offset, limit=limit))
            result["status"] = "stored"
        except GristError as exc:
            if "404" not in str(exc):
                raise
            result["status"] = "schema_unavailable"
        except ValueError as exc:
            raise _error(409, "STORED_BASELINE_CONFLICT", str(exc))
    return result


@app.get("/api/catalog/normalized")
def normalized_inspection(
    path: str,
    sheet: str = "",
    process: str = "",
    master: str = "",
    part: str = "",
    material: str = "",
    status: str = "",
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
) -> dict[str, Any]:
    """Read-only row inspection from the accepted Safari baseline."""
    from app.config import load_material_mapping
    from app.normalized import capture_source_rows, project_snapshot
    from app.repository import sha256_file

    workbook_path = _resolve_preview_workbook(path)
    repository = _get_repository()
    file_id = _costing_file_id_for_path(workbook_path, repository)
    accepted = repository.latest_accepted_costing_snapshot(file_id) if file_id else None
    current_hash = sha256_file(workbook_path)
    cost_drift_assessment = None
    if accepted is None:
        from app.milestone2 import _configured_process_lines, extract_raw_steel, read_ods
        raw_path = _costing_root() / "Template DB" / "MaterialCostDB.ods"
        document = read_ods(workbook_path)
        lines = _configured_process_lines(document)
        raw_rows, _ = extract_raw_steel(read_ods(raw_path, {"RawSteel"})) if raw_path.is_file() else ([], [])
        semantic_content = {"process_lists": lines, "material_master_state": {
            row["unique_item_list"]: {} for row in raw_rows if row.get("unique_item_list")
        }}
        snapshot_key = f"local-unaccepted:{current_hash}"
        accepted_hash = current_hash
        source_row_cells = capture_source_rows(document, set(lines))
        dependency_hashes = {name: sha256_file(source) for name, source in {
            "raw_steel": raw_path,
            "rate_log_dump": _costing_root() / "Template DB" / "Spares List - Master.ods",
        }.items() if source.is_file()}
    else:
        semantic_content = accepted.semantic_content
        snapshot_key = accepted.snapshot_key
        accepted_hash = accepted.source_hashes.get("selected_workbook_saved")
        dependency_hashes = accepted.source_hashes
        from app.milestone2 import _configured_process_lines, extract_product_workbook, read_ods
        from app.normalized_import import assess_cost_drift
        document = read_ods(workbook_path)
        current_lists = _configured_process_lines(document)
        workbook_total = extract_product_workbook(document)["summary_totals"]["grand_total"]["cached_value"]
        if workbook_total is not None:
            cost_drift_assessment = assess_cost_drift(semantic_content, {"process_lists": current_lists}, workbook_total)
        if cost_drift_assessment and cost_drift_assessment["cost_only_confirmed"]:
            semantic_content = {**semantic_content, "process_lists": current_lists}
            source_row_cells = capture_source_rows(document, set(current_lists))
        else:
            source_row_cells = None
    projection = project_snapshot(
        semantic_content,
        snapshot_key=snapshot_key,
        source_hash=str(current_hash if cost_drift_assessment and cost_drift_assessment["cost_only_confirmed"] else accepted_hash or ""),
        material_mappings=load_material_mapping(),
        dependency_hashes=dependency_hashes,
        source_row_cells=source_row_cells,
    )
    if accepted is not None and repository.adapter_name == "grist-safari":
        from app.normalized_store import GristNormalizedStore
        store = GristNormalizedStore(repository.client)
        try:
            all_persisted = store.query(snapshot_key=snapshot_key, filters={}, offset=0, limit=100000)
            if all_persisted["total"]:
                persisted = store.query(snapshot_key=snapshot_key,
                                        filters={"sheet": sheet, "process": process, "master": master, "part": part,
                                                 "material": material, "status": status},
                                        offset=offset, limit=limit)
                from collections import Counter
                persistent_counts = Counter((row["observation"]["sheet"], row["observation"]["status"]) for row in all_persisted["items"])
                expected_counts = projection["reconciliation"]["expected_counts"]
                count_differences = [{"sheet": name, "status": state, "expected": expected,
                                      "actual": persistent_counts[(name, state)]}
                                     for name, statuses in expected_counts.items() for state, expected in statuses.items()
                                     if persistent_counts[(name, state)] != expected]
                return {"baseline": {"snapshotKey": snapshot_key, "semanticHash": accepted.semantic_hash,
                           "sourceHash": accepted_hash, "currentSourceHash": current_hash,
                           "sourceMatchesBaseline": current_hash == accepted_hash, "accepted": True,
                           "costDriftAssessment": cost_drift_assessment},
                        "persistence": "grist", "counts": {"persisted_rows": len(all_persisted["items"])},
                        "reconciliation": {**projection["reconciliation"], "persisted_count_differences": count_differences},
                        "exceptions": projection["exceptions"], **persisted}
        except GristError as exc:
            if "404" not in str(exc):
                raise
    observations = {item["key"]: item for item in projection["source_observations"]}
    masters = {item["key"]: item for item in projection["line_masters"]}
    revisions = {item["master_key"]: item for item in projection["line_revisions"]}
    details = {item["revision_key"]: item for item in projection["line_details"]}
    rows = []
    for mapping in projection["source_mappings"]:
        observation = observations[mapping["observation_key"]]
        master = masters.get(mapping["master_key"])
        revision = revisions.get(mapping["master_key"])
        detail = details.get(revision["key"]) if revision else None
        row = {"mapping": mapping, "observation": observation, "master": master, "revision": revision, "detail": detail,
               "audit": [item for item in projection["audit"] if item["master_key"] == mapping["master_key"]]}
        if sheet and observation["sheet"] != sheet: continue
        if process and (master or {}).get("process_type") != process: continue
        if master and (row["master"] or {}).get("key") != master: continue
        if part and part.casefold() not in observation["part_display_name"].casefold(): continue
        if material and material.casefold() not in (detail or {}).get("material_display_name", "").casefold(): continue
        if status and observation["status"] != status: continue
        rows.append(row)
    return {"baseline": {"snapshotKey": snapshot_key, "semanticHash": accepted.semantic_hash if accepted else None,
                          "sourceHash": accepted_hash, "currentSourceHash": current_hash,
                          "sourceMatchesBaseline": current_hash == accepted_hash if accepted else None,
                          "accepted": accepted is not None, "costDriftAssessment": cost_drift_assessment},
            "persistence": "projection_only", "counts": {key: len(value) for key, value in projection.items() if isinstance(value, list)},
            "reconciliation": projection["reconciliation"], "exceptions": projection["exceptions"],
            "total": len(rows), "items": rows[offset:offset + limit]}


@app.post("/api/catalog/costing-review/accept")
def accept_costing_review(
    request: Request,
    payload: dict[str, Any] = Body(...),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    """Re-refresh, re-compare, and explicitly accept a non-ambiguous cost state."""
    relative_path = str(payload.get("path") or "").strip()
    reason = str(payload.get("reason") or "").strip()
    actor = _request_actor(request).strip()
    expected_hash = str(payload.get("semanticHash") or "").strip()
    expected_sources = payload.get("sourceHashes")
    expected_baseline = payload.get("acceptedSnapshotKey")
    decisions = payload.get("ambiguityDecisions") or {}
    if not relative_path:
        raise _error(400, "COSTING_FILE_REQUIRED", "Select a product costing workbook first.")
    if not reason:
        raise _error(422, "REASON_REQUIRED", "Enter the reason for accepting this costing reconciliation.")
    if not idempotency_key:
        raise _error(400, "IDEMPOTENCY_KEY_REQUIRED", "Costing acceptance requires an Idempotency-Key header.")
    if not expected_hash or not isinstance(expected_sources, dict):
        raise _error(400, "REVIEW_FINGERPRINT_REQUIRED", "The acceptance request must include the reviewed semantic hash and source revisions.")
    try:
        current = costing_review({"path": relative_path})
    except HTTPException:
        raise
    comparison = current.get("semantic_comparison", {})
    if current.get("semantic_snapshot", {}).get("semantic_hash") != expected_hash or current.get("semantic_snapshot", {}).get("source_hashes") != expected_sources:
        raise _error(409, "STALE_COSTING_PREVIEW", "The selected workbook or one of its ODS dependencies changed after review. Refresh and compare again.")
    if (current.get("accepted_baseline") or {}).get("snapshot_key") != expected_baseline:
        raise _error(409, "STALE_COSTING_BASELINE", "The accepted Safari baseline changed after review. Refresh and compare again.")
    if current.get("status") != "ready_for_owner_review":
        raise _error(409, "COSTING_RECONCILIATION_BLOCKED", "Blocked rates, unreadable data, or incomplete costing inputs must be resolved before acceptance.")
    comparison = resolve_semantic_ambiguities(comparison, decisions if isinstance(decisions, dict) else {})
    if comparison.get("owner_review_required"):
        raise _error(409, "AMBIGUOUS_COSTING_CHANGES_REMAIN", "Resolve each ambiguous line match in the review panel before accepting the new baseline.", {"ambiguities": comparison.get("ambiguities", [])})
    file_id = current.get("costing_file_id")
    if not file_id:
        raise _error(409, "COSTING_FILE_NOT_REGISTERED", "Associate this selected workbook with its Product Model in Safari before accepting a costing baseline.")
    repository = _get_repository()
    try:
        result = repository.accept_costing_snapshot(
            costing_file_id=file_id,
            semantic_snapshot=current["semantic_snapshot"],
            changes=comparison.get("changes", []),
            actor=actor,
            reason=reason,
            expected_previous_snapshot_key=expected_baseline,
            expected_semantic_hash=expected_hash,
            expected_source_hashes=expected_sources,
            idempotency_key=idempotency_key,
        )
    except GovernanceConflict as exc:
        status_code = 403 if exc.code == "APPROVER_NOT_AUTHORIZED" else 409 if exc.code.startswith(("STALE", "AMBIGUOUS", "IDEMPOTENCY", "SCHEMA", "COSTING_ACCEPTANCE")) else 422
        raise _error(status_code, exc.code, str(exc))
    return {**result, "comparison": comparison, "adapter": repository.adapter_name}


def _get_repository() -> SafariRepository:
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
            for item in result.issues:
                repository.upsert_issue(item)
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
    # An inspect of an already-registered file is evidence for reconciliation,
    # not implicit acceptance of a changed source fingerprint. Preserve the
    # stored baseline until the reviewed source-revision action updates it.
    repository.add_file(DomainCostingFile(id=file_id, relative_path=existing.relative_path if existing else node.relative_path, normalized_path=existing.normalized_path if existing else node.relative_path.casefold(), name=existing.name if existing else node.name, extension=node.extension, size_bytes=existing.size_bytes if existing else (node.size_bytes or 0), modified_at=existing.modified_at if existing else (node.modified_at or ""), file_hash=existing.file_hash if existing else node.content_hash, product_id=existing.product_id if existing else None, candidate_classification=node.candidate_classification, mapping_status=existing.mapping_status if existing else node.mapping_status, readable=existing.readable if existing else node.readable, parse_error=existing.parse_error if existing else node.parse_error, source="filesystem-explorer"))
    observation_id = f"observation:{file_id}:{node.modified_at}:{node.content_hash or 'unhashed'}"
    has_history = any(item.file_id == file_id for item in repository.observations)
    if not has_history and (existing is None or not existing.file_hash or existing.file_hash == node.content_hash):
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


def _explorer_node_payload(node: Any, *, conflicting_file_ids: set[str] | None = None) -> dict[str, Any]:
    projected = _registry_status(node, conflicting_file_ids=conflicting_file_ids).to_dict()
    directory_path = node.relative_path if node.type == "directory" else Path(node.relative_path).parent.as_posix()
    repository = _get_repository()
    projected["directoryProductMapping"] = repository.effective_directory_mapping(directory_path) if hasattr(repository, "effective_directory_mapping") else None
    projected["directoryMappingProposals"] = [item.to_dict() for item in repository.directory_mappings.values() if item.normalized_path == directory_path.casefold() and item.status == "proposed"] if hasattr(repository, "directory_mappings") else []
    return projected


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


def _request_actor(request: Request) -> str:
    user = _request_user(request)
    return user["username"] if user["username"] != "Local user" else user["id"]


def _error(status: int, code: str, message: str, extra: dict[str, Any] | None = None) -> HTTPException:
    detail = {"code": code, "message": message}
    if extra:
        detail.update(extra)
    return HTTPException(status_code=status, detail=detail)

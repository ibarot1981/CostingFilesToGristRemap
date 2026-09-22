"""Read the canonical Product-ProductModelNo-ModelCode ODS catalog."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
import re
from typing import Any, Iterable
from uuid import uuid4

from app.domain import IdentityAlias, ImportBatch, Product, ProductModel, ProductModelCode, ReconciliationIssue, utc_now
from app.exceptions import CatalogImportError
from app.repository import sha256_file
from app.utils import normalize_header, normalize_text
from app.workbook import OdsWorkbook


APPROVED_GC_CODES = frozenset({"GCMC-7.5", "GCMC-10", "GCMC18-7.5", "GCMC18-10"})


@dataclass(frozen=True)
class CatalogImportResult:
    source_file: str
    products: tuple[Product, ...]
    models: tuple[ProductModel, ...]
    codes: tuple[ProductModelCode, ...]
    aliases: tuple[IdentityAlias, ...]
    issues: tuple[ReconciliationIssue, ...]
    row_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "sourceFile": self.source_file,
            "rowCount": self.row_count,
            "products": [item.to_dict() for item in self.products],
            "models": [item.to_dict() for item in self.models],
            "codes": [item.to_dict() for item in self.codes],
            "aliases": [item.to_dict() for item in self.aliases],
            "issues": [item.to_dict() for item in self.issues],
        }


@dataclass(frozen=True)
class CatalogImportPlan:
    source_file: str
    source_hash: str | None
    parser_version: str
    idempotent: bool
    products_to_upsert: int
    models_to_upsert: int
    codes_to_upsert: int
    aliases_to_upsert: int
    issues_to_record: int
    import_batch: ImportBatch

    def to_dict(self) -> dict[str, Any]:
        return {"sourceFile": self.source_file, "sourceHash": self.source_hash, "parserVersion": self.parser_version, "idempotent": self.idempotent, "productsToUpsert": self.products_to_upsert, "modelsToUpsert": self.models_to_upsert, "codesToUpsert": self.codes_to_upsert, "aliasesToUpsert": self.aliases_to_upsert, "issuesToRecord": self.issues_to_record, "importBatch": self.import_batch.to_dict()}


def import_catalog(path: Path, *, sheet: str | None = None) -> CatalogImportResult:
    """Import exact display values while never saving or recalculating *path*."""

    workbook = OdsWorkbook.load(path)
    sheet_name = sheet or next(iter(workbook.sheets), None)
    if not sheet_name:
        raise CatalogImportError("Catalog workbook has no sheets")
    rows = workbook.sheets[sheet_name]
    return import_catalog_rows(rows, source_file=str(path))


def plan_catalog_import(repository: Any, result: CatalogImportResult, *, apply: bool = False, parser_version: str = "catalog-0.2") -> CatalogImportPlan:
    """Create a deterministic import plan; mutate the repository only if asked."""
    source_path = Path(result.source_file)
    source_hash = sha256_file(source_path) if source_path.exists() and source_path.is_file() else None
    prior = next((batch for batch in repository.import_batches.values() if batch.source_file == result.source_file and batch.source_hash == source_hash and batch.parser_version == parser_version and batch.status in {"planned", "applied"}), None)
    now = utc_now()
    if prior is not None:
        return CatalogImportPlan(result.source_file, source_hash, parser_version, True, 0, 0, 0, 0, 0, prior)
    batch = ImportBatch(id=f"catalog-batch-{uuid4().hex}", source_file=result.source_file, source_hash=source_hash, parser_version=parser_version, started_at=now, completed_at=now if apply else None, status="applied" if apply else "planned", outcome="catalog identities applied" if apply else "catalog identities planned")
    if apply:
        for item in result.products:
            repository.add_product(item)
        for item in result.models:
            repository.add_model(item)
        for item in result.codes:
            repository.add_code(item)
        repository.aliases.update({item.id: item for item in result.aliases})
        repository.issues.update({item.id: item for item in result.issues})
        repository.import_batches[batch.id] = batch
    return CatalogImportPlan(result.source_file, source_hash, parser_version, False, len(result.products), len(result.models), len(result.codes), len(result.aliases), len(result.issues), batch)


import_catalog_into_repository = plan_catalog_import


def import_catalog_rows(rows: Iterable[list[Any]], *, source_file: str = "catalog.ods") -> CatalogImportResult:
    materialized = [list(row) for row in rows]
    if not materialized:
        return CatalogImportResult(source_file, (), (), (), (), (), 0)
    header_index, indexes = _find_columns(materialized)
    products: dict[str, Product] = {}
    models: dict[str, ProductModel] = {}
    codes: dict[str, ProductModelCode] = {}
    aliases: dict[str, IdentityAlias] = {}
    issues: list[ReconciliationIssue] = []
    current_product = ""
    current_model = ""
    for row_number, row in enumerate(materialized[header_index + 1 :], start=header_index + 2):
        product_name = _cell(row, indexes["product"]) or current_product
        model_number = _cell(row, indexes["model"]) or current_model
        code_value = _cell(row, indexes["code"])
        description = _cell(row, indexes["description"])
        if product_name:
            current_product = product_name
        if model_number:
            current_model = model_number
        if not product_name and not model_number and not code_value:
            continue
        if not product_name or not model_number or not code_value:
            issues.append(ReconciliationIssue(id=f"issue-row-{row_number}", issue_type="incomplete_catalog_row", severity="error", message="Product, Model Number, and Model Code are required.", source_file=source_file, source_row=row_number))
            continue
        product_key = normalize_text(product_name)
        product_id = f"product:{product_key}"
        products.setdefault(product_id, Product(id=product_id, name=product_name, source_row=row_number, source_file=source_file))
        model_key = f"{product_key}|{normalize_text(model_number)}"
        model_id = f"model:{model_key}"
        model_legacy = _is_legacy_spares_only(model_number, description, product_name)
        models.setdefault(model_id, ProductModel(id=model_id, product_id=product_id, model_number=model_number, name="", legacy_spares_only=model_legacy, source_row=row_number, source_file=source_file))
        if not description:
            issues.append(ReconciliationIssue(id=f"issue-blank-description-{row_number}", issue_type="blank_description", severity="warning", message=f"Catalog row {row_number} has a blank Model Code description.", source_file=source_file, source_row=row_number, entity_id=model_id))
        persisted_code = approved_gc_code(code_value, model_number, description)
        code_key = f"{model_id}|{normalize_text(persisted_code)}"
        existing = codes.get(code_key)
        if existing is not None:
            known_source_values = {normalize_text(value) for value in existing.source_values}
            if persisted_code in APPROVED_GC_CODES and normalize_text(code_value) not in known_source_values:
                codes[code_key] = replace(existing, source_values=(*existing.source_values, code_value))
                alias_id = f"alias:{existing.id}:{row_number}"
                aliases[alias_id] = IdentityAlias(id=alias_id, entity_type="ProductModelCode", entity_id=existing.id, value=code_value, normalized_value=normalize_text(code_value), source=source_file, source_row=row_number)
            else:
                # Retain the canonical row for review, but fail closed until
                # duplicate identities have been explicitly disambiguated.
                codes[code_key] = replace(existing, active=False)
                issues.append(ReconciliationIssue(id=f"issue-duplicate-code-{row_number}", issue_type="duplicate_catalog_code", severity="error", message=f"Duplicate Model Code {code_value!r} for {model_number}; review before activation.", source_file=source_file, source_row=row_number, entity_id=existing.id))
            continue
        code_id = f"code:{code_key}"
        codes[code_key] = ProductModelCode(id=code_id, model_id=model_id, code=persisted_code, description=description, legacy_spares_only=_is_legacy_spares_only(code_value, description, model_number), source_values=(code_value,), source_row=row_number, source_file=source_file)
        if persisted_code != code_value:
            alias_id = f"alias:{code_id}:{row_number}"
            aliases[alias_id] = IdentityAlias(id=alias_id, entity_type="ProductModelCode", entity_id=code_id, value=code_value, normalized_value=normalize_text(code_value), source=source_file, source_row=row_number)
    return CatalogImportResult(source_file, tuple(products.values()), tuple(models.values()), tuple(codes.values()), tuple(aliases.values()), tuple(issues), len(materialized) - header_index - 1)


def approved_gc_code(value: str, model_number: str = "", description: str = "") -> str:
    """Map duplicate GC source spellings to the approved persisted values."""

    combined = " ".join((value, model_number, description)).casefold().replace(" ", "")
    if "gcm" not in combined:
        return value
    prefix = "GCMC18" if "gcmc18" in combined or "18" in combined else "GCMC"
    if "7.5" in combined or "7,5" in combined:
        return f"{prefix}-7.5"
    rating_text = " ".join((value, description)).casefold().replace(" ", "")
    if re.search(r"(?<![0-9])10(?:[^0-9]|$)", rating_text) or "10kg" in rating_text:
        return f"{prefix}-10"
    return value


def _find_columns(rows: list[list[Any]]) -> tuple[int, dict[str, int]]:
    aliases = {
        "product": ("product", "product name", "productfamily"),
        "model": ("productmodelno", "product model no", "product model number", "product model", "model number", "model"),
        "code": ("modelcode", "product model code", "productmodelcode", "code"),
        "description": ("description", "model description", "product model desc", "productmodeldesc", "commercial description"),
    }
    for index, row in enumerate(rows[:12]):
        normalized = {normalize_header(value): column for column, value in enumerate(row) if normalize_header(value)}
        found = {}
        for name, candidates in aliases.items():
            for candidate in candidates:
                if normalize_header(candidate) in normalized:
                    found[name] = normalized[normalize_header(candidate)]
                    break
        if len(found) >= 3:
            found.setdefault("description", max(found.values()) + 1)
            return index, found
    raise CatalogImportError("Catalog headers must include Product, Product Model, and Product Model Code columns.")


def _cell(row: list[Any], index: int | None) -> str:
    if index is None or index >= len(row) or row[index] is None:
        return ""
    return str(row[index]).strip()


def _is_legacy_spares_only(*values: str) -> bool:
    return "bush" in " ".join(values).casefold()

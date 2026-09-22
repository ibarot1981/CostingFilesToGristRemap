"""Read models and costing-file associations from the Grist backend."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.grist import GristClient


ASSOCIATION_TABLES = ("CostingFileRegistry", "CostingFileModelCode")


@dataclass(frozen=True)
class ProductModelCode:
    id: int
    code: str
    description: str


@dataclass(frozen=True)
class ProductModel:
    id: int
    model_number: str
    model_name: str
    codes: tuple[ProductModelCode, ...]


def fetch_product_models(client: GristClient) -> list[ProductModel]:
    """Return ProductMaster rows with their ProductModelMaster2 children."""

    masters = client.fetch_table_records_with_ids("ProductMaster")
    codes = client.fetch_table_records_with_ids("ProductModelMaster2")
    codes_by_master: dict[int, list[ProductModelCode]] = {}
    for record in codes:
        fields = record.get("fields", {})
        master_id = _reference_id(fields.get("ProductMaster"))
        if master_id is None:
            continue
        codes_by_master.setdefault(master_id, []).append(
            ProductModelCode(
                id=int(record["id"]),
                code=_text(fields.get("ProductModelCode")),
                description=_text(fields.get("ProductModelDesc")),
            )
        )

    output = []
    for record in masters:
        fields = record.get("fields", {})
        row_id = int(record["id"])
        output.append(
            ProductModel(
                id=row_id,
                model_number=_text(fields.get("ProductModelNo")),
                model_name=_text(fields.get("ProductModelName")),
                codes=tuple(sorted(codes_by_master.get(row_id, []), key=lambda item: item.code.casefold())),
            )
        )
    return sorted(output, key=lambda item: (item.model_number.casefold(), item.id))


def fetch_associations(client: GristClient) -> tuple[bool, list[dict[str, Any]]]:
    """Read association rows when both proposed Grist tables are installed."""

    try:
        registry = client.fetch_table_records_with_ids(ASSOCIATION_TABLES[0])
        model_codes = client.fetch_table_records_with_ids(ASSOCIATION_TABLES[1])
    except Exception as exc:
        message = str(exc)
        if "404" in message or "Table not found" in message:
            return False, []
        raise
    return True, [{"registry": registry, "modelCodes": model_codes}]


def _reference_id(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, list) and len(value) > 1 and value[0] == "L":
        for item in value[1:]:
            if isinstance(item, int):
                return item
    return None


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()

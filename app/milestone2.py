"""Read-only ODS semantic extraction and parity checks for Milestone 2.

This module reads formula text and cached cell values from ODS packages without
opening LibreOffice, recalculating external links, or writing to source files.
It deliberately keeps source display values intact and does not consult Grist
or Google Sheets.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import zipfile
from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_UP, localcontext
from pathlib import Path
from typing import Any, Iterable
from xml.etree import ElementTree as ET

from app.config import load_app_config


PRODUCTS_ROOT = Path(r"C:\Irshad\Safari\DRWGD\Products Costing")
DEFAULT_RAW_STEEL = PRODUCTS_ROOT / "Template DB" / "MaterialCostDB.ods"
DEFAULT_RATE_DUMP = PRODUCTS_ROOT / "Template DB" / "Spares List - Master.ods"
DEFAULT_PILOT = PRODUCTS_ROOT / "S1KHF" / "Local" / "Safari 1000 HF Local V 4.2.ods"
DEFAULT_OUTPUT = Path("reports/milestone2_s1khf")

_NS = {
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
}
_TAG = {
    name: f"{{{uri}}}"
    for prefix, uri in _NS.items()
    for name in (prefix,)
}
_CELL_TAGS = {
    f"{{{_NS['table']}}}table-cell",
    f"{{{_NS['table']}}}covered-table-cell",
}
_READING_CONTEXT = Decimal("0.000000001")
BUSINESS_TOTAL_TOLERANCE = Decimal("100")
OWNER_REPORTED_TOTAL = Decimal("11131.84")
OWNER_REPORTED_ON = date(2026, 9, 28)
OWNER_REPORTED_RATE_DUMP_SHA256 = "7d31b79efcb45298d88aed6eac73f2cc7412a171a6976bab1773ce63faa87d4b"
OWNER_REPORTED_PILOT_SHA256 = "6143676d05e93b32080cf220dce71ad9b599f763e5921747ff645dfbf65bb695"


@dataclass(frozen=True)
class OdsCell:
    """A single ODS cell, retaining formula, typed cache, and displayed text."""

    row: int
    column: int
    formula: str | None
    value_type: str | None
    value: Any
    display_text: str
    attributes: dict[str, str] = field(default_factory=dict)

    @property
    def coordinate(self) -> str:
        return f"{column_letters(self.column)}{self.row}"


@dataclass
class OdsSheet:
    name: str
    rows: dict[int, dict[int, OdsCell]]

    def cell(self, row: int, column: int) -> OdsCell:
        return self.rows.get(row, {}).get(
            column,
            OdsCell(row, column, None, None, None, "", {}),
        )

    def nonempty_rows(self) -> Iterable[tuple[int, dict[int, OdsCell]]]:
        for row_number in sorted(self.rows):
            yield row_number, self.rows[row_number]


@dataclass
class OdsDocument:
    path: Path
    sheets: dict[str, OdsSheet]


def column_letters(column: int) -> str:
    """Return a one-based spreadsheet column number as letters."""
    result = ""
    while column:
        column, remainder = divmod(column - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _local_name(name: str) -> str:
    return name.rsplit("}", 1)[-1]


def _typed_value(attrs: dict[str, str], displayed: str) -> Any:
    value_type = attrs.get("value-type")
    if value_type in {"float", "currency", "percentage"}:
        raw = attrs.get("value")
        if raw is None:
            return None
        try:
            return Decimal(raw)
        except InvalidOperation:
            return raw
    if value_type == "date":
        raw = attrs.get("date-value")
        if raw:
            try:
                return date.fromisoformat(raw[:10])
            except ValueError:
                return raw
    if value_type == "time":
        return attrs.get("time-value", displayed or None)
    if value_type == "boolean":
        raw = attrs.get("boolean-value", "").casefold()
        return raw == "true" if raw in {"true", "false"} else None
    if value_type in {"string", "error"}:
        return attrs.get("string-value", displayed)
    if "value" in attrs:
        try:
            return Decimal(attrs["value"])
        except InvalidOperation:
            return attrs["value"]
    return displayed if displayed else None


def _cell_from_xml(cell: ET.Element, row: int, column: int) -> OdsCell:
    attrs = {_local_name(key): value for key, value in cell.attrib.items()}
    paragraphs = cell.findall(".//text:p", _NS)
    displayed = "\n".join("".join(p.itertext()) for p in paragraphs)
    return OdsCell(
        row=row,
        column=column,
        formula=attrs.get("formula"),
        value_type=attrs.get("value-type"),
        value=_typed_value(attrs, displayed),
        display_text=displayed,
        attributes=attrs,
    )


def read_ods(path: Path, sheet_names: set[str] | None = None) -> OdsDocument:
    """Read selected sheets from ODS XML; never recalculate or save the file."""
    if path.suffix.casefold() != ".ods":
        raise ValueError(f"Expected an .ods file: {path}")
    if not path.is_file():
        raise FileNotFoundError(path)
    try:
        with zipfile.ZipFile(path) as package:
            content = ET.fromstring(package.read("content.xml"))
    except (OSError, zipfile.BadZipFile, ET.ParseError) as exc:
        raise ValueError(f"Could not read ODS package {path}: {exc}") from exc

    tables: dict[str, OdsSheet] = {}
    for table in content.findall(".//table:table", _NS):
        name = table.get(f"{{{_NS['table']}}}name", "")
        if not name or (sheet_names is not None and name not in sheet_names):
            continue
        rows: dict[int, dict[int, OdsCell]] = {}
        row_number = 1
        # Headers and grouped sections (notably repeated Calc header rows) are
        # nested in table:table-header-rows/table:table-row-group elements.
        for row_xml in table.findall(".//table:table-row", _NS):
            repeated_rows = int(
                row_xml.get(f"{{{_NS['table']}}}number-rows-repeated", "1")
            )
            row_cells: dict[int, OdsCell] = {}
            column_number = 1
            for xml_cell in list(row_xml):
                if xml_cell.tag not in _CELL_TAGS:
                    continue
                repeated_columns = int(
                    xml_cell.get(f"{{{_NS['table']}}}number-columns-repeated", "1")
                )
                cell = _cell_from_xml(xml_cell, row_number, column_number)
                if cell.formula is not None or cell.value is not None or cell.display_text:
                    for offset in range(min(repeated_columns, 4096)):
                        col = column_number + offset
                        row_cells[col] = OdsCell(
                            row_number, col, cell.formula, cell.value_type,
                            cell.value, cell.display_text, cell.attributes,
                        )
                column_number += repeated_columns
            if row_cells:
                for offset in range(min(repeated_rows, 10000)):
                    expanded_row = row_number + offset
                    rows[expanded_row] = {
                        col: OdsCell(
                            expanded_row, col, cell.formula, cell.value_type,
                            cell.value, cell.display_text, cell.attributes,
                        )
                        for col, cell in row_cells.items()
                    }
            row_number += repeated_rows
        tables[name] = OdsSheet(name, rows)
    return OdsDocument(path, tables)


def _cell_value(sheet: OdsSheet, row: int, column: int) -> Any:
    return sheet.cell(row, column).value


def _string(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


def _decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        return None
    try:
        return Decimal(str(value).strip().replace(",", ""))
    except InvalidOperation:
        return None


def _round_up_whole_rupee(value: Decimal) -> Decimal:
    """Round a positive costing line total up to the next whole rupee."""
    return value.quantize(Decimal("1"), rounding=ROUND_UP)


def _blank(value: Any) -> bool:
    return value is None or value == ""


def _header_row(sheet: OdsSheet, labels: set[str], search_rows: int = 20) -> int:
    wanted = {" ".join(label.casefold().split()) for label in labels}
    for row_number, cells in sheet.nonempty_rows():
        if row_number > search_rows:
            break
        values = {" ".join(_string(c.value).casefold().split()) for c in cells.values()}
        if wanted.issubset(values):
            return row_number
    raise ValueError(f"Expected headers {sorted(labels)!r} were not found on {sheet.name}.")


def _column_map(sheet: OdsSheet, header_row: int) -> dict[str, int]:
    return {
        " ".join(_string(cell.value).casefold().split()): column
        for column, cell in sheet.rows.get(header_row, {}).items()
        if not _blank(cell.value)
    }


def _pick_column(columns: dict[str, int], *names: str) -> int:
    for name in names:
        key = " ".join(name.casefold().split())
        if key in columns:
            return columns[key]
    raise ValueError(f"Could not locate any expected column from {names!r}.")


def _cell_json(cell: OdsCell) -> dict[str, Any]:
    return {
        "cell": cell.coordinate,
        "cached_value": _json_value(cell.value),
        "cached_display": cell.display_text,
        "value_type": cell.value_type,
        "formula": cell.formula,
    }


def _semantic_cell_evidence(cell: OdsCell) -> dict[str, Any]:
    """Keep compact provenance for a semantic snapshot; field values live beside it."""
    evidence: dict[str, Any] = {"cell": cell.coordinate}
    if cell.formula:
        evidence["formula"] = cell.formula
        evidence["cached_value"] = _json_value(cell.value)
    return evidence


def _compact_cell_evidence(cells: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    compact: dict[str, dict[str, Any]] = {}
    for name, cell in cells.items():
        evidence: dict[str, Any] = {"cell": cell.get("cell")}
        if cell.get("formula"):
            evidence["formula"] = cell["formula"]
            evidence["cached_value"] = cell.get("cached_value")
        compact[name] = evidence
    return compact


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _file_metadata(path: Path, observed_at: datetime) -> dict[str, Any]:
    stat = path.stat()
    return {
        "path": str(path.resolve()),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "size_bytes": stat.st_size,
        "modified_at": datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(),
        "observed_at": observed_at.astimezone().isoformat(),
    }


def _parse_log_date(cell: OdsCell) -> tuple[date | None, str]:
    """Parse typed dates or the dump's confirmed D-M-Y text without locale guessing."""
    if isinstance(cell.value, datetime):
        return cell.value.date(), "typed_date"
    if isinstance(cell.value, date):
        return cell.value, "typed_date"
    raw = _string(cell.value).strip()
    for pattern in ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, pattern).date(), "parsed_text_date"
        except ValueError:
            continue
    return None, "blank_or_invalid"


def _record_cells(
    sheet: OdsSheet,
    row_number: int,
    mapping: dict[str, int],
) -> dict[str, OdsCell]:
    return {field_name: sheet.cell(row_number, column) for field_name, column in mapping.items()}


def _active_flag(value: Any) -> str:
    if _blank(value):
        return "active"
    if _string(value).strip().casefold() == "no":
        return "historical"
    return "unexpected"


def _key_value(value: Any) -> str:
    number = _decimal(value)
    if number is not None:
        return format(number.normalize(), "f")
    return _string(value)


def _line_key(record: dict[str, Any]) -> tuple[str, ...]:
    return (
        _string(record.get("machine_piece_description")),
        _string(record.get("material_to_cut")),
        _key_value(record.get("dimension_mm")),
        _key_value(record.get("quantity")),
        _string(record.get("optional_item_group_1")),
    )


_PROCESS_IDENTITY_FIELDS: dict[str, tuple[str, ...]] = {
    "5. Material Cut List Price": (
        "product_part_name", "material_to_cut", "dimension_to_cut_mm", "qty",
        "optional_item_group_1",
    ),
    "Tool Shop Items": (
        "product_part_name", "item_name", "material_to_cut",
        "dimension_to_cut_mm", "qty", "optional_item_group_1",
    ),
    "Stores and Consumables List": (
        "item_code", "item_name", "product_part_name", "qty",
        "optional_item_group_1",
    ),
    "CNC Cut List": (
        "product_part_name", "material_to_cut", "length", "width", "thickness",
        "qty", "optional_item_group_1",
    ),
    "Labour - Paint - Packing": (
        "item_code", "product_part_name", "activity", "qty",
        "optional_item_group_1",
    ),
}

_PROCESS_COST_FIELDS = frozenset({
    "rate", "amount", "cut_piece_cost", "transport_rate", "unloading_rate",
    "fabrication_rate", "total_material_used_inches", "kg_per_mtr",
    "grams_per_inch", "total_grams", "weight_kg", "total_weight",
})
_PROCESS_CONTEXT_FIELDS = frozenset({"date", "remarks", "cr_log", "item_line_no"})
_SEMANTIC_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "sheet_config.yaml"


def _configured_active_status(value: Any, inactive_values: Iterable[str]) -> str:
    if _blank(value):
        return "active"
    folded = _string(value).strip().casefold()
    if folded in {item.strip().casefold() for item in inactive_values}:
        return "historical"
    if folded in {"yes", "y", "true", "1", "active", "in use", "used"}:
        return "active"
    return "unexpected"


def _find_configured_header(sheet: OdsSheet, aliases: dict[str, list[str]], preferred: int) -> int:
    """Find a configured header without treating source row position as identity."""
    alias_values = [
        " ".join(value.casefold().split())
        for values in aliases.values() for value in values
    ]
    for row_number in (preferred, *range(1, 21)):
        if row_number not in sheet.rows:
            continue
        present = {
            " ".join(_string(cell.value).casefold().split())
            for cell in sheet.rows[row_number].values()
        }
        matched = sum(1 for alias in set(alias_values) if alias in present)
        if matched >= 2:
            return row_number
    raise ValueError(f"Configured headers were not found on {sheet.name}.")


def _configured_process_lines(document: OdsDocument) -> dict[str, list[dict[str, Any]]]:
    """Extract source-owned line semantics from the configured costing lists."""
    config = load_app_config(_SEMANTIC_CONFIG_PATH)
    result: dict[str, list[dict[str, Any]]] = {}
    for sheet_name, identity_fields in _PROCESS_IDENTITY_FIELDS.items():
        sheet = document.sheets.get(sheet_name)
        sheet_config = config.supported_sheets.get(sheet_name)
        if sheet is None or sheet_config is None:
            continue
        aliases = sheet_config.aliases
        header = _find_configured_header(sheet, aliases, sheet_config.header_row)
        columns = _column_map(sheet, header)
        field_columns: dict[str, int] = {}
        for field_name, names in aliases.items():
            for name in names:
                column = columns.get(" ".join(name.casefold().split()))
                if column is not None:
                    field_columns[field_name] = column
                    break
        if not field_columns:
            continue
        effective_identity = tuple(name for name in identity_fields if name in field_columns)
        if not effective_identity:
            continue
        source_headers = {name: _string(sheet.cell(header, column).value).strip()
            for name, column in field_columns.items()}
        source_header_cells = {name: sheet.cell(header, column).coordinate
            for name, column in field_columns.items()}
        rows: list[dict[str, Any]] = []
        for row_number, _ in sheet.nonempty_rows():
            if row_number <= header:
                continue
            cells = _record_cells(sheet, row_number, field_columns)
            values = {name: cell.value for name, cell in cells.items()}
            required_any = sheet_config.inactive_rules.required_any_fields or list(sheet_config.part_name_fields)
            if not any(not _blank(values.get(name)) for name in required_any):
                continue
            identity = {
                name: _key_value(values.get(name))
                for name in effective_identity
            }
            if not any(identity.values()):
                continue
            rows.append({
                "process_list": sheet_name,
                "source_row": row_number,
                "status": _configured_active_status(
                    values.get("in_use"), sheet_config.inactive_rules.inactive_values
                ) if "in_use" in values else "active",
                "identity_fields": list(effective_identity),
                "identity_values": identity,
                "fields": {name: _json_value(value) for name, value in values.items()},
                "source_headers": source_headers,
                "source_header_cells": source_header_cells,
                "header_row": header,
                "available_fields": sorted(field_columns),
                "source_cells": {name: _semantic_cell_evidence(cell) for name, cell in cells.items()},
                "cost_value": _json_value(values.get("amount")),
                "cr_reference": _string(values.get("cr_log")).strip() or None,
            })
        result[sheet_name] = rows
    return result


def part_mapping_source_diagnostics(document: OdsDocument) -> dict[str, dict[str, Any]]:
    """Report the source label header selected for each Part Mapping sheet."""
    config = load_app_config(_SEMANTIC_CONFIG_PATH)
    label_fields = {
        "5. Material Cut List Price": "product_part_name",
        "Tool Shop Items": "product_part_name",
        "CNC Cut List": "part_category",
    }
    result: dict[str, dict[str, Any]] = {}
    for sheet_name, label_field in label_fields.items():
        sheet = document.sheets.get(sheet_name)
        if sheet is None:
            result[sheet_name] = {"present": False, "status": "sheet_missing", "labelField": label_field,
                "labelHeader": None, "labelHeaderCell": None, "ambiguousLabelHeaders": [], "headerRow": None}
            continue
        aliases = config.supported_sheets[sheet_name].aliases
        try:
            header = _find_configured_header(sheet, aliases, config.supported_sheets[sheet_name].header_row)
        except ValueError:
            result[sheet_name] = {"present": True, "status": "header_missing", "labelField": label_field,
                "labelHeader": None, "labelHeaderCell": None, "ambiguousLabelHeaders": [], "headerRow": None}
            continue
        label_aliases = {" ".join(alias.casefold().split()) for alias in aliases.get(label_field, [])}
        # Scan header cells directly so duplicate columns with identical text are
        # still reported as ambiguous (the general alias map is intentionally lossy).
        distinct = [(_string(cell.value).strip(), column) for column, cell in sheet.rows.get(header, {}).items()
            if " ".join(_string(cell.value).casefold().split()) in label_aliases]
        selected = distinct[0] if distinct else None
        result[sheet_name] = {"present": True, "status": "ok" if selected else "label_column_missing",
            "labelField": label_field, "labelHeader": selected[0] if selected else None,
            "labelHeaderCell": sheet.cell(header, selected[1]).coordinate if selected else None,
            "ambiguousLabelHeaders": [label for label, _ in distinct] if len(distinct) > 1 else [],
            "headerRow": header}
        if len(distinct) > 1:
            result[sheet_name]["status"] = "label_column_ambiguous"
    return result


def _extract_total_summary_semantics(document: OdsDocument) -> dict[str, Any]:
    sheet = document.sheets.get("Total Summary")
    if sheet is None:
        return {"option_groups": [], "items": []}
    header = None
    for row_number, cells in sheet.nonempty_rows():
        values = {" ".join(_string(cell.value).casefold().split()) for cell in cells.values()}
        if "summary" in values and "amount rs" in values:
            header = row_number
            break
    if header is None:
        return {"option_groups": [], "items": []}
    headings = {
        column: _string(cell.value).strip()
        for column, cell in sheet.rows.get(header, {}).items()
        if not _blank(cell.value)
    }
    label_column = next((column for column, label in headings.items() if label.casefold() == "summary"), None)
    amount_column = next((column for column, label in headings.items() if label.casefold() == "amount rs"), None)
    option_groups = [
        label for column, label in headings.items()
        if column not in {label_column, amount_column} and label.strip()
    ]
    items: list[dict[str, Any]] = []
    for row_number, _ in sheet.nonempty_rows():
        if row_number <= header:
            continue
        label_cell = sheet.cell(row_number, label_column or 1)
        label = _string(label_cell.value).strip()
        if not label:
            continue
        for column, group in headings.items():
            if column == label_column:
                continue
            cell = sheet.cell(row_number, column)
            if _blank(cell.value) and not cell.formula:
                continue
            items.append({
                "line_label": label,
                "cost_or_option_group": group,
                "value": _json_value(cell.value),
                "formula": cell.formula,
                "source_cell": cell.coordinate,
            })
    return {"option_groups": option_groups, "items": items}


def _same_number(left: Any, right: Decimal | None) -> tuple[bool, Decimal | None]:
    actual = _decimal(left)
    if actual is None or right is None:
        return actual == right, None
    delta = actual - right
    return abs(delta) <= _READING_CONTEXT, delta


def _decimal_sum(values: Iterable[Decimal]) -> Decimal:
    with localcontext() as context:
        context.prec = 34
        return sum(values, Decimal(0))


def _parse_range_end(formula: str | None) -> int | None:
    if not formula:
        return None
    match = re.search(r"\$D\$2:\.?\$H\$?(\d+)", formula, re.IGNORECASE)
    return int(match.group(1)) if match else None


def _parse_formula_limit(formula: str | None, column_letter: str) -> int | None:
    if not formula:
        return None
    matches = re.findall(
        rf"\$SteelLog\.\${re.escape(column_letter)}\$?\d+:\$SteelLog\.\${re.escape(column_letter)}\$?(\d+)",
        formula,
        re.IGNORECASE,
    )
    return max((int(value) for value in matches), default=None)


def extract_raw_steel(document: OdsDocument) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    sheet = document.sheets["RawSteel"]
    header = _header_row(sheet, {"Sr. No.", "Material Type", "Size", "Unique Item List"})
    columns = _column_map(sheet, header)
    field_columns = {
        "serial": _pick_column(columns, "Sr. No."),
        "material_type": _pick_column(columns, "Material Type"),
        "size": _pick_column(columns, "Size"),
        "unique_item_list": _pick_column(columns, "Unique Item List"),
        "kg_factor": _pick_column(columns, "Kgs per Mtr/SqMtr"),
        "grams_per_inch": _pick_column(columns, "gms Per Inch"),
        "default_rate": _pick_column(columns, "Current Rate per KG"),
        "unused_rate_range": _pick_column(columns, "Rate Range Per KG"),
    }
    records: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    for row_number, row in sheet.nonempty_rows():
        if row_number <= header:
            continue
        cells = _record_cells(sheet, row_number, field_columns)
        values = {key: cell.value for key, cell in cells.items()}
        if all(_blank(value) for value in values.values()):
            continue
        name = _string(values["unique_item_list"])
        if not name:
            issues.append({"code": "RAW_STEEL_BLANK_IDENTITY", "source_cell": cells["unique_item_list"].coordinate})
        if _blank(values["material_type"]):
            issues.append({"code": "RAW_STEEL_BLANK_MATERIAL_TYPE", "source_cell": cells["material_type"].coordinate, "material": name})
        for key in ("kg_factor", "grams_per_inch"):
            number = _decimal(values[key])
            if number is None or number <= 0:
                issues.append({"code": "RAW_STEEL_INVALID_WEIGHT_FACTOR", "field": key, "source_cell": cells[key].coordinate, "material": name, "value": _json_value(values[key])})
        default_rate = _decimal(values["default_rate"])
        if default_rate is None or default_rate < 0:
            issues.append({"code": "RAW_STEEL_INVALID_DEFAULT_RATE", "source_cell": cells["default_rate"].coordinate, "material": name, "value": _json_value(values["default_rate"])})
        records.append({
            "source_row": row_number,
            "material_type": values["material_type"],
            "size": values["size"],
            "unique_item_list": name,
            "kg_factor": values["kg_factor"],
            "grams_per_inch": values["grams_per_inch"],
            "default_rate_per_kg": values["default_rate"],
            "unused_rate_range_per_kg": values["unused_rate_range"],
            "cells": {key: _cell_json(cell) for key, cell in cells.items()},
        })
    counts = Counter(record["unique_item_list"] for record in records if record["unique_item_list"])
    for name, count in sorted(counts.items()):
        if count > 1:
            rows = [record["source_row"] for record in records if record["unique_item_list"] == name]
            issues.append({"code": "RAW_STEEL_DUPLICATE_UNIQUE_ITEM_LIST", "material": name, "rows": rows})
    return records, issues


def extract_rate_log(document: OdsDocument, sheet_name: str = "SteelRateLog") -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    sheet = document.sheets[sheet_name]
    header = _header_row(sheet, {"Unique Item List", "Rate per KG. Basic Rate Only", "Date of Entry"})
    columns = _column_map(sheet, header)
    field_columns = {
        "material": _pick_column(columns, "Unique Item List"),
        "rate": _pick_column(columns, "Rate per KG. Basic Rate Only"),
        "quantity": _pick_column(columns, "Qty Ordered (Kgs)"),
        "date": _pick_column(columns, "Date of Entry"),
        "description": _pick_column(columns, "Description or specifications of Item if Any"),
        "remarks": _pick_column(columns, "Remarks"),
    }
    records: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    for row_number, _row in sheet.nonempty_rows():
        if row_number <= header:
            continue
        cells = _record_cells(sheet, row_number, field_columns)
        values = {key: cell.value for key, cell in cells.items()}
        if all(_blank(value) for value in values.values()):
            continue
        parsed_date, date_status = _parse_log_date(cells["date"])
        rate = _decimal(values["rate"])
        material = _string(values["material"])
        item = {
            "source_row": row_number,
            "material": material,
            "rate_per_kg": rate,
            "quantity_ordered": values["quantity"],
            "date_raw": _string(values["date"]),
            "date_iso": parsed_date.isoformat() if parsed_date else None,
            "date_parse_status": date_status,
            "description": values["description"],
            "remarks": values["remarks"],
            "has_formula": any(cell.formula for cell in cells.values()),
            "cached_values_readable": all(
                cell.formula is None or cell.value is not None for cell in cells.values()
            ),
            "cells": {key: _cell_json(cell) for key, cell in cells.items()},
        }
        if not material:
            issues.append({"code": "RATE_LOG_BLANK_MATERIAL", "source_cell": cells["material"].coordinate})
        if rate is None or rate <= 0:
            issues.append({"code": "RATE_LOG_INVALID_RATE", "source_row": row_number, "source_cell": cells["rate"].coordinate, "material": material, "value": _json_value(values["rate"])})
        if not parsed_date:
            issues.append({"code": "RATE_LOG_INVALID_DATE", "source_row": row_number, "source_cell": cells["date"].coordinate, "material": material, "value": item["date_raw"]})
        if not item["cached_values_readable"]:
            issues.append({"code": "RATE_LOG_UNREADABLE_CACHE", "source_row": row_number, "material": material})
        records.append(item)
    return records, issues


def extract_product_workbook(document: OdsDocument) -> dict[str, Any]:
    steel_log = document.sheets["SteelLog"]
    log_header = _header_row(steel_log, {"Unique Item List Helper", "Unique Item List", "Rate per KG. Basic Rate Only"}, 10)
    log_columns = _column_map(steel_log, log_header)
    log_map = {
        "helper": _pick_column(log_columns, "Unique Item List Helper"),
        "material": _pick_column(log_columns, "Unique Item List"),
        "rate": _pick_column(log_columns, "Rate per KG. Basic Rate Only"),
        "quantity": _pick_column(log_columns, "Qty Ordered (Kgs)"),
        "date": _pick_column(log_columns, "Date of Entry"),
        "description": _pick_column(log_columns, "Description or specifications of Item if Any"),
        "remarks": _pick_column(log_columns, "Remarks"),
    }
    imported_log: list[dict[str, Any]] = []
    for row_number, _ in steel_log.nonempty_rows():
        if row_number <= log_header:
            continue
        cells = _record_cells(steel_log, row_number, log_map)
        values = {key: cell.value for key, cell in cells.items()}
        if all(_blank(value) for value in values.values()):
            continue
        parsed_date, status = _parse_log_date(cells["date"])
        imported_log.append({
            "source_row": row_number,
            "helper": values["helper"],
            "material": values["material"],
            "rate_per_kg": _decimal(values["rate"]),
            "quantity_ordered": values["quantity"],
            "date_raw": _string(values["date"]),
            "date_iso": parsed_date.isoformat() if parsed_date else None,
            "date_parse_status": status,
            "description": values["description"],
            "remarks": values["remarks"],
            "cells": {key: _cell_json(cell) for key, cell in cells.items()},
        })

    iron = document.sheets["1. Iron and Steel"]
    iron_header = _header_row(iron, {"Unique Item List", "Kgs per Mtr/SqMtr", "Current Rate per KG"}, 20)
    iron_columns = _column_map(iron, iron_header)
    iron_map = {
        "material": _pick_column(iron_columns, "Unique Item List"),
        "kg_factor": _pick_column(iron_columns, "Kgs per Mtr/SqMtr"),
        "grams_per_inch": _pick_column(iron_columns, "gms Per Inch"),
        "default_rate": _pick_column(iron_columns, "Current Rate per KG"),
        "rate_used": _pick_column(iron_columns, "Rate Range Per KG Used in Costing"),
        "latest_rate": _pick_column(iron_columns, "Latest Current Rate"),
        "maximum_rate": _pick_column(iron_columns, "Max rate So far"),
        "latest_date": _pick_column(iron_columns, "Latest Rate update Date"),
    }
    iron_rows: list[dict[str, Any]] = []
    for row_number, _ in iron.nonempty_rows():
        if row_number <= iron_header:
            continue
        cells = _record_cells(iron, row_number, iron_map)
        values = {key: cell.value for key, cell in cells.items()}
        if _blank(values["material"]):
            continue
        parsed_date, status = _parse_log_date(cells["latest_date"])
        iron_rows.append({
            "source_row": row_number,
            "material": _string(values["material"]),
            "kg_factor": values["kg_factor"],
            "grams_per_inch": values["grams_per_inch"],
            "default_rate_per_kg": values["default_rate"],
            "rate_used_per_kg": values["rate_used"],
            "latest_rate_per_kg": values["latest_rate"],
            "maximum_rate_per_kg": values["maximum_rate"],
            "latest_date_raw": _string(values["latest_date"]),
            "latest_date_iso": parsed_date.isoformat() if parsed_date else None,
            "latest_date_parse_status": status,
            "cells": {key: _cell_json(cell) for key, cell in cells.items()},
        })

    mcl = document.sheets["5. Material Cut List Price"]
    mcl_header = _header_row(mcl, {"Machine Piece Description", "Material to Cut", "Dimension to Cut (mm)", "Quantity Nos"}, 20)
    mcl_columns = _column_map(mcl, mcl_header)
    mcl_map = {
        "machine_piece_description": _pick_column(mcl_columns, "Machine Piece Description"),
        "material_to_cut": _pick_column(mcl_columns, "Material to Cut"),
        "dimension_inches": _pick_column(mcl_columns, "Dimension to Cut (inches)"),
        "dimension_mm": _pick_column(mcl_columns, "Dimension to Cut (mm)"),
        "quantity": _pick_column(mcl_columns, "Quantity Nos"),
        "total_inches": _pick_column(mcl_columns, "Total Material Used in Inches"),
        "kg_factor": _pick_column(mcl_columns, "KG per Mtr"),
        "grams_per_inch": _pick_column(mcl_columns, "Grm per inch"),
        "total_grams": _pick_column(mcl_columns, "Total Grams"),
        "rate_per_kg": _pick_column(mcl_columns, "Rate per KG"),
        "material_cost": _pick_column(mcl_columns, "Cost of Cut Piece in Rs"),
        "transport": _pick_column(mcl_columns, "Transport Rate"),
        "unloading": _pick_column(mcl_columns, "Unloading Rate"),
        "fabrication": _pick_column(mcl_columns, "Fabrication Rate"),
        "grand_total": _pick_column(mcl_columns, "Grand Total Cost of Piece"),
        "optional_item_group_1": _pick_column(mcl_columns, "Optional Item Group 1"),
        "in_use": _pick_column(mcl_columns, "In Use"),
        "date": _pick_column(mcl_columns, "Date"),
        "remarks": _pick_column(mcl_columns, "Remarks"),
        "item_line_no": _pick_column(mcl_columns, "Item Line No"),
        "cr_log": _pick_column(mcl_columns, "CR Log"),
    }
    mcl_rows: list[dict[str, Any]] = []
    for row_number, _ in mcl.nonempty_rows():
        if row_number <= mcl_header:
            continue
        cells = _record_cells(mcl, row_number, mcl_map)
        values = {key: cell.value for key, cell in cells.items()}
        if _blank(values["material_to_cut"]) and _blank(values["machine_piece_description"]):
            continue
        mcl_rows.append({
            "source_row": row_number,
            **values,
            "status": _active_flag(values["in_use"]),
            "identity_key": _line_key(values),
            "cells": {key: _cell_json(cell) for key, cell in cells.items()},
        })

    parameters = {
        "selector": iron.cell(1, 12).value,
        "unloading_per_kg": iron.cell(1, 8).value,
        "transport_per_kg": iron.cell(2, 8).value,
        "fabrication_per_kg": iron.cell(3, 8).value,
        "cells": {
            "selector": _cell_json(iron.cell(1, 12)),
            "unloading_per_kg": _cell_json(iron.cell(1, 8)),
            "transport_per_kg": _cell_json(iron.cell(2, 8)),
            "fabrication_per_kg": _cell_json(iron.cell(3, 8)),
        },
    }
    totals = {
        "weight_kg": mcl.cell(2, 3),
        "material_cost": mcl.cell(3, 3),
        "transport": mcl.cell(4, 3),
        "unloading": mcl.cell(5, 3),
        "fabrication": mcl.cell(6, 3),
        "grand_total": mcl.cell(7, 3),
    }
    return {
        "steel_log": imported_log,
        "iron_rows": iron_rows,
        "mcl_rows": mcl_rows,
        "parameters": parameters,
        "summary_totals": {name: _cell_json(cell) for name, cell in totals.items()},
        "sheet_contracts": {
            "SteelLog": {"header_row": log_header, "fields": log_map},
            "1. Iron and Steel": {"header_row": iron_header, "fields": iron_map},
            "5. Material Cut List Price": {"header_row": mcl_header, "fields": mcl_map},
        },
    }


def resolve_material_rates(
    raw_materials: list[dict[str, Any]],
    rate_rows: list[dict[str, Any]],
    selector: Any,
    safety_margin: Decimal = Decimal("2"),
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    by_material: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rate_rows:
        if row["material"]:
            by_material[row["material"]].append(row)
    material_names = {row["unique_item_list"] for row in raw_materials if row["unique_item_list"]}
    issues: list[dict[str, Any]] = []
    for material, rows in sorted(by_material.items()):
        if material not in material_names:
            for row in rows:
                issues.append({"code": "RATE_LOG_MATERIAL_ABSENT_FROM_RAW_STEEL", "material": material, "source_row": row["source_row"]})
    results: dict[str, dict[str, Any]] = {}
    raw_by_name: dict[str, dict[str, Any]] = {}
    for row in raw_materials:
        name = row["unique_item_list"]
        if name:
            raw_by_name.setdefault(name, row)
    for material, raw in raw_by_name.items():
        matching = by_material.get(material, [])
        default = _decimal(raw["default_rate_per_kg"])
        valid_rates = [row["rate_per_kg"] for row in matching if row["rate_per_kg"] is not None]
        last_row = matching[-1] if matching else None
        if matching and last_row and last_row["rate_per_kg"] is None:
            latest = None
            issues.append({"code": "LATEST_RATE_ROW_INVALID", "material": material, "source_row": last_row["source_row"]})
        elif last_row:
            latest = last_row["rate_per_kg"]
        else:
            latest = default
        nonzero_rates = [rate for rate in valid_rates if rate != 0]
        maximum = max(nonzero_rates) if nonzero_rates else latest
        maximum_row = next((
            row for row in reversed(matching)
            if row["rate_per_kg"] is not None and row["rate_per_kg"] == maximum and row["rate_per_kg"] != 0
        ), last_row)
        selector_text = _string(selector).strip()
        using_latest = selector_text == "Latest Rate"
        selected = latest if using_latest else maximum
        selected_row = last_row if using_latest else maximum_row
        final = selected + safety_margin if selected is not None else None
        results[material] = {
            "material": material,
            "raw_steel_source_row": raw["source_row"],
            "default_rate_per_kg": default,
            "matching_rate_rows": len(matching),
            "last_rate_log_row": last_row["source_row"] if last_row else None,
            "last_logged_rate_per_kg": last_row["rate_per_kg"] if last_row else None,
            "last_logged_rate_date": last_row["date_iso"] if last_row else None,
            "maximum_rate_log_row": maximum_row["source_row"] if maximum_row else None,
            "selected_rate_source_row": selected_row["source_row"] if selected_row else None,
            "selected_rate_date": selected_row["date_iso"] if selected_row else None,
            "latest_rate_per_kg": latest,
            "maximum_logged_rate_per_kg": maximum,
            "selector": selector_text,
            "selected_rate_basis": "latest_rate" if using_latest else "maximum_rate",
            "selected_rate_before_safety_margin": selected,
            "safety_margin_per_kg": safety_margin,
            "final_rate_per_kg": final,
            "fallback_used": not matching,
            "rate_source": "default_master_rate" if not matching else "rate_log",
            "matching_rate_row_count": len(matching),
        }

        ordered_dates = [
            (row["source_row"], date.fromisoformat(row["date_iso"]))
            for row in matching
            if row["date_iso"]
        ]
        for previous, current in zip(ordered_dates, ordered_dates[1:]):
            if current[1] < previous[1]:
                issues.append({
                    "code": "RATE_LOG_ROW_ORDER_DATE_REGRESSION",
                    "material": material,
                    "previous_source_row": previous[0],
                    "previous_date": previous[1].isoformat(),
                    "later_source_row": current[0],
                    "later_row_date": current[1].isoformat(),
                    "selected_by_workbook": "last_matching_source_row",
                })
        by_date: dict[date, list[dict[str, Any]]] = defaultdict(list)
        for row in matching:
            if row["date_iso"]:
                by_date[date.fromisoformat(row["date_iso"])].append(row)
        for entry_date, same_day_rows in by_date.items():
            rates = {row["rate_per_kg"] for row in same_day_rows if row["rate_per_kg"] is not None}
            if len(rates) > 1:
                issues.append({
                    "code": "RATE_LOG_SAME_DATE_MULTIPLE_RATES",
                    "material": material,
                    "date": entry_date.isoformat(),
                    "source_rows": [row["source_row"] for row in same_day_rows],
                    "rates_per_kg": sorted(_json_value(rate) for rate in rates),
                    "selected_by_workbook": "last_matching_source_row",
                })
        if ordered_dates and last_row:
            latest_date = date.fromisoformat(last_row["date_iso"]) if last_row["date_iso"] else None
            if latest_date and latest_date < max(entry_date for _, entry_date in ordered_dates):
                issues.append({
                    "code": "RATE_LOG_LAST_ROW_NOT_MAX_DATE",
                    "material": material,
                    "last_source_row": last_row["source_row"],
                    "last_row_date": latest_date.isoformat(),
                    "max_date": max(entry_date for _, entry_date in ordered_dates).isoformat(),
                    "selected_rate_per_kg": _json_value(latest),
                })
    return results, issues


def apply_rate_display_controls(
    resolutions: dict[str, dict[str, Any]],
    rate_rows: list[dict[str, Any]],
    validation_issues: list[dict[str, Any]],
    observed_on: date,
) -> list[dict[str, Any]]:
    """Hide a material's effective rate while any source-rate error is unresolved.

    Legacy formula results remain available for read-only parity evidence. UI
    consumers must use display_rate_per_kg and display_status instead.
    """
    blocking_codes = {
        "RATE_LOG_INVALID_RATE",
        "RATE_LOG_INVALID_DATE",
        "LATEST_RATE_ROW_INVALID",
    }
    blockers: dict[str, dict[str, Any]] = {}

    def add_block(material: str, code: str, source_row: int | None) -> None:
        if not material:
            return
        entry = blockers.setdefault(material, {"codes": set(), "source_rows": set()})
        entry["codes"].add(code)
        if source_row is not None:
            entry["source_rows"].add(source_row)

    for issue in validation_issues:
        code = issue.get("code")
        if code in blocking_codes:
            add_block(_string(issue.get("material")), code, issue.get("source_row"))
    blocked: list[dict[str, Any]] = []
    for material, resolution in resolutions.items():
        issue = blockers.get(material)
        if issue:
            resolution["display_status"] = "blocked_user_resolution_required"
            resolution["display_rate_per_kg"] = None
            resolution["display_issue_codes"] = sorted(issue["codes"])
            resolution["display_blocking_source_rows"] = sorted(issue["source_rows"])
            resolution["display_scope"] = "all_workbooks_using_material"
            blocked.append({
                "material": material,
                "issue_codes": sorted(issue["codes"]),
                "source_rows": sorted(issue["source_rows"]),
                "display_rate_per_kg": None,
                "scope": "all_workbooks_using_material",
            })
        else:
            resolution["display_status"] = "available"
            resolution["display_rate_per_kg"] = resolution["final_rate_per_kg"]
            resolution["display_issue_codes"] = []
            resolution["display_blocking_source_rows"] = []
            resolution["display_scope"] = "all_workbooks_using_material"
    return blocked


def build_mcl_rate_warning_index(
    product_path: Path,
    raw_steel_path: Path,
    rate_dump_path: Path,
) -> dict[str, Any]:
    """Return active MCL row warnings for confirmed invalid material rates.

    This is a read-only UI projection. Invalid rates/dates stay linked to their
    source rows, but effective rates and dependent line totals are withheld by
    the preview endpoint.
    """
    absent_sources = [str(path) for path in (raw_steel_path, rate_dump_path) if not path.is_file()]
    if absent_sources:
        return {
            "status": "unavailable",
            "message": "Rate validation is unavailable because one or more source workbooks could not be found.",
            "missingSources": absent_sources,
            "materialColumn": None,
            "maskedColumns": [],
            "rows": {},
            "globalWarnings": [],
        }

    raw_materials, _raw_issues = extract_raw_steel(
        read_ods(raw_steel_path, {"RawSteel"})
    )
    rate_rows, rate_issues = extract_rate_log(
        read_ods(rate_dump_path, {"SteelRateLog"})
    )
    product = extract_product_workbook(read_ods(product_path, {
        "SteelLog", "1. Iron and Steel", "5. Material Cut List Price"
    }))
    selector = product["parameters"]["selector"]
    resolutions, resolution_issues = resolve_material_rates(
        raw_materials, rate_rows, selector
    )
    all_rate_issues = rate_issues + resolution_issues
    blocked = apply_rate_display_controls(
        resolutions, rate_rows, all_rate_issues, date.today()
    )
    blocked_by_material = {row["material"]: row for row in blocked}
    issue_codes = {"RATE_LOG_INVALID_RATE", "RATE_LOG_INVALID_DATE", "LATEST_RATE_ROW_INVALID"}
    source_contract = product["sheet_contracts"]["5. Material Cut List Price"]
    fields = source_contract["fields"]
    masked_field_names = ["rate_per_kg", "material_cost", "grand_total"]
    row_warnings: dict[str, dict[str, Any]] = {}
    for row in product["mcl_rows"]:
        if row["status"] != "active":
            continue
        material = _string(row["material_to_cut"])
        blocked_rate = blocked_by_material.get(material)
        if not blocked_rate:
            continue
        evidence = [
            issue for issue in all_rate_issues
            if issue.get("code") in issue_codes and issue.get("material") == material
        ]
        row_warnings[str(row["source_row"])] = {
            "severity": "warning",
            "label": "Rate blocked",
            "message": (
                "A confirmed invalid SteelRateLog entry needs review. "
                f"Source row(s) {', '.join(str(value) for value in blocked_rate['source_rows']) or 'unknown'}; "
                f"cell(s) {', '.join(sorted({issue['source_cell'] for issue in evidence if issue.get('source_cell')}) or ['unknown'])}. "
                "The effective rate and dependent line totals are hidden."
            ),
            "material": material,
            "issueCodes": blocked_rate["issue_codes"],
            "rateLogRows": blocked_rate["source_rows"],
            "rateLogCells": sorted({
                issue["source_cell"] for issue in evidence if issue.get("source_cell")
            }),
            "hiddenFields": masked_field_names,
        }
    global_warnings = [
        {
            "code": issue["code"],
            "sourceCell": issue.get("source_cell"),
            "sourceRow": issue.get("source_row"),
            "message": "A nonempty SteelRateLog row has no material name, so it cannot be attached to a Material Cut List row.",
        }
        for issue in rate_issues
        if issue.get("code") == "RATE_LOG_BLANK_MATERIAL"
    ]
    return {
        "status": "warnings" if row_warnings or global_warnings else "clear",
        "message": None,
        "missingSources": [],
        "checkedAt": datetime.now().astimezone().isoformat(),
        "sources": {
            "rawSteel": _file_metadata(raw_steel_path, datetime.now().astimezone()),
            "rateDump": _file_metadata(rate_dump_path, datetime.now().astimezone()),
        },
        "materialColumn": fields["material_to_cut"],
        "maskedColumns": [fields[name] for name in masked_field_names],
        "rows": row_warnings,
        "globalWarnings": global_warnings,
    }


def compare_dump_to_product_log(
    source_rows: list[dict[str, Any]],
    product_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    source_by_row = {row["source_row"]: row for row in source_rows}
    local_by_row = {row["source_row"]: row for row in product_rows}
    for row_number in sorted(set(source_by_row) | set(local_by_row)):
        source = source_by_row.get(row_number)
        local = local_by_row.get(row_number)
        if source is None or local is None:
            issues.append({"code": "PRODUCT_STEEL_LOG_ROW_COVERAGE_MISMATCH", "source_row": row_number, "dump_present": source is not None, "product_present": local is not None})
            continue
        for field_name in ("material", "rate_per_kg", "quantity_ordered", "date_iso", "description", "remarks"):
            left = source[field_name]
            right = local[field_name]
            if field_name == "rate_per_kg":
                same = left == right
            else:
                same = _string(left) == _string(right)
            if not same:
                issues.append({"code": "PRODUCT_STEEL_LOG_VALUE_MISMATCH", "field": field_name, "source_row": row_number, "dump_value": _json_value(left), "product_value": _json_value(right)})
    return issues


def compare_dump_rates_to_product_snapshot(
    dump_resolution: dict[str, dict[str, Any]],
    product_resolution: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Report the expected lag between the current dump and a workbook cache."""
    issues: list[dict[str, Any]] = []
    for material in sorted(set(dump_resolution) | set(product_resolution)):
        current = dump_resolution.get(material)
        cached = product_resolution.get(material)
        if current is None or cached is None:
            issues.append({
                "code": "DUMP_PRODUCT_RATE_IDENTITY_COVERAGE_MISMATCH",
                "material": material,
                "dump_present": current is not None,
                "product_present": cached is not None,
            })
            continue
        for field_name in ("latest_rate_per_kg", "maximum_logged_rate_per_kg", "final_rate_per_kg", "last_logged_rate_date"):
            current_value = current[field_name]
            cached_value = cached[field_name]
            if current_value != cached_value:
                issues.append({
                    "code": "DUMP_PRODUCT_RATE_SNAPSHOT_DIFFERENCE",
                    "material": material,
                    "field": field_name,
                    "current_dump_value": _json_value(current_value),
                    "product_cached_value": _json_value(cached_value),
                    "current_dump_last_row": current["last_rate_log_row"],
                    "product_cached_last_row": cached["last_rate_log_row"],
                })
    return issues


def validate_product_log_occurrence_keys(product_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Check the helper key used by the workbook's last-occurrence VLOOKUP."""
    occurrences: dict[str, int] = defaultdict(int)
    issues: list[dict[str, Any]] = []
    for row in product_rows:
        material = _string(row["material"])
        if not material:
            continue
        occurrences[material] += 1
        expected = f"{material}-{occurrences[material]}"
        if _string(row["helper"]) != expected:
            cell = row["cells"]["helper"]
            issues.append({
                "code": "STEELLOG_HELPER_OCCURRENCE_KEY_MISMATCH",
                "source_row": row["source_row"],
                "material": material,
                "cached_helper": _json_value(row["helper"]),
                "expected_helper": expected,
                "source_cell": cell["cell"],
                "formula": cell["formula"],
            })
    return issues


def _diff_cell(
    issues: list[dict[str, Any]],
    code: str,
    material: str,
    field_name: str,
    cell: dict[str, Any],
    expected: Any,
) -> None:
    if expected is None:
        return
    cached_value = cell["cached_value"]
    if isinstance(expected, Decimal):
        same, delta = _same_number(cached_value, expected)
        if not same:
            issues.append({
                "code": code,
                "material": material,
                "field": field_name,
                "source_cell": cell["cell"],
                "cached_value": _json_value(cached_value),
                "expected_value": _json_value(expected),
                "difference": _json_value(delta),
                "formula": cell["formula"],
            })
    elif _string(cached_value) != _string(expected):
        issues.append({
            "code": code,
            "material": material,
            "field": field_name,
            "source_cell": cell["cell"],
            "cached_value": _json_value(cached_value),
            "expected_value": _json_value(expected),
            "formula": cell["formula"],
        })


def compare_product_workbook(
    raw_materials: list[dict[str, Any]],
    dump_rows: list[dict[str, Any]],
    product: dict[str, Any],
    resolutions: dict[str, dict[str, Any]],
    master_lookup_end: int | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    coverage_findings: list[dict[str, Any]] = []
    raw_lookup: dict[str, dict[str, Any]] = {}
    for row in raw_materials:
        if row["unique_item_list"]:
            raw_lookup.setdefault(row["unique_item_list"], row)
    active_mcl_rows_by_material: dict[str, list[int]] = defaultdict(list)
    for row in product["mcl_rows"]:
        if row["status"] == "active":
            active_mcl_rows_by_material[_string(row["material_to_cut"])].append(row["source_row"])
    iron_lookup: dict[str, dict[str, Any]] = {}
    for row in product["iron_rows"]:
        iron_lookup.setdefault(row["material"], row)
    formula_reference_rows = 0
    for material, raw in raw_lookup.items():
        iron = iron_lookup.get(material)
        if not iron:
            active_mcl_rows = active_mcl_rows_by_material.get(material, [])
            normalized_material = " ".join(material.casefold().split())
            spacing_candidates = [
                row["material"]
                for row in product["iron_rows"]
                if " ".join(row["material"].casefold().split()) == normalized_material
            ]
            issues.append({
                "code": (
                    "PRODUCT_IRON_MATERIAL_NAME_MISMATCH"
                    if spacing_candidates else "PRODUCT_IRON_MISSING_MATERIAL"
                ),
                "material": material,
                "raw_steel_row": raw["source_row"],
                "product_material_candidates": spacing_candidates,
                "active_mcl_source_rows": active_mcl_rows,
                "classification": (
                    "blocking_active_dependency" if active_mcl_rows
                    else "warning_unreferenced_master_material"
                ),
                "blocking": bool(active_mcl_rows),
            })
            continue
        if master_lookup_end is not None and raw["source_row"] > master_lookup_end:
            formula_reference_rows += 1
            for field_name, key in (("kg_factor", "kg_factor"), ("grams_per_inch", "grams_per_inch"), ("default_rate_per_kg", "default_rate")):
                cell = iron["cells"][key]
                if _string(cell["cached_value"]).strip() in {"#N/A", "#VALUE!", "#REF!"} or _blank(cell["cached_value"]):
                    dependent_mcl_rows = active_mcl_rows_by_material.get(material, [])
                    finding = {
                        "code": "PRODUCT_LOOKUP_RANGE_EXCLUDES_RAW_STEEL_ROW",
                        "material": material,
                        "raw_steel_row": raw["source_row"],
                        "product_source_cell": cell["cell"],
                        "field": field_name,
                        "lookup_end_row": master_lookup_end,
                        "cached_value": cell["cached_value"],
                        "formula": cell["formula"],
                        "active_mcl_source_rows": dependent_mcl_rows,
                        "classification": "blocking_active_dependency" if dependent_mcl_rows else "warning_consistency_only",
                        "blocking": bool(dependent_mcl_rows),
                    }
                    coverage_findings.append(finding)
                    if dependent_mcl_rows:
                        issues.append({
                            "code": "PRODUCT_LOOKUP_RANGE_MISSES_ACTIVE_MATERIAL",
                            "material": material,
                            "raw_steel_row": raw["source_row"],
                            "lookup_end_row": master_lookup_end,
                            "active_mcl_source_rows": dependent_mcl_rows,
                        })
            continue
        for field, raw_key, iron_key in (
            ("material_identity", "unique_item_list", "material"),
            ("kg_factor", "kg_factor", "kg_factor"),
            ("grams_per_inch", "grams_per_inch", "grams_per_inch"),
            ("default_rate_per_kg", "default_rate_per_kg", "default_rate"),
        ):
            if master_lookup_end is not None and raw["source_row"] > master_lookup_end and field != "material_identity":
                continue
            raw_value = raw[raw_key]
            cached_cell = iron["cells"][iron_key]
            expected = _decimal(raw_value) if field != "material_identity" else _string(raw_value)
            _diff_cell(issues, "IRON_AND_STEEL_RAW_STEEL_PARITY", material, field, cached_cell, expected)
        resolution = resolutions.get(material)
        if resolution:
            for field, key, expected in (
                ("latest_rate_per_kg", "latest_rate", resolution["latest_rate_per_kg"]),
                ("maximum_rate_per_kg", "maximum_rate", resolution["maximum_logged_rate_per_kg"]),
                ("selected_rate_per_kg", "rate_used", resolution["final_rate_per_kg"]),
            ):
                _diff_cell(issues, "IRON_AND_STEEL_RATE_PARITY", material, field, iron["cells"][key], expected)
            expected_date = resolution["last_logged_rate_date"]
            cached_date = iron["latest_date_iso"]
            if expected_date:
                if cached_date != expected_date:
                    issues.append({
                        "code": "IRON_AND_STEEL_RATE_DATE_PARITY",
                        "material": material,
                        "source_cell": iron["cells"]["latest_date"]["cell"],
                        "cached_value": iron["latest_date_raw"],
                        "cached_parsed_date": cached_date,
                        "expected_date": expected_date,
                        "formula": iron["cells"]["latest_date"]["formula"],
                    })
            elif resolution["fallback_used"] and iron["latest_date_raw"]:
                try:
                    cached_as_date = date.fromisoformat(iron["latest_date_raw"][:10])
                except ValueError:
                    cached_as_date = None
                if cached_as_date and cached_as_date.year <= 1900:
                    resolution["cached_date_artifact"] = {
                        "source_cell": iron["cells"]["latest_date"]["cell"],
                        "cached_display": iron["latest_date_raw"],
                        "semantic_rate_log_date": None,
                        "reason": "Workbook fallback uses the numeric default rate where a date is expected.",
                    }
    active = [row for row in product["mcl_rows"] if row["status"] == "active"]
    historical = [row for row in product["mcl_rows"] if row["status"] == "historical"]
    active_keys: dict[tuple[str, ...], list[int]] = defaultdict(list)
    for row in product["mcl_rows"]:
        if row["status"] == "active":
            active_keys[row["identity_key"]].append(row["source_row"])
        else:
            # Historical rows are retained as cached evidence. Their formulas
            # are not recalculated into current costing.
            continue
        material = _string(row["material_to_cut"])
        raw = raw_lookup.get(material)
        if raw is None:
            if row["status"] == "active":
                issues.append({"code": "ACTIVE_MCL_MATERIAL_ABSENT_FROM_RAW_STEEL", "material": material, "source_row": row["source_row"], "source_cell": row["cells"]["material_to_cut"]["cell"]})
            continue
        if master_lookup_end is not None and raw["source_row"] > master_lookup_end:
            issues.append({"code": "ACTIVE_MCL_MATERIAL_BEYOND_LOOKUP_RANGE", "material": material, "raw_steel_row": raw["source_row"], "lookup_end_row": master_lookup_end, "source_row": row["source_row"]})
            continue
        if material not in resolutions:
            if row["status"] == "active":
                issues.append({"code": "ACTIVE_MCL_RATE_UNRESOLVED", "material": material, "source_row": row["source_row"]})
            continue
        resolution = resolutions[material]
        line_cells = row["cells"]
        for field_name, expected in (
            ("kg_factor", _decimal(raw["kg_factor"])),
            ("grams_per_inch", _decimal(raw["grams_per_inch"])),
            ("rate_per_kg", resolution["final_rate_per_kg"]),
        ):
            _diff_cell(issues, "MCL_LOOKUP_PARITY", material, field_name, line_cells[field_name], expected)
        inches = _decimal(row["dimension_inches"])
        quantity = _decimal(row["quantity"])
        grams_per_inch = _decimal(raw["grams_per_inch"])
        rate = resolution["final_rate_per_kg"]
        if inches is None or quantity is None or grams_per_inch is None or rate is None:
            issues.append({"code": "MCL_INVALID_CALCULATION_INPUT", "status": row["status"], "material": material, "source_row": row["source_row"], "dimension_inches": _json_value(row["dimension_inches"]), "quantity": _json_value(row["quantity"])})
            continue
        with localcontext() as context:
            context.prec = 34
            total_inches = inches * quantity
            total_grams = total_inches * grams_per_inch
            weight_kg = total_grams / Decimal(1000)
            material_cost = weight_kg * rate
            transport = weight_kg * (_decimal(product["parameters"]["transport_per_kg"]) or Decimal(0))
            unloading = weight_kg * (_decimal(product["parameters"]["unloading_per_kg"]) or Decimal(0))
            fabrication = weight_kg * (_decimal(product["parameters"]["fabrication_per_kg"]) or Decimal(0))
            grand_total = material_cost + transport + unloading + fabrication
        calculated = {
            "total_inches": total_inches,
            "total_grams": total_grams,
            "material_cost": material_cost,
            "transport": transport,
            "unloading": unloading,
            "fabrication": fabrication,
            "grand_total": grand_total,
        }
        for field_name, expected in calculated.items():
            cached = row["cells"][field_name]
            value = cached["cached_value"]
            actual = _decimal(value)
            delta = actual - expected if actual is not None else None
            same = actual is not None and abs(delta or Decimal(0)) <= _READING_CONTEXT
            if not same:
                issues.append({
                    "code": "MCL_INDEPENDENT_CALCULATION_DIFFERENCE",
                    "status": row["status"],
                    "material": material,
                    "source_row": row["source_row"],
                    "field": field_name,
                    "source_cell": cached["cell"],
                    "cached_value": _json_value(value),
                    "independent_value": _json_value(expected),
                    "difference": _json_value(delta),
                    "formula": cached["formula"],
                })
        row["independent_calculation"] = calculated
        row["business_output_rounding"] = {
            "rule": "grand_total_cost_per_active_line_round_up_to_whole_rupee",
            "exact_grand_total": grand_total,
            "rounded_grand_total": _round_up_whole_rupee(grand_total),
        }
        row["resolved_rate"] = resolution
    for key, rows in active_keys.items():
        if len(rows) > 1:
            issues.append({"code": "ACTIVE_MCL_DUPLICATE_COMPOSITE_KEY", "key": list(key), "source_rows": rows})
    unexpected = [row for row in product["mcl_rows"] if row["status"] == "unexpected"]
    for row in unexpected:
        issues.append({"code": "MCL_UNEXPECTED_IN_USE_VALUE", "source_row": row["source_row"], "value": _json_value(row["in_use"])})

    active_totals = {
        "weight_kg": _decimal_sum([row["independent_calculation"]["total_grams"] / Decimal(1000) for row in active if "independent_calculation" in row]),
        "material_cost": _decimal_sum([row["independent_calculation"]["material_cost"] for row in active if "independent_calculation" in row]),
        "transport": _decimal_sum([row["independent_calculation"]["transport"] for row in active if "independent_calculation" in row]),
        "unloading": _decimal_sum([row["independent_calculation"]["unloading"] for row in active if "independent_calculation" in row]),
        "fabrication": _decimal_sum([row["independent_calculation"]["fabrication"] for row in active if "independent_calculation" in row]),
        "grand_total": _decimal_sum([row["independent_calculation"]["grand_total"] for row in active if "independent_calculation" in row]),
        "grand_total_rounded_up_per_line": _decimal_sum([row["business_output_rounding"]["rounded_grand_total"] for row in active if "business_output_rounding" in row]),
    }
    cached_line_fields = {
        "weight_kg": ("total_grams", Decimal(1000)),
        "material_cost": ("material_cost", Decimal(1)),
        "transport": ("transport", Decimal(1)),
        "unloading": ("unloading", Decimal(1)),
        "fabrication": ("fabrication", Decimal(1)),
        "grand_total": ("grand_total", Decimal(1)),
    }
    cached_line_sums: dict[str, Decimal] = {}
    historical_cached_contribution: dict[str, Decimal] = {}
    for summary_field, (line_field, divisor) in cached_line_fields.items():
        all_cached = [
            _decimal(row["cells"][line_field]["cached_value"])
            for row in product["mcl_rows"]
        ]
        all_cached = [value for value in all_cached if value is not None]
        history_cached = [
            _decimal(row["cells"][line_field]["cached_value"])
            for row in historical
        ]
        history_cached = [value for value in history_cached if value is not None]
        cached_line_sums[summary_field] = _decimal_sum(all_cached) / divisor
        historical_cached_contribution[summary_field] = _decimal_sum(history_cached) / divisor
    summary_diffs = []
    for field_name, formula_expected in cached_line_sums.items():
        active_expected = active_totals[field_name]
        cached = product["summary_totals"][field_name]
        actual = _decimal(cached["cached_value"])
        formula_delta = actual - formula_expected if actual is not None else None
        active_delta = actual - active_expected if actual is not None else None
        formula_match = actual is not None and abs(formula_delta or Decimal(0)) <= _READING_CONTEXT
        active_match = actual is not None and abs(active_delta or Decimal(0)) <= _READING_CONTEXT
        evidence = {
            "field": field_name,
            "source_cell": cached["cell"],
            "cached_value": cached["cached_value"],
            "sum_of_cached_line_values": _json_value(formula_expected),
            "difference_from_cached_line_sum": _json_value(formula_delta),
            "cached_summary_matches_line_sum": formula_match,
            "active_only_semantic_value": _json_value(active_expected),
            "difference_from_active_only": _json_value(active_delta),
            "active_only_matches_cached": active_match,
            "historical_cached_contribution": _json_value(historical_cached_contribution[field_name]),
            "formula": cached["formula"],
        }
        summary_diffs.append(evidence)
        if not formula_match:
            issues.append({"code": "MCL_CACHED_SUMMARY_LINE_SUM_DIFFERENCE", **evidence})
        if not active_match and formula_match and active_expected != formula_expected:
            issues.append({
                "code": "MCL_SUMMARY_INCLUDES_HISTORICAL_LINES",
                "classification": "known_legacy_cache_contribution",
                "blocking": False,
                **evidence,
                "historical_line_count": len(historical),
                "historical_cached_contribution": _json_value(historical_cached_contribution[field_name]),
                "business_rule": "In Use = No rows are historical and excluded from current costing.",
            })

    return issues, {
        "active_mcl_count": len(active),
        "historical_mcl_count": len(historical),
        "mcl_total_rows": len(product["mcl_rows"]),
        "unique_active_keys": len(active_keys),
        "active_duplicate_key_count": sum(1 for rows in active_keys.values() if len(rows) > 1),
        "raw_steel_records_beyond_formula_lookup_range": formula_reference_rows,
        "lookup_range_coverage_findings": coverage_findings,
        "lookup_range_coverage_warning_count": sum(1 for item in coverage_findings if not item.get("blocking")),
        "lookup_range_coverage_blocker_count": sum(1 for item in coverage_findings if item.get("blocking")),
        "active_only_summary_totals": active_totals,
        "cached_line_sum_totals": cached_line_sums,
        "historical_cached_contribution": historical_cached_contribution,
        "historical_rows_with_nonzero_cached_cost": len({
            row["source_row"] for row in historical
            if any(
                (_decimal(row["cells"][field]["cached_value"]) or Decimal(0)) != 0
                for field in ("material_cost", "transport", "unloading", "fabrication", "grand_total")
            )
        }),
        "historical_rows_with_blank_formula_caches": len({
            row["source_row"] for row in historical
            if any(
                row["cells"][field]["formula"] and _blank(row["cells"][field]["cached_value"])
                for field in ("total_grams", "material_cost", "transport", "unloading", "fabrication", "grand_total")
            )
        }),
        "summary_comparison": summary_diffs,
    }


def _match_mcl_snapshot_rows(
    previous_rows: list[dict[str, Any]], current_rows: list[dict[str, Any]]
) -> tuple[dict[tuple[str, ...], dict[str, Any]], dict[tuple[str, ...], dict[str, Any]], list[dict[str, Any]]]:
    """Match MCL observations only by the owner-approved composite identity."""
    previous_groups: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    current_groups: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in previous_rows:
        previous_groups[tuple(row["identity_key"])].append(row)
    for row in current_rows:
        current_groups[tuple(row["identity_key"])].append(row)

    previous: dict[tuple[str, ...], dict[str, Any]] = {}
    current: dict[tuple[str, ...], dict[str, Any]] = {}
    ambiguous: list[dict[str, Any]] = []
    for key in set(previous_groups) | set(current_groups):
        old_rows = previous_groups.get(key, [])
        new_rows = current_groups.get(key, [])
        if len(old_rows) > 1 or len(new_rows) > 1:
            ambiguous.append({
                "code": "MCL_COMPOSITE_KEY_AMBIGUOUS",
                "identity_key": list(key),
                "previous_source_rows": [row["source_row"] for row in old_rows],
                "current_source_rows": [row["source_row"] for row in new_rows],
                "message": "Duplicate composite keys are retained for review and were not paired or deduplicated.",
            })
            continue
        if old_rows:
            previous[key] = old_rows[0]
        if new_rows:
            current[key] = new_rows[0]
    return previous, current, ambiguous


def _compare_mcl_snapshot_content(
    previous_rows: list[dict[str, Any]], current_rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Describe additions, removals, status, and review-context changes by D-034 key."""
    previous, current, ambiguous = _match_mcl_snapshot_rows(previous_rows, current_rows)
    changes = list(ambiguous)
    for key in sorted(set(previous) | set(current)):
        old = previous.get(key)
        new = current.get(key)
        if old is None:
            changes.append({
                "code": "MCL_LINE_ADDED",
                "change": "addition",
                "identity_key": list(key),
                "previous_source_row": None,
                "current_source_row": new["source_row"],
                "current_status": new["status"],
            })
            continue
        if new is None:
            changes.append({
                "code": "MCL_LINE_REMOVED",
                "change": "removal",
                "identity_key": list(key),
                "previous_source_row": old["source_row"],
                "current_source_row": None,
                "previous_status": old["status"],
            })
            continue
        if old["status"] != new["status"]:
            changes.append({
                "code": "MCL_LINE_USAGE_CHANGED",
                "change": "removed_from_current_costing" if new["status"] == "historical" else "added_to_current_costing",
                "identity_key": list(key),
                "previous_source_row": old["source_row"],
                "current_source_row": new["source_row"],
                "previous_status": old["status"],
                "current_status": new["status"],
            })
        context_fields = ("date", "remarks", "cr_log")
        changed_context = {
            field: {"previous": _json_value(old.get(field)), "current": _json_value(new.get(field))}
            for field in context_fields
            if _string(old.get(field)) != _string(new.get(field))
        }
        if changed_context:
            changes.append({
                "code": "MCL_REVIEW_CONTEXT_CHANGED",
                "change": "review_context_changed",
                "identity_key": list(key),
                "previous_source_row": old["source_row"],
                "current_source_row": new["source_row"],
                "fields": changed_context,
            })
    return changes


def _identity_tuple(line: dict[str, Any]) -> tuple[str, ...]:
    values = line.get("identity_values", {})
    return tuple(_string(values.get(name)) for name in line.get("identity_fields", []))


def _semantic_line_ref(line: dict[str, Any]) -> str:
    """A content-derived reference; source row and MCL ID are evidence only."""
    payload = {
        "process_list": line.get("process_list"),
        "identity_values": line.get("identity_values", {}),
        "fields": line.get("fields", {}),
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _line_comparable_state(line: dict[str, Any]) -> dict[str, Any]:
    return {
        name: value for name, value in line.get("fields", {}).items()
        if name not in _PROCESS_COST_FIELDS | _PROCESS_CONTEXT_FIELDS
    }


def _line_cost(line: dict[str, Any]) -> Decimal | None:
    value = line.get("current_cost", line.get("cost_value"))
    return _decimal(value)


def _line_source_state(line: dict[str, Any] | None) -> dict[str, Any] | None:
    if line is None:
        return None
    return {
        "process_list": line.get("process_list"),
        "status": line.get("status"),
        "identity_fields": line.get("identity_fields", []),
        "identity_values": line.get("identity_values", {}),
        "fields": line.get("fields", {}),
        "current_cost": _json_value(_line_cost(line)),
        "cr_reference": line.get("cr_reference"),
        "source_evidence": {
            "source_row": line.get("source_row"),
            "source_cells": line.get("source_cells", {}),
        },
        "content_reference": _semantic_line_ref(line),
    }


def _candidate_line_match(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    old_values = old.get("identity_values", {})
    new_values = new.get("identity_values", {})
    common_fields = set(old.get("identity_fields", [])) & set(new.get("identity_fields", []))
    comparable = [
        name for name in common_fields
        if _string(old_values.get(name)) and _string(new_values.get(name))
    ]
    equal_fields = [
        name for name in comparable
        if _string(old_values.get(name)) == _string(new_values.get(name))
    ]
    anchors = {
        "product_part_name", "machine_piece_description", "item_name", "item_code",
        "activity", "toolshop_part_name",
    }
    shared_anchor = any(name in anchors for name in equal_fields)
    old_cr = _string(old.get("cr_reference")).strip()
    new_cr = _string(new.get("cr_reference")).strip()
    common_cr = old_cr and new_cr and old_cr.casefold() == new_cr.casefold()
    return {
        "matching_fields": equal_fields,
        "changed_fields": [name for name in comparable if name not in equal_fields],
        "compared_field_count": len(comparable),
        "similarity": (len(equal_fields) / len(comparable)) if comparable else 0.0,
        "shared_anchor": shared_anchor,
        "shared_cr_reference": bool(common_cr),
    }


def _pair_structural_lines(
    previous_lines: list[dict[str, Any]], current_lines: list[dict[str, Any]]
) -> tuple[list[tuple[dict[str, Any], dict[str, Any], str, dict[str, Any]]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Pair exact keys and only unique, evidence-backed changed-key candidates."""
    old_groups: dict[tuple[str, tuple[str, ...]], list[dict[str, Any]]] = defaultdict(list)
    new_groups: dict[tuple[str, tuple[str, ...]], list[dict[str, Any]]] = defaultdict(list)
    for line in previous_lines:
        old_groups[(line["process_list"], _identity_tuple(line))].append(line)
    for line in current_lines:
        new_groups[(line["process_list"], _identity_tuple(line))].append(line)

    paired: list[tuple[dict[str, Any], dict[str, Any], str, dict[str, Any]]] = []
    ambiguous: list[dict[str, Any]] = []
    used_old: set[int] = set()
    used_new: set[int] = set()
    for key in set(old_groups) | set(new_groups):
        old_rows, new_rows = old_groups.get(key, []), new_groups.get(key, [])
        if len(old_rows) > 1 or len(new_rows) > 1:
            ambiguous.append({
                "change_type": "ambiguous_line_identity",
                "classification": "owner_review_required",
                "process_list": key[0],
                "identity_key": list(key[1]),
                "previous_candidates": [_line_source_state(row) for row in old_rows],
                "current_candidates": [_line_source_state(row) for row in new_rows],
                "reason": "The agreed semantic key is duplicated; neither row order nor MCL ID can distinguish the lines.",
            })
            used_old.update(id(row) for row in old_rows)
            used_new.update(id(row) for row in new_rows)
        elif old_rows and new_rows:
            paired.append((old_rows[0], new_rows[0], "exact", {"matching_fields": old_rows[0].get("identity_fields", [])}))
            used_old.add(id(old_rows[0]))
            used_new.add(id(new_rows[0]))

    unmatched_old = [line for line in previous_lines if id(line) not in used_old]
    unmatched_new = [line for line in current_lines if id(line) not in used_new]
    candidates: list[dict[str, Any]] = []
    for old in unmatched_old:
        for new in unmatched_new:
            evidence = _candidate_line_match(old, new)
            same_list = old["process_list"] == new["process_list"]
            enough = evidence["compared_field_count"] >= (3 if not same_list else 2)
            strong = (
                enough and evidence["similarity"] >= (0.60 if not same_list else 0.50)
                and evidence["shared_anchor"]
            )
            contextual = evidence["shared_anchor"] or evidence["shared_cr_reference"]
            if strong or contextual:
                candidates.append({"old": old, "new": new, "evidence": evidence, "strong": strong})

    old_edges: dict[int, list[dict[str, Any]]] = defaultdict(list)
    new_edges: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        old_edges[id(candidate["old"])].append(candidate)
        new_edges[id(candidate["new"])].append(candidate)
    paired_old: set[int] = set()
    paired_new: set[int] = set()
    for old_id, old_choices in old_edges.items():
        old_top = max(item["evidence"]["similarity"] for item in old_choices)
        best_old = [item for item in old_choices if item["evidence"]["similarity"] == old_top]
        if len(best_old) != 1 or not best_old[0]["strong"]:
            continue
        candidate = best_old[0]
        new_id = id(candidate["new"])
        new_choices = new_edges[new_id]
        new_top = max(item["evidence"]["similarity"] for item in new_choices)
        best_new = [item for item in new_choices if item["evidence"]["similarity"] == new_top]
        if candidate["evidence"]["similarity"] != new_top or len(best_new) != 1 or best_new[0] is not candidate:
            continue
        match_kind = "movement" if candidate["old"]["process_list"] != candidate["new"]["process_list"] else "probable_modification"
        paired.append((candidate["old"], candidate["new"], match_kind, candidate["evidence"]))
        paired_old.add(old_id)
        paired_new.add(new_id)

    ambiguous_candidates = [
        item for item in candidates
        if id(item["old"]) not in paired_old and id(item["new"]) not in paired_new
    ]
    emitted_ambiguous: set[tuple[str, str]] = set()
    for candidate in ambiguous_candidates:
        old, new = candidate["old"], candidate["new"]
        pair_key = (_semantic_line_ref(old), _semantic_line_ref(new))
        if pair_key in emitted_ambiguous:
            continue
        emitted_ambiguous.add(pair_key)
        ambiguous.append({
            "change_type": "ambiguous_line_match",
            "classification": "owner_review_required",
            "candidate_pair_key": hashlib.sha256("|".join(pair_key).encode("ascii")).hexdigest(),
            "previous_candidate": _line_source_state(old),
            "current_candidate": _line_source_state(new),
            "match_evidence": candidate["evidence"],
            "reason": "More than one plausible match exists or the available fields do not support a confident automatic pairing.",
        })
    ambiguous_old = {id(item["old"]) for item in ambiguous_candidates}
    ambiguous_new = {id(item["new"]) for item in ambiguous_candidates}
    remaining_old = [line for line in unmatched_old if id(line) not in paired_old and id(line) not in ambiguous_old]
    remaining_new = [line for line in unmatched_new if id(line) not in paired_new and id(line) not in ambiguous_new]
    return paired, remaining_old, remaining_new, ambiguous


def _semantic_change(
    change_type: str,
    classification: str,
    old: dict[str, Any] | None,
    new: dict[str, Any] | None,
    *,
    fields: dict[str, Any] | None = None,
    match_kind: str = "exact",
    match_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    old_cost = _line_cost(old) if old else None
    new_cost = _line_cost(new) if new else None
    delta = new_cost - old_cost if old_cost is not None and new_cost is not None else None
    before = _line_source_state(old)
    after = _line_source_state(new)
    item = {
        "change_type": change_type,
        "classification": classification,
        "match_kind": match_kind,
        "previous_state": before,
        "current_state": after,
        "field_changes": fields or {},
        "cost_impact": _json_value(delta),
        "match_evidence": match_evidence or {},
        "cr_reference": {
            "previous": old.get("cr_reference") if old else None,
            "current": new.get("cr_reference") if new else None,
        },
        "reason": (
            "Authoritative ODS rate/source data changed." if classification == "rate_source_change"
            else (new or old or {}).get("cr_reference")
            or "The selected authoritative ODS material/process list changed; no CR reference was present in the source row."
        ),
    }
    key_payload = {
        "change_type": change_type,
        "classification": classification,
        "before": before,
        "after": after,
        "field_changes": fields or {},
    }
    item["change_key"] = hashlib.sha256(json.dumps(key_payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
    return item


def _structural_change_type(fields: dict[str, Any], old: dict[str, Any], new: dict[str, Any]) -> str:
    if old.get("process_list") != new.get("process_list"):
        return "item_moved_between_process_lists"
    changed_names = set(fields)
    if "optional_item_group_1" in changed_names:
        return "option_group_changed"
    if "in_use_status" in changed_names:
        return "line_usage_changed"
    if changed_names & {"material_to_cut", "material_used", "material"}:
        return "line_material_changed"
    if changed_names & {"dimension_to_cut_mm", "dimension_to_cut_inches", "length", "width", "thickness"}:
        return "line_dimension_changed"
    if changed_names & {"qty", "quantity"}:
        return "line_quantity_changed"
    return "existing_line_modified"


def compare_semantic_snapshots(
    previous_snapshot: dict[str, Any] | None,
    current_snapshot: dict[str, Any],
) -> dict[str, Any]:
    """Compare accepted business state with current ODS semantics, never cell position."""
    if previous_snapshot is None:
        return {
            "baseline_exists": False,
            "previous_snapshot_key": None,
            "current_semantic_hash": current_snapshot.get("semantic_hash"),
            "changes": [],
            "ambiguities": [],
            "change_count": 0,
            "owner_review_required": False,
            "reason": "No accepted snapshot exists yet; accepting this run will establish the initial baseline.",
        }
    if previous_snapshot.get("semantic_hash") == current_snapshot.get("semantic_hash"):
        return {
            "baseline_exists": True,
            "previous_snapshot_key": previous_snapshot.get("snapshot_key"),
            "previous_semantic_hash": previous_snapshot.get("semantic_hash"),
            "current_semantic_hash": current_snapshot.get("semantic_hash"),
            "changes": [],
            "ambiguities": [],
            "change_count": 0,
            "owner_review_required": False,
            "reason": "Semantic costing content is unchanged from the accepted snapshot.",
        }
    changes: list[dict[str, Any]] = []
    ambiguities: list[dict[str, Any]] = []

    old_rates = previous_snapshot.get("content", {}).get("rate_state", {})
    new_rates = current_snapshot.get("content", {}).get("rate_state", {})
    for material in sorted(set(old_rates) & set(new_rates)):
        old, new = old_rates.get(material), new_rates.get(material)
        old_rate = _decimal(old.get("effective_rate_per_kg")) if old else None
        new_rate = _decimal(new.get("effective_rate_per_kg")) if new else None
        if old is not None and new is not None and old_rate == new_rate and old.get("status") == new.get("status") and old.get("source") == new.get("source") and old.get("rate_date") == new.get("rate_date") and old.get("selected_rate_basis") == new.get("selected_rate_basis"):
            continue
        previous_lines = [
            line for line in previous_snapshot.get("content", {}).get("process_lists", {}).get("5. Material Cut List Price", [])
            if line.get("status") == "active" and _string(line.get("fields", {}).get("material_to_cut")) == material
        ]
        current_lines = [
            line for line in current_snapshot.get("content", {}).get("process_lists", {}).get("5. Material Cut List Price", [])
            if line.get("status") == "active" and _string(line.get("fields", {}).get("material_to_cut")) == material
        ]
        previous_groups: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
        current_groups: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
        for line in previous_lines:
            previous_groups[_identity_tuple(line)].append(line)
        for line in current_lines:
            current_groups[_identity_tuple(line)].append(line)
        stable_pairs = [
            (previous_groups[key][0], current_groups[key][0])
            for key in set(previous_groups) & set(current_groups)
            if len(previous_groups[key]) == len(current_groups[key]) == 1
            and _line_comparable_state(previous_groups[key][0]) == _line_comparable_state(current_groups[key][0])
        ]
        for previous_line, current_line in stable_pairs:
            kg = _decimal(current_line.get("weight_kg"))
            if kg is None:
                grams = _decimal(current_line.get("fields", {}).get("total_grams"))
                kg = grams / Decimal(1000) if grams is not None else None
            delta = kg * (new_rate - old_rate) if kg is not None and old_rate is not None and new_rate is not None else None
            key_payload = [material, _identity_tuple(current_line), old, new]
            changes.append({
                "change_key": hashlib.sha256(json.dumps(key_payload, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":")).encode("utf-8")).hexdigest(),
                "change_type": "rate_source_data_changed",
                "classification": "rate_source_change",
                "process_list": current_line.get("process_list"),
                "material": material,
                "previous_state": old,
                "current_state": new,
                "field_changes": {
                    "effective_rate_per_kg": {"previous": _json_value(old_rate), "current": _json_value(new_rate)},
                    "source_row": {"previous": old.get("source_row") if old else None, "current": new.get("source_row") if new else None},
                    "rate_date": {"previous": old.get("rate_date") if old else None, "current": new.get("rate_date") if new else None},
                    "selected_rate_basis": {"previous": old.get("selected_rate_basis") if old else None, "current": new.get("selected_rate_basis") if new else None},
                },
                "cost_impact": _json_value(delta),
                "reason": "The refreshed authoritative RawSteel or SteelRateLog state changed for this unchanged active costing line.",
                "source_evidence": {"previous": old.get("source_evidence") if old else None, "current": new.get("source_evidence") if new else None},
                "affected_lines": [{"previous": _line_source_state(previous_line), "current": _line_source_state(current_line)}],
            })
        if not stable_pairs:
            # Preserve a source-level change when every consuming line is new,
            # removed, modified, or ambiguous. Its companion structural item
            # carries the line-specific state and total line cost impact.
            changes.append({
                "change_key": hashlib.sha256(f"rate|{material}|{previous_snapshot.get('semantic_hash')}|{current_snapshot.get('semantic_hash')}".encode("utf-8")).hexdigest(),
                "change_type": "rate_source_data_changed",
                "classification": "rate_source_change",
                "process_list": None,
                "material": material,
                "previous_state": old,
                "current_state": new,
                "field_changes": {
                    "effective_rate_per_kg": {"previous": _json_value(old_rate), "current": _json_value(new_rate)},
                    "source_row": {"previous": old.get("source_row") if old else None, "current": new.get("source_row") if new else None},
                    "rate_date": {"previous": old.get("rate_date") if old else None, "current": new.get("rate_date") if new else None},
                    "selected_rate_basis": {"previous": old.get("selected_rate_basis") if old else None, "current": new.get("selected_rate_basis") if new else None},
                },
                "cost_impact": None,
                "reason": "The refreshed authoritative rate source changed; affected line structure is separately recorded or requires identity resolution.",
                "source_evidence": {"previous": old.get("source_evidence") if old else None, "current": new.get("source_evidence") if new else None},
                "affected_lines": [],
            })

    old_lists = previous_snapshot.get("content", {}).get("process_lists", {})
    new_lists = current_snapshot.get("content", {}).get("process_lists", {})
    all_names = sorted(set(old_lists) | set(new_lists))
    old_all = [line for name in all_names for line in old_lists.get(name, [])]
    new_all = [line for name in all_names for line in new_lists.get(name, [])]
    paired, removed, added, ambiguous = _pair_structural_lines(old_all, new_all)
    ambiguities.extend(ambiguous)
    for old, new, match_kind, evidence in paired:
        old_state, new_state = _line_comparable_state(old), _line_comparable_state(new)
        fields = {
            name: {"previous": old_state.get(name), "current": new_state.get(name)}
            for name in sorted(set(old_state) | set(new_state))
            if old_state.get(name) != new_state.get(name)
        }
        if old.get("status") != new.get("status"):
            fields["in_use_status"] = {"previous": old.get("status"), "current": new.get("status")}
        for context in ("remarks", "date", "cr_log"):
            old_value = old.get("fields", {}).get(context)
            new_value = new.get("fields", {}).get(context)
            if old_value != new_value:
                fields[context] = {"previous": old_value, "current": new_value}
        if old["process_list"] != new["process_list"]:
            changes.append(_semantic_change("item_moved_between_process_lists", "design_structure_change", old, new, fields=fields, match_kind=match_kind, match_evidence=evidence))
        elif fields:
            change_type = _structural_change_type(fields, old, new)
            changes.append(_semantic_change(change_type, "design_structure_change", old, new, fields=fields, match_kind=match_kind, match_evidence=evidence))

    for old in removed:
        changes.append(_semantic_change("costing_line_removed", "design_structure_change", old, None, match_kind="unmatched"))
    for new in added:
        changes.append(_semantic_change("costing_line_added", "design_structure_change", None, new, match_kind="unmatched"))

    old_summary = previous_snapshot.get("content", {}).get("summary_structure", {})
    new_summary = current_snapshot.get("content", {}).get("summary_structure", {})
    old_groups, new_groups = set(old_summary.get("option_groups", [])), set(new_summary.get("option_groups", []))
    for group in sorted(new_groups - old_groups):
        changes.append({"change_key": hashlib.sha256(f"option-added|{group}".encode("utf-8")).hexdigest(), "change_type": "option_group_added", "classification": "design_structure_change", "previous_state": None, "current_state": {"option_group": group}, "field_changes": {}, "cost_impact": None, "reason": "A new option group appeared in the refreshed selected workbook."})
    for group in sorted(old_groups - new_groups):
        changes.append({"change_key": hashlib.sha256(f"option-removed|{group}".encode("utf-8")).hexdigest(), "change_type": "option_group_removed", "classification": "design_structure_change", "previous_state": {"option_group": group}, "current_state": None, "field_changes": {}, "cost_impact": None, "reason": "An option group disappeared from the refreshed selected workbook."})

    old_parameters = previous_snapshot.get("content", {}).get("parameters", {})
    new_parameters = current_snapshot.get("content", {}).get("parameters", {})
    for name in sorted(set(old_parameters) | set(new_parameters)):
        if old_parameters.get(name) == new_parameters.get(name):
            continue
        changes.append({
            "change_key": hashlib.sha256(f"parameter|{name}|{old_parameters.get(name)}|{new_parameters.get(name)}".encode("utf-8")).hexdigest(),
            "change_type": "costing_parameter_changed",
            "classification": "other_costing_structure_change",
            "field": name,
            "previous_state": old_parameters.get(name),
            "current_state": new_parameters.get(name),
            "field_changes": {name: {"previous": old_parameters.get(name), "current": new_parameters.get(name)}},
            "cost_impact": None,
            "reason": "A costing selector or shared process rate changed in the selected authoritative workbook.",
        })
    old_parameter_evidence = previous_snapshot.get("content", {}).get("parameter_evidence", {})
    new_parameter_evidence = current_snapshot.get("content", {}).get("parameter_evidence", {})
    for name in sorted(set(old_parameter_evidence) | set(new_parameter_evidence)):
        old_formula = (old_parameter_evidence.get(name) or {}).get("formula")
        new_formula = (new_parameter_evidence.get(name) or {}).get("formula")
        if old_formula == new_formula:
            continue
        changes.append({
            "change_key": hashlib.sha256(f"parameter-formula|{name}|{old_formula}|{new_formula}".encode("utf-8")).hexdigest(),
            "change_type": "costing_formula_changed",
            "classification": "other_costing_structure_change",
            "field": name,
            "previous_state": old_formula,
            "current_state": new_formula,
            "field_changes": {"formula": {"previous": old_formula, "current": new_formula}},
            "cost_impact": None,
            "reason": "A selected-workbook costing formula changed and requires semantic review.",
        })

    def summary_values(summary: dict[str, Any]) -> dict[tuple[str, str], list[dict[str, Any]]]:
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for item in summary.get("items", []):
            grouped[(item.get("line_label", ""), item.get("cost_or_option_group", ""))].append(item)
        return grouped

    old_summary_items, new_summary_items = summary_values(old_summary), summary_values(new_summary)
    for key in sorted(set(old_summary_items) | set(new_summary_items)):
        old_items, new_items = old_summary_items.get(key, []), new_summary_items.get(key, [])
        if len(old_items) > 1 or len(new_items) > 1:
            ambiguities.append({
                "change_type": "ambiguous_summary_identity",
                "classification": "owner_review_required",
                "summary_key": list(key),
                "previous_candidates": old_items,
                "current_candidates": new_items,
                "reason": "The summary label and option heading do not uniquely identify a costing cell.",
            })
            continue
        old_item, new_item = (old_items[0] if old_items else None), (new_items[0] if new_items else None)
        if old_item is None or new_item is None:
            continue
        label, group = key
        if old_item.get("value") != new_item.get("value") and not (label.casefold() == "grand total" and group.casefold() == "amount rs"):
            old_amount, new_amount = _decimal(old_item.get("value")), _decimal(new_item.get("value"))
            changes.append({
                "change_key": hashlib.sha256(f"summary|{label}|{group}|{old_item.get('value')}|{new_item.get('value')}".encode("utf-8")).hexdigest(),
                "change_type": "option_cost_or_summary_value_changed",
                "classification": "other_costing_structure_change",
                "previous_state": old_item,
                "current_state": new_item,
                "field_changes": {"value": {"previous": old_item.get("value"), "current": new_item.get("value")}},
                "cost_impact": _json_value(new_amount - old_amount) if old_amount is not None and new_amount is not None else None,
                "reason": "A named costing or option-group amount changed in Total Summary; the line-level cause is preserved separately when identifiable.",
            })
        if old_item.get("formula") != new_item.get("formula"):
            changes.append({
                "change_key": hashlib.sha256(f"summary-formula|{label}|{group}|{old_item.get('formula')}|{new_item.get('formula')}".encode("utf-8")).hexdigest(),
                "change_type": "summary_formula_changed",
                "classification": "other_costing_structure_change",
                "previous_state": old_item,
                "current_state": new_item,
                "field_changes": {"formula": {"previous": old_item.get("formula"), "current": new_item.get("formula")}},
                "cost_impact": None,
                "reason": "A named Total Summary calculation formula changed.",
            })

    old_materials = previous_snapshot.get("content", {}).get("material_master_state", {})
    new_materials = current_snapshot.get("content", {}).get("material_master_state", {})
    for material in sorted(set(old_materials) & set(new_materials)):
        old_fields = old_materials[material].get("fields", {})
        new_fields = new_materials[material].get("fields", {})
        field_changes = {
            name: {"previous": old_fields.get(name), "current": new_fields.get(name)}
            for name in sorted(set(old_fields) | set(new_fields))
            if old_fields.get(name) != new_fields.get(name)
        }
        if field_changes:
            changes.append({
                "change_key": hashlib.sha256(f"rawsteel-input|{material}|{field_changes}".encode("utf-8")).hexdigest(),
                "change_type": "material_costing_factor_changed",
                "classification": "other_costing_structure_change",
                "material": material,
                "previous_state": old_materials[material],
                "current_state": new_materials[material],
                "field_changes": field_changes,
                "cost_impact": None,
                "reason": "RawSteel material identity or weight-conversion inputs changed for a material used in the selected costing.",
            })

    previous_total = _decimal(old_summary.get("grand_total"))
    current_total = _decimal(new_summary.get("grand_total"))
    return {
        "baseline_exists": True,
        "previous_snapshot_key": previous_snapshot.get("snapshot_key"),
        "previous_semantic_hash": previous_snapshot.get("semantic_hash"),
        "current_semantic_hash": current_snapshot.get("semantic_hash"),
        "changes": changes,
        "ambiguities": ambiguities,
        "change_count": len(changes),
        "owner_review_required": bool(ambiguities),
        "costing_total": {
            "previous": _json_value(previous_total),
            "current": _json_value(current_total),
            "difference": _json_value(current_total - previous_total) if previous_total is not None and current_total is not None else None,
        },
    }


def resolve_semantic_ambiguities(
    comparison: dict[str, Any], decisions: dict[str, str] | None = None
) -> dict[str, Any]:
    """Apply explicit owner choices to uncertain candidate pairs for this acceptance only."""
    decisions = decisions or {}
    resolved = deepcopy(comparison)
    changes = list(resolved.get("changes", []))
    still_ambiguous: list[dict[str, Any]] = []
    used_previous: set[str] = set()
    used_current: set[str] = set()
    line_ambiguities = [
        item for item in resolved.get("ambiguities", [])
        if item.get("change_type") == "ambiguous_line_match"
    ]
    still_ambiguous.extend(
        item for item in resolved.get("ambiguities", [])
        if item.get("change_type") != "ambiguous_line_match"
    )
    # Allocate explicit same-line pairings first. A separate decision on a
    # competing candidate then removes only the unallocated endpoint(s).
    for ambiguity in line_ambiguities:
        if decisions.get(_string(ambiguity.get("candidate_pair_key"))) != "pair":
            continue
        pair_key = _string(ambiguity.get("candidate_pair_key"))
        old = ambiguity.get("previous_candidate") or {}
        new = ambiguity.get("current_candidate") or {}
        old_ref = _string(old.get("content_reference"))
        new_ref = _string(new.get("content_reference"))
        if old_ref in used_previous or new_ref in used_current:
            still_ambiguous.append({**ambiguity, "reason": "An owner decision attempted to use a line more than once; choose a one-to-one match."})
            continue
        old_fields = old.get("fields", {})
        new_fields = new.get("fields", {})
        field_changes = {
            name: {"previous": old_fields.get(name), "current": new_fields.get(name)}
            for name in sorted(set(old_fields) | set(new_fields))
            if old_fields.get(name) != new_fields.get(name)
            and name not in _PROCESS_COST_FIELDS | _PROCESS_CONTEXT_FIELDS
        }
        change_type = _structural_change_type(field_changes, old, new)
        old_cost, new_cost = _decimal(old.get("current_cost")), _decimal(new.get("current_cost"))
        changes.append({
            "change_key": hashlib.sha256(f"owner-pair|{pair_key}|{old_ref}|{new_ref}".encode("utf-8")).hexdigest(),
            "change_type": change_type,
            "classification": "design_structure_change",
            "match_kind": "owner_confirmed_pair",
            "previous_state": old,
            "current_state": new,
            "field_changes": field_changes,
            "cost_impact": _json_value(new_cost - old_cost) if old_cost is not None and new_cost is not None else None,
            "match_evidence": ambiguity.get("match_evidence", {}),
            "owner_resolution": "paired_as_same_design_line",
            "cr_reference": {"previous": old.get("cr_reference"), "current": new.get("cr_reference")},
            "reason": new.get("cr_reference") or "Owner confirmed these ODS rows represent the same design line.",
        })
        used_previous.add(old_ref)
        used_current.add(new_ref)

    for ambiguity in line_ambiguities:
        pair_key = _string(ambiguity.get("candidate_pair_key"))
        decision = decisions.get(pair_key, "")
        if decision == "pair":
            # A conflicting pair has already been retained in still_ambiguous.
            continue
        if decision != "separate":
            still_ambiguous.append(ambiguity)
            continue
        old = ambiguity.get("previous_candidate") or {}
        new = ambiguity.get("current_candidate") or {}
        old_ref = _string(old.get("content_reference"))
        new_ref = _string(new.get("content_reference"))
        endpoints = []
        if old_ref not in used_previous:
            endpoints.append(("costing_line_removed", old, None, old_ref, used_previous))
        if new_ref not in used_current:
            endpoints.append(("costing_line_added", None, new, new_ref, used_current))
        for change_type, before, after, endpoint_ref, used in endpoints:
            changes.append({
                "change_key": hashlib.sha256(f"owner-separate|{pair_key}|{change_type}".encode("utf-8")).hexdigest(),
                "change_type": change_type,
                "classification": "design_structure_change",
                "match_kind": "owner_confirmed_separate_lines",
                "previous_state": before,
                "current_state": after,
                "field_changes": {},
                "cost_impact": None,
                "owner_resolution": "confirmed_as_separate_lines",
                "reason": "Owner confirmed the old line was removed and the new line was independently added.",
            })
            used.add(endpoint_ref)
    resolved["changes"] = changes
    resolved["ambiguities"] = still_ambiguous
    resolved["change_count"] = len(changes)
    resolved["owner_review_required"] = bool(still_ambiguous)
    return resolved


def _build_semantic_snapshot(
    *,
    product_document: OdsDocument,
    product: dict[str, Any],
    raw_materials: list[dict[str, Any]],
    rate_log_rows: list[dict[str, Any]],
    active_rates: dict[str, dict[str, Any]],
    sources: dict[str, dict[str, Any]],
    observed_at: datetime,
    total_cost: Any,
) -> dict[str, Any]:
    process_lists = _configured_process_lines(product_document)
    calculated_mcl = {row["source_row"]: row for row in product["mcl_rows"]}
    for line in process_lists.get("5. Material Cut List Price", []):
        parsed = calculated_mcl.get(line["source_row"])
        if parsed is None:
            continue
        calculation = parsed.get("independent_calculation")
        if calculation is not None:
            line["current_cost"] = _json_value(calculation.get("grand_total"))
            line["weight_kg"] = _json_value(
                (_decimal(calculation.get("total_grams")) or Decimal(0)) / Decimal(1000)
            )
        else:
            line["current_cost"] = line.get("cost_value")
            line["weight_kg"] = None
    summary_structure = _extract_total_summary_semantics(product_document)
    summary_total = next((
        item.get("value") for item in summary_structure["items"]
        if item.get("line_label", "").casefold() == "grand total"
        and item.get("cost_or_option_group", "").casefold() == "amount rs"
    ), total_cost)
    summary_structure["grand_total"] = _json_value(summary_total)
    used_materials = sorted({
        _string(line.get("fields", {}).get("material_to_cut") or line.get("fields", {}).get("material_used"))
        for rows in process_lists.values() for line in rows if line.get("status") == "active"
        if _string(line.get("fields", {}).get("material_to_cut") or line.get("fields", {}).get("material_used"))
    })
    raw_hash = sources.get("raw_steel", {}).get("sha256")
    rate_hash = sources.get("rate_log_dump", {}).get("sha256")
    raw_by_name = {row["unique_item_list"]: row for row in raw_materials if row.get("unique_item_list")}
    rate_state: dict[str, Any] = {}
    for material in used_materials:
        rate = active_rates.get(material)
        if rate is None:
            rate_state[material] = {
                "status": "missing_or_unresolved",
                "effective_rate_per_kg": None,
                "source_row": None,
                "source_hash": rate_hash,
                "source_evidence": {"material": material, "raw_steel_sha256": raw_hash, "rate_log_sha256": rate_hash},
            }
            continue
        selected_rate_rows = {
            row_number for row_number in (
                rate.get("selected_rate_source_row"),
                rate.get("last_rate_log_row"),
            ) if row_number is not None
        }
        selected_rate_evidence = [
            row for row in rate_log_rows
            if row.get("material") == material and row.get("source_row") in selected_rate_rows
        ]
        rate_state[material] = {
            "status": rate.get("display_status", "available"),
            "effective_rate_per_kg": _json_value(rate.get("display_rate_per_kg", rate.get("final_rate_per_kg"))),
            "source_row": rate.get("selected_rate_source_row") or rate.get("raw_steel_source_row"),
            "source": rate.get("rate_source"),
            "rate_before_safety_margin": _json_value(rate.get("selected_rate_before_safety_margin")),
            "safety_margin_per_kg": _json_value(rate.get("safety_margin_per_kg")),
            "rate_date": rate.get("selected_rate_date"),
            "selected_rate_basis": rate.get("selected_rate_basis"),
            "source_hash": rate_hash if rate.get("rate_source") == "rate_log" else raw_hash,
            "raw_steel_row": rate.get("raw_steel_source_row"),
            "matching_rate_row_count": rate.get("matching_rate_row_count", 0),
            "source_evidence": {
                "material": material,
                "raw_steel_sha256": raw_hash,
                "rate_log_sha256": rate_hash,
                "raw_steel_row": rate.get("raw_steel_source_row"),
                "selected_rate_source_row": rate.get("selected_rate_source_row"),
                "last_rate_log_row": rate.get("last_rate_log_row"),
                "maximum_rate_log_row": rate.get("maximum_rate_log_row"),
                "rate_log_rows": sorted(selected_rate_rows),
                "raw_steel_values": _json_value({
                    key: value for key, value in raw_by_name.get(material, {}).items()
                    if key not in {"cells"}
                }),
                "rate_log_values": _json_value([
                    {key: value for key, value in row.items() if key not in {"cells"}}
                    for row in selected_rate_evidence
                ]),
                "raw_steel_cells": _compact_cell_evidence(raw_by_name.get(material, {}).get("cells", {})),
                "rate_log_cells": [_compact_cell_evidence(row.get("cells", {})) for row in selected_rate_evidence],
            },
        }
    content = {
        "process_lists": process_lists,
        "rate_state": rate_state,
        "material_master_state": {
            material: {
                "fields": {
                    key: _json_value(raw_by_name[material].get(key))
                    for key in ("material_type", "size", "kg_factor", "grams_per_inch")
                },
                "source_evidence": {
                    "source_row": raw_by_name[material].get("source_row"),
                    "source_hash": raw_hash,
                    "cells": _compact_cell_evidence(raw_by_name[material].get("cells", {})),
                },
            }
            for material in used_materials if material in raw_by_name
        },
        "summary_structure": summary_structure,
        "parameters": {
            name: _json_value(value)
            for name, value in product.get("parameters", {}).items()
            if name != "cells"
        },
        "parameter_evidence": _compact_cell_evidence(product.get("parameters", {}).get("cells", {})),
        "total_cost": _json_value(summary_total),
        "mcl_total": _json_value(total_cost),
    }

    # Source coordinates, row numbers, and MCL IDs are retained as evidence but
    # excluded from semantic identity so inserting/moving rows cannot fabricate
    # a business change.
    hash_lists: dict[str, list[dict[str, Any]]] = {}
    for list_name, rows in process_lists.items():
        hash_rows = []
        for line in rows:
            source_cells = line.get("source_cells", {})
            hash_rows.append({
                "status": line.get("status"),
                "identity_values": line.get("identity_values", {}),
                "fields": line.get("fields", {}),
                "formulae": {
                    name: re.sub(r"(\$?[A-Z]{1,3}\$?)\d+", r"\1#", cell.get("formula") or "")
                    for name, cell in source_cells.items() if cell.get("formula")
                },
                "current_cost": line.get("current_cost", line.get("cost_value")),
            })
        hash_lists[list_name] = sorted(
            hash_rows,
            key=lambda item: json.dumps(item, sort_keys=True, ensure_ascii=False, separators=(",", ":")),
        )
    hash_rates = {
        material: {
            key: state.get(key)
            for key in (
                "status", "effective_rate_per_kg", "source", "rate_before_safety_margin",
                "safety_margin_per_kg", "rate_date", "selected_rate_basis",
            )
        }
        for material, state in rate_state.items()
    }
    hash_materials = {
        material: state.get("fields", {})
        for material, state in content["material_master_state"].items()
    }
    hash_content = {
        "process_lists": hash_lists,
        "rate_state": hash_rates,
        "material_master_state": hash_materials,
        "summary_structure": [
            {
                **{key: value for key, value in item.items() if key not in {"source_cell", "formula"}},
                "formula": re.sub(r"(\$?[A-Z]{1,3}\$?)\d+", r"\1#", item.get("formula") or ""),
            }
            for item in summary_structure.get("items", [])
        ],
        "option_groups": sorted(summary_structure.get("option_groups", [])),
        "parameters": content["parameters"],
        "parameter_formulas": {
            name: re.sub(r"(\$?[A-Z]{1,3}\$?)\d+", r"\1#", cell.get("formula") or "")
            for name, cell in content["parameter_evidence"].items()
        },
        "total_cost": content["total_cost"],
        "mcl_total": content["mcl_total"],
    }
    semantic_hash = hashlib.sha256(
        json.dumps(hash_content, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "version": 1,
        "observed_at": observed_at.isoformat(),
        "semantic_hash": semantic_hash,
        "source_hashes": {
            name: sources[name].get("sha256")
            for name in ("selected_workbook_saved", "raw_steel", "rate_log_dump")
        },
        "source_evidence": sources,
        "content": content,
    }


def build_current_costing_review(
    selected_product_path: Path,
    refreshed_product_path: Path,
    raw_steel_path: Path,
    rate_dump_path: Path,
    accepted_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a refreshed, read-only line calculation and cache-parity review."""
    observed_at = datetime.now().astimezone()
    saved_document = read_ods(selected_product_path)
    current_document = read_ods(refreshed_product_path)
    saved_product = extract_product_workbook(saved_document)
    product = extract_product_workbook(current_document)
    raw_materials, raw_issues = extract_raw_steel(read_ods(raw_steel_path, {"RawSteel"}))
    dump_rows, dump_issues = extract_rate_log(read_ods(rate_dump_path, {"SteelRateLog"}))

    selector = product["parameters"]["selector"]
    current_rates, rate_resolution_issues = resolve_material_rates(raw_materials, dump_rows, selector)
    saved_rates, _saved_rate_issues = resolve_material_rates(raw_materials, saved_product["steel_log"], selector)
    blocking_rate_findings = dump_issues + rate_resolution_issues
    blocked_materials = apply_rate_display_controls(
        current_rates, dump_rows, blocking_rate_findings, observed_at.date()
    )
    if _string(selector).strip() not in {"Latest Rate", "Max Rate", "Maximum Rate"}:
        rate_resolution_issues.append({
            "code": "UNSUPPORTED_RATE_SELECTOR",
            "value": _json_value(selector),
            "message": "The selected workbook's rate selector is not recognized; no rate may be used until reviewed.",
        })
        for material in sorted({
            _string(row["material_to_cut"])
            for row in product["mcl_rows"] if row["status"] == "active"
        }):
            resolution = current_rates.get(material)
            if resolution is not None:
                resolution["display_status"] = "blocked_user_resolution_required"
                resolution["display_rate_per_kg"] = None
                resolution["display_issue_codes"] = ["UNSUPPORTED_RATE_SELECTOR"]
                resolution["display_blocking_source_rows"] = []
            blocked_materials.append({
                "material": material,
                "issue_codes": ["UNSUPPORTED_RATE_SELECTOR"],
                "source_rows": [],
                "display_rate_per_kg": None,
                "scope": "all_workbooks_using_material",
            })
    blocked_by_material = {item["material"]: item for item in blocked_materials}
    for material, blocked_item in blocked_by_material.items():
        blocked_item["source_cells"] = sorted({
            _string(issue.get("source_cell"))
            for issue in blocking_rate_findings
            if issue.get("material") == material and issue.get("source_cell")
        })

    formula = None
    for row in product["iron_rows"]:
        formula = next((
            cell["formula"] for cell in row["cells"].values()
            if cell["formula"] and "$D$2" in cell["formula"] and "$H$" in cell["formula"]
        ), None)
        if formula:
            break
    lookup_end = _parse_range_end(formula)

    # Re-run the exact selected workbook's formulas against the current
    # refreshed source snapshot. Cached line values remain separate evidence.
    current_product = deepcopy(product)
    parity_issues, parity = compare_product_workbook(
        raw_materials, dump_rows, current_product, current_rates, lookup_end
    )
    saved_product_for_parity = deepcopy(saved_product)
    saved_parity_issues, saved_parity = compare_product_workbook(
        raw_materials, saved_product["steel_log"], saved_product_for_parity, saved_rates, lookup_end
    )
    _saved_by_key, _current_by_key, snapshot_match_issues = _match_mcl_snapshot_rows(
        saved_product["mcl_rows"], current_product["mcl_rows"]
    )
    snapshot_changes = _compare_mcl_snapshot_content(
        saved_product["mcl_rows"], current_product["mcl_rows"]
    )

    active_materials = {
        _string(row["material_to_cut"])
        for row in current_product["mcl_rows"] if row["status"] == "active"
    }
    rate_changes = []
    for material in sorted(active_materials):
        old = saved_rates.get(material)
        new = current_rates.get(material)
        if old is None or new is None or old["final_rate_per_kg"] == new["final_rate_per_kg"]:
            continue
        affected = [
            row for row in current_product["mcl_rows"]
            if row["status"] == "active" and _string(row["material_to_cut"]) == material
        ]
        weight_kg = _decimal_sum([
            _decimal(row.get("independent_calculation", {}).get("total_grams")) / Decimal(1000)
            for row in affected
            if row.get("independent_calculation", {}).get("total_grams") is not None
        ])
        delta = None
        if new["display_status"] == "available" and old["final_rate_per_kg"] is not None and new["final_rate_per_kg"] is not None:
            delta = weight_kg * (new["final_rate_per_kg"] - old["final_rate_per_kg"])
        rate_changes.append({
            "material": material,
            "previous_rate_per_kg": _json_value(old["final_rate_per_kg"]),
            "current_rate_per_kg": None if new["display_status"] != "available" else _json_value(new["display_rate_per_kg"]),
            "previous_source_row": old["selected_rate_source_row"] or old["raw_steel_source_row"],
            "current_source_row": new["selected_rate_source_row"] or new["raw_steel_source_row"],
            "current_rate_date": new["selected_rate_date"],
            "affected_mcl_source_rows": [row["source_row"] for row in affected],
            "estimated_active_material_cost_delta": _json_value(delta),
            "display_status": new["display_status"],
        })

    saved_lines_by_key, _, _ = _match_mcl_snapshot_rows(saved_product["mcl_rows"], [])
    lines = []
    blocked_active_rows = []
    for row in current_product["mcl_rows"]:
        material = _string(row["material_to_cut"])
        rate = current_rates.get(material)
        is_active = row["status"] == "active"
        blocked = is_active and (
            material in blocked_by_material or "independent_calculation" not in row
        )
        unclassified = row["status"] == "unexpected"
        if blocked:
            blocked_active_rows.append(row["source_row"])
        previous_row = saved_lines_by_key.get(tuple(row["identity_key"]))
        cached_fields = (
            "total_inches", "total_grams", "rate_per_kg", "material_cost",
            "transport", "unloading", "fabrication", "grand_total",
        )
        cached_current = {
            field: row["cells"][field]["cached_value"] for field in cached_fields
        }
        cached_saved = ({
            field: previous_row["cells"][field]["cached_value"] for field in cached_fields
        } if previous_row else None)
        source_cells = deepcopy(row["cells"])
        if blocked or unclassified:
            for values in (cached_current, cached_saved):
                if values is not None:
                    for field in ("rate_per_kg", "material_cost", "grand_total"):
                        values[field] = None
            for field in ("rate_per_kg", "material_cost", "grand_total"):
                if field in source_cells:
                    source_cells[field]["cached_value"] = None
                    source_cells[field]["cached_display"] = None
        calculation = row.get("independent_calculation") if is_active and not blocked else None
        lines.append({
            "source_row": row["source_row"],
            "status": row["status"],
            "identity_key": list(row["identity_key"]),
            "machine_piece_description": _json_value(row["machine_piece_description"]),
            "material_to_cut": material,
            "dimension_mm": _json_value(row["dimension_mm"]),
            "dimension_inches": _json_value(row["dimension_inches"]),
            "quantity": _json_value(row["quantity"]),
            "optional_item_group_1": _json_value(row["optional_item_group_1"]),
            "date": _json_value(row["date"]),
            "remarks": _json_value(row["remarks"]),
            "cr_log": _json_value(row["cr_log"]),
            "item_line_no": _json_value(row["item_line_no"]),
            "source_cells": source_cells,
            "rate_status": rate.get("display_status") if rate else "unavailable",
            "rate": None if blocked or unclassified or rate is None else {
                "per_kg": _json_value(rate.get("display_rate_per_kg")),
                "basis": rate.get("selected_rate_basis"),
                "source": rate.get("rate_source"),
                "source_row": rate.get("selected_rate_source_row") or rate.get("raw_steel_source_row"),
                "raw_steel_source_row": rate.get("raw_steel_source_row"),
                "matching_rate_row_count": rate.get("matching_rate_row_count", 0),
                "date": rate.get("selected_rate_date"),
                "safety_margin_per_kg": _json_value(rate.get("safety_margin_per_kg")),
            },
            "rate_warning": blocked_by_material.get(material) if blocked else None,
            "calculation_status": "invalid_usage_flag" if unclassified else "historical" if not is_active else "blocked" if blocked else "calculated",
            "current_calculation": None if calculation is None else {
                **{name: _json_value(value) for name, value in calculation.items()},
                "rounded_grand_total_up": _json_value(row["business_output_rounding"]["rounded_grand_total"]),
            },
            "refreshed_workbook_cached_values": cached_current,
            "saved_workbook_cached_values": cached_saved,
            "cached_formulae": {
                field: row["cells"][field]["formula"] for field in cached_fields
            },
            "cached_grand_total_difference": (
                _json_value(
                    (_decimal(cached_current["grand_total"]) - calculation["grand_total"])
                ) if calculation and _decimal(cached_current["grand_total"]) is not None else None
            ),
        })

    active_rows = [row for row in current_product["mcl_rows"] if row["status"] == "active"]
    unclassified_rows = [row["source_row"] for row in current_product["mcl_rows"] if row["status"] == "unexpected"]
    blocked = bool(blocked_active_rows or unclassified_rows)

    def review_summary_comparison(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result = []
        for item in rows:
            row = {key: _json_value(value) for key, value in item.items()}
            if blocked and item.get("field") in {"material_cost", "grand_total"}:
                for key in (
                    "cached_value", "sum_of_cached_line_values", "difference_from_cached_line_sum",
                    "active_only_semantic_value", "difference_from_active_only", "historical_cached_contribution",
                ):
                    row[key] = None
            result.append(row)
        return result

    totals = None if blocked else {
        name: _json_value(value)
        for name, value in parity["active_only_summary_totals"].items()
    }
    saved_summary = {
        name: cell["cached_value"] for name, cell in saved_product["summary_totals"].items()
    }
    refreshed_summary = {
        name: cell["cached_value"] for name, cell in product["summary_totals"].items()
    }
    if blocked:
        for summary_values in (saved_summary, refreshed_summary):
            for field in ("material_cost", "grand_total"):
                summary_values[field] = None
        for field in ("material_cost", "grand_total"):
            saved_parity["historical_cached_contribution"][field] = None
    if blocked:
        status = "blocked_rate_or_input_review_required"
    else:
        status = "ready_for_owner_review"
    issues = [*raw_issues, *dump_issues, *rate_resolution_issues, *parity_issues]
    historical_cache_findings = [
        {"basis": "saved_cache_historical_evidence", **issue}
        for issue in saved_parity_issues
    ]
    issues.extend(snapshot_match_issues)
    sensitive_fields = {"rate_per_kg", "material_cost", "grand_total"}
    blocked_material_names = set(blocked_by_material)
    blocked_material_names.update(
        _string(row["material_to_cut"])
        for row in current_product["mcl_rows"]
        if row["status"] == "active" and "independent_calculation" not in row
    )
    for finding in [*issues, *historical_cache_findings]:
        finding_material = _string(finding.get("material"))
        blocked_sensitive_finding = finding_material in blocked_material_names or (blocked and not finding_material)
        if blocked_sensitive_finding and finding.get("field") in sensitive_fields:
            for key in ("cached_value", "expected_value", "independent_value", "difference"):
                if key in finding:
                    finding[key] = None
    sources = {
        "selected_workbook_saved": _file_metadata(selected_product_path, observed_at),
        "selected_workbook_refreshed_copy": _file_metadata(refreshed_product_path, observed_at),
        "raw_steel": _file_metadata(raw_steel_path, observed_at),
        "rate_log_dump": _file_metadata(rate_dump_path, observed_at),
    }
    sources["selected_workbook_saved"]["sheet_count"] = len(saved_document.sheets)
    sources["selected_workbook_saved"]["external_reference_count"] = None
    sources["selected_workbook_refreshed_copy"]["sheet_count"] = len(current_document.sheets)
    sources["selected_workbook_refreshed_copy"]["external_reference_count"] = None
    # The disposable copy is deleted as soon as the request finishes. Identify
    # it by the selected source path without returning a dangling temp path.
    sources["selected_workbook_refreshed_copy"]["path"] = str(selected_product_path.resolve())
    semantic_snapshot = _build_semantic_snapshot(
        product_document=current_document,
        product=current_product,
        raw_materials=raw_materials,
        rate_log_rows=dump_rows,
        active_rates=current_rates,
        sources=sources,
        observed_at=observed_at,
        total_cost=refreshed_summary.get("grand_total"),
    )
    semantic_comparison = compare_semantic_snapshots(accepted_snapshot, semantic_snapshot)
    from app.workflow_review import processing_review_evidence
    workflow_evidence = processing_review_evidence(semantic_snapshot, semantic_comparison, set(current_document.sheets), accepted_snapshot)
    return {
        "schema_version": "milestone2-costing-review-2",
        "status": status,
        "read_only": True,
        "observed_at": observed_at.isoformat(),
        "authority": "Explorer-selected product workbook for its assigned Model Codes, with current RawSteel and SteelRateLog source snapshots.",
        "snapshot_comparison_basis": "Current refreshed semantic costing structure and resolved rates compared with the last accepted Safari snapshot. Saved workbook caches remain historical evidence only.",
        "matching_key_fields": [
            "Machine Piece Description", "Material to Cut", "Dimension to Cut (mm)",
            "Quantity Nos", "Optional Item Group 1",
        ],
        "sources": sources,
        "refresh": {"required": True, "verified": True},
        "parameters": {
            "selector": _json_value(selector),
            "lookup_range_end_row": lookup_end,
            "transport_per_kg": _json_value(product["parameters"]["transport_per_kg"]),
            "unloading_per_kg": _json_value(product["parameters"]["unloading_per_kg"]),
            "fabrication_per_kg": _json_value(product["parameters"]["fabrication_per_kg"]),
            "business_total_tolerance": _json_value(BUSINESS_TOTAL_TOLERANCE),
            "rounding": "Round each active MCL line grand total upward to a whole rupee; keep exact values for parity.",
        },
        "summary": {
            "active_line_count": len(active_rows),
            "historical_line_count": len([row for row in current_product["mcl_rows"] if row["status"] == "historical"]),
            "blocked_active_line_count": len(blocked_active_rows),
            "unclassified_line_count": len(unclassified_rows),
            "active_totals": totals,
            "saved_workbook_summary_cache": saved_summary,
            "refreshed_workbook_summary_cache": refreshed_summary,
            "historical_cached_contribution": {
                name: _json_value(value)
                for name, value in saved_parity["historical_cached_contribution"].items()
            },
            "saved_cache_summary_comparison": [
                *review_summary_comparison(saved_parity["summary_comparison"])
            ],
            "current_total_vs_saved_summary": (
                _json_value(
                    parity["active_only_summary_totals"]["grand_total"]
                    - (_decimal(saved_summary.get("grand_total")) or Decimal(0))
                ) if not blocked and _decimal(saved_summary.get("grand_total")) is not None else None
            ),
            "summary_comparison": [
                *review_summary_comparison(parity["summary_comparison"])
            ],
        },
        "lines": lines,
        "rate_changes_since_saved_snapshot": rate_changes,
        "material_list_changes_after_refresh": snapshot_changes,
        "semantic_snapshot": semantic_snapshot,
        "semantic_comparison": semantic_comparison,
        "processing_evidence": workflow_evidence,
        "issues": [{key: _json_value(value) for key, value in issue.items()} for issue in issues],
        "historical_cache_findings": [
            {key: _json_value(value) for key, value in issue.items()}
            for issue in historical_cache_findings
        ],
    }


def scan_layout_variants(root: Path) -> list[dict[str, Any]]:
    """Inspect S1KHF ODS package structure/formula signatures without recalculation."""
    paths = sorted(root.rglob("*.ods"), key=lambda path: str(path).casefold())
    results: list[dict[str, Any]] = []
    target_names = {"SteelLog", "1. Iron and Steel", "5. Material Cut List Price"}
    for path in paths:
        try:
            document = read_ods(path, target_names)
            sheets = document.sheets
            item: dict[str, Any] = {
                "path": str(path.resolve()),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "size_bytes": path.stat().st_size,
                "relevant_sheets": sorted(sheets),
            }
            iron = sheets.get("1. Iron and Steel")
            if iron:
                selector = iron.cell(1, 12)
                header_row = None
                try:
                    header_row = _header_row(iron, {"Unique Item List", "Kgs per Mtr/SqMtr", "Current Rate per KG"}, 20)
                except ValueError:
                    pass
                sample_row = (header_row + 1) if header_row else 6
                sample_formulas = [
                    iron.cell(sample_row, col).formula
                    for col in range(5, 12)
                    if iron.cell(sample_row, col).formula
                ]
                source_formula = next((f for f in sample_formulas if "$D$2" in f and "$H$" in f), None)
                item["iron_contract"] = {
                    "header_row": header_row,
                    "selector": _json_value(selector.value),
                    "selector_formula": selector.formula,
                    "sample_source_lookup_end_row": _parse_range_end(source_formula),
                    "has_maxifs": any("MAXIFS" in (formula or "").upper() for formula in sample_formulas),
                    "has_last_occurrence_lookup": any("SUMPRODUCT" in (formula or "").upper() for formula in sample_formulas),
                    "has_two_rupee_safety_margin": any(re.search(r"\+\s*2(?:[;\)\s]|$)", formula or "") for formula in sample_formulas),
                    "sample_formula_cells": {
                        iron.cell(sample_row, col).coordinate: iron.cell(sample_row, col).formula
                        for col in range(5, 12) if iron.cell(sample_row, col).formula
                    },
                }
            mcl = sheets.get("5. Material Cut List Price")
            if mcl:
                header_row = None
                try:
                    header_row = _header_row(mcl, {"Machine Piece Description", "Material to Cut", "Dimension to Cut (mm)", "Quantity Nos"}, 20)
                except ValueError:
                    pass
                item["mcl_header_row"] = header_row
            steel_log = sheets.get("SteelLog")
            if steel_log:
                try:
                    log_header = _header_row(steel_log, {"Unique Item List Helper", "Unique Item List", "Rate per KG. Basic Rate Only"}, 10)
                except ValueError:
                    log_header = None
                item["steel_log_header_row"] = log_header
                item["steel_log_nonempty_row_count"] = sum(1 for rn, row in steel_log.nonempty_rows() if rn > (log_header or 0) and any(cell.value is not None for cell in row.values()))
            features = []
            contract = item.get("iron_contract")
            if contract:
                features = [
                    f"selector={contract['selector']}",
                    f"lookup_end={contract['sample_source_lookup_end_row']}",
                    f"MAXIFS={contract['has_maxifs']}",
                    f"last_occurrence={contract['has_last_occurrence_lookup']}",
                    f"safety_margin_plus_2={contract['has_two_rupee_safety_margin']}",
                ]
            item["formula_signature"] = "; ".join(features) if features else "no Iron and Steel formula signature"
            results.append(item)
        except Exception as exc:  # retain the inventory even if a file is unreadable
            results.append({"path": str(path.resolve()), "error": f"{type(exc).__name__}: {exc}"})
    return results


def classify_layout_variants(
    variants: list[dict[str, Any]], selected_workbook_path: Path | str
) -> list[dict[str, Any]]:
    """Check the selected costing workbook; inventory other files as samples only."""
    findings: list[dict[str, Any]] = []
    selected_path = Path(selected_workbook_path).resolve()
    selected = next((
        workbook for workbook in variants
        if Path(workbook.get("path", "")).resolve() == selected_path
    ), None)
    if selected is None:
        return [{
            "code": "SELECTED_PRODUCT_WORKBOOK_NOT_IN_VARIANT_SCAN",
            "path": str(selected_path),
            "review": "The selected workbook was parsed, but was not included in the configured sample-workbook inventory.",
        }]
    contract = selected.get("iron_contract")
    if not contract:
        return [{
            "code": "SELECTED_PRODUCT_WORKBOOK_CONTRACT_UNREADABLE",
            "path": str(selected_path),
            "review": "The selected workbook does not expose the expected 1. Iron and Steel formula contract.",
        }]
    selector = _string(contract.get("selector"))
    if selector not in {"Latest Rate", "Max Rate"}:
        findings.append({
            "code": "SELECTED_PRODUCT_WORKBOOK_SELECTOR_UNSUPPORTED",
            "path": str(selected_path),
            "selector": selector,
            "review": "Inspect the selected workbook's selector formula before applying the resolver.",
        })
    for workbook in variants:
        if Path(workbook.get("path", "")).resolve() != selected_path:
            # Files in the same folder may be alternate scenarios or samples.
            # Their selector/range differences do not affect selected Model Codes.
            continue
        expected_features = {
            "has_maxifs": True,
            "has_last_occurrence_lookup": True,
            "has_two_rupee_safety_margin": True,
        }
        for feature, expected in expected_features.items():
            observed = contract.get(feature)
            if observed != expected:
                findings.append({
                    "code": "SELECTED_PRODUCT_WORKBOOK_FORMULA_VARIANT",
                    "path": str(selected_path),
                    "feature": feature,
                    "observed": observed,
                    "pilot_expected": expected,
                    "review": "Inspect the selected workbook formula and confirm its costing semantics before applying the resolver.",
                })
    return findings


def _issue_counts(issues: list[dict[str, Any]]) -> dict[str, int]:
    return dict(sorted(Counter(issue["code"] for issue in issues).items()))


def _markdown_table(headers: list[str], rows: Iterable[Iterable[Any]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    for row in rows:
        cells = [str(value).replace("|", "\\|").replace("\n", " ") for value in row]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _fmt(value: Any) -> str:
    if isinstance(value, Decimal):
        return format(value, "f")
    if value is None:
        return "—"
    return str(value)


def _fmt_currency(value: Any) -> str:
    decimal_value = _decimal(value)
    if decimal_value is None:
        return "—"
    return f"{decimal_value:,.2f}"


def build_report(snapshot: dict[str, Any]) -> str:
    summary = snapshot["summary"]
    files = snapshot["sources"]
    refresh_status = snapshot["external_sheet_refresh"]
    owner_comparison = snapshot["owner_reported_total_comparison"]
    refresh_label = refresh_status["status"]
    if refresh_status["method"]:
        refresh_label += f" ({refresh_status['method']})"
    if owner_comparison["source_snapshot_matches_owner_observation"]:
        owner_total_report = (
            f"The owner confirmed that the selected pilot workbook displayed **₹{_fmt_currency(owner_comparison['owner_displayed_total'])}** at **{owner_comparison['pinned_pilot_summary_cell']}** after choosing **Update External Sheets** on **{owner_comparison['owner_observed_on']}**. The saved C7 cache is **₹{_fmt_currency(owner_comparison['pinned_pilot_summary_cached_value'])}**. Replaying active MCL lines against the matching SteelRateLog dump gives **₹{_fmt_currency(snapshot['refreshed_source_calculation']['active_only_totals']['grand_total'])}** before output rounding. The difference is explained by the two historical line values still present in the saved cache (**₹{_fmt_currency(snapshot['refreshed_source_calculation']['saved_historical_cache_contribution'])}**). Together these reconstruct **₹{_fmt_currency(snapshot['refreshed_source_calculation']['reconstructed_summary_with_saved_history'])}**, displayed as **₹{_fmt_currency(owner_comparison['owner_displayed_total'])}**. The owner total, selected workbook, and rate dump refer to the same source revisions and reconcile to displayed paise."
        )
    elif owner_comparison["current_snapshot_reconciles_to_owner_display"]:
        owner_total_report = (
            f"The owner reported C7 = **₹{_fmt_currency(owner_comparison['owner_displayed_total'])}** on **{owner_comparison['owner_observed_on']}** for pilot workbook revision `{owner_comparison['owner_pilot_workbook_sha256']}`. The current selected workbook is a later revision (`{owner_comparison['current_pilot_workbook_sha256']}`), while the SteelRateLog dump revision is unchanged. Its current C7 cache is **₹{_fmt_currency(owner_comparison['pinned_pilot_summary_cached_value'])}**; replaying active lines against the current dump gives **₹{_fmt_currency(snapshot['refreshed_source_calculation']['active_only_totals']['grand_total'])}**, and adding the two historical cached line values (**₹{_fmt_currency(snapshot['refreshed_source_calculation']['saved_historical_cache_contribution'])}**) reconstructs **₹{_fmt_currency(owner_comparison['reconstructed_refreshed_total_with_saved_historical_cache'])}** (**₹{_fmt_currency(owner_comparison['reconstructed_display_total'])}** at displayed precision). Thus the current workbook revision is reconciled to the previously reported total; the hash change itself is not a costing issue."
        )
    else:
        owner_total_report = (
            f"The owner-reported C7 value **₹{_fmt_currency(owner_comparison['owner_displayed_total'])}** was observed on **{owner_comparison['owner_observed_on']}** after **Update External Sheets**, against rate dump SHA-256 `{owner_comparison['owner_rate_dump_sha256']}` and pilot workbook SHA-256 `{owner_comparison['owner_pilot_workbook_sha256']}`. This report uses dump SHA-256 `{owner_comparison['current_rate_dump_sha256']}` and pilot workbook SHA-256 `{owner_comparison['current_pilot_workbook_sha256']}`; at least one input revision differs, so the earlier C7 value cannot be reconciled against this snapshot. Replaying active MCL lines against the current dump gives **₹{_fmt_currency(snapshot['refreshed_source_calculation']['active_only_totals']['grand_total'])}** before output rounding. Refresh the selected workbook against this source revision and record its resulting C7 before comparing totals."
        )
    quality_rows = []
    for finding in snapshot.get("rate_log_data_quality_findings", []):
        code = finding.get("code")
        if code == "RATE_LOG_INVALID_DATE":
            detail = f"Unparseable date in {finding.get('source_cell', 'date cell')}"
        elif code == "RATE_LOG_INVALID_RATE":
            detail = f"Invalid rate value: {_fmt(finding.get('value'))}"
        else:
            detail = "No exact RawSteel `Unique Item List` match"
        quality_rows.append([
            finding.get("source_row", "—"),
            finding.get("material", "—"),
            f"₹{_fmt_currency(finding.get('rate_per_kg'))}",
            finding.get("date_raw") or "—",
            detail,
        ])
    output = [
        "# Milestone 2 — S1KHF Material and Rate Pilot Parity",
        "",
        f"**Status: {summary['status']}**",
        "",
        "This report is a read-only baseline and parity result for the confirmed S1KHF pilot. It reads stored formulas/caches and separately replays current available SteelRateLog data; it does not itself open LibreOffice, refresh external links, access Google Sheets, connect to Grist, or modify any source workbook.",
        f"External-sheet refresh confirmation: **{refresh_label}**.",
        "",
        "## Source observations",
        "",
        _markdown_table(
            ["Role", "File", "SHA-256", "Modified", "Observed"],
            [[role, Path(data["path"]).name, data["sha256"], data["modified_at"], data["observed_at"]] for role, data in files.items()],
        ),
        "",
        f"- RawSteel rows: **{summary['raw_steel_record_count']}**; exact Unique Item List duplicates: **{summary['raw_steel_duplicate_count']}**.",
        f"- SteelRateLog data rows: **{summary['rate_log_record_count']}**, source rows {summary['rate_log_first_source_row']}–{summary['rate_log_last_source_row']}.",
        f"- Product SteelLog cached rows: **{summary['product_rate_log_record_count']}**; overlapping dump/import value mismatches: **{summary['product_log_mismatch_count']}**; current dump rows absent from the product cache: **{summary['product_log_missing_dump_rows']}**.",
        f"- Current-dump versus product-cache resolved-rate differences: **{summary['dump_product_rate_difference_materials']}** materials (**{summary['dump_product_rate_difference_count']}** field-level differences). The saved-workbook baseline uses its local SteelLog cache; current-cost replay uses the latest available external dump.",
        f"- Active Material Cut List rows: **{summary['active_mcl_count']}**; historical (`In Use = No`) rows: **{summary['historical_mcl_count']}**.",
        f"- Rate-log latest valid entry date: **{summary['most_recent_valid_rate_log_date']}**; dump freshness status: **{summary['freshness_status']}**.",
        f"- Current rates blocked from display pending user resolution: **{summary['rate_display_blocked_material_count']} materials** across all consuming workbooks.",
        "",
        "## Confirmed rate calculation reproduced",
        "",
        "1. Match the exact RawSteel `Unique Item List` text.",
        "2. Select the last matching SteelLog occurrence in the current source snapshot's row order for Latest Current Rate; use the RawSteel default when no rate-log row exists.",
        "3. Calculate Max Rate from non-zero entries in the current source snapshot only, falling back to the latest/default rate when none exists.",
        "4. Use the selected workbook's exact `1. Iron and Steel!L1` selector (`Latest Rate` selects latest; `Max Rate` selects maximum rate), then add its ₹2/kg safety margin.",
        "5. Keep the rate-log date from the final matching row. A no-log default has semantic `rateLogDate = null`, even when the workbook cache displays a 1900-era date.",
        "",
        f"Pilot selector: **{summary['rate_selector']}**; transport **₹{_fmt(summary['transport_per_kg'])}/kg**, unloading **₹{_fmt(summary['unloading_per_kg'])}/kg**, fabrication **₹{_fmt(summary['fabrication_per_kg'])}/kg**.",
        "",
        "The ₹2/kg is a safety margin added after selecting the logged/default material rate; it remains separate from the purchase observation. Transport, unloading, and fabrication are separate per-kg cost components.",
        "",
        "The product workbook's saved SteelLog cache may be stale. For current costing, refresh external sheets and resolve against the newest available authoritative SteelRateLog snapshot. This report keeps the saved-cache baseline for provenance and separately replays the selected workbook's formulas using the current dump; it does not open LibreOffice or refresh links itself. Semantic rate changes affecting active MCL lines are surfaced as reconciliation issues, while a file hash/mtime change alone is not.",
        "",
        "## Parity summary",
        "",
        _markdown_table(
            ["Measure", "Cached summary", "Sum of cached line values", "Delta", "Active-only calculation", "Cached history contribution"],
            [[row["field"], row["cached_value"], row["sum_of_cached_line_values"], row["difference_from_cached_line_sum"], row["active_only_semantic_value"], row["historical_cached_contribution"]] for row in summary["summary_comparison"]],
        ),
        "",
        f"The saved-workbook baseline calculation compared the **{summary['active_mcl_count']} active** lines using its embedded SteelLog snapshot. Those active cached line totals match that saved-snapshot calculation within numeric representation noise. The workbook summary cells use plain `SUM` formulas over cached line values. Their saved caches include a nonzero contribution from **{summary['historical_rows_with_nonzero_cached_cost']}** historical (`In Use = No`) rows; **{summary['historical_rows_with_blank_formula_caches']}** historical rows have formula cells with blank cached values. Historical lines are excluded from current semantic costing.",
        "",
        f"Proposed costing output rounds each active line's Grand Total Cost of Piece upward to the next whole rupee before summing; the projected active total is **₹{_fmt(summary['active_only_summary_totals']['grand_total_rounded_up_per_line'])}**. Exact components and legacy cached-formula parity remain recorded separately.",
        f"Business tolerance is **±₹{_fmt(summary['business_total_tolerance'])}** at the overall costing-total level. The `1e-9` guard is only for numeric representation in exact line comparisons; it is not the business tolerance.",
        f"## Owner-reported total after external-sheet refresh",
        "",
        owner_total_report,
        f"The current dump changes resolved rates for **{len(snapshot['refreshed_source_calculation']['active_material_rate_changes'])}** materials used by active MCL lines. The current-source formula replay is **{'provisional' if snapshot['refreshed_source_calculation']['provisional_only'] else 'not provisional for this owner-confirmed snapshot'}**; **{len(snapshot['refreshed_source_calculation']['blocked_active_materials'])}** active materials are blocked by invalid rate entries. Future-looking parsed dates are informational and do not block rates; row order controls Latest Rate. Historical MCL rows remain excluded from the semantic current-cost total even though stale cached values contribute to the legacy ODS summary.",
        _markdown_table(
            ["Active material", "Old → refreshed rate/kg", "MCL rows", "Active cost delta", "Effective rate display"],
            [[
                change["material"],
                f"₹{_fmt_currency(change['old_rate_per_kg'])} → ₹{_fmt_currency(change['new_rate_per_kg'])}",
                ", ".join(str(row) for row in change["affected_mcl_source_rows"]),
                f"₹{_fmt_currency(change['active_cost_delta'])}",
                change["effective_rate_display_status"],
            ] for change in snapshot["refreshed_source_calculation"]["active_material_rate_changes"]],
        ),
        "",
        "## Findings requiring review",
        "",
    ]
    if snapshot["issue_counts"]:
        output.append(_markdown_table(["Finding", "Count"], snapshot["issue_counts"].items()))
    else:
        output.append("No extraction or parity findings were generated.")
    product_name_findings = [
        issue for issue in snapshot["issues"]
        if issue.get("code") in {
            "PRODUCT_IRON_MATERIAL_NAME_MISMATCH",
            "PRODUCT_IRON_MISSING_MATERIAL",
        }
    ]
    if product_name_findings:
        product_name_report = "; ".join(
            f"RawSteel row {issue.get('raw_steel_row')} `{issue.get('material')}` has no exact product Iron row"
            + (f" (spacing candidate: `{', '.join(issue['product_material_candidates'])}`)" if issue.get("product_material_candidates") else "")
            + (" and is not used by active MCL rows" if not issue.get("active_mcl_source_rows") else f"; active MCL rows {issue['active_mcl_source_rows']} depend on it")
            for issue in product_name_findings
        )
    else:
        product_name_report = "No RawSteel-to-product Iron material-name mismatches remain."
    output.extend([
        "",
        f"Blocking findings: **{summary['blocking_issue_count']}**. Nonblocking observations: **{summary['nonblocking_finding_count']}**. {product_name_report}",
    ])
    output.extend([
        "",
        "### Rate-log data-quality entries",
        "",
        _markdown_table(["Source row", "Material", "Rate/kg", "Date text", "Finding"], quality_rows)
        if quality_rows else "No invalid rate-log values, dates, or unmapped material names were found.",
        "",
        "",
        "### Source revision differences (not issues by themselves)",
        "",
        f"The newer SteelRateLog dump has **{summary['product_log_missing_dump_rows']}** rows absent from the product-local SteelLog cache and **{summary['dump_product_rate_difference_count']}** changed resolved-rate fields across **{summary['dump_product_rate_difference_materials']}** materials. A source revision by itself is informational; changed effective rates for materials used by active selected-workbook lines are emitted separately as costing reconciliation issues.",
        "",
        "### Material lookup coverage observations",
        "",
        f"The selected workbook's RawSteel lookup range omits **{summary['raw_steel_beyond_lookup_count']}** master records, producing **{len(summary['lookup_range_coverage_findings'])}** missing cached factor/default-rate cells. Of these, **{summary['lookup_range_coverage_warning_count']}** are unused-range warnings and **{summary['lookup_range_coverage_blocker_count']}** affect active MCL materials. Unused out-of-range rows are consistency warnings, not blockers; an active material that depends on a missing lookup remains blocking. All RawSteel rows are preserved as master evidence.",
    ])
    output.extend([
        "",
        "### Material lookup range",
        "",
        f"The pilot material-name list contains {summary['raw_steel_record_count']} RawSteel entries through source row {summary['raw_steel_last_source_row']}. Its lookup formulas use RawSteel rows 2–{summary['product_lookup_end_row']}. **{summary['raw_steel_beyond_lookup_count']}** RawSteel records are beyond that lookup range. The detailed snapshot marks which cached lookup cells are missing or `#N/A`, and whether an active Material Cut List line is affected.",
        f"Active MCL lines affected by the lookup range: **{summary['active_materials_beyond_lookup_count']} distinct materials**.",
        "",
        "### Rate-log ordering and current latest behavior",
        "",
        f"The owner confirmed that the last matching source row determines Latest Rate, including when dates regress or tie. The snapshot retains **{summary['rate_log_materials_with_date_regressions']}** materials with row/date regressions, **{summary['rate_log_last_row_older_than_max_date_count']}** whose last row is older than another row's date, and **{summary['rate_log_materials_with_same_date_rate_conflicts']}** with different same-date rates as diagnostic observations; these do not override the row-order rule. **{summary['future_rate_log_date_count']}** dump rows have dates after the observation date. They remain informational because date locale/format differences are possible, and they do not block rate display. Max Rate is calculated from non-zero rates available in this refreshed dump snapshot; archived/deleted source rows are outside that maximum.",
        "",
        "### Dump freshness",
        "",
        f"The dump was last modified at {files['rate_log_dump']['modified_at']}; the latest valid Date of Entry is {summary['most_recent_valid_rate_log_date']}. No fixed refresh cadence or stale-age threshold is currently set; the snapshot records source mtime and observation time. Scripted dump acquisition is a future automation task.",
        "",
        "## S1KHF workbook variant scan",
        "",
        _markdown_table(
            ["Workbook", "Role", "Relevant sheets", "Selector", "Lookup end", "Range review", "Formula signature"],
            [[Path(row["path"]).name, "Selected default" if row.get("selected_for_pilot") else "Sample/alternate", ", ".join(row.get("relevant_sheets", [])), (row.get("iron_contract") or {}).get("selector", "—"), (row.get("iron_contract") or {}).get("sample_source_lookup_end_row", "—"), "warning: consistency only" if row.get("lookup_range_warning") else "—", row.get("formula_signature", row.get("error", "—"))] for row in snapshot["variant_scan"]],
        ),
        "",
        f"**Alternate workbook lookup-range warnings:** {summary['alternate_workbook_lookup_range_warning_count']}. These are consistency follow-ups only and do not block the selected workbook's costing. If an alternate workbook is later selected for its Model Codes, recheck whether an active line depends on a row outside its lookup range.",
        "",
        "The explorer-selected workbook is the default costing source for its assigned Model Codes. Other ODS files in the folder may be maximum-rate samples or scenario workbooks; they are inventoried for context, but their formulas do not affect the selected Model Codes and do not create issues by themselves.",
        "",
        _markdown_table(
            ["Finding", "Workbook", "Observed value", "Pilot baseline", "Review"],
            [[finding["code"], Path(finding["path"]).name, finding.get("selector", finding.get("lookup_end_row", finding.get("feature"))), finding.get("pilot_selector", finding.get("pilot_lookup_end_row", finding.get("pilot_expected"))), finding["review"]] for finding in snapshot["variant_findings"]],
        ) if snapshot["variant_findings"] else "No unsupported formula variant was identified in the selected workbook; other selector/range variants remain listed above as sample or scenario workbooks.",
        "",
        "## Proposed Safari schema additions (not applied)",
        "",
        "- `MaterialMasterObservation`: immutable source file revision, source sheet/row/cells, exact source identity, source fields, formula/cache evidence, parser version, and observation timestamp.",
        "- `MaterialRateLogObservation`: immutable dump revision and source-row order, exact material display, basic rate, quantity, parsed/raw date, description/remarks, formula/cache readability, and source provenance.",
        "- `ProductMaterialRateResolution`: product file observation, exact ODS material identity, default/latest/max inputs, selector, safety margin, selected rate, source log row/date, fallback state, and formula/cache evidence.",
        "- `MaterialCostingPolicy`: proposed future ₹2/kg default safety margin with optional per-material overrides, effective dates, actor, and reason. The margin remains separate from the observed purchase rate.",
        "- Product costing settings: proposed unloading/fabrication defaults with per-product overrides; transport remains separate and may have supplier-specific actual observations.",
        "- `CostParityRun` and `CostParityLine`: pinned source revisions, formula/rule versions, active composite key, input values, cached and independent values, differences, issue references, and acceptance state/tolerance.",
        "- `ExternalDependencyObservation`: source path/reference, expected sheet/range, observed file hash/mtime, cached-formula status, and freshness decision.",
        "",
        "No live schema plan or write was run. The live schema review step remains required before any Safari persistence.",
        "",
        "## Pilot parity acceptance criteria",
        "",
        "1. All three source files have stable SHA-256, size, mtime, and observation metadata in the snapshot.",
        "2. RawSteel and SteelRateLog rows preserve exact source identities, source row/cells, formula text, and cached values; invalid/duplicate/missing fields are explicitly accounted for.",
        "3. Product SteelLog agrees row-by-row with the dump for all six imported fields, or every difference has a reviewed explanation. Differences between snapshots are source revisions, not issues by themselves.",
        "4. Every material used in an active Material Cut List line resolves to exactly one RawSteel row and a valid weight factor/rate path, or has a review issue.",
        "5. The independent resolver reproduces active line costs from the latest refreshed external source snapshot, while preserving the saved-workbook cache as a separate baseline. Business output rounds each active line Grand Total Cost of Piece upward to a whole rupee.",
        "6. External sheets are refreshed before a current costing comparison. If a refresh cannot be performed, cached data is labeled stale and cannot silently stand in for current rates. Confirmed invalid entries block effective rates until user resolution; future-looking parsed dates are informational.",
        "7. Representative workbook variants are inventoried and any selector, range, formula, or layout differences are either supported explicitly or kept outside the accepted scope.",
        "8. A reviewer can trace each reported difference to source file, sheet, row, cell, formula, cached value, and independent value in the JSON snapshot.",
        "9. No source ODS, `Costing-New`, or live Safari Manufacturing data changes during extraction; any future Safari schema additions are applied only through the explicit schema-review process.",
        "",
        "## Remaining owner questions",
        "",
    ])
    for question in snapshot["open_questions"]:
        output.append(f"- {question}")
    output.extend([
        "",
        "Full extracted rows, per-cell formulas/caches, both dump and product-cache resolver inputs/outputs, refreshed-source line calculations, semantic rate-change issues, differences, validation findings, and variant inventory are in `MILESTONE_2_SEMANTIC_SNAPSHOT.json`.",
        "",
    ])
    return "\n".join(output)


def _snapshot_json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Unsupported JSON value: {type(value).__name__}")


def run_pilot(
    raw_steel_path: Path = DEFAULT_RAW_STEEL,
    rate_dump_path: Path = DEFAULT_RATE_DUMP,
    pilot_path: Path = DEFAULT_PILOT,
    variant_root: Path | None = None,
    observed_at: datetime | None = None,
    external_refresh_confirmed: bool = False,
) -> dict[str, Any]:
    observed_at = observed_at or datetime.now().astimezone()
    raw_doc = read_ods(raw_steel_path, {"RawSteel"})
    dump_doc = read_ods(rate_dump_path, {"SteelRateLog"})
    product_doc = read_ods(pilot_path, {"SteelLog", "1. Iron and Steel", "5. Material Cut List Price"})
    raw_materials, raw_issues = extract_raw_steel(raw_doc)
    dump_rows, dump_issues = extract_rate_log(dump_doc)
    product = extract_product_workbook(product_doc)
    metadata = {
        "raw_steel": _file_metadata(raw_steel_path, observed_at),
        "rate_log_dump": _file_metadata(rate_dump_path, observed_at),
        "pilot_product_workbook": _file_metadata(pilot_path, observed_at),
    }
    dump_resolution, rate_issues = resolve_material_rates(raw_materials, dump_rows, product["parameters"]["selector"])
    product_resolution, product_rate_issues = resolve_material_rates(
        raw_materials, product["steel_log"], product["parameters"]["selector"]
    )
    ordering_codes = {
        "RATE_LOG_ROW_ORDER_DATE_REGRESSION",
        "RATE_LOG_LAST_ROW_NOT_MAX_DATE",
        "RATE_LOG_SAME_DATE_MULTIPLE_RATES",
    }
    rate_ordering_observations = [issue for issue in rate_issues if issue.get("code") in ordering_codes]
    for finding in rate_ordering_observations:
        finding["classification"] = "informational_last_matching_row_policy_accepted"
    rate_issues = [issue for issue in rate_issues if issue.get("code") not in ordering_codes]
    source_revision_findings = compare_dump_to_product_log(dump_rows, product["steel_log"])
    source_revision_findings.extend(compare_dump_rates_to_product_snapshot(dump_resolution, product_resolution))
    for finding in source_revision_findings:
        finding["classification"] = "source_revision_difference_not_an_issue_by_itself"
    import_issues = validate_product_log_occurrence_keys(product["steel_log"])
    formula = None
    for row in product["iron_rows"]:
        formula = next((cell["formula"] for cell in row["cells"].values() if cell["formula"] and "$D$2" in cell["formula"] and "$H$" in cell["formula"]), None)
        if formula:
            break
    lookup_end = _parse_range_end(formula)
    parity_issues, parity_summary = compare_product_workbook(raw_materials, product["steel_log"], product, product_resolution, lookup_end)
    issues = raw_issues + dump_issues + rate_issues + import_issues + parity_issues
    names = {row["unique_item_list"] for row in raw_materials}
    future_date_rows = []
    for row in dump_rows:
        if row["date_iso"] and date.fromisoformat(row["date_iso"]) > observed_at.date():
            future_date_rows.append(row)
    future_date_observations = [
        {
            "classification": "informational_date_only_row_order_authoritative",
            "material": row["material"],
            "source_row": row["source_row"],
            "date_raw": row["date_raw"],
            "date_iso": row["date_iso"],
            "observed_on": observed_at.date().isoformat(),
        }
        for row in future_date_rows
    ]
    blocking_rate_validation = dump_issues + rate_issues + product_rate_issues
    rate_display_blocked = apply_rate_display_controls(
        dump_resolution,
        dump_rows,
        blocking_rate_validation,
        observed_at.date(),
    )
    apply_rate_display_controls(
        product_resolution,
        dump_rows,
        blocking_rate_validation,
        observed_at.date(),
    )
    # The workbook's embedded SteelLog can be stale. Re-run the selected
    # workbook's active-line formulas against the latest available external
    # source snapshot, retaining the embedded-cache replay separately above.
    refreshed_product = deepcopy(product)
    refreshed_source_issues, refreshed_source_summary = compare_product_workbook(
        raw_materials, dump_rows, refreshed_product, dump_resolution, lookup_end
    )
    active_materials = sorted({
        _string(row["material_to_cut"])
        for row in product["mcl_rows"]
        if row["status"] == "active"
    })
    active_material_rate_changes: list[dict[str, Any]] = []
    for material in active_materials:
        previous = product_resolution.get(material)
        current = dump_resolution.get(material)
        if (
            not previous
            or not current
            or previous["final_rate_per_kg"] is None
            or current["final_rate_per_kg"] is None
            or previous["final_rate_per_kg"] == current["final_rate_per_kg"]
        ):
            continue
        affected_lines = [
            row for row in refreshed_product["mcl_rows"]
            if row["status"] == "active" and _string(row["material_to_cut"]) == material
        ]
        rate_delta = current["final_rate_per_kg"] - previous["final_rate_per_kg"]
        line_cost_delta = _decimal_sum([
            row["independent_calculation"]["total_grams"] / Decimal(1000) * rate_delta
            for row in affected_lines
            if "independent_calculation" in row
        ])
        active_material_rate_changes.append({
            "code": "ACTIVE_MATERIAL_RATE_CHANGED_AFTER_EXTERNAL_REFRESH",
            "material": material,
            "old_rate_per_kg": previous["final_rate_per_kg"],
            "new_rate_per_kg": current["final_rate_per_kg"],
            "rate_delta_per_kg": rate_delta,
            "old_source_row": previous["selected_rate_source_row"] or previous["raw_steel_source_row"],
            "new_source_row": current["selected_rate_source_row"] or current["raw_steel_source_row"],
            "new_rate_date": current["selected_rate_date"],
            "affected_mcl_source_rows": [row["source_row"] for row in affected_lines],
            "active_cost_delta": line_cost_delta,
            "effective_rate_display_status": current.get("display_status"),
            "requires_reconciliation": True,
        })
    refreshed_active_total = refreshed_source_summary["active_only_summary_totals"]["grand_total"]
    saved_historical_cache_contribution = parity_summary["historical_cached_contribution"]["grand_total"]
    reconstructed_current_summary = refreshed_active_total + saved_historical_cache_contribution
    blocked_materials_by_name = {item["material"]: item for item in rate_display_blocked}
    blocked_active_materials = [
        blocked_materials_by_name[material]
        for material in active_materials
        if material in blocked_materials_by_name
    ]
    # Parsed dates after the observation date are retained as informational
    # evidence. They do not override source-row ordering or block rate display.
    issues = raw_issues + dump_issues + rate_issues + import_issues + parity_issues + active_material_rate_changes
    latest_valid_date = max((
        date.fromisoformat(row["date_iso"])
        for row in dump_rows
        if row["date_iso"] and date.fromisoformat(row["date_iso"]) <= observed_at.date()
    ), default=None)
    material_with_regression = {
        issue.get("material") for issue in rate_ordering_observations
        if issue.get("code") == "RATE_LOG_ROW_ORDER_DATE_REGRESSION"
    }
    max_date_disagreement = {
        issue.get("material") for issue in rate_ordering_observations
        if issue.get("code") == "RATE_LOG_LAST_ROW_NOT_MAX_DATE"
    }
    same_date_rate_conflicts = {
        issue.get("material") for issue in rate_ordering_observations
        if issue.get("code") == "RATE_LOG_SAME_DATE_MULTIPLE_RATES"
    }
    observed_age_days = (observed_at.date() - latest_valid_date).days if latest_valid_date else None
    fallback_materials = sum(1 for row in dump_resolution.values() if row["fallback_used"])
    cached_1900_dates = sum(1 for row in product_resolution.values() if row.get("cached_date_artifact"))
    dump_formula_cells = [
        cell for row in dump_rows for cell in row["cells"].values() if cell["formula"]
    ]
    summary = {
        "status": "READY FOR OWNER REVIEW",
        "raw_steel_record_count": len(raw_materials),
        "raw_steel_duplicate_count": sum(1 for issue in raw_issues if issue["code"] == "RAW_STEEL_DUPLICATE_UNIQUE_ITEM_LIST"),
        "raw_steel_last_source_row": max((row["source_row"] for row in raw_materials), default=None),
        "raw_steel_beyond_lookup_count": sum(1 for row in raw_materials if lookup_end is not None and row["source_row"] > lookup_end),
        "active_materials_beyond_lookup_count": len({
            _string(row["material_to_cut"])
            for row in product["mcl_rows"]
            if row["status"] == "active"
            and lookup_end is not None
            and (raw_source := next((material["source_row"] for material in raw_materials if material["unique_item_list"] == _string(row["material_to_cut"])), None)) is not None
            and raw_source > lookup_end
        }),
        "rate_log_record_count": len(dump_rows),
        "rate_log_first_source_row": min((row["source_row"] for row in dump_rows), default=None),
        "rate_log_last_source_row": max((row["source_row"] for row in dump_rows), default=None),
        "product_rate_log_record_count": len(product["steel_log"]),
        "product_log_mismatch_count": sum(1 for issue in source_revision_findings if issue["code"] == "PRODUCT_STEEL_LOG_VALUE_MISMATCH"),
        "product_log_missing_dump_rows": sum(1 for issue in source_revision_findings if issue["code"] == "PRODUCT_STEEL_LOG_ROW_COVERAGE_MISMATCH" and issue["dump_present"] and not issue["product_present"]),
        "dump_product_rate_difference_count": sum(1 for issue in source_revision_findings if issue["code"] == "DUMP_PRODUCT_RATE_SNAPSHOT_DIFFERENCE"),
        "dump_product_rate_difference_materials": len({issue["material"] for issue in source_revision_findings if issue["code"] == "DUMP_PRODUCT_RATE_SNAPSHOT_DIFFERENCE"}),
        "active_mcl_count": parity_summary["active_mcl_count"],
        "historical_mcl_count": parity_summary["historical_mcl_count"],
        "mcl_total_rows": parity_summary["mcl_total_rows"],
        "rate_selector": _string(product["parameters"]["selector"]),
        "transport_per_kg": _decimal(product["parameters"]["transport_per_kg"]),
        "unloading_per_kg": _decimal(product["parameters"]["unloading_per_kg"]),
        "fabrication_per_kg": _decimal(product["parameters"]["fabrication_per_kg"]),
        "product_lookup_end_row": lookup_end,
        "most_recent_valid_rate_log_date": latest_valid_date.isoformat() if latest_valid_date else None,
        "most_recent_rate_log_age_days": observed_age_days,
        "future_rate_log_date_count": len(future_date_rows),
        "rate_display_blocked_material_count": len(rate_display_blocked),
        "business_total_tolerance": BUSINESS_TOTAL_TOLERANCE,
        "active_total_within_tolerance_of_cached_summary": all(
            abs(_decimal(row["difference_from_active_only"]) or Decimal(0)) <= BUSINESS_TOTAL_TOLERANCE
            for row in parity_summary["summary_comparison"]
            if row["field"] == "grand_total"
        ),
        "freshness_status": "observed_no_threshold",
        "rate_log_materials_with_date_regressions": len(material_with_regression),
        "rate_log_last_row_older_than_max_date_count": len(max_date_disagreement),
        "rate_log_materials_with_same_date_rate_conflicts": len(same_date_rate_conflicts),
        "active_mcl_latest_row_not_max_date_count": len({
            _string(row["material_to_cut"]) for row in product["mcl_rows"]
            if row["status"] == "active" and _string(row["material_to_cut"]) in max_date_disagreement
        }),
        "fallback_material_count": fallback_materials,
        "fallback_cached_1900_date_count": cached_1900_dates,
        "dump_formula_cell_count": len(dump_formula_cells),
        "dump_formula_cache_unreadable_count": sum(1 for row in dump_rows if not row["cached_values_readable"]),
        "workbook_mcl_summary_formula_contains_all_rows": all(
            "SUM(" in (cell["formula"] or "").upper() and "SUMIF" not in (cell["formula"] or "").upper()
            for cell in product["summary_totals"].values()
        ),
        **parity_summary,
    }
    open_questions = []
    if not external_refresh_confirmed:
        open_questions.append("Confirm the selected workbook's external sheets were refreshed before using current-cost results; saved caches are not current rate authority.")
    open_questions.append("Complete the invalid SteelRateLog entry definition beyond nonnumeric prices and other confirmed invalid values; affected material rates remain hidden until user resolution. Future-looking dates alone are informational.")
    variant_scan = scan_layout_variants(variant_root) if variant_root else []
    for workbook in variant_scan:
        workbook["selected_for_pilot"] = (
            Path(workbook.get("path", "")).resolve() == pilot_path.resolve()
        )
        contract = workbook.get("iron_contract") or {}
        lookup_end_row = contract.get("sample_source_lookup_end_row")
        if (
            isinstance(lookup_end_row, int)
            and summary["raw_steel_last_source_row"]
            and lookup_end_row < summary["raw_steel_last_source_row"]
            and not workbook["selected_for_pilot"]
        ):
            workbook["lookup_range_warning"] = {
                "code": "WORKBOOK_LOOKUP_RANGE_SHORT_OF_RAW_STEEL",
                "classification": "warning_consistency_only",
                "lookup_end_row": lookup_end_row,
                "raw_steel_last_source_row": summary["raw_steel_last_source_row"],
                "blocking": False,
                "review": "Extend this alternate workbook's lookup range for consistency when convenient; it does not affect the selected Model Codes. Recheck dependencies if this workbook is later selected.",
            }
    summary["alternate_workbook_lookup_range_warning_count"] = sum(
        1 for workbook in variant_scan if workbook.get("lookup_range_warning")
    )
    variant_findings = classify_layout_variants(variant_scan, pilot_path) if variant_root else []
    issues.extend(variant_findings)
    blocking_issues = [issue for issue in issues if issue.get("blocking", True)]
    summary["blocking_issue_count"] = len(blocking_issues)
    summary["nonblocking_finding_count"] = len(issues) - len(blocking_issues)
    if blocking_issues:
        summary["status"] = "NOT ACCEPTED - blocking rate issues and parity findings remain open"
    elif not external_refresh_confirmed:
        summary["status"] = "NOT ACCEPTED - external-sheet refresh is not confirmed"
    elif open_questions:
        summary["status"] = "READY FOR OWNER REVIEW - invalid-entry policy remains open"
    elif issues:
        summary["status"] = "READY FOR OWNER REVIEW - nonblocking observations remain"
    active_grand_total = parity_summary["active_only_summary_totals"]["grand_total"]
    cached_grand_total = next(
        row["cached_value"] for row in parity_summary["summary_comparison"]
        if row["field"] == "grand_total"
    )
    owner_displayed_total = OWNER_REPORTED_TOTAL
    owner_source_matches = (
        metadata["rate_log_dump"]["sha256"] == OWNER_REPORTED_RATE_DUMP_SHA256
        and metadata["pilot_product_workbook"]["sha256"] == OWNER_REPORTED_PILOT_SHA256
    )
    current_cached_display = _decimal(cached_grand_total).quantize(Decimal("0.01"))
    displayed_total_difference = owner_displayed_total - active_grand_total.quantize(Decimal("0.01"))
    reconstructed_display_total = reconstructed_current_summary.quantize(Decimal("0.01"))
    current_snapshot_reconciles_to_owner_display = (
        metadata["rate_log_dump"]["sha256"] == OWNER_REPORTED_RATE_DUMP_SHA256
        and current_cached_display == owner_displayed_total
        and reconstructed_display_total == owner_displayed_total
        and abs(owner_displayed_total - active_grand_total.quantize(Decimal("0.01"))) <= BUSINESS_TOTAL_TOLERANCE
    )
    reported_comparison = {
        "source": "owner_reported",
        "owner_displayed_total": owner_displayed_total,
        "owner_observed_on": OWNER_REPORTED_ON.isoformat(),
        "owner_rate_dump_sha256": OWNER_REPORTED_RATE_DUMP_SHA256,
        "owner_pilot_workbook_sha256": OWNER_REPORTED_PILOT_SHA256,
        "current_rate_dump_sha256": metadata["rate_log_dump"]["sha256"],
        "current_pilot_workbook_sha256": metadata["pilot_product_workbook"]["sha256"],
        "source_snapshot_matches_owner_observation": owner_source_matches,
        "current_snapshot_reconciles_to_owner_display": current_snapshot_reconciles_to_owner_display,
        "reported_difference_from_saved_cache_active_total": displayed_total_difference,
        "compared_with_active_total": active_grand_total,
        "pinned_pilot_summary_cell": "C7",
        "pinned_pilot_summary_cached_value": _decimal(cached_grand_total),
        "current_snapshot_c7_display_value": current_cached_display,
        "current_snapshot_c7_matches_owner_display": current_cached_display == owner_displayed_total,
        "pinned_pilot_summary_difference_from_active_total": _decimal(cached_grand_total) - active_grand_total,
        "refreshed_active_total": refreshed_active_total,
        "refreshed_active_total_difference_from_owner_display": owner_displayed_total - refreshed_active_total,
        "reconstructed_refreshed_total_with_saved_historical_cache": reconstructed_current_summary,
        "reconstructed_display_total": reconstructed_display_total,
        "reconstruction_difference_at_display_precision": owner_displayed_total - reconstructed_display_total,
        "exceeds_business_tolerance_after_reconciliation": abs(
            owner_displayed_total - refreshed_active_total
        ) > BUSINESS_TOTAL_TOLERANCE,
        "reconciled": current_snapshot_reconciles_to_owner_display,
    }
    provisional_reasons = []
    if not external_refresh_confirmed:
        provisional_reasons.append("external_sheet_refresh_not_confirmed")
    if blocked_active_materials:
        provisional_reasons.append("active_material_rates_blocked_pending_user_resolution")
    rate_rows_by_source = {row["source_row"]: row for row in dump_rows}
    rate_log_data_quality_findings = []
    for issue in issues:
        if issue.get("code") not in {
            "RATE_LOG_INVALID_RATE",
            "RATE_LOG_INVALID_DATE",
            "RATE_LOG_MATERIAL_ABSENT_FROM_RAW_STEEL",
        }:
            continue
        source_row = issue.get("source_row")
        source_record = rate_rows_by_source.get(source_row, {})
        rate_log_data_quality_findings.append({
            **issue,
            "rate_per_kg": source_record.get("rate_per_kg"),
            "date_raw": source_record.get("date_raw"),
            "date_iso": source_record.get("date_iso"),
            "quantity_ordered": source_record.get("quantity_ordered"),
            "description": source_record.get("description"),
            "remarks": source_record.get("remarks"),
        })
    snapshot = {
        "schema_version": "milestone2-semantic-snapshot-1",
        "read_only": True,
        "observed_at": observed_at.astimezone().isoformat(),
        "authority": {
            "raw_steel": "MaterialCostDB.ods / RawSteel — interim material identity, weight factors, default rates",
            "rate_log": "Spares List - Master.ods / SteelRateLog — interim rate observations consumed by costing workbooks",
            "product_workbook": "The workbook selected in the explorer is the default costing source for its assigned Model Codes; other files are samples/scenarios unless explicitly selected",
            "safety_margin_policy": "Current selected-workbook behavior adds ₹2/kg; future design may use a ₹2 default with per-material override, kept separate from purchase-rate observations",
            "refresh_policy": "The costing system must initiate or require and verify external-sheet refresh before every current costing comparison; treat saved caches as historical evidence only. If refresh is unavailable or unverified, label a replay against the newest captured authoritative source snapshot provisional and do not accept it. No fixed automated acquisition cadence or stale-age threshold; scripted source acquisition is future work",
            "rounding_policy": "round_each_active_mcl_line_grand_total_up_to_whole_rupee_before_summing; preserve_source_precision_for_legacy_parity",
            "latest_rate_policy": "last matching source row wins even when entry dates regress or tie",
            "rate_log_snapshot_policy": "Latest Rate uses the last matching row in the current refreshed source snapshot; Max Rate uses the maximum non-zero rate among rows present in that snapshot only. Archived/deleted source rows are outside the current maximum; Grist may retain them for history but must distinguish retained history from the current costing snapshot",
            "date_display_policy": "preserve raw and parsed dates; future-looking dates are informational and do not block rates because locale/format differences can change interpretation; row order determines Latest Rate",
            "rate_issue_display_policy": "confirmed invalid rate entries block effective rate display across all workbooks using that material until user resolution; future-looking dates alone do not block",
            "business_total_tolerance": "±₹100",
            "upstream_google_sheets_accessed": False,
            "grist_accessed": False,
        },
        "sources": metadata,
        "summary": summary,
        "raw_steel": raw_materials,
        "steel_rate_log": dump_rows,
        "product_workbook": product,
        "rate_resolutions_from_current_dump": dump_resolution,
        "rate_resolutions_from_product_cached_steel_log": product_resolution,
        "external_sheet_refresh": {
            "required_for_current_costing": True,
            "status": "owner_confirmed" if external_refresh_confirmed else "not_confirmed",
            "method": "Update External Sheets" if external_refresh_confirmed else None,
        },
        "refreshed_source_calculation": {
            "source": "current SteelRateLog dump as refreshed-source snapshot proxy; selected workbook supplies MCL rows, formulas, and selector",
            "active_only_totals": refreshed_source_summary["active_only_summary_totals"],
            "saved_historical_cache_contribution": saved_historical_cache_contribution,
            "reconstructed_summary_with_saved_history": reconstructed_current_summary,
            "blocked_active_materials": blocked_active_materials,
            "provisional_only": bool(provisional_reasons),
            "provisional_reasons": provisional_reasons,
            "active_material_rate_changes": active_material_rate_changes,
            "active_lines": [
                {
                    "source_row": row["source_row"],
                    "identity_key": row["identity_key"],
                    "material": row["material_to_cut"],
                    "resolved_rate": row.get("resolved_rate"),
                    "calculation": row.get("independent_calculation"),
                    "business_output_rounding": row.get("business_output_rounding"),
                }
                for row in refreshed_product["mcl_rows"]
                if row["status"] == "active"
            ],
        },
        "issues": issues,
        "rate_log_data_quality_findings": rate_log_data_quality_findings,
        "issue_counts": _issue_counts(issues),
        "rate_log_ordering_observations": rate_ordering_observations,
        "future_date_observations": future_date_observations,
        "rate_display_blocked_materials": rate_display_blocked,
        "owner_reported_total_comparison": reported_comparison,
        "source_revision_findings": source_revision_findings,
        "variant_scan": variant_scan,
        "variant_findings": variant_findings,
        "open_questions": open_questions,
        "master_unique_names": sorted(names),
    }
    return snapshot


def write_report(snapshot: dict[str, Any], output_dir: Path = DEFAULT_OUTPUT) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "MILESTONE_2_SEMANTIC_SNAPSHOT.json"
    report_path = output_dir / "MILESTONE_2_PILOT_PARITY_REPORT.md"
    json_path.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False, default=_snapshot_json_default) + "\n", encoding="utf-8")
    report_path.write_text(build_report(snapshot), encoding="utf-8")
    return report_path, json_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only Milestone 2 ODS semantic extraction and S1KHF parity report.")
    parser.add_argument("--raw-steel", type=Path, default=DEFAULT_RAW_STEEL)
    parser.add_argument("--rate-dump", type=Path, default=DEFAULT_RATE_DUMP)
    parser.add_argument("--pilot", type=Path, default=DEFAULT_PILOT)
    parser.add_argument("--variant-root", type=Path, default=PRODUCTS_ROOT / "S1KHF")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--external-refresh-confirmed",
        action="store_true",
        help="Owner has already refreshed the selected workbook's external sheets before this read-only snapshot.",
    )
    args = parser.parse_args()
    snapshot = run_pilot(
        args.raw_steel,
        args.rate_dump,
        args.pilot,
        args.variant_root,
        external_refresh_confirmed=args.external_refresh_confirmed,
    )
    report, snapshot_path = write_report(snapshot, args.output_dir)
    print(f"Report: {report}")
    print(f"Snapshot: {snapshot_path}")
    print(json.dumps({"status": snapshot["summary"]["status"], "summary": snapshot["summary"], "issue_counts": snapshot["issue_counts"]}, indent=2, default=_snapshot_json_default))


if __name__ == "__main__":
    main()

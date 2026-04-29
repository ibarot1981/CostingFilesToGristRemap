"""ODS workbook reading, writing, and sheet-level operations."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

try:
    from pyexcel_ods3 import get_data, save_data
except ImportError:  # pragma: no cover - exercised by users without dependencies
    get_data = None
    save_data = None

from app.config_models import SheetConfig
from app.exceptions import WorkbookError
from app.utils import display_text, is_blank, is_zero_or_blank, normalize_header, normalize_text


Row = list[Any]


@dataclass(frozen=True)
class RowInfo:
    """A row with its one-based row number."""

    number: int
    values: Row


@dataclass
class InactiveDecision:
    """Result of inactive-row classification."""

    inactive: bool
    reasons: list[str]


@dataclass
class ColumnMap:
    """Canonical field name to sheet column index lookup."""

    field_to_index: dict[str, int]
    warnings: list[str]

    def index_for(self, field: str) -> int | None:
        """Return the zero-based column index for a canonical field."""
        return self.field_to_index.get(field)

    def indexes_for(self, fields: Iterable[str]) -> dict[str, int]:
        """Return indexes for the requested canonical fields that exist."""
        return {field: idx for field in fields if (idx := self.index_for(field)) is not None}


class OdsWorkbook:
    """In-memory ODS workbook that preserves sheets and unknown columns."""

    def __init__(self, path: Path, sheets: OrderedDict[str, list[Row]]) -> None:
        self.path = path
        self.sheets = sheets

    @classmethod
    def load(cls, path: Path) -> "OdsWorkbook":
        """Load an ODS workbook from disk."""
        if get_data is None:
            raise WorkbookError("pyexcel-ods3 is not installed. Run: pip install -r requirements.txt")
        if not path.exists():
            raise WorkbookError(f"ODS file not found: {path}")
        if path.suffix.lower() != ".ods":
            raise WorkbookError(f"Expected a .ods file, got: {path.name}")
        try:
            data = get_data(str(path))
        except Exception as exc:
            raise WorkbookError(f"Could not read ODS file {path}: {exc}") from exc
        return cls(path=path, sheets=OrderedDict((name, list(rows)) for name, rows in data.items()))

    def save_as(self, path: Path) -> None:
        """Write the workbook to a new ODS file with static cell values only."""
        if save_data is None:
            raise WorkbookError("pyexcel-ods3 is not installed. Run: pip install -r requirements.txt")
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            save_data(str(path), writer_safe_sheets(self.sheets))
        except Exception as exc:
            raise WorkbookError(f"Could not write ODS file {path}: {exc}") from exc

    def require_sheet(self, name: str) -> list[Row]:
        """Return a sheet by name or raise a friendly error."""
        if name not in self.sheets:
            raise WorkbookError(f"Workbook does not contain required sheet: {name}")
        return self.sheets[name]

    def sheet_view(self, name: str, config: SheetConfig) -> "SheetView":
        """Create a SheetView for a configured sheet."""
        return SheetView(name=name, rows=self.require_sheet(name), config=config)

    def keep_only_sheets(self, sheet_names: Iterable[str]) -> None:
        """Drop every sheet except the named sheets, preserving requested order."""
        wanted = OrderedDict()
        for sheet_name in sheet_names:
            if sheet_name in self.sheets:
                wanted[sheet_name] = self.sheets[sheet_name]
        self.sheets = wanted


class SheetView:
    """Operations against a configured ODS sheet."""

    def __init__(self, name: str, rows: list[Row], config: SheetConfig) -> None:
        self.name = name
        self.rows = rows
        self.config = config
        self.header_index = config.header_row - 1
        if self.header_index < 0:
            raise WorkbookError(f"{name}: header_row must be 1 or greater.")
        if self.header_index >= len(rows):
            raise WorkbookError(f"{name}: header row {config.header_row} is outside the sheet.")

    @property
    def header(self) -> Row:
        """Return the configured header row."""
        return self.rows[self.header_index]

    def column_map(self) -> ColumnMap:
        """Map canonical configured field names to actual sheet columns."""
        normalized_headers = {
            normalize_header(header): index
            for index, header in enumerate(self.header)
            if not is_blank(header)
        }
        field_to_index: dict[str, int] = {}
        warnings: list[str] = []
        for field, aliases in self.config.aliases.items():
            candidates = [field, *aliases]
            match = None
            for candidate in candidates:
                index = normalized_headers.get(normalize_header(candidate))
                if index is not None:
                    match = index
                    break
            if match is not None:
                field_to_index[field] = match
            else:
                warnings.append(f"{self.name}: no column found for configured field '{field}'.")
        return ColumnMap(field_to_index=field_to_index, warnings=warnings)

    def data_rows(self) -> Iterable[RowInfo]:
        """Yield data rows after the configured header row."""
        for zero_based_index in range(self.header_index + 1, len(self.rows)):
            yield RowInfo(number=zero_based_index + 1, values=self.rows[zero_based_index])

    def last_non_empty_row_number(self) -> int:
        """Return the one-based last row number where any cell has a value."""
        for zero_based_index in range(len(self.rows) - 1, -1, -1):
            if any(not is_blank(value) for value in self.rows[zero_based_index]):
                return zero_based_index + 1
        return 0

    def get_value(self, row: Row, index: int | None) -> Any:
        """Safely get a cell value from a row."""
        if index is None or index >= len(row):
            return ""
        return row[index]

    def set_value(self, row: Row, index: int, value: Any) -> None:
        """Safely set a cell value, extending the row when needed."""
        while len(row) <= index:
            row.append("")
        row[index] = value

    def classify_row(self, row: Row, column_map: ColumnMap) -> InactiveDecision:
        """Classify a row as active or inactive using config rules."""
        rules = self.config.inactive_rules
        reasons: list[str] = []

        if rules.blank_rows and all(is_blank(value) for value in row):
            reasons.append("blank row")

        required_indexes = column_map.indexes_for(rules.required_any_fields)
        if required_indexes and all(is_blank(self.get_value(row, idx)) for idx in required_indexes.values()):
            fields = ", ".join(required_indexes.keys())
            reasons.append(f"none of required fields has a value ({fields})")

        qty_indexes = column_map.indexes_for(rules.qty_fields)
        if qty_indexes and all(is_zero_or_blank(self.get_value(row, idx)) for idx in qty_indexes.values()):
            fields = ", ".join(qty_indexes.keys())
            reasons.append(f"qty is blank or zero ({fields})")

        inactive_values = {normalize_text(value) for value in rules.inactive_values}
        flag_indexes = column_map.indexes_for(rules.inactive_flag_fields)
        for field, idx in flag_indexes.items():
            value = self.get_value(row, idx)
            if normalize_text(value) in inactive_values:
                reasons.append(f"inactive flag in {field}: {display_text(value)}")

        return InactiveDecision(inactive=bool(reasons), reasons=reasons)

    def remove_inactive_rows(self) -> list[dict[str, Any]]:
        """Remove inactive data rows and return an audit list."""
        column_map = self.column_map()
        kept = self.rows[: self.header_index + 1]
        removed: list[dict[str, Any]] = []
        last_value_row_number = self.last_non_empty_row_number()

        for row_info in self.data_rows():
            decision = self.classify_row(row_info.values, column_map)
            if decision.inactive:
                if not (
                    row_info.number > last_value_row_number
                    and all(is_blank(value) for value in row_info.values)
                ):
                    removed.append(
                        {
                            "sheet": self.name,
                            "row_number": row_info.number,
                            "reason": "; ".join(decision.reasons),
                        }
                    )
                continue
            kept.append(row_info.values)

        self.rows[:] = kept
        return removed

    def delete_columns(self, indexes: list[int]) -> None:
        """Delete columns from the sheet, highest index first."""
        for row in self.rows:
            for index in sorted(indexes, reverse=True):
                if index < len(row):
                    del row[index]


def writer_safe_sheets(sheets: OrderedDict[str, list[Row]]) -> OrderedDict[str, list[Row]]:
    """Return sheet data that can be written by pyexcel-ods3.

    The ODS writer cannot create a sheet with zero columns. Some costing files
    contain placeholder empty sheets, so keep those sheets with one blank cell
    in the generated file instead of failing the whole export. All values are
    copied into new lists so the output is value-only and cannot carry formula
    metadata from the source workbook.
    """
    safe: OrderedDict[str, list[Row]] = OrderedDict()
    for sheet_name, rows in sheets.items():
        if not rows or max((len(row) for row in rows), default=0) == 0:
            safe[sheet_name] = [[""]]
        else:
            safe[sheet_name] = [[static_cell_value(value) for value in row] for row in rows]
    return safe


def static_cell_value(value: Any) -> Any:
    """Return a cell value that will be written as static data, never a formula."""
    if isinstance(value, str) and value.startswith("="):
        return "'" + value
    return value

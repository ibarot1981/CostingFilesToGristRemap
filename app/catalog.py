"""Read-only discovery of costing workbooks on disk.

The catalog deliberately reads ODS packages directly instead of loading them
through LibreOffice.  This keeps the initial scan fast, does not recalculate
formulas, and cannot write back to production costing files.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
import re
from typing import Iterable
from zipfile import BadZipFile, ZipFile


SHEET_PATTERN = re.compile(rb'<table:table\b[^>]*\btable:name="([^"]+)"')
FORMULA_PATTERN = re.compile(rb'(?:table:formula|of:formula)="([^"]+)"')
EXTERNAL_REFERENCE_PATTERN = re.compile(
    r"(?:file:///|\.\./|\.\\|[A-Za-z]:[/\\])",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class CostingFile:
    """Metadata collected without changing or recalculating an ODS file."""

    relative_path: str
    name: str
    directory: str
    size_bytes: int
    modified_at: str
    sheets: tuple[str, ...]
    formula_count: int
    external_reference_count: int
    external_reference_samples: tuple[str, ...]
    readable: bool
    error: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class CatalogSummary:
    root: str
    generated_at: str
    file_count: int
    readable_count: int
    unreadable_count: int
    formula_count: int
    files_with_external_references: int
    external_reference_count: int
    top_level_counts: tuple[tuple[str, int], ...]
    common_sheets: tuple[tuple[str, int], ...]
    common_sheet_signatures: tuple[dict[str, object], ...]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def scan_costing_file(path: Path, root: Path) -> CostingFile:
    """Inspect one ODS zip package without evaluating formulas."""

    stat = path.stat()
    relative = path.relative_to(root)
    common = {
        "relative_path": str(relative),
        "name": path.name,
        "directory": str(relative.parent) if relative.parent != Path(".") else "",
        "size_bytes": stat.st_size,
        "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
    }
    try:
        with ZipFile(path) as package:
            package_names = package.namelist()
            if "content.xml" not in package_names:
                reason = (
                    "Encrypted ODS package cannot be scanned without its password"
                    if "encrypted-package" in package_names
                    else "ODS package does not contain content.xml"
                )
                return CostingFile(
                    **common,
                    sheets=(),
                    formula_count=0,
                    external_reference_count=0,
                    external_reference_samples=(),
                    readable=False,
                    error=reason,
                )
            with package.open("content.xml") as content_stream:
                sheets, formula_count, external = _scan_content_stream(content_stream)
    except (BadZipFile, OSError, RuntimeError) as exc:
        return CostingFile(
            **common,
            sheets=(),
            formula_count=0,
            external_reference_count=0,
            external_reference_samples=(),
            readable=False,
            error=f"{type(exc).__name__}: {exc}",
        )

    return CostingFile(
        **common,
        sheets=sheets,
        formula_count=formula_count,
        external_reference_count=len(external),
        external_reference_samples=external[:3],
        readable=True,
    )


def scan_costing_root(root: Path, workers: int = 8) -> tuple[CatalogSummary, list[CostingFile]]:
    """Scan every ODS workbook below *root* and return summary and detail."""

    resolved_root = root.resolve()
    if not resolved_root.is_dir():
        raise FileNotFoundError(f"Costing root does not exist: {resolved_root}")
    paths = sorted(resolved_root.rglob("*.ods"), key=lambda item: str(item).casefold())
    with ProcessPoolExecutor(max_workers=max(1, workers)) as pool:
        files = list(pool.map(_scan_path_pair, ((item, resolved_root) for item in paths), chunksize=4))

    readable = [item for item in files if item.readable]
    top_level = Counter(_top_level(item.relative_path) for item in files)
    sheet_counts = Counter(sheet for item in readable for sheet in item.sheets)
    signatures = Counter(item.sheets for item in readable)
    summary = CatalogSummary(
        root=str(resolved_root),
        generated_at=datetime.now(tz=timezone.utc).isoformat(),
        file_count=len(files),
        readable_count=len(readable),
        unreadable_count=len(files) - len(readable),
        formula_count=sum(item.formula_count for item in readable),
        files_with_external_references=sum(item.external_reference_count > 0 for item in readable),
        external_reference_count=sum(item.external_reference_count for item in readable),
        top_level_counts=tuple(top_level.most_common()),
        common_sheets=tuple(sheet_counts.most_common()),
        common_sheet_signatures=tuple(
            {"count": count, "sheets": list(sheet_names)}
            for sheet_names, count in signatures.most_common(25)
        ),
    )
    return summary, files


def most_recent(files: Iterable[CostingFile], count: int = 30) -> list[CostingFile]:
    """Return files ordered by their timezone-aware ISO modification time."""

    return sorted(files, key=lambda item: item.modified_at, reverse=True)[:count]


def _top_level(relative_path: str) -> str:
    parts = Path(relative_path).parts
    return parts[0] if len(parts) > 1 else "(root)"


def _scan_path_pair(pair: tuple[Path, Path]) -> CostingFile:
    path, root = pair
    return scan_costing_file(path, root)


def _scan_content_stream(stream: object) -> tuple[tuple[str, ...], int, tuple[str, ...]]:
    """Scan XML attributes in bounded memory.

    ODS content.xml entries can expand to hundreds of megabytes.  A 64 KiB
    overlap safely covers ordinary sheet-name and formula attributes that cross
    chunk boundaries while the end-position rule prevents double counting.
    """

    overlap = 64 * 1024
    chunk_size = 1024 * 1024
    carry = b""
    previous_total = 0
    sheets: list[str] = []
    formula_count = 0
    external: list[str] = []
    while True:
        chunk = stream.read(chunk_size)  # type: ignore[attr-defined]
        if not chunk:
            break
        data = carry + chunk
        data_start = previous_total - len(carry)
        for match in SHEET_PATTERN.finditer(data):
            if data_start + match.end() > previous_total:
                sheet_name = unescape(match.group(1).decode("utf-8", "replace"))
                # LibreOffice stores cached external ranges as table elements
                # whose names are file URLs. They are dependencies, not user-
                # visible workbook sheets.
                if not EXTERNAL_REFERENCE_PATTERN.search(sheet_name):
                    sheets.append(sheet_name)
        for match in FORMULA_PATTERN.finditer(data):
            if data_start + match.end() <= previous_total:
                continue
            formula_count += 1
            value = match.group(1).decode("utf-8", "replace")
            if EXTERNAL_REFERENCE_PATTERN.search(value):
                external.append(unescape(value)[:500])
        previous_total += len(chunk)
        carry = data[-overlap:]
    return tuple(sheets), formula_count, tuple(external)

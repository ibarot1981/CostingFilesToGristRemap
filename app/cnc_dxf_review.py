"""DXF path review and save flow for CNC verification."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import os
from pathlib import Path, PureWindowsPath
import threading
import traceback
from typing import Any
from urllib.parse import parse_qs
import webbrowser

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

from app.exceptions import ConfigError
from app.grist import GristClient
from app.reports import ReportWriter, report_dir_for_source
from app.utils import as_decimal, display_text, normalize_text, slugify


DXF_PATH_FIELD = "DXF_File_Path"
CNC_MASTER_TABLE_ID = "CNCPartsMaster"
CNC_MASTER_QTY_FIELD = "Qty_Of_Part_Used"
CNC_MASTER_READY_FIELD = "Ready"
CNC_MASTER_VERIFIED_FIELD = "Verified"
MASTER_MATERIAL_TABLE_ID = "MasterMaterial"
PRODUCT_MODEL_CONFIG_TABLE_ID = "ProductModelConfig"
PRODUCT_PART_NAME_FIELD = "ProductPartName_ProductPartName"
PRODUCT_PART_CNC_TABLE_ID = "ProductPartCNCList"
PRODUCT_PART_CNC_PRODUCT_PART_FIELD = "ProductPartName"
PRODUCT_PART_CNC_CNC_MASTER_FIELD = "CNC_Part_File_Name"
PRODUCT_PART_CNC_MATERIAL_FIELD = "MaterialToCut"
PRODUCT_PART_CNC_VERIFIED_FIELD = "Verified"
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DXFCandidate:
    """One exact-name DXF file candidate."""

    full_path: str
    relative_folder: str
    relative_path: str
    file_name: str
    group: str
    score: int


@dataclass(frozen=True)
class DXFScanCatalog:
    """Indexed DXF files and manual folder/file options."""

    exact_name_index: dict[str, list[Path]]
    folder_files: dict[str, tuple[str, ...]]
    folder_options: tuple[str, ...]


@dataclass(frozen=True)
class ProductPartChoice:
    """One ProductPart option available for ODS assignment rows."""

    row_id: int
    product_part_id: int
    name: str


@dataclass(frozen=True)
class ExistingProductPartCncAssignment:
    """One existing ProductPartCNCList assignment row."""

    row_id: int
    product_part_id: int
    cnc_master_id: int
    material_to_cut_id: int | None
    verified: bool


@dataclass(frozen=True)
class ODSMasterMatch:
    """One CNCPartsMaster candidate for an ODS-only row."""

    row_id: int
    cnc_part_name_prefix: str
    cnc_part_name: str
    thickness: str
    option_group: str
    qty: str
    current_dxf_path: str
    ready: bool
    verified: bool


@dataclass
class DXFReviewItem:
    """One linked CNCPartsMaster row to review."""

    item_id: str
    cnc_master_row_id: int | None
    cnc_part_name: str
    linked_product_parts: list[str]
    grist_row_ids: list[Any]
    current_dxf_path: str
    status: str
    candidates: list[DXFCandidate] = field(default_factory=list)
    selected_candidate_path: str = ""
    checked_by_default: bool = True
    reason: str = ""
    manual_folder_default: str = ""
    manual_filename_default: str = ""
    source_kind: str = "grist"
    ods_row_number: int | None = None
    ods_qty: str = ""
    ods_option_group: str = ""
    master_matches: list[ODSMasterMatch] = field(default_factory=list)
    selected_master_row_id: int | None = None
    product_part_choices: list[ProductPartChoice] = field(default_factory=list)
    selected_product_part_id: int | None = None
    mapped_material_name: str = ""
    mapped_material_id: int | None = None


@dataclass(frozen=True)
class DXFReviewOutcome:
    """Result of the interactive DXF review flow."""

    action: str
    mode: str
    updated_rows: int
    cleared_rows: int
    unchanged_rows: int
    skipped_rows: int
    created_rows: int
    assignment_rows_updated: int


@dataclass(frozen=True)
class ReviewContext:
    """Workbook-derived hints used to rank candidate folders."""

    source_compact: str
    prefer_local: bool
    prefer_export: bool
    prefer_combo: bool
    prefer_hf: bool
    preferred_top_folders: tuple[str, ...]


def review_and_update_cnc_dxf_paths(
    *,
    console: Console,
    workbook_path: Path,
    client: GristClient,
    grist_records: list[dict[str, Any]],
    ods_only_rows: list[dict[str, Any]],
    product_part_choices: list[str],
    report_base_dir: str | Path,
) -> tuple[Path, DXFReviewOutcome | None]:
    """Run the DXF review flow for linked CNCPartsMaster rows."""
    dxf_root = load_dxf_root_from_environment()
    console.print(f"[cyan]Scanning DXF root:[/cyan] {dxf_root}")

    writer = ReportWriter(report_dir_for_source(report_base_dir, workbook_path))
    cnc_master_records = client.fetch_table_records_with_ids(CNC_MASTER_TABLE_ID)
    master_material_records = client.fetch_table_records_with_ids(MASTER_MATERIAL_TABLE_ID)
    product_model_config_records = client.fetch_table_records_with_ids(PRODUCT_MODEL_CONFIG_TABLE_ID)
    product_part_cnc_records = client.fetch_table_records_with_ids(PRODUCT_PART_CNC_TABLE_ID)
    dxf_catalog = scan_dxf_catalog(dxf_root)
    existing_assignments = existing_product_part_cnc_assignments(product_part_cnc_records)
    review_items = build_review_items(
        workbook_path=workbook_path,
        dxf_root=dxf_root,
        dxf_catalog=dxf_catalog,
        grist_records=grist_records,
        cnc_master_records=cnc_master_records,
        ods_only_rows=ods_only_rows,
        product_part_choices=product_part_choices_for_names(product_model_config_records, product_part_choices),
        master_material_ids_by_name=build_master_material_id_lookup(master_material_records),
    )
    payload = review_payload(
        workbook_path=workbook_path,
        dxf_root=dxf_root,
        review_items=review_items,
    )
    json_path = writer.write_json("verify_cnc_cut_list_dxf_review", payload)
    show_dxf_review_summary(console, review_items)

    if not review_items:
        console.print("[yellow]No linked CNC parts were found for DXF review.[/yellow]")
        return json_path, None

    review_server = DXFReviewServer(
        console=console,
        client=client,
        dxf_catalog=dxf_catalog,
        existing_assignments=existing_assignments,
        review_items=review_items,
    )
    outcome = review_server.serve()
    return json_path, outcome


def load_dxf_root_from_environment() -> Path:
    """Return the configured DXF root directory from .env."""
    load_dotenv()
    raw = os.getenv("DXF_ROOT_PATH", "").strip().strip('"')
    if not raw:
        raise ConfigError("DXF review needs DXF_ROOT_PATH in .env.")
    path = Path(raw)
    if not path.exists() or not path.is_dir():
        raise ConfigError(f"DXF_ROOT_PATH does not exist or is not a folder: {path}")
    return path


def build_review_items(
    *,
    workbook_path: Path,
    dxf_root: Path,
    dxf_catalog: DXFScanCatalog,
    grist_records: list[dict[str, Any]],
    cnc_master_records: list[dict[str, Any]],
    ods_only_rows: list[dict[str, Any]],
    product_part_choices: list[ProductPartChoice],
    master_material_ids_by_name: dict[str, int],
) -> list[DXFReviewItem]:
    """Build one review item per linked CNCPartsMaster row in scope."""
    context = build_review_context(workbook_path=workbook_path, dxf_root=dxf_root)
    cnc_master_by_id = {
        record.get("id"): split_grist_record(record)[1]
        for record in cnc_master_records
        if record.get("id") is not None
    }
    by_master_id: dict[str, DXFReviewItem] = {}

    for record in grist_records:
        grist_row_id, fields = split_grist_record(record)
        linked_master_id = linked_record_id(fields.get("CNC_Part_File_Name"))
        cnc_part_name = display_text(fields.get("CNC_Part_File_Name_CNCPartName"))
        product_part_name = display_text(fields.get("ProductPartName_ProductPartName2"))
        item_key = str(linked_master_id if linked_master_id is not None else f"grist-{grist_row_id}")

        if item_key in by_master_id:
            item = by_master_id[item_key]
            if product_part_name and product_part_name not in item.linked_product_parts:
                item.linked_product_parts.append(product_part_name)
            if grist_row_id not in item.grist_row_ids:
                item.grist_row_ids.append(grist_row_id)
            continue

        cnc_master_fields = cnc_master_by_id.get(linked_master_id or -1, {})
        current_dxf_path = display_text(cnc_master_fields.get(DXF_PATH_FIELD))
        if linked_master_id is None or not cnc_master_fields:
            by_master_id[item_key] = DXFReviewItem(
                item_id=item_key,
                cnc_master_row_id=linked_master_id,
                cnc_part_name=cnc_part_name,
                linked_product_parts=[product_part_name] if product_part_name else [],
                grist_row_ids=[grist_row_id],
                current_dxf_path=current_dxf_path,
                status="missing_linked_master",
                checked_by_default=False,
                reason="Linked CNCPartsMaster row is missing or cannot be resolved.",
            )
            continue
        if not cnc_master_qty_in_use(cnc_master_fields):
            continue

        candidates = ranked_candidates_for_name(
            part_name=display_text(cnc_master_fields.get("CNCPartName")) or cnc_part_name,
            dxf_catalog=dxf_catalog,
            context=context,
            dxf_root=dxf_root,
        )
        manual_folder_default, manual_filename_default = split_stored_dxf_value(current_dxf_path)
        status = (
            "mapped"
            if is_exact_mapped_path(current_dxf_path=current_dxf_path, dxf_root=dxf_root)
            else candidate_status(candidates)
        )
        if status == "not_found":
            manual_folder_default = ""
            manual_filename_default = ""
        checked_by_default = status in {"auto_selected", "ambiguous"}
        by_master_id[item_key] = DXFReviewItem(
            item_id=item_key,
            cnc_master_row_id=linked_master_id,
            cnc_part_name=display_text(cnc_master_fields.get("CNCPartName")) or cnc_part_name,
            linked_product_parts=[product_part_name] if product_part_name else [],
            grist_row_ids=[grist_row_id],
            current_dxf_path=current_dxf_path,
            status=status,
            candidates=candidates,
            selected_candidate_path=selected_candidate_path_for_value(
                candidates=candidates,
                current_dxf_path=current_dxf_path,
                dxf_root=dxf_root,
            ) or (candidates[0].full_path if candidates else ""),
            checked_by_default=checked_by_default,
            reason=review_reason(status=status, candidates=candidates, current_dxf_path=current_dxf_path),
            manual_folder_default=manual_folder_default,
            manual_filename_default=manual_filename_default,
        )

    ods_items = build_ods_review_items(
        workbook_path=workbook_path,
        dxf_root=dxf_root,
        dxf_catalog=dxf_catalog,
        cnc_master_records=cnc_master_records,
        ods_only_rows=ods_only_rows,
        product_part_choices=product_part_choices,
        master_material_ids_by_name=master_material_ids_by_name,
    )
    return sorted(
        [*by_master_id.values(), *ods_items],
        key=lambda item: (status_sort_key(item.status), item.cnc_part_name.casefold(), item.item_id),
    )


def build_review_context(*, workbook_path: Path, dxf_root: Path) -> ReviewContext:
    """Return ranking hints derived from the workbook path."""
    workbook_text = " ".join(workbook_path.parts).casefold()
    source_compact = compact_text(workbook_text)
    preferred_top_folders = [
        child.name
        for child in dxf_root.iterdir()
        if child.is_dir() and compact_text(child.name) and compact_text(child.name) in source_compact
    ]
    preferred_top_folders.sort(key=lambda value: (-len(compact_text(value)), value.casefold()))
    return ReviewContext(
        source_compact=source_compact,
        prefer_local="local" in workbook_text,
        prefer_export="export" in workbook_text,
        prefer_combo="combo" in workbook_text,
        prefer_hf="hf" in workbook_text,
        preferred_top_folders=tuple(preferred_top_folders),
    )


def product_part_choices_for_names(
    product_part_records: list[dict[str, Any]],
    allowed_names: list[str],
) -> list[ProductPartChoice]:
    """Return ProductPart choices from ProductModelConfig limited to the current verification scope."""
    allowed = {normalize_text(name): name for name in allowed_names if name.strip()}
    choices_by_name: dict[str, ProductPartChoice] = {}
    for record in product_part_records:
        row_id, fields = split_grist_record(record)
        if row_id is None:
            continue
        product_part_id = linked_record_id(fields.get("ProductPartName"))
        name = display_text(fields.get(PRODUCT_PART_NAME_FIELD))
        normalized = normalize_text(name)
        if (
            not name
            or product_part_id is None
            or normalized not in allowed
            or normalized in choices_by_name
        ):
            continue
        choices_by_name[normalized] = ProductPartChoice(
            row_id=int(row_id),
            product_part_id=product_part_id,
            name=name,
        )
    return sorted(choices_by_name.values(), key=lambda choice: choice.name.casefold())


def existing_product_part_cnc_keys(grist_records: list[dict[str, Any]]) -> set[tuple[int, int]]:
    """Return existing ProductPart-to-CNC master link pairs in scope."""
    keys: set[tuple[int, int]] = set()
    for record in grist_records:
        _, fields = split_grist_record(record)
        product_part_id = linked_record_id(fields.get(PRODUCT_PART_CNC_PRODUCT_PART_FIELD))
        cnc_master_id = linked_record_id(fields.get(PRODUCT_PART_CNC_CNC_MASTER_FIELD))
        if product_part_id is None or cnc_master_id is None:
            continue
        keys.add((product_part_id, cnc_master_id))
    return keys


def existing_product_part_cnc_assignments(
    grist_records: list[dict[str, Any]],
) -> dict[tuple[int, int], list[ExistingProductPartCncAssignment]]:
    """Return existing ProductPartCNCList assignments keyed by ProductPart/CNC pair."""
    assignments: dict[tuple[int, int], list[ExistingProductPartCncAssignment]] = {}
    for record in grist_records:
        row_id, fields = split_grist_record(record)
        if row_id is None:
            continue
        product_part_id = linked_record_id(fields.get(PRODUCT_PART_CNC_PRODUCT_PART_FIELD))
        cnc_master_id = linked_record_id(fields.get(PRODUCT_PART_CNC_CNC_MASTER_FIELD))
        if product_part_id is None or cnc_master_id is None:
            continue
        assignments.setdefault((product_part_id, cnc_master_id), []).append(
            ExistingProductPartCncAssignment(
                row_id=int(row_id),
                product_part_id=product_part_id,
                cnc_master_id=cnc_master_id,
                material_to_cut_id=linked_record_id(fields.get(PRODUCT_PART_CNC_MATERIAL_FIELD)),
                verified=grist_bool(fields.get(PRODUCT_PART_CNC_VERIFIED_FIELD)),
            )
        )
    return assignments


def build_master_material_id_lookup(master_material_records: list[dict[str, Any]]) -> dict[str, int]:
    """Return MasterMaterial row ids keyed by normalized material name."""
    lookup: dict[str, int] = {}
    for record in master_material_records:
        row_id, fields = split_grist_record(record)
        if row_id is None:
            continue
        name = display_text(fields.get("MasterMaterial"))
        normalized = normalize_text(name)
        if not name or normalized in lookup:
            continue
        lookup[normalized] = int(row_id)
    return lookup


def build_ods_review_items(
    *,
    workbook_path: Path,
    dxf_root: Path,
    dxf_catalog: DXFScanCatalog,
    cnc_master_records: list[dict[str, Any]],
    ods_only_rows: list[dict[str, Any]],
    product_part_choices: list[ProductPartChoice],
    master_material_ids_by_name: dict[str, int],
) -> list[DXFReviewItem]:
    """Build review rows for ODS entries missing from ProductPartCNCList."""
    if not ods_only_rows:
        return []

    context = build_review_context(workbook_path=workbook_path, dxf_root=dxf_root)
    by_name: dict[str, list[ODSMasterMatch]] = defaultdict(list)
    for record in cnc_master_records:
        row_id, fields = split_grist_record(record)
        if row_id is None:
            continue
        cnc_part_name = display_text(fields.get("CNCPartName"))
        if not cnc_part_name:
            continue
        by_name[cnc_part_name.casefold()].append(
            ODSMasterMatch(
                row_id=int(row_id),
                cnc_part_name_prefix=display_text(fields.get("CNCPartNamePrefix_CNCPartName"))
                or display_text(fields.get("CNCPartNamePrefix")),
                cnc_part_name=cnc_part_name,
                thickness=display_text(fields.get("Thickness")),
                option_group=display_text(fields.get("Optional_Item_Group_1")),
                qty=display_text(fields.get(CNC_MASTER_QTY_FIELD)),
                current_dxf_path=display_text(fields.get(DXF_PATH_FIELD)),
                ready=grist_bool(fields.get(CNC_MASTER_READY_FIELD)),
                verified=grist_bool(fields.get(CNC_MASTER_VERIFIED_FIELD)),
            )
        )

    items: list[DXFReviewItem] = []
    default_product_part_id = product_part_choices[0].product_part_id if len(product_part_choices) == 1 else None
    for row in ods_only_rows:
        cnc_part_name = display_text(row.get("product_part_name"))
        if not cnc_part_name:
            continue

        matched_masters = sorted(
            by_name.get(cnc_part_name.casefold(), []),
            key=lambda match: ods_master_sort_key(
                match,
                ods_option_group=display_text(row.get("optional_item_group_1")),
                ods_qty=display_text(row.get("qty")),
            ),
        )
        if not matched_masters:
            continue

        recommended_master = matched_masters[0]
        candidates = ranked_candidates_for_name(
            part_name=cnc_part_name,
            dxf_catalog=dxf_catalog,
            context=context,
            dxf_root=dxf_root,
        )
        manual_folder_default, manual_filename_default = split_stored_dxf_value(recommended_master.current_dxf_path)
        selected_candidate_path = selected_candidate_path_for_value(
            candidates=candidates,
            current_dxf_path=recommended_master.current_dxf_path,
            dxf_root=dxf_root,
        )
        if not selected_candidate_path and not candidates:
            manual_folder_default = ""
            manual_filename_default = ""
        mapped_material_name = display_text(row.get("grist_material"))
        mapped_material_id = master_material_ids_by_name.get(normalize_text(mapped_material_name))

        items.append(
            DXFReviewItem(
                item_id=f"ods-{display_text(row.get('row_number')) or slugify(cnc_part_name)}",
                cnc_master_row_id=recommended_master.row_id,
                cnc_part_name=cnc_part_name,
                linked_product_parts=[],
                grist_row_ids=[],
                current_dxf_path=recommended_master.current_dxf_path,
                status="ods_exact_matched",
                candidates=candidates,
                selected_candidate_path=selected_candidate_path or (candidates[0].full_path if candidates else ""),
                checked_by_default=True,
                reason=ods_review_reason(
                    matched_masters=matched_masters,
                    candidates=candidates,
                    current_dxf_path=recommended_master.current_dxf_path,
                    ods_qty=display_text(row.get("qty")),
                ),
                manual_folder_default=manual_folder_default,
                manual_filename_default=manual_filename_default,
                source_kind="ods_only",
                ods_row_number=int(row["row_number"]) if str(row.get("row_number", "")).isdigit() else None,
                ods_qty=display_text(row.get("qty")),
                ods_option_group=display_text(row.get("optional_item_group_1")),
                master_matches=matched_masters,
                selected_master_row_id=recommended_master.row_id,
                product_part_choices=product_part_choices,
                selected_product_part_id=default_product_part_id,
                mapped_material_name=mapped_material_name,
                mapped_material_id=mapped_material_id,
            )
        )
    return items


def ods_master_sort_key(match: ODSMasterMatch, *, ods_option_group: str, ods_qty: str) -> tuple[int, int, int, int, int]:
    """Rank CNC master matches for an ODS-only row."""
    option_match = 0 if normalize_text(match.option_group) == normalize_text(ods_option_group) else 1
    qty_match = 0 if key_like(match.qty) == key_like(ods_qty) else 1
    ready_verified = 0 if match.ready and match.verified else 1
    has_path = 0 if match.current_dxf_path else 1
    return option_match, qty_match, ready_verified, has_path, match.row_id


def key_like(value: Any) -> str:
    """Normalize a display value for exact review comparisons."""
    number = as_decimal(value)
    if number is not None:
        return format(number.normalize(), "f")
    return normalize_text(value)


def selected_candidate_path_for_value(
    *,
    candidates: list[DXFCandidate],
    current_dxf_path: str,
    dxf_root: Path,
) -> str:
    """Return the candidate full path matching a stored DXF value, when available."""
    resolved = resolve_stored_dxf_path(current_dxf_path=current_dxf_path, dxf_root=dxf_root)
    if resolved is None:
        return ""
    resolved_text = str(resolved)
    for candidate in candidates:
        if candidate.full_path == resolved_text:
            return candidate.full_path
    return ""


def ods_review_reason(
    *,
    matched_masters: list[ODSMasterMatch],
    candidates: list[DXFCandidate],
    current_dxf_path: str,
    ods_qty: str,
) -> str:
    """Return review help text for an ODS-only assignment row."""
    master_note = (
        "One CNCPartsMaster row matched this ODS file."
        if len(matched_masters) == 1
        else f"{len(matched_masters)} CNCPartsMaster rows matched this ODS file."
    )
    qty_matches_any_master = any(key_like(match.qty) == key_like(ods_qty) for match in matched_masters)
    qty_note = (
        ""
        if qty_matches_any_master
        else f" ODS qty is {ods_qty or '<blank>'}, but matched CNCPartsMaster qty values are "
        f"{', '.join(match.qty or '0' for match in matched_masters)}."
        " Saving one link will not make this row move to Matched while the qty differs."
    )
    if current_dxf_path:
        return f"{master_note}{qty_note} Saving will set Ready/Verified to Yes and add a ProductPartCNCList row."
    if candidates:
        return f"{master_note}{qty_note} Choose a ProductPart and save to add the ProductPartCNCList row."
    return f"{master_note}{qty_note} Choose a folder and filename, then save to add the ProductPartCNCList row."


def scan_dxf_catalog(dxf_root: Path) -> DXFScanCatalog:
    """Index exact-name DXF files and folder/file choices under the configured root."""
    exact_name_index: dict[str, list[Path]] = defaultdict(list)
    folder_files: dict[str, list[str]] = defaultdict(list)
    for path in dxf_root.rglob("*"):
        if not path.is_file() or path.suffix.lower() != ".dxf":
            continue
        if any(part.casefold() == "old" for part in path.parts):
            continue
        exact_name_index[path.stem.casefold()].append(path)
        folder_files[folder_value_from_full_path(path=path, dxf_root=dxf_root)].append(path.name)

    normalized_folder_files = {
        folder: tuple(sorted(set(files), key=str.casefold))
        for folder, files in sorted(folder_files.items(), key=lambda item: item[0].casefold())
    }
    return DXFScanCatalog(
        exact_name_index=dict(exact_name_index),
        folder_files=normalized_folder_files,
        folder_options=tuple(normalized_folder_files.keys()),
    )


def ranked_candidates_for_name(
    *,
    part_name: str,
    dxf_catalog: DXFScanCatalog,
    context: ReviewContext,
    dxf_root: Path,
) -> list[DXFCandidate]:
    """Return ranked exact-name candidates for one CNC part name."""
    candidates = []
    for path in dxf_catalog.exact_name_index.get(part_name.casefold(), []):
        candidates.append(
            DXFCandidate(
                full_path=str(path),
                relative_folder=folder_value_from_full_path(path=path, dxf_root=dxf_root),
                relative_path=stored_value_from_full_path(path=path, dxf_root=dxf_root),
                file_name=path.name,
                group=top_level_group(path=path, dxf_root=dxf_root),
                score=candidate_score(path=path, dxf_root=dxf_root, context=context),
            )
        )
    return sorted(
        candidates,
        key=lambda candidate: (-candidate.score, candidate.group.casefold(), candidate.relative_folder.casefold(), candidate.full_path.casefold()),
    )


def stored_value_from_full_path(*, path: Path, dxf_root: Path) -> str:
    """Return the stored DXF path text relative from Nesting, including filename."""
    nesting_root = nesting_root_path(dxf_root)
    relative_path = path.relative_to(nesting_root)
    return "\\".join((nesting_root.name, *relative_path.parts))


def folder_value_from_full_path(*, path: Path, dxf_root: Path) -> str:
    """Return the stored DXF folder text relative from Nesting."""
    stored_parts = PureWindowsPath(stored_value_from_full_path(path=path, dxf_root=dxf_root)).parent.parts
    value = "\\".join(stored_parts)
    return value.rstrip("\\") + "\\"


def nesting_root_path(dxf_root: Path) -> Path:
    """Return the absolute Nesting root for the configured DXF path."""
    parts = list(dxf_root.parts)
    try:
        nesting_index = next(index for index, part in enumerate(parts) if part.casefold() == "nesting")
    except StopIteration as exc:  # pragma: no cover - defensive only
        raise ConfigError("DXF_ROOT_PATH must be inside a folder named 'Nesting'.") from exc
    return Path(*parts[: nesting_index + 1])


def resolve_stored_dxf_path(*, current_dxf_path: str, dxf_root: Path) -> Path | None:
    """Resolve a stored DXF path relative from Nesting to an absolute path."""
    normalized = normalize_stored_dxf_value(current_dxf_path)
    if not normalized:
        return None
    stored_parts = PureWindowsPath(normalized).parts
    if not stored_parts:
        return None

    nesting_root = nesting_root_path(dxf_root)
    if stored_parts[0].casefold() == nesting_root.name.casefold():
        return nesting_root.parent.joinpath(*stored_parts)
    return nesting_root.joinpath(*stored_parts)


def is_exact_mapped_path(*, current_dxf_path: str, dxf_root: Path) -> bool:
    """Return True when the stored DXF path resolves to an existing DXF file."""
    resolved = resolve_stored_dxf_path(current_dxf_path=current_dxf_path, dxf_root=dxf_root)
    return bool(resolved and resolved.is_file() and resolved.suffix.lower() == ".dxf")


def normalize_stored_dxf_value(value: str) -> str:
    """Normalize a stored Grist DXF path to Windows-style separators."""
    return display_text(value).replace("/", "\\").strip().strip("\\")


def split_stored_dxf_value(value: str) -> tuple[str, str]:
    """Split a stored DXF path into folder and filename defaults."""
    normalized = normalize_stored_dxf_value(value)
    if not normalized:
        return "", ""
    stored_path = PureWindowsPath(normalized)
    if stored_path.suffix.lower() == ".dxf":
        folder = "\\".join(stored_path.parent.parts)
        return (folder.rstrip("\\") + "\\") if folder else "", stored_path.name
    return normalized.rstrip("\\") + "\\", ""


def review_reason(*, status: str, candidates: list[DXFCandidate], current_dxf_path: str) -> str:
    """Return a row reason for the review UI."""
    if status == "mapped":
        return "An exact DXF file path is already saved in Grist."
    if status == "not_found":
        if current_dxf_path:
            return "No exact non-Old DXF file was found. Pick a folder and filename to remap or leave blank to clear."
        return "No exact non-Old DXF file was found. Pick a folder and filename to map."
    return "" if candidates else "No exact non-Old DXF file was found."


def cnc_master_qty_in_use(fields: dict[str, Any]) -> bool:
    """Return True when the linked CNCPartsMaster row is still in use."""
    qty = as_decimal(fields.get(CNC_MASTER_QTY_FIELD))
    return qty is not None and qty > 0


def grist_bool(value: Any) -> bool:
    """Return a reliable boolean from Grist values."""
    if isinstance(value, bool):
        return value
    return display_text(value).casefold() in {"1", "true", "yes", "y"}


def top_level_group(*, path: Path, dxf_root: Path) -> str:
    """Return the top folder under DXF used for candidate grouping."""
    relative = path.relative_to(dxf_root)
    return relative.parts[0] if relative.parts else "DXF"


def candidate_score(*, path: Path, dxf_root: Path, context: ReviewContext) -> int:
    """Rank a candidate path for preselection."""
    relative = path.relative_to(dxf_root)
    relative_text = " ".join(relative.parts).casefold()
    relative_compact = compact_text(relative_text)
    top_level = relative.parts[0] if relative.parts else ""
    score = 0

    if top_level in context.preferred_top_folders:
        score += 120
    if compact_text(top_level) in context.source_compact:
        score += 80
    if context.prefer_local and "local" in relative_text:
        score += 50
    if context.prefer_export and "export" in relative_text:
        score += 50
    if context.prefer_combo and "combo" in relative_text:
        score += 40
    if context.prefer_hf and "hf" in relative_text:
        score += 15
    if top_level.casefold() == "s1k common items":
        score += 25
    if "common" in relative_text:
        score += 10
    if relative_compact.startswith(context.source_compact):
        score += 25
    return score


def candidate_status(candidates: list[DXFCandidate]) -> str:
    """Return the review status from candidate count."""
    if len(candidates) == 1:
        return "auto_selected"
    if len(candidates) > 1:
        return "ambiguous"
    return "not_found"


def compact_text(value: str) -> str:
    """Normalize text and drop separators for fuzzy folder ranking."""
    return "".join(char for char in normalize_text(value) if char.isalnum())


def linked_record_id(value: Any) -> int | None:
    """Return a linked Grist record ID from a link field value."""
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, list):
        for item in value:
            linked = linked_record_id(item)
            if linked is not None:
                return linked
    text = display_text(value)
    return int(text) if text.isdigit() else None


def split_grist_record(record: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
    """Return record ID and fields from raw or fields-only Grist data."""
    fields = record.get("fields")
    if isinstance(fields, dict):
        return record.get("id"), fields
    return record.get("id"), record


def review_payload(
    *,
    workbook_path: Path,
    dxf_root: Path,
    review_items: list[DXFReviewItem],
) -> dict[str, Any]:
    """Return a JSON-friendly payload for audit/reporting."""
    return {
        "source_file": str(workbook_path),
        "dxf_root": str(dxf_root),
        "counts": review_counts(review_items),
        "details": [
            {
                "item_id": item.item_id,
                "cnc_master_row_id": item.cnc_master_row_id,
                "cnc_part_name": item.cnc_part_name,
                "linked_product_parts": item.linked_product_parts,
                "grist_row_ids": item.grist_row_ids,
                "current_dxf_path": item.current_dxf_path,
                "status": item.status,
                "source_kind": item.source_kind,
                "ods_row_number": item.ods_row_number,
                "ods_qty": item.ods_qty,
                "ods_option_group": item.ods_option_group,
                "mapped_material_name": item.mapped_material_name,
                "mapped_material_id": item.mapped_material_id,
                "selected_master_row_id": item.selected_master_row_id,
                "selected_product_part_id": item.selected_product_part_id,
                "selected_candidate_path": item.selected_candidate_path,
                "reason": item.reason,
                "product_part_choices": [
                    {"row_id": choice.row_id, "name": choice.name}
                    for choice in item.product_part_choices
                ],
                "master_matches": [
                    {
                        "row_id": match.row_id,
                        "cnc_part_name_prefix": match.cnc_part_name_prefix,
                        "cnc_part_name": match.cnc_part_name,
                        "thickness": match.thickness,
                        "option_group": match.option_group,
                        "qty": match.qty,
                        "current_dxf_path": match.current_dxf_path,
                        "ready": match.ready,
                        "verified": match.verified,
                    }
                    for match in item.master_matches
                ],
                "candidates": [
                    {
                        "full_path": candidate.full_path,
                        "relative_folder": candidate.relative_folder,
                        "relative_path": candidate.relative_path,
                        "group": candidate.group,
                        "score": candidate.score,
                    }
                    for candidate in item.candidates
                ],
            }
            for item in review_items
        ],
    }


def review_counts(review_items: list[DXFReviewItem]) -> dict[str, int]:
    """Return compact DXF review counts by status."""
    counts = {
        "mapped": 0,
        "ods exact matched": 0,
        "auto selected": 0,
        "ambiguous": 0,
        "not found": 0,
        "missing linked master": 0,
    }
    for item in review_items:
        if item.status == "mapped":
            counts["mapped"] += 1
        elif item.status == "ods_exact_matched":
            counts["ods exact matched"] += 1
        elif item.status == "auto_selected":
            counts["auto selected"] += 1
        elif item.status == "ambiguous":
            counts["ambiguous"] += 1
        elif item.status == "not_found":
            counts["not found"] += 1
        else:
            counts["missing linked master"] += 1
    return counts


def show_dxf_review_summary(console: Console, review_items: list[DXFReviewItem]) -> None:
    """Render a summary table before opening the browser review."""
    counts = review_counts(review_items)
    table = Table(title="CNC DXF Review Summary")
    table.add_column("Category", style="cyan")
    table.add_column("Count", justify="right", style="bold")
    for label, count in counts.items():
        table.add_row(label.title(), str(count))
    table.add_row("Total", str(len(review_items)))
    console.print(table)


def status_sort_key(status: str) -> int:
    """Sort review rows in a stable, useful order."""
    order = {
        "mapped": 0,
        "ods_exact_matched": 1,
        "auto_selected": 2,
        "ambiguous": 3,
        "not_found": 4,
        "missing_linked_master": 5,
    }
    return order.get(status, 99)


class DXFReviewServer:
    """Small local HTTP server for interactive DXF review."""

    def __init__(
        self,
        *,
        console: Console,
        client: GristClient,
        dxf_catalog: DXFScanCatalog,
        existing_assignments: dict[tuple[int, int], list[ExistingProductPartCncAssignment]],
        review_items: list[DXFReviewItem],
    ) -> None:
        self.console = console
        self.client = client
        self.dxf_catalog = dxf_catalog
        self.existing_assignments = existing_assignments
        self.review_items = review_items
        self.outcome: DXFReviewOutcome | None = None
        self.done = threading.Event()

    def serve(self) -> DXFReviewOutcome | None:
        """Start the local review server and wait for save/cancel."""
        handler = self.build_handler()
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        server.daemon_threads = True
        server.review_server = self  # type: ignore[attr-defined]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        url = f"http://127.0.0.1:{server.server_port}/"
        self.console.print(f"[cyan]Open DXF review page:[/cyan] {url}")
        webbrowser.open(url, new=1, autoraise=True)

        self.done.wait()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        return self.outcome

    def build_handler(self) -> type[BaseHTTPRequestHandler]:
        """Create a request handler bound to this server instance."""
        parent = self

        class Handler(BaseHTTPRequestHandler):
            """Serve DXF review pages and apply user selections."""

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
                return

            def do_GET(self) -> None:  # noqa: N802
                if self.path != "/":
                    self.send_error(404)
                    return
                page = render_review_page(parent.review_items, parent.dxf_catalog)
                body = page.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self) -> None:  # noqa: N802
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    raw_body = self.rfile.read(length).decode("utf-8")
                    form = parse_qs(raw_body, keep_blank_values=True)
                    if self.path == "/cancel":
                        parent.outcome = DXFReviewOutcome(
                            action="cancelled",
                            mode="cancel",
                            updated_rows=0,
                            cleared_rows=0,
                            unchanged_rows=0,
                            skipped_rows=len(parent.review_items),
                            created_rows=0,
                            assignment_rows_updated=0,
                        )
                        self.respond_complete(parent.outcome)
                        parent.done.set()
                        return
                    if self.path != "/save":
                        self.send_error(404)
                        return

                    mode = display_text(form.get("save_mode", [""])[0]).casefold()
                    if mode not in {"all", "checked"}:
                        self.send_error(400, "Unknown save mode.")
                        return

                    outcome = apply_review_form(
                        client=parent.client,
                        dxf_catalog=parent.dxf_catalog,
                        existing_assignments=parent.existing_assignments,
                        review_items=parent.review_items,
                        form=form,
                    ) if mode == "checked" else apply_review_form(
                        client=parent.client,
                        dxf_catalog=parent.dxf_catalog,
                        existing_assignments=parent.existing_assignments,
                        review_items=parent.review_items,
                        form=form,
                        force_all=True,
                    )
                    parent.outcome = outcome
                    self.respond_complete(outcome)
                    parent.done.set()
                except Exception as exc:  # pragma: no cover - exercised via browser
                    logger.exception("DXF review save failed")
                    self.respond_error(exc)

            def respond_complete(self, outcome: DXFReviewOutcome) -> None:
                page = render_completion_page(outcome)
                body = page.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def respond_error(self, exc: Exception) -> None:
                page = render_error_page(exc)
                body = page.encode("utf-8")
                self.send_response(500)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        return Handler


def apply_review_form(
    *,
    client: GristClient,
    dxf_catalog: DXFScanCatalog,
    existing_assignments: dict[tuple[int, int], list[ExistingProductPartCncAssignment]],
    review_items: list[DXFReviewItem],
    form: dict[str, list[str]],
    force_all: bool = False,
) -> DXFReviewOutcome:
    """Apply reviewed DXF path selections to CNCPartsMaster."""
    updates_by_master_id: dict[int, dict[str, Any]] = {}
    assignment_updates_by_row_id: dict[int, dict[str, Any]] = {}
    new_assignment_records: list[dict[str, Any]] = []
    pending_assignment_keys = set(existing_assignments)
    updated_rows = 0
    cleared_rows = 0
    unchanged_rows = 0
    skipped_rows = 0
    created_rows = 0
    assignment_rows_updated = 0

    for item in review_items:
        if item.cnc_master_row_id is None or item.status == "missing_linked_master":
            skipped_rows += 1
            continue

        if item.status == "mapped":
            wants_unmap = checkbox_value(form, item.item_id, prefix="unmap__")
            if not wants_unmap:
                if force_all:
                    unchanged_rows += 1
                else:
                    skipped_rows += 1
                continue
        else:
            checked = force_all or checkbox_value(form, item.item_id)
            if not checked:
                skipped_rows += 1
                continue

        target_master_id = item.cnc_master_row_id
        if item.source_kind == "ods_only":
            target_master_id = selected_master_id(form, item) or target_master_id
        if target_master_id is None:
            skipped_rows += 1
            continue

        target_master = master_match_for_item(item, target_master_id)
        current_dxf_path = target_master.current_dxf_path if target_master is not None else item.current_dxf_path
        new_value = requested_dxf_value(
            item=item,
            form=form,
            dxf_catalog=dxf_catalog,
            current_dxf_path=current_dxf_path,
        )
        if new_value is None:
            skipped_rows += 1
            continue

        fields_to_update: dict[str, Any] = {}
        if new_value != current_dxf_path:
            fields_to_update[DXF_PATH_FIELD] = new_value
        if item.status == "mapped" and new_value == "":
            fields_to_update[DXF_PATH_FIELD] = ""
        if item.source_kind == "ods_only" and target_master is not None:
            if not target_master.ready:
                fields_to_update[CNC_MASTER_READY_FIELD] = True
            if not target_master.verified:
                fields_to_update[CNC_MASTER_VERIFIED_FIELD] = True

            product_part_id = selected_product_part_id(form, item)
            if product_part_id is None or not new_value:
                skipped_rows += 1
                continue
            if item.mapped_material_id is None:
                skipped_rows += 1
                continue
            assignment_key = (product_part_id, target_master_id)
            if assignment_key in existing_assignments:
                for assignment in existing_assignments[assignment_key]:
                    assignment_fields: dict[str, Any] = {}
                    if assignment.material_to_cut_id != item.mapped_material_id:
                        assignment_fields[PRODUCT_PART_CNC_MATERIAL_FIELD] = item.mapped_material_id
                    if not assignment.verified:
                        assignment_fields[PRODUCT_PART_CNC_VERIFIED_FIELD] = True
                    if not assignment_fields:
                        continue
                    existing_assignment_fields = assignment_updates_by_row_id.setdefault(assignment.row_id, {})
                    existing_assignment_fields.update(assignment_fields)
            if assignment_key not in pending_assignment_keys:
                new_assignment_records.append(
                    {
                        "fields": {
                            PRODUCT_PART_CNC_PRODUCT_PART_FIELD: product_part_id,
                            PRODUCT_PART_CNC_CNC_MASTER_FIELD: target_master_id,
                            PRODUCT_PART_CNC_MATERIAL_FIELD: item.mapped_material_id,
                            PRODUCT_PART_CNC_VERIFIED_FIELD: True,
                        }
                    }
                )
                pending_assignment_keys.add(assignment_key)
                created_rows += 1

        if not fields_to_update:
            unchanged_rows += 1
            continue

        existing = updates_by_master_id.setdefault(target_master_id, {})
        existing.update(fields_to_update)
        if DXF_PATH_FIELD in fields_to_update:
            if fields_to_update[DXF_PATH_FIELD]:
                updated_rows += 1
            else:
                cleared_rows += 1
        else:
            unchanged_rows += 1

    updates = [{"id": master_id, "fields": fields} for master_id, fields in updates_by_master_id.items()]
    for batch in chunk_updates(updates, 100):
        client.update_table_records(CNC_MASTER_TABLE_ID, batch)
    assignment_updates = [{"id": row_id, "fields": fields} for row_id, fields in assignment_updates_by_row_id.items()]
    for batch in chunk_updates(assignment_updates, 100):
        client.update_table_records(PRODUCT_PART_CNC_TABLE_ID, batch)
    assignment_rows_updated = len(assignment_updates)
    for batch in chunk_updates(new_assignment_records, 100):
        client.create_table_records(PRODUCT_PART_CNC_TABLE_ID, batch)

    return DXFReviewOutcome(
        action="saved",
        mode="all" if force_all else "checked",
        updated_rows=updated_rows,
        cleared_rows=cleared_rows,
        unchanged_rows=unchanged_rows,
        skipped_rows=skipped_rows,
        created_rows=created_rows,
        assignment_rows_updated=assignment_rows_updated,
    )


def checkbox_value(form: dict[str, list[str]], item_id: str, *, prefix: str = "checked__") -> bool:
    """Return True when a named review checkbox is selected."""
    return display_text(form.get(f"{prefix}{item_id}", [""])[0]).casefold() in {"1", "true", "on", "yes"}


def selected_master_id(form: dict[str, list[str]], item: DXFReviewItem) -> int | None:
    """Return the chosen CNCPartsMaster row for an ODS-only review item."""
    selected = display_text(form.get(f"master__{item.item_id}", [""])[0])
    if selected.isdigit():
        return int(selected)
    return item.selected_master_row_id


def selected_product_part_id(form: dict[str, list[str]], item: DXFReviewItem) -> int | None:
    """Return the chosen ProductPart row for an ODS-only review item."""
    selected = display_text(form.get(f"product_part__{item.item_id}", [""])[0])
    if selected.isdigit():
        return int(selected)
    return item.selected_product_part_id


def master_match_for_item(item: DXFReviewItem, master_row_id: int) -> ODSMasterMatch | None:
    """Return the selected master match for an ODS-only review row."""
    if item.source_kind != "ods_only":
        return None
    return next((match for match in item.master_matches if match.row_id == master_row_id), None)


def requested_dxf_value(
    *,
    item: DXFReviewItem,
    form: dict[str, list[str]],
    dxf_catalog: DXFScanCatalog,
    current_dxf_path: str,
) -> str | None:
    """Return the requested DXF path value for a review row."""
    if item.status == "mapped":
        return ""

    selected_path = display_text(form.get(f"selected__{item.item_id}", [""])[0])
    if item.candidates and selected_path:
        candidate = next((candidate for candidate in item.candidates if candidate.full_path == selected_path), None)
        if candidate is None:
            return None
        return candidate.relative_path

    manual_folder = display_text(form.get(f"manual_folder__{item.item_id}", [""])[0])
    manual_filename = display_text(form.get(f"manual_filename__{item.item_id}", [""])[0])
    manual_selection = resolve_manual_dxf_selection(
        dxf_catalog=dxf_catalog,
        folder=manual_folder,
        filename=manual_filename,
    )
    if manual_selection is None and (manual_folder or manual_filename):
        return None
    if manual_selection:
        return manual_selection
    return current_dxf_path


def resolve_manual_dxf_selection(*, dxf_catalog: DXFScanCatalog, folder: str, filename: str) -> str | None:
    """Return a stored DXF path from manual folder and filename selection."""
    normalized_folder = display_text(folder).replace("/", "\\").strip()
    normalized_filename = display_text(filename).strip()
    if not normalized_folder and not normalized_filename:
        return None
    if not normalized_folder or not normalized_filename:
        return None

    folder_key = normalized_folder.rstrip("\\") + "\\"
    available_files = dxf_catalog.folder_files.get(folder_key)
    if not available_files:
        return None

    requested = normalized_filename.casefold()
    requested_stem = requested[:-4] if requested.endswith(".dxf") else requested
    for file_name in available_files:
        if file_name.casefold() == requested or Path(file_name).stem.casefold() == requested_stem:
            return f"{folder_key}{file_name}"
    return None


def chunk_updates(values: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    """Split updates into API-friendly chunks."""
    return [values[index : index + size] for index in range(0, len(values), size)]


def render_review_page(review_items: list[DXFReviewItem], dxf_catalog: DXFScanCatalog) -> str:
    """Render the interactive DXF review page."""
    counts = review_counts(review_items)
    sections = []
    for status, title in [
        ("mapped", "Exact Filepath Mapped"),
        ("ods_exact_matched", "ODS Exact File Matched"),
        ("auto_selected", "Auto-Selected Exact Matches"),
        ("ambiguous", "Ambiguous Exact Matches"),
        ("not_found", "Exact Matches Not Found"),
        ("missing_linked_master", "Missing Linked CNCPartsMaster Rows"),
    ]:
        rows = [item for item in review_items if item.status == status]
        sections.append(render_review_section(status=status, title=title, rows=rows, dxf_catalog=dxf_catalog))

    manual_files_json = json.dumps({folder: list(files) for folder, files in dxf_catalog.folder_files.items()})

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>CNC DXF Review</title>
  <style>
    :root {{
      --bg: #f4f5f6;
      --panel: #ffffff;
      --line: #d7dde4;
      --ink: #1f2933;
      --muted: #64748b;
      --good: #1d7f43;
      --warn: #a15c00;
      --bad: #b42318;
      --accent: #0f5d7a;
      --shadow: 0 12px 28px rgba(18, 31, 46, 0.08);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Segoe UI", Arial, sans-serif;
      background: var(--bg);
      color: var(--ink);
    }}
    header {{
      padding: 26px 30px 20px;
      background: #fff;
      border-bottom: 1px solid var(--line);
    }}
    h1, h2, h3 {{ margin: 0; }}
    p {{ margin: 0; }}
    .meta {{
      margin-top: 10px;
      color: var(--muted);
      font-size: 14px;
      display: flex;
      gap: 18px;
      flex-wrap: wrap;
    }}
    .toolbar {{
      position: sticky;
      top: 0;
      z-index: 5;
      display: flex;
      gap: 10px;
      align-items: center;
      flex-wrap: wrap;
      padding: 12px 30px;
      background: rgba(255,255,255,0.96);
      border-bottom: 1px solid var(--line);
      backdrop-filter: blur(4px);
    }}
    .toolbar .spacer {{
      flex: 1 1 auto;
    }}
    .section-heading {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      flex-wrap: wrap;
    }}
    .section-toolbar {{
      display: flex;
      gap: 10px;
      align-items: center;
      flex-wrap: wrap;
    }}
    button {{
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px 14px;
      background: #fff;
      cursor: pointer;
      font-weight: 600;
    }}
    button.primary {{
      background: #0f5d7a;
      color: #fff;
      border-color: #0f5d7a;
    }}
    button.warn {{
      background: #fff7ed;
      border-color: #f59e0b;
    }}
    main {{
      padding: 22px 30px 40px;
    }}
    .metrics {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 12px;
      margin-bottom: 20px;
    }}
    .metric {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 10px;
      box-shadow: var(--shadow);
      padding: 14px;
    }}
    .metric span {{
      display: block;
      font-size: 28px;
      font-weight: 700;
    }}
    .metric strong {{
      color: var(--muted);
      font-size: 13px;
    }}
    section {{
      margin-top: 18px;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 12px;
      box-shadow: var(--shadow);
      overflow: hidden;
    }}
    section header {{
      padding: 16px 18px;
      border-bottom: 1px solid var(--line);
      background: #fbfcfd;
    }}
    .section-note {{
      color: var(--muted);
      font-size: 13px;
      margin-top: 4px;
    }}
    table {{
      min-width: 100%;
      width: max-content;
      border-collapse: collapse;
    }}
    th, td {{
      padding: 10px 12px;
      text-align: left;
      vertical-align: top;
      border-bottom: 1px solid var(--line);
      font-size: 13px;
      overflow-wrap: anywhere;
    }}
    th {{
      background: #f3f5f7;
      color: #455468;
      font-weight: 700;
      white-space: nowrap;
    }}
    td.small {{
      color: var(--muted);
      font-size: 12px;
    }}
    .status-pill {{
      display: inline-block;
      border-radius: 999px;
      padding: 4px 10px;
      font-size: 12px;
      font-weight: 700;
    }}
    .mapped, .ods_exact_matched {{ background: #e7f0ff; color: var(--accent); }}
    .auto_selected {{ background: #e9f7ef; color: var(--good); }}
    .ambiguous {{ background: #fff7ed; color: var(--warn); }}
    .not_found, .missing_linked_master {{ background: #fdecec; color: var(--bad); }}
    select, input[type="text"] {{
      width: min(100%, 520px);
      min-width: 220px;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 8px 10px;
      background: #fff;
    }}
    .muted {{
      color: var(--muted);
    }}
    .empty {{
      padding: 16px 18px;
      color: var(--muted);
    }}
    .stack > div + div {{
      margin-top: 4px;
    }}
    .record-list {{
      display: grid;
      gap: 14px;
      padding: 12px;
    }}
    .record-shell {{
      border: 1px solid var(--line);
      border-radius: 10px;
      background: #fff;
      overflow: hidden;
    }}
    .record-wrap {{
      overflow-x: auto;
      overflow-y: hidden;
      scrollbar-gutter: stable both-edges;
      background: #fff;
    }}
    .table-wrap {{
      overflow-x: auto;
      overflow-y: hidden;
      scrollbar-gutter: stable both-edges;
      background: #fff;
    }}
    .record-table th.col-use,
    .record-table td.col-use {{
      min-width: 56px;
      width: 56px;
      white-space: nowrap;
    }}
    .record-table th.col-status,
    .record-table td.col-status {{
      min-width: 96px;
      white-space: nowrap;
    }}
    .record-table th.col-source,
    .record-table td.col-source {{
      min-width: 120px;
    }}
    .record-table th.col-reference,
    .record-table td.col-reference {{
      min-width: 84px;
      width: 84px;
      white-space: nowrap;
    }}
    .record-table th.col-cnc-part,
    .record-table td.col-cnc-part {{
      min-width: 980px;
    }}
    .record-table th.col-product-part,
    .record-table td.col-product-part {{
      min-width: 260px;
    }}
    .record-table th.col-path,
    .record-table td.col-path {{
      min-width: 260px;
    }}
    .record-table th.col-mapping,
    .record-table td.col-mapping {{
      min-width: 280px;
    }}
    .record-table th.col-notes,
    .record-table td.col-notes {{
      min-width: 220px;
    }}
    .choice-list {{
      display: block;
    }}
    .master-summary {{
      padding: 8px 10px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fbfcfd;
      font-size: 12px;
      color: var(--ink);
    }}
    .master-actions {{
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      align-items: center;
    }}
    .choice-table {{
      width: max-content;
      min-width: 860px;
      border-collapse: collapse;
      border: 1px solid var(--line);
      border-radius: 10px;
      overflow: hidden;
      background: #fff;
    }}
    .choice-table th,
    .choice-table td {{
      padding: 8px 10px;
      font-size: 12px;
      border-bottom: 1px solid var(--line);
      vertical-align: middle;
      white-space: nowrap;
    }}
    .choice-table th {{
      position: sticky;
      top: 0;
      background: #eef3f7;
      z-index: 1;
    }}
    .choice-table tr:last-child td {{
      border-bottom: 0;
    }}
    .choice-table td.wrap {{
      white-space: normal;
      min-width: 180px;
    }}
    .choice-table input[type="radio"] {{
      transform: scale(1.05);
    }}
    .choice-table .selected-row td {{
      background: #f5fbff;
    }}
    .modal-backdrop {{
      position: fixed;
      inset: 0;
      z-index: 40;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 20px;
      background: rgba(17, 24, 39, 0.55);
    }}
    .modal-backdrop[hidden] {{
      display: none;
    }}
    .modal-card {{
      width: min(1180px, 96vw);
      max-height: 88vh;
      display: flex;
      flex-direction: column;
      border-radius: 14px;
      background: #fff;
      box-shadow: 0 20px 48px rgba(15, 23, 42, 0.28);
      overflow: hidden;
    }}
    .modal-header,
    .modal-footer {{
      padding: 14px 18px;
      background: #fbfcfd;
    }}
    .modal-header {{
      border-bottom: 1px solid var(--line);
    }}
    .modal-footer {{
      border-top: 1px solid var(--line);
      display: flex;
      justify-content: flex-end;
      gap: 10px;
      flex-wrap: wrap;
    }}
    .modal-title {{
      font-size: 18px;
      font-weight: 700;
    }}
    .modal-subtitle {{
      margin-top: 4px;
      color: var(--muted);
      font-size: 13px;
    }}
    .modal-body {{
      overflow: auto;
      background: #fff;
    }}
    .modal-table-wrap {{
      overflow: auto;
      max-height: calc(88vh - 150px);
      scrollbar-gutter: stable both-edges;
    }}
    .modal-table-wrap .choice-table {{
      min-width: 1120px;
      width: max-content;
      border: 0;
      border-radius: 0;
    }}
    .pill-yes,
    .pill-no {{
      display: inline-block;
      min-width: 32px;
      text-align: center;
      border-radius: 999px;
      padding: 2px 8px;
      font-size: 11px;
      font-weight: 700;
    }}
    .pill-yes {{
      background: #e9f7ef;
      color: var(--good);
    }}
    .pill-no {{
      background: #fdecec;
      color: var(--bad);
    }}
  </style>
</head>
<body>
  <header>
    <h1>CNC DXF Review</h1>
    <div class="meta">
      <span>Exact filename only</span>
      <span>Any folder named Old/old/OLD is ignored</span>
      <span>DXF File Path stores the relative DXF path from Nesting, including filename</span>
    </div>
  </header>
  <form method="post" action="/save">
    <div class="toolbar">
      <button class="primary" type="submit" name="save_mode" value="all">Save All Reviewed Rows</button>
      <button type="submit" name="save_mode" value="checked">Save Only Checked Rows</button>
      <button class="warn" type="submit" formaction="/cancel">Cancel</button>
    </div>
    <main>
      <div class="metrics">
        <article class="metric"><span>{counts["mapped"]}</span><strong>Mapped</strong></article>
        <article class="metric"><span>{counts["ods exact matched"]}</span><strong>ODS exact file matched</strong></article>
        <article class="metric"><span>{counts["auto selected"]}</span><strong>Auto selected</strong></article>
        <article class="metric"><span>{counts["ambiguous"]}</span><strong>Ambiguous</strong></article>
        <article class="metric"><span>{counts["not found"]}</span><strong>Not found</strong></article>
        <article class="metric"><span>{counts["missing linked master"]}</span><strong>Missing linked master</strong></article>
      </div>
      {''.join(sections)}
    </main>
  </form>
  <script>
    const manualFolderFiles = {manual_files_json};

    function setSectionChecks(sectionId, checked) {{
      const section = document.getElementById(sectionId);
      if (!section) {{
        return;
      }}
      section.querySelectorAll('input[type="checkbox"][name^="checked__"]').forEach((box) => {{
        if (!box.disabled) {{
          box.checked = checked;
        }}
      }});
    }}

    function refreshManualFilenameOptions(itemId) {{
      const folderSelect = document.querySelector(`[data-manual-folder="${{itemId}}"]`);
      const fileInput = document.querySelector(`[data-manual-file="${{itemId}}"]`);
      const datalist = document.getElementById(`manual-files-${{itemId}}`);
      if (!folderSelect || !fileInput || !datalist) {{
        return;
      }}

      const files = manualFolderFiles[folderSelect.value] || [];
      datalist.innerHTML = files.map((file) => `<option value="${{file}}"></option>`).join("");
      if (!folderSelect.value) {{
        fileInput.placeholder = "Select a folder first";
      }} else {{
        fileInput.placeholder = "Enter or choose a DXF filename";
      }}
    }}

    document.querySelectorAll("[data-manual-folder]").forEach((select) => {{
      const itemId = select.getAttribute("data-manual-folder");
      refreshManualFilenameOptions(itemId);
      select.addEventListener("change", () => refreshManualFilenameOptions(itemId));
    }});

    function refreshMasterChoiceRows(itemId) {{
      document.querySelectorAll(`input[name="modal_master__${{itemId}}"]`).forEach((radio) => {{
        const row = radio.closest("tr");
        if (row) {{
          row.classList.toggle("selected-row", radio.checked);
        }}
      }});
    }}

    function openMasterModal(itemId) {{
      const hiddenInput = document.querySelector(`[data-master-hidden="${{itemId}}"]`);
      const modal = document.getElementById(`master-modal-${{itemId}}`);
      if (!hiddenInput || !modal) {{
        return;
      }}
      const selectedValue = hiddenInput.value;
      const radios = document.querySelectorAll(`input[name="modal_master__${{itemId}}"]`);
      let matched = false;
      radios.forEach((radio) => {{
        radio.checked = radio.value === selectedValue;
        matched = matched || radio.checked;
      }});
      if (!matched && radios.length > 0) {{
        radios[0].checked = true;
      }}
      refreshMasterChoiceRows(itemId);
      modal.hidden = false;
      document.body.style.overflow = "hidden";
    }}

    function closeMasterModal(itemId) {{
      const modal = document.getElementById(`master-modal-${{itemId}}`);
      if (!modal) {{
        return;
      }}
      modal.hidden = true;
      document.body.style.overflow = "";
    }}

    function applyMasterSelection(itemId) {{
      const selected = document.querySelector(`input[name="modal_master__${{itemId}}"]:checked`);
      const hiddenInput = document.querySelector(`[data-master-hidden="${{itemId}}"]`);
      const summary = document.querySelector(`[data-master-summary="${{itemId}}"]`);
      if (!selected || !hiddenInput || !summary) {{
        return;
      }}
      hiddenInput.value = selected.value;
      summary.textContent = selected.dataset.summary || "";
      closeMasterModal(itemId);
    }}

    document.addEventListener("change", (event) => {{
      const target = event.target;
      if (!(target instanceof HTMLInputElement)) {{
        return;
      }}
      if (target.name.startsWith("modal_master__")) {{
        const itemId = target.name.replace("modal_master__", "");
        refreshMasterChoiceRows(itemId);
      }}
    }});

    document.addEventListener("keydown", (event) => {{
      if (event.key !== "Escape") {{
        return;
      }}
      const openModal = document.querySelector(".modal-backdrop:not([hidden])");
      if (openModal instanceof HTMLElement) {{
        openModal.hidden = true;
        document.body.style.overflow = "";
      }}
    }});
  </script>
</body>
</html>"""


def render_review_section(*, status: str, title: str, rows: list[DXFReviewItem], dxf_catalog: DXFScanCatalog) -> str:
    """Render one review section."""
    if not rows:
        return f"""<section id="{escape(status)}">
  <header>
    <h2>{escape(title)}</h2>
    <p class="section-note">No rows.</p>
  </header>
  <div class="empty">Nothing to review here.</div>
</section>"""

    use_column_label = "Unmap" if status == "mapped" else "Use"
    header = (
        "<tr>"
        f'<th class="col-use">{escape(use_column_label)}</th>'
        '<th class="col-status">Status</th>'
        '<th class="col-source">Source</th>'
        '<th class="col-reference">Reference</th>'
        '<th class="col-cnc-part">CNC Part</th>'
        '<th class="col-product-part">Linked Product Parts</th>'
        '<th class="col-path">Current DXF File Path</th>'
        '<th class="col-mapping">Mapping Control</th>'
        '<th class="col-notes">Notes</th>'
        "</tr>"
    )
    if status == "ods_exact_matched":
        body = "\n".join(render_review_record_shell(header, item, dxf_catalog=dxf_catalog) for item in rows)
        content = f'<div class="record-list">{body}</div>'
    else:
        body = "\n".join(render_review_row(item, dxf_catalog=dxf_catalog) for item in rows)
        content = f"""<div class="table-wrap">
    <table class="review-table">
      <thead>{header}</thead>
      <tbody>{body}</tbody>
    </table>
  </div>"""
    header_actions = render_section_actions(status)
    return f"""<section id="{escape(status)}">
  <header>
    <div class="section-heading">
      <h2>{escape(title)}</h2>
      {header_actions}
    </div>
    <p class="section-note">{escape(section_note(status))}</p>
  </header>
  {content}
</section>"""


def render_review_record_shell(header_html: str, item: DXFReviewItem, *, dxf_catalog: DXFScanCatalog) -> str:
    """Render one review item as its own horizontally scrollable record shell."""
    row_html = render_review_row(item, dxf_catalog=dxf_catalog)
    return f"""<div class="record-shell">
  <div class="record-wrap">
    <table class="record-table">
      <thead>{header_html}</thead>
      <tbody>{row_html}</tbody>
    </table>
  </div>
</div>"""


def render_section_actions(status: str) -> str:
    """Render section-level action buttons where applicable."""
    if status not in {"ods_exact_matched", "auto_selected", "ambiguous", "not_found"}:
        return ""
    escaped_status = escape(status)
    return (
        '<div class="section-toolbar">'
        f'<button type="button" onclick="setSectionChecks(\'{escaped_status}\', true)">Select All Rows</button>'
        f'<button type="button" onclick="setSectionChecks(\'{escaped_status}\', false)">Unselect All Rows</button>'
        "</div>"
    )


def render_review_row(item: DXFReviewItem, *, dxf_catalog: DXFScanCatalog) -> str:
    """Render one review row."""
    checkbox_name = "unmap__" if item.status == "mapped" else "checked__"
    checkbox = (
        f'<input type="checkbox" name="{checkbox_name}{escape(item.item_id)}" value="1"'
        f'{" checked" if item.checked_by_default else ""}'
        f'{" disabled" if item.status == "missing_linked_master" else ""}>'
    )
    notes = [escape(item.reason)] if item.reason else []
    if item.grist_row_ids:
        notes.append(f"Grist rows: {escape(', '.join(display_text(value) for value in item.grist_row_ids))}")
    if item.source_kind == "ods_only":
        notes.append(f"ODS row: {escape(display_text(item.ods_row_number)) or 'N/A'}")
        if item.ods_option_group:
            notes.append(f"ODS option: {escape(item.ods_option_group)}")
        if item.ods_qty:
            notes.append(f"ODS qty: {escape(item.ods_qty)}")
        if item.mapped_material_name:
            notes.append(f"Mapped material: {escape(item.mapped_material_name)}")
        else:
            notes.append("Mapped material: &lt;missing&gt;")
    candidate_control = render_candidate_control(item, dxf_catalog=dxf_catalog)
    return f"""<tr>
  <td class="col-use">{checkbox}</td>
  <td class="col-status"><span class="status-pill {escape(item.status)}">{escape(display_status(item.status))}</span></td>
  <td class="col-source">{render_source_label(item)}</td>
  <td class="col-reference">{render_source_reference(item)}</td>
  <td class="col-cnc-part">{render_cnc_part_cell(item)}</td>
  <td class="col-product-part">{render_product_part_cell(item)}</td>
  <td class="col-path">{render_current_path_cell(item)}</td>
  <td class="col-mapping">{candidate_control}</td>
  <td class="col-notes">{'<br>'.join(notes) if notes else '<span class="muted">None</span>'}</td>
</tr>"""


def render_source_label(item: DXFReviewItem) -> str:
    """Return the source label shown in the review table."""
    if item.source_kind == "ods_only":
        return "ODS / Only In ODS"
    if item.grist_row_ids:
        return "Grist / ProductPartCNCList"
    return "Unknown"


def render_source_reference(item: DXFReviewItem) -> str:
    """Return source row or record references shown in the review table."""
    if item.source_kind == "ods_only":
        return escape(display_text(item.ods_row_number)) or '<span class="muted">N/A</span>'
    if item.grist_row_ids:
        return escape(", ".join(display_text(value) for value in item.grist_row_ids))
    return '<span class="muted">N/A</span>'


def master_summary_text(match: ODSMasterMatch | None) -> str:
    """Return a compact summary for the selected CNCPartsMaster row."""
    if match is None:
        return "No CNCPartsMaster row selected."
    prefix = match.cnc_part_name_prefix or "<blank>"
    thickness = match.thickness or "<blank>"
    qty = match.qty or "0"
    option_group = match.option_group or "<blank>"
    return (
        f"Record {match.row_id} | Prefix: {prefix} | Thickness: {thickness} | "
        f"Qty: {qty} | Option: {option_group}"
    )


def render_cnc_part_cell(item: DXFReviewItem) -> str:
    """Render the CNC part cell, including master selection for ODS-only rows."""
    if item.source_kind != "ods_only":
        return f"""<div class="stack">
    <div><strong>{escape(item.cnc_part_name)}</strong></div>
    <div class="small">CNCPartsMaster row: {escape(display_text(item.cnc_master_row_id)) or "N/A"}</div>
  </div>"""

    item_id = escape(item.item_id)
    selected_match = next(
        (match for match in item.master_matches if match.row_id == item.selected_master_row_id),
        item.master_matches[0] if item.master_matches else None,
    )
    selected_summary = master_summary_text(selected_match)
    rows = []
    for match in item.master_matches:
        checked = " checked" if selected_match is not None and match.row_id == selected_match.row_id else ""
        selected_row_class = " class=\"selected-row\"" if checked else ""
        summary_text = master_summary_text(match)
        current_path = escape(match.current_dxf_path) if match.current_dxf_path else '<span class="muted">Blank</span>'
        rows.append(
            f"""<tr{selected_row_class}>
      <td><input type="radio" name="modal_master__{item_id}" value="{match.row_id}" data-summary="{escape(summary_text)}"{checked}></td>
      <td>{match.row_id}</td>
      <td>{escape(match.cnc_part_name_prefix) or '<span class="muted">&lt;blank&gt;</span>'}</td>
      <td class="wrap">{escape(match.cnc_part_name) or '<span class="muted">&lt;blank&gt;</span>'}</td>
      <td>{escape(match.thickness) or '<span class="muted">&lt;blank&gt;</span>'}</td>
      <td>{escape(match.qty) or "0"}</td>
      <td>{escape(match.option_group) or '<span class="muted">&lt;blank&gt;</span>'}</td>
      <td><span class="{'pill-yes' if match.ready else 'pill-no'}">{'Yes' if match.ready else 'No'}</span></td>
      <td><span class="{'pill-yes' if match.verified else 'pill-no'}">{'Yes' if match.verified else 'No'}</span></td>
      <td class="wrap">{current_path}</td>
    </tr>"""
        )
    return f"""<div class="stack">
    <div><strong>{escape(item.cnc_part_name)}</strong></div>
    <div class="small">Choose CNCPartsMaster row</div>
    <input type="hidden" name="master__{item_id}" value="{escape(display_text(selected_match.row_id if selected_match else ''))}" data-master-hidden="{item_id}">
    <div class="master-summary" data-master-summary="{item_id}">{escape(selected_summary)}</div>
    <div class="master-actions">
      <button type="button" onclick="openMasterModal('{item_id}')">Choose Grist Row</button>
    </div>
    <div class="modal-backdrop" id="master-modal-{item_id}" hidden onclick="if (event.target === this) closeMasterModal('{item_id}')">
      <div class="modal-card" role="dialog" aria-modal="true" aria-labelledby="master-modal-title-{item_id}">
        <div class="modal-header">
          <div class="modal-title" id="master-modal-title-{item_id}">Select CNCPartsMaster row</div>
          <div class="modal-subtitle">{escape(item.cnc_part_name)} | ODS row {escape(display_text(item.ods_row_number) or 'N/A')}</div>
        </div>
        <div class="modal-body">
          <div class="modal-table-wrap">
            <table class="choice-table">
              <thead>
                <tr>
                  <th>Use</th>
                  <th>Record ID</th>
                  <th>CNCPartNamePrefix</th>
                  <th>CNCPartName</th>
                  <th>Thickness</th>
                  <th>Qty Of Part Used</th>
                  <th>Optional Item Group 1</th>
                  <th>Ready</th>
                  <th>Verified</th>
                  <th>Current DXF File Path</th>
                </tr>
              </thead>
              <tbody>{''.join(rows)}</tbody>
            </table>
          </div>
        </div>
        <div class="modal-footer">
          <button type="button" onclick="closeMasterModal('{item_id}')">Cancel</button>
          <button class="primary" type="button" onclick="applyMasterSelection('{item_id}')">Use Selected Row</button>
        </div>
      </div>
    </div>
  </div>"""


def render_product_part_cell(item: DXFReviewItem) -> str:
    """Render linked ProductPart names or a ProductPart selector."""
    if item.source_kind != "ods_only":
        return "<br>".join(escape(part) for part in item.linked_product_parts) or '<span class="muted">None</span>'

    options = ['<option value="">Select ProductPart</option>']
    for choice in item.product_part_choices:
        selected = " selected" if choice.product_part_id == item.selected_product_part_id else ""
        options.append(f'<option value="{choice.product_part_id}"{selected}>{escape(choice.name)}</option>')
    return f"""<div class="stack">
    <div class="small">Choose ProductPart to assign</div>
    <div><select name="product_part__{escape(item.item_id)}">{''.join(options)}</select></div>
  </div>"""


def render_current_path_cell(item: DXFReviewItem) -> str:
    """Render current DXF path details."""
    if item.source_kind != "ods_only":
        return escape(item.current_dxf_path) if item.current_dxf_path else '<span class="muted">Blank</span>'

    lines = []
    for match in item.master_matches:
        path_value = escape(match.current_dxf_path) if match.current_dxf_path else '<span class="muted">Blank</span>'
        lines.append(f"Row {match.row_id}: {path_value}")
    return "<br>".join(lines) if lines else '<span class="muted">Blank</span>'


def render_candidate_control(item: DXFReviewItem, *, dxf_catalog: DXFScanCatalog) -> str:
    """Render candidate select grouped by folder."""
    if item.status == "missing_linked_master":
        return '<span class="muted">Cannot update without a linked CNCPartsMaster row.</span>'
    if item.status == "mapped":
        return '<span class="muted">Check Unmap to clear the saved DXF filepath.</span>'
    if not item.candidates:
        return render_manual_mapping_control(item, dxf_catalog=dxf_catalog)

    by_group: dict[str, list[DXFCandidate]] = defaultdict(list)
    for candidate in item.candidates:
        by_group[candidate.group].append(candidate)

    groups = []
    for group in sorted(by_group.keys(), key=str.casefold):
        options = []
        for candidate in by_group[group]:
            selected = " selected" if candidate.full_path == item.selected_candidate_path else ""
            label = candidate.relative_path
            options.append(
                f'<option value="{escape(candidate.full_path)}"{selected}>{escape(label)}</option>'
            )
        groups.append(f'<optgroup label="{escape(group)}">{"".join(options)}</optgroup>')
    return f'<select name="selected__{escape(item.item_id)}">{"".join(groups)}</select>'


def render_manual_mapping_control(item: DXFReviewItem, *, dxf_catalog: DXFScanCatalog) -> str:
    """Render manual folder and filename controls for unmatched rows."""
    folder_options = ['<option value="">Select folder</option>']
    for folder in dxf_catalog.folder_options:
        selected = " selected" if folder == item.manual_folder_default else ""
        folder_options.append(f'<option value="{escape(folder)}"{selected}>{escape(folder)}</option>')

    item_id = escape(item.item_id)
    filename_value = escape(item.manual_filename_default)
    placeholder = "Enter or choose a DXF filename" if item.manual_folder_default else "Select a folder first"
    return f"""<div class="stack">
    <div>
      <select name="manual_folder__{item_id}" data-manual-folder="{item_id}">
        {''.join(folder_options)}
      </select>
    </div>
    <div>
      <input
        type="text"
        name="manual_filename__{item_id}"
        value="{filename_value}"
        list="manual-files-{item_id}"
        data-manual-file="{item_id}"
        placeholder="{escape(placeholder)}">
      <datalist id="manual-files-{item_id}"></datalist>
    </div>
  </div>"""


def section_note(status: str) -> str:
    """Return help text for a review section."""
    notes = {
        "mapped": "A full DXF filepath is already saved in Grist. Check Unmap only if you want to clear it.",
        "ods_exact_matched": "ODS rows not yet in ProductPartCNCList. Choose a ProductPart, confirm the CNCPartsMaster row, then save to add the assignment and DXF path.",
        "auto_selected": "One exact non-Old candidate was found and preselected.",
        "ambiguous": "Multiple exact non-Old candidates were found. The top-ranked folder is preselected.",
        "not_found": "No exact non-Old candidate was found. Pick a folder and DXF filename to save an exact filepath, or leave it blank to clear on save.",
        "missing_linked_master": "These rows could not be updated because the linked CNCPartsMaster row is missing.",
    }
    return notes.get(status, "")


def display_status(status: str) -> str:
    """Return a user-facing review status."""
    if status == "ods_exact_matched":
        return "ODS Match"
    return status.replace("_", " ").title()


def render_completion_page(outcome: DXFReviewOutcome) -> str:
    """Render a simple completion/cancel page."""
    title = "DXF Review Saved" if outcome.action == "saved" else "DXF Review Cancelled"
    message = (
        f"Mode: {outcome.mode.title()} | Updated: {outcome.updated_rows} | "
        f"Cleared: {outcome.cleared_rows} | Unchanged: {outcome.unchanged_rows} | "
        f"Skipped: {outcome.skipped_rows} | Created: {outcome.created_rows} | "
        f"Assignment Rows Updated: {outcome.assignment_rows_updated}"
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{escape(title)}</title>
  <style>
    body {{
      margin: 0;
      font-family: "Segoe UI", Arial, sans-serif;
      background: #f4f5f6;
      color: #1f2933;
      display: grid;
      place-items: center;
      min-height: 100vh;
    }}
    article {{
      background: #fff;
      border: 1px solid #d7dde4;
      border-radius: 14px;
      padding: 28px 32px;
      box-shadow: 0 12px 28px rgba(18, 31, 46, 0.08);
      max-width: 680px;
    }}
    h1 {{
      margin: 0 0 10px;
    }}
    p {{
      margin: 0;
      color: #64748b;
      line-height: 1.5;
    }}
  </style>
</head>
<body>
  <article>
    <h1>{escape(title)}</h1>
    <p>{escape(message)}</p>
  </article>
</body>
</html>"""


def render_error_page(exc: Exception) -> str:
    """Render a readable error page instead of dropping the HTTP response."""
    detail = display_text(exc) or exc.__class__.__name__
    stack = traceback.format_exc()
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>DXF Review Save Failed</title>
  <style>
    body {{
      margin: 0;
      font-family: "Segoe UI", Arial, sans-serif;
      background: #f4f5f6;
      color: #1f2933;
      display: grid;
      place-items: center;
      min-height: 100vh;
      padding: 24px;
    }}
    article {{
      background: #fff;
      border: 1px solid #d7dde4;
      border-radius: 14px;
      padding: 28px 32px;
      box-shadow: 0 12px 28px rgba(18, 31, 46, 0.08);
      max-width: 860px;
      width: 100%;
    }}
    h1 {{
      margin: 0 0 10px;
      color: #b42318;
    }}
    p {{
      margin: 0 0 12px;
      color: #64748b;
      line-height: 1.5;
    }}
    pre {{
      margin: 0;
      padding: 14px;
      border-radius: 10px;
      background: #f8fafc;
      border: 1px solid #d7dde4;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      font-size: 12px;
    }}
  </style>
</head>
<body>
  <article>
    <h1>DXF review save failed</h1>
    <p>{escape(detail)}</p>
    <p>The utility kept running, and the full error was written to the log. You can use the browser Back button to review selections again after fixing the issue.</p>
    <pre>{escape(stack)}</pre>
  </article>
</body>
</html>"""

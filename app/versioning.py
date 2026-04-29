"""Create-new-version workflow for ODS costing files."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.prompt import Confirm, Prompt

from app.config_models import AppConfig, CostingRules
from app.reports import ReportWriter, report_dir_for_source
from app.utils import display_text, is_blank, slugify
from app.workbook import OdsWorkbook, SheetView


def collect_part_names(workbook: OdsWorkbook, app_config: AppConfig) -> list[str]:
    """Collect unique active part names from all configured sheets."""
    part_names: dict[str, str] = {}
    for sheet_name, sheet_config in app_config.supported_sheets.items():
        if sheet_name not in workbook.sheets:
            continue
        sheet = workbook.sheet_view(sheet_name, sheet_config)
        column_map = sheet.column_map()
        part_indexes = column_map.indexes_for(sheet_config.part_name_fields)
        if not part_indexes:
            continue

        for row_info in sheet.data_rows():
            if sheet.classify_row(row_info.values, column_map).inactive:
                continue
            for index in part_indexes.values():
                value = display_text(sheet.get_value(row_info.values, index))
                if value:
                    part_names.setdefault(value.casefold(), value)
    return sorted(part_names.values(), key=str.casefold)


def ask_part_remaps(console: Console, part_names: list[str]) -> dict[str, str]:
    """Ask for one-by-one part name remaps."""
    remaps: dict[str, str] = {}
    if not part_names:
        console.print("[yellow]No active part names were found in configured sheets.[/yellow]")
        return remaps

    console.print("[bold]Part name remapping[/bold]")
    console.print("Leave the new name blank to keep the old name.")
    for name in part_names:
        console.print(f"\nOld part name: [cyan]{name}[/cyan]")
        new_name = Prompt.ask("Enter new name for this part", default="", show_default=False).strip()
        remaps[name] = new_name or name
    return remaps


def create_new_version(
    *,
    console: Console,
    workbook: OdsWorkbook,
    app_config: AppConfig,
    source_path: Path,
) -> tuple[Path, Path]:
    """Run the new-version workflow and write a new ODS plus remap CSV report."""
    part_names = collect_part_names(workbook, app_config)
    remaps = ask_part_remaps(console, part_names)
    include_costing = Confirm.ask("Should costing data be included?", default=True)

    changes: list[dict[str, Any]] = []
    removed_rows: list[dict[str, Any]] = []
    costing_actions: list[dict[str, Any]] = []

    for sheet_name, sheet_config in app_config.supported_sheets.items():
        if sheet_name not in workbook.sheets:
            console.print(f"[yellow]Skipping missing sheet:[/yellow] {sheet_name}")
            continue
        sheet = workbook.sheet_view(sheet_name, sheet_config)
        column_map = sheet.column_map()

        part_indexes = column_map.indexes_for(sheet_config.part_name_fields)
        for row_info in sheet.data_rows():
            if sheet.classify_row(row_info.values, column_map).inactive:
                continue
            for field_name, index in part_indexes.items():
                old_value = display_text(sheet.get_value(row_info.values, index))
                new_value = remaps.get(old_value, old_value)
                if old_value and old_value != new_value:
                    sheet.set_value(row_info.values, index, new_value)
                    changes.append(
                        {
                            "sheet": sheet_name,
                            "row_number": row_info.number,
                            "field": field_name,
                            "old_part_name": old_value,
                            "new_part_name": new_value,
                        }
                    )

        if not include_costing:
            costing_actions.extend(apply_costing_exclusion(sheet, sheet_config.costing))

        removed_rows.extend(sheet.remove_inactive_rows())

    workbook.keep_only_sheets(app_config.supported_sheets.keys())
    output_path = build_output_path(source_path, Path(app_config.output_dir))
    workbook.save_as(output_path)

    report_rows = changes or [
        {
            "sheet": "",
            "row_number": "",
            "field": "",
            "old_part_name": "",
            "new_part_name": "",
            "message": "No part names changed.",
        }
    ]
    for row in removed_rows:
        report_rows.append({"message": "Removed inactive row", **row})
    for row in costing_actions:
        report_rows.append({"message": "Costing data excluded", **row})

    report_path = ReportWriter(report_dir_for_source(app_config.reports_dir, source_path)).write_csv(
        f"remap_{source_path.stem}",
        report_rows,
    )
    return output_path, report_path


def apply_costing_exclusion(sheet: SheetView, costing: CostingRules) -> list[dict[str, Any]]:
    """Blank or remove configured costing columns on active data rows."""
    column_map = sheet.column_map()
    indexes = column_map.indexes_for(costing.fields)
    actions: list[dict[str, Any]] = []
    if not indexes:
        return actions

    if costing.mode == "remove":
        sheet.delete_columns(list(indexes.values()))
        return [
            {
                "sheet": sheet.name,
                "row_number": "",
                "field": field_name,
                "action": "removed column",
            }
            for field_name in indexes
        ]

    for row_info in sheet.data_rows():
        if sheet.classify_row(row_info.values, column_map).inactive:
            continue
        for field_name, index in indexes.items():
            if not is_blank(sheet.get_value(row_info.values, index)):
                sheet.set_value(row_info.values, index, "")
                actions.append(
                    {
                        "sheet": sheet.name,
                        "row_number": row_info.number,
                        "field": field_name,
                        "action": "blanked cell",
                    }
                )
    return actions


def build_output_path(source_path: Path, output_dir: Path) -> Path:
    """Build a non-overwriting output file path."""
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    candidate = output_dir / f"{slugify(source_path.stem)}_new_version_{stamp}.ods"
    counter = 2
    while candidate.exists():
        candidate = output_dir / f"{slugify(source_path.stem)}_new_version_{stamp}_{counter}.ods"
        counter += 1
    return candidate

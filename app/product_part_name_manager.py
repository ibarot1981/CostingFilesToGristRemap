"""Interactive utilities for maintaining product part names in Grist."""

from __future__ import annotations

from datetime import datetime
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
import re
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

from app.config import load_material_mapping
from app.config_models import AppConfig, SheetConfig
from app.exceptions import GristError
from app.grist import GristClient
from app.utils import as_decimal, display_text, is_blank, normalize_header, normalize_text
from app.verification import verify_sheet_with_grist
from app.workbook import OdsWorkbook, SheetView


PRODUCT_PART_MASTER_TABLE = "ProductPartMaster"
PRODUCT_PART_MS_LIST_TABLE = "ProductPartMSList"
PRODUCT_PART_TOOLSHOP_LIST_TABLE = "ProductPartToolShopList"
PRODUCT_PART_CNC_LIST_TABLE = "ProductPartCNCList"
PRODUCT_MODEL_CONFIG_TABLE = "ProductModelConfig"
PRODUCT_MODEL_MASTER_TABLE = "ProductModelMaster2"
MASTER_MATERIAL_TABLE = "MasterMaterial"
BLANKS_LABEL = "Blanks"
SHOW_ALL_LABEL = "__SHOW_ALL__"
GO_BACK_LABEL = "__GO_BACK__"
PART_CATEGORY_HEADER = "Part Category"
CNC_PRODUCT_PART_FIELD = "ProductPartName_ProductPartName2"
MS_SHEET_NAME = "5. Material Cut List Price"
TOOL_SHOP_SHEET_NAME = "Tool Shop Items"


@dataclass(frozen=True)
class ProductPartSummarySheetSpec:
    """One ODS-to-Grist summary comparison target."""

    sheet_name: str
    menu_label: str
    ods_field_label: str
    use_configured_product_part_name: bool


@dataclass(frozen=True)
class ProductPartMasterRow:
    """One ProductPartMaster row used for selection and updates."""

    row_id: int
    product_part_name: str
    part_description: str


@dataclass(frozen=True)
class ProductModelChoice:
    """One distinct ProductModelConfig model-code selection."""

    product_model_id: int
    product_model_code: str
    description: str
    product_part_names: set[str]
    product_part_ids: set[int]


@dataclass(frozen=True)
class ProductPartMSListRow:
    """One ProductPartMSList row used in assignment previews."""

    row_id: int
    record_id: str
    machine_piece_desc: str
    material_to_cut: str
    length_mm: str
    qty_nos: str
    option_group_1_temp: str
    remarks: str
    current_product_part_name: str
    current_product_part_id: int | None
    material_row_id: int | None
    part_status: str
    part_status_remark: str


@dataclass(frozen=True)
class ExistingMsProductPartChoice:
    """One distinct current ProductPartName value found in ProductPartMSList."""

    product_part_name: str
    row_count: int


@dataclass(frozen=True)
class MasterMaterialRow:
    """One MasterMaterial row used for MaterialToCut resolution."""

    row_id: int
    master_material: str


@dataclass(frozen=True)
class OdsMsImportRow:
    """One active Material Cut List ODS row prepared for ProductPartMSList import."""

    row_number: int
    machine_piece_desc: str
    ods_material_used: str
    mapped_grist_material_name: str
    mapped_grist_material_id: int
    dimension_mm: str
    dimension_mm_value: Any
    qty: str
    qty_value: Any
    optional_item_group_1: str
    remarks: str


@dataclass(frozen=True)
class MsImportIssue:
    """One Material Cut List ODS row that cannot be imported safely yet."""

    row_number: int
    machine_piece_desc: str
    ods_material_used: str
    reason: str


@dataclass(frozen=True)
class MsSyncAction:
    """One planned ProductPartMSList create or update action."""

    action: str
    target_status: str
    detail: str
    ods_row: OdsMsImportRow | None = None
    existing_row: ProductPartMSListRow | None = None


@dataclass(frozen=True)
class OdsToolShopImportRow:
    """One active Tool Shop ODS row prepared for ProductPartToolShopList import."""

    row_number: int
    machine_piece_desc: str
    ods_material_used: str
    mapped_grist_material_name: str
    mapped_grist_material_id: int | None
    dimension_mm: str
    dimension_mm_value: Any
    qty: str
    qty_value: Any
    optional_item_group_1: str
    remarks: str
    job_remarks: str


@dataclass(frozen=True)
class ToolShopImportIssue:
    """One Tool Shop ODS row that cannot be imported safely yet."""

    row_number: int
    machine_piece_desc: str
    ods_material_used: str
    reason: str


@dataclass(frozen=True)
class ExistingToolShopChoice:
    """One distinct MachinePieceDesc value found in Tool Shop ODS rows."""

    machine_piece_desc: str
    row_count: int


@dataclass(frozen=True)
class ProductPartToolShopRow:
    """One existing ProductPartToolShopList row used for duplicate checks."""

    row_id: int
    product_part_id: int | None
    product_part_name: str
    material_row_id: int | None
    material_name: str
    length_mm: str
    qty_nos: str
    option_group_1: str
    toolshop_part_name: str
    job_remarks: str


@dataclass(frozen=True)
class ToolShopDuplicateMatch:
    """One import row that already exists in ProductPartToolShopList."""

    ods_row: OdsToolShopImportRow
    existing_row: ProductPartToolShopRow


@dataclass(frozen=True)
class PartMatchSuggestion:
    """One suggested Grist ProductPartName for an ODS label."""

    product_part_name: str
    score: int


@dataclass(frozen=True)
class OdsPartLabel:
    """One distinct ODS label collected from a sheet."""

    value: str
    row_count: int


@dataclass(frozen=True)
class PartComparisonResult:
    """Match outcome for one ODS part label."""

    ods_value: str
    status: str
    matched_name: str
    suggestions: tuple[PartMatchSuggestion, ...]


@dataclass(frozen=True)
class PartComparisonSummary:
    """Per-sheet summary of ODS-to-Grist comparison outcomes."""

    sheet_name: str
    menu_label: str
    ods_field_label: str
    total_ods_entries: int
    matched_count: int
    ambiguous_count: int
    missing_count: int
    results: tuple[PartComparisonResult, ...]


@dataclass(frozen=True)
class MissingPartResolution:
    """One user-entered mapping for an unresolved ODS label."""

    ods_value: str
    target_product_part_name: str
    part_description: str
    sheets: tuple[str, ...]


@dataclass(frozen=True)
class UnresolvedOdsEntry:
    """One distinct unresolved ODS label across one or more sheets."""

    ods_value: str
    sheets: tuple[str, ...]
    suggestions: tuple[PartMatchSuggestion, ...]


SUMMARY_COMPARE_SHEETS = (
    ProductPartSummarySheetSpec(
        sheet_name="5. Material Cut List Price",
        menu_label="Material Cut List",
        ods_field_label="Machine Piece Description",
        use_configured_product_part_name=True,
    ),
    ProductPartSummarySheetSpec(
        sheet_name="Tool Shop Items",
        menu_label="Tool Shop",
        ods_field_label="Machine Piece Description",
        use_configured_product_part_name=True,
    ),
    ProductPartSummarySheetSpec(
        sheet_name="CNC Cut List",
        menu_label="CNC Cut List",
        ods_field_label="Part Category",
        use_configured_product_part_name=False,
    ),
)


def show_selection_source(console: Console, source: str, detail: str) -> None:
    """Render a prominent source banner before a selection table."""
    console.print(
        Panel(
            f"[bold]Source:[/bold] {source}\n{detail}",
            title="Selection Source",
            border_style="yellow",
            padding=(0, 2),
        )
    )


def manage_product_part_names(
    console: Console,
    workbook: OdsWorkbook,
    app_config: AppConfig,
    material_mapping_path: Path,
) -> None:
    """Run the interactive Product Part Name manager."""
    client = GristClient.from_environment()
    while True:
        choice = ask_product_part_name_menu(console)
        if choice == "1":
            add_product_part_name(console, client)
        elif choice == "2":
            assign_part_to_config(console, client)
        elif choice == "3":
            assign_product_part_name(console, client)
        elif choice == "4":
            summary_compare_ods_parts(console, client, workbook, app_config)
        elif choice == "5":
            import_toolshop_rows_from_ods(
                console,
                client,
                workbook,
                app_config,
                material_mapping_path,
            )
        elif choice == "6":
            import_ms_rows_from_ods(
                console,
                client,
                workbook,
                app_config,
                material_mapping_path,
            )
        else:
            return


def ask_product_part_name_menu(console: Console) -> str:
    """Render the Product Part Name manager menu."""
    table = Table(title="Manage Product Part Names")
    table.add_column("Option", justify="right", style="cyan")
    table.add_column("Action")
    table.add_row("1", "Add new Product Part Name")
    table.add_row("2", "Assign part to config")
    table.add_row("3", "Assign Product Part to MS Cut List")
    table.add_row("4", "Summary Compare ODS Parts vs Grist")
    table.add_row("5", "Add Tool Shop rows from ODS to Grist")
    table.add_row("6", "Add New MS Cut List from ODS")
    table.add_row("7", "Back")
    console.print(table)
    return Prompt.ask("Choose an option", choices=["1", "2", "3", "4", "5", "6", "7"], default="1")


def summary_compare_ods_parts(
    console: Console,
    client: GristClient,
    workbook: OdsWorkbook,
    app_config: AppConfig,
) -> None:
    """Compare distinct ODS part labels against Grist product-part names in scope."""
    console.print(
        Panel(
            "\n".join(
                [
                    "Choose the ProductModelCode to scope the comparison.",
                    "The tool will compare distinct ODS part labels from Material Cut List, Tool Shop, and CNC",
                    "against the matching Grist product-part names already in scope for that model.",
                    "Unresolved ODS labels can then be batch created in ProductPartMaster and assigned to ProductModelConfig.",
                ]
            ),
            title="ODS vs Grist Product Part Summary",
            border_style="cyan",
            padding=(1, 2),
        )
    )
    model_choice = choose_product_model_code_for_part_summary(console, client)
    if model_choice is None:
        console.print("[yellow]Nothing changed.[/yellow]")
        return

    master_rows = fetch_product_part_master_rows(client)
    summary_results: list[PartComparisonSummary] = []
    for spec in SUMMARY_COMPARE_SHEETS:
        sheet_config = app_config.supported_sheets.get(spec.sheet_name)
        if sheet_config is None:
            console.print(f"[yellow]Skipping missing sheet config:[/yellow] {spec.sheet_name}")
            continue
        ods_labels = collect_distinct_ods_part_labels(workbook, sheet_config, spec)
        grist_names = fetch_scoped_sheet_product_part_names(client, model_choice, sheet_config, spec)
        summary_results.append(
            build_part_comparison_summary(
                spec=spec,
                ods_labels=ods_labels,
                grist_names=grist_names,
                master_rows=master_rows,
            )
        )

    if not summary_results:
        console.print("[yellow]No summary sheets were available to compare.[/yellow]")
        return

    render_part_comparison_summaries(console, model_choice, summary_results)
    unresolved = collect_unresolved_ods_entries(summary_results, master_rows)
    if not unresolved:
        console.print("[green]All distinct ODS entries matched confidently in the current Grist scope.[/green]")
        return

    while True:
        review_action = ask_unresolved_review_action(console, len(unresolved))
        if review_action == "3":
            return
        if review_action == "1":
            unresolved_to_review = unresolved
            break
        selected_entries = choose_selected_unresolved_entries(console, unresolved)
        if selected_entries is None:
            continue
        unresolved_to_review = selected_entries
        break

    resolutions = prompt_missing_part_resolutions(console, unresolved_to_review, master_rows)
    if not resolutions:
        console.print("[yellow]No product part names were queued for creation or assignment.[/yellow]")
        return

    apply_missing_part_resolutions(console, client, model_choice, master_rows, resolutions)


def import_toolshop_rows_from_ods(
    console: Console,
    client: GristClient,
    workbook: OdsWorkbook,
    app_config: AppConfig,
    material_mapping_path: Path,
) -> None:
    """Create ProductPartToolShopList rows from ODS Tool Shop Items rows."""
    sheet_config = app_config.supported_sheets.get(TOOL_SHOP_SHEET_NAME)
    if sheet_config is None:
        raise GristError(f"Missing sheet configuration for {TOOL_SHOP_SHEET_NAME}.")

    console.print(
        Panel(
            "\n".join(
                [
                    "This workflow creates ProductPartToolShopList rows from active ODS Tool Shop Items rows.",
                    "You will choose a ProductModelCode, then a ProductPartName already configured for that model.",
                    "Next you will select one or more ODS Machine Piece Description groups to import into Grist.",
                    "After the rows are created, Tool Shop verification will run immediately with All options selected.",
                ]
            ),
            title="Import Tool Shop Rows From ODS",
            border_style="cyan",
            padding=(1, 2),
        )
    )

    model_choice = choose_product_model_code(console, client)
    if model_choice is None:
        console.print("[yellow]Nothing changed.[/yellow]")
        return

    product_part_row = choose_product_part_row_for_model_choice(console, client, model_choice)
    if product_part_row is None:
        console.print("[yellow]Nothing changed.[/yellow]")
        return

    material_mapping = load_material_mapping(material_mapping_path)
    master_material_rows = fetch_master_material_rows(client)
    importable_rows, import_issues = collect_toolshop_import_rows(
        workbook,
        sheet_config,
        material_mapping,
        master_material_rows,
    )
    if import_issues:
        show_toolshop_import_issues(console, import_issues)
        if not Confirm.ask(
            "Continue with only the Tool Shop ODS rows that have valid material mappings?",
            default=False,
        ):
            console.print("[yellow]Nothing changed.[/yellow]")
            return
    if not importable_rows:
        console.print("[yellow]No eligible Tool Shop ODS rows were available to import.[/yellow]")
        return

    while True:
        selected_machine_piece_descs = choose_ods_toolshop_machine_piece_descs(console, importable_rows)
        if selected_machine_piece_descs is None:
            console.print("[yellow]Nothing changed.[/yellow]")
            return

        selected_rows = filter_toolshop_import_rows_by_machine_piece_descs(
            importable_rows,
            selected_machine_piece_descs,
        )
        if not selected_rows:
            console.print("[yellow]No Tool Shop ODS rows matched that Machine Piece Description selection.[/yellow]")
            continue

        console.print(f"[cyan]Selected ProductModelCode:[/cyan] {model_choice.product_model_code}")
        console.print(f"[cyan]Selected ProductPartName:[/cyan] {product_part_row.product_part_name}")
        show_toolshop_import_rows(console, selected_rows, title="Tool Shop ODS Rows To Import")
        preview_action = ask_toolshop_import_preview_action(console, len(selected_rows))
        if preview_action == "1":
            break
        if preview_action == "2":
            console.print("[cyan]Going back to the ODS Machine Piece Description selection.[/cyan]")
            continue
        console.print("[yellow]Nothing changed.[/yellow]")
        return

    existing_rows = fetch_product_part_toolshop_rows(client)
    duplicate_matches = collect_toolshop_duplicate_matches(existing_rows, selected_rows, product_part_row)
    rows_to_create = list(selected_rows)
    if duplicate_matches:
        show_toolshop_duplicate_matches(console, duplicate_matches, product_part_row.product_part_name)
        duplicate_action = ask_toolshop_duplicate_action(console, len(duplicate_matches))
        if duplicate_action == "1":
            duplicate_row_numbers = {match.ods_row.row_number for match in duplicate_matches}
            rows_to_create = [
                row
                for row in selected_rows
                if row.row_number not in duplicate_row_numbers
            ]
            if not rows_to_create:
                console.print("[yellow]All selected rows were duplicates. Nothing changed.[/yellow]")
                return
        elif duplicate_action == "3":
            console.print("[yellow]Nothing changed.[/yellow]")
            return

    show_toolshop_create_plan(console, rows_to_create, product_part_row, model_choice)
    if not Confirm.ask(
        f"Create {len(rows_to_create)} ProductPartToolShopList row(s) in Grist now?",
        default=True,
    ):
        console.print("[yellow]Nothing changed.[/yellow]")
        return

    created = client.create_table_records(
        PRODUCT_PART_TOOLSHOP_LIST_TABLE,
        [build_toolshop_create_payload(row, product_part_row) for row in rows_to_create],
    )
    console.print(
        f"[green]Created {len(created)} ProductPartToolShopList row(s) for:[/green] "
        f"{product_part_row.product_part_name}"
    )

    verification_product_index = toolshop_verification_product_index(client, model_choice)
    console.print(
        Panel(
            "\n".join(
                [
                    f"Running Tool Shop verification for ProductModelCode {model_choice.product_model_code}.",
                    "ODS option scope is being forced to All options for this post-import verification run.",
                    "You will still be asked whether to update TallyWithODS in Grist.",
                ]
            ),
            title="Post-Import Verification",
            border_style="cyan",
            padding=(1, 2),
        )
    )
    verification_run = verify_sheet_with_grist(
        console=console,
        workbook=workbook,
        app_config=app_config,
        sheet_name=TOOL_SHOP_SHEET_NAME,
        material_mapping_path=material_mapping_path,
        replay_filter_mode=2,
        replay_product_index=verification_product_index,
        replay_scope_mode=3,
    )
    if verification_run is None:
        console.print("[yellow]Tool Shop verification was not completed.[/yellow]")
        return

    _, csv_path, json_path, html_path, _, _, _, _, _, _, _ = verification_run
    console.print(f"[green]Tool Shop HTML report:[/green] {html_path}")
    console.print(f"[green]Tool Shop CSV report:[/green] {csv_path}")
    console.print(f"[green]Tool Shop JSON report:[/green] {json_path}")


def choose_product_part_row_for_model_choice(
    console: Console,
    client: GristClient,
    model_choice: ProductModelChoice,
) -> ProductPartMasterRow | None:
    """Prompt for a ProductPartName limited to the selected ProductModelCode scope."""
    scoped_rows = [
        row
        for row in fetch_product_part_master_rows(client)
        if row.row_id in model_choice.product_part_ids
    ]
    if not scoped_rows:
        raise GristError(
            f"ProductModelCode {model_choice.product_model_code} has no ProductPartMaster rows in scope."
        )

    while True:
        show_selection_source(
            console,
            "Grist",
            f"Selecting ProductPartName values from ProductModelConfig scope for {model_choice.product_model_code}.",
        )
        table = Table(title=f"ProductPartName Scope for {model_choice.product_model_code}")
        table.add_column("Option", justify="right", style="cyan")
        table.add_column("Action")
        table.add_row("1", "Search ProductPartName / PartDescription")
        table.add_row("2", "Show all scoped ProductPartNames")
        table.add_row("3", "Back")
        console.print(table)
        action = Prompt.ask("Choose an option", choices=["1", "2", "3"], default="2")
        if action == "3":
            return None
        if action == "1":
            query = Prompt.ask(
                "Search ProductPartName or PartDescription inside the selected ProductModelCode",
                default="",
                show_default=False,
            ).strip()
            matches = search_product_part_master_rows(scoped_rows, query)
            if not matches:
                console.print("[yellow]No scoped ProductPartName values matched that search.[/yellow]")
                continue
            selected_row = choose_product_part_master_row_from_matches(console, matches)
        else:
            selected_row = choose_product_part_master_row_from_matches(console, scoped_rows)
        if selected_row is None:
            continue
        return selected_row


def collect_toolshop_import_rows(
    workbook: OdsWorkbook,
    sheet_config: SheetConfig,
    material_mapping: dict[str, str],
    master_material_rows: list[MasterMaterialRow],
) -> tuple[list[OdsToolShopImportRow], list[ToolShopImportIssue]]:
    """Return active Tool Shop ODS rows that can be safely imported."""
    sheet = workbook.sheet_view(TOOL_SHOP_SHEET_NAME, sheet_config)
    column_map = sheet.column_map()
    machine_piece_index = column_map.index_for("product_part_name")
    material_index = column_map.index_for("material_to_cut")
    dimension_index = column_map.index_for("dimension_to_cut_mm")
    qty_index = column_map.index_for("qty")
    option_group_index = column_map.index_for("optional_item_group_1")
    remarks_index = column_map.index_for("toolshop_part_name")
    job_remarks_index = column_map.index_for("job_remarks")

    missing_required = [
        label
        for label, index in (
            ("Machine Piece Description", machine_piece_index),
            ("Material Used", material_index),
            ("Dimension in mm", dimension_index),
            ("Qty", qty_index),
        )
        if index is None
    ]
    if missing_required:
        raise GristError(
            f"{TOOL_SHOP_SHEET_NAME}: missing required ODS column(s): {', '.join(missing_required)}."
        )

    master_by_name = {
        normalize_text(row.master_material): row
        for row in master_material_rows
    }
    importable_rows: list[OdsToolShopImportRow] = []
    issues: list[ToolShopImportIssue] = []
    for row_info in sheet.data_rows():
        decision = sheet.classify_row(row_info.values, column_map)
        if decision.inactive:
            continue

        machine_piece_desc = display_text(sheet.get_value(row_info.values, machine_piece_index)).strip()
        ods_material_used = display_text(sheet.get_value(row_info.values, material_index)).strip()
        if not machine_piece_desc:
            issues.append(
                ToolShopImportIssue(
                    row_number=row_info.number,
                    machine_piece_desc="",
                    ods_material_used=ods_material_used,
                    reason="Machine Piece Description is blank.",
                )
            )
            continue
        mapped_material_name = material_mapping.get(ods_material_used)
        master_material_row: MasterMaterialRow | None = None
        if mapped_material_name:
            master_material_row = master_by_name.get(normalize_text(mapped_material_name))
        if mapped_material_name and master_material_row is None:
            issues.append(
                ToolShopImportIssue(
                    row_number=row_info.number,
                    machine_piece_desc=machine_piece_desc,
                    ods_material_used=ods_material_used,
                    reason=f"Mapped Grist material '{mapped_material_name}' was not found in MasterMaterial.",
                )
            )
            continue

        dimension_value = sheet.get_value(row_info.values, dimension_index)
        qty_value = sheet.get_value(row_info.values, qty_index)
        importable_rows.append(
            OdsToolShopImportRow(
                row_number=row_info.number,
                machine_piece_desc=machine_piece_desc,
                ods_material_used=ods_material_used,
                mapped_grist_material_name=(
                    "" if master_material_row is None else master_material_row.master_material
                ),
                mapped_grist_material_id=None if master_material_row is None else master_material_row.row_id,
                dimension_mm=display_text(dimension_value),
                dimension_mm_value=dimension_value,
                qty=display_text(qty_value),
                qty_value=qty_value,
                optional_item_group_1=display_text(sheet.get_value(row_info.values, option_group_index)),
                remarks=display_text(sheet.get_value(row_info.values, remarks_index)),
                job_remarks=display_text(sheet.get_value(row_info.values, job_remarks_index)),
            )
        )
    return sorted(
        importable_rows,
        key=lambda row: (
            normalize_text(row.machine_piece_desc),
            row.row_number,
        ),
    ), issues


def show_toolshop_import_issues(console: Console, issues: list[ToolShopImportIssue]) -> None:
    """Render Tool Shop ODS rows that cannot be imported yet."""
    table = Table(title="Tool Shop Import Issues", caption=f"{len(issues)} row(s)")
    table.add_column("ODS Row", justify="right")
    table.add_column("Machine Piece Description")
    table.add_column("Material Used")
    table.add_column("Reason")
    for issue in issues:
        table.add_row(
            str(issue.row_number),
            issue.machine_piece_desc or BLANKS_LABEL,
            issue.ods_material_used,
            issue.reason,
        )
    console.print(table)


def choose_ods_toolshop_machine_piece_descs(
    console: Console,
    rows: list[OdsToolShopImportRow],
) -> list[str] | None:
    """Prompt for one or more Machine Piece Description values from Tool Shop ODS rows."""
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.machine_piece_desc] = counts.get(row.machine_piece_desc, 0) + 1

    choices = [
        ExistingToolShopChoice(machine_piece_desc=name, row_count=count)
        for name, count in counts.items()
    ]
    choices.sort(key=lambda item: item.machine_piece_desc.casefold())
    show_selection_source(
        console,
        "ODS",
        "Selecting Machine Piece Description values from active Tool Shop Items rows that passed import checks.",
    )
    table = Table(title="Tool Shop ODS Machine Piece Description")
    table.add_column("Option", justify="right", style="cyan")
    table.add_column("Machine Piece Description")
    table.add_column("Rows", justify="right")
    for index, choice in enumerate(choices, start=1):
        table.add_row(str(index), choice.machine_piece_desc, str(choice.row_count))
    back_option = len(choices) + 1
    table.add_row(str(back_option), "Back", "")
    console.print(table)

    valid_choices = {str(index) for index in range(1, back_option + 1)}
    while True:
        raw = Prompt.ask(
            "Choose Machine Piece Description number(s), comma-separated",
            default="",
            show_default=False,
        ).strip()
        selected_numbers = [part.strip() for part in raw.split(",") if part.strip()]
        if not selected_numbers:
            console.print("[yellow]Please enter one or more option numbers.[/yellow]")
            continue
        invalid = [part for part in selected_numbers if part not in valid_choices]
        if invalid:
            console.print(f"[yellow]Invalid option number(s):[/yellow] {', '.join(invalid)}")
            continue
        if len(selected_numbers) == 1 and selected_numbers[0] == str(back_option):
            return None
        if any(part == str(back_option) for part in selected_numbers):
            console.print("[yellow]Back must be selected by itself.[/yellow]")
            continue
        selected_indexes: list[int] = []
        seen_indexes: set[int] = set()
        for part in selected_numbers:
            index = int(part) - 1
            if index in seen_indexes:
                continue
            seen_indexes.add(index)
            selected_indexes.append(index)
        return [choices[index].machine_piece_desc for index in selected_indexes]


def filter_toolshop_import_rows_by_machine_piece_descs(
    rows: list[OdsToolShopImportRow],
    machine_piece_descs: list[str],
) -> list[OdsToolShopImportRow]:
    """Return Tool Shop import rows belonging to the selected Machine Piece Description values."""
    wanted = {normalize_text(value) for value in machine_piece_descs}
    return [row for row in rows if normalize_text(row.machine_piece_desc) in wanted]


def show_toolshop_import_rows(
    console: Console,
    rows: list[OdsToolShopImportRow],
    *,
    title: str,
) -> None:
    """Render Tool Shop ODS rows that are about to be imported."""
    table = Table(title=title, caption=f"{len(rows)} row(s)")
    table.add_column("ODS Row", justify="right")
    table.add_column("Machine Piece Description")
    table.add_column("ODS Material Used")
    table.add_column("Mapped Grist Material")
    table.add_column("Dimension mm")
    table.add_column("Qty")
    table.add_column("Optional Item Group 1")
    table.add_column("Remarks")
    table.add_column("Job Remarks")
    for row in rows:
        table.add_row(
            str(row.row_number),
            row.machine_piece_desc,
            row.ods_material_used,
            row.mapped_grist_material_name or "(blank - no material mapping)",
            row.dimension_mm,
            row.qty,
            row.optional_item_group_1,
            row.remarks,
            row.job_remarks,
        )
    console.print(table)


def ask_toolshop_import_preview_action(console: Console, row_count: int) -> str:
    """Ask what to do after previewing the Tool Shop ODS rows to import."""
    table = Table(title="Tool Shop Import Preview Action")
    table.add_column("Option", justify="right", style="cyan")
    table.add_column("Action")
    table.add_row("1", f"Continue with these {row_count} row(s)")
    table.add_row("2", "Go back to Machine Piece Description selection")
    table.add_row("3", "Cancel")
    console.print(table)
    return Prompt.ask("Choose next step", choices=["1", "2", "3"], default="1")


def fetch_master_material_rows(client: GristClient) -> list[MasterMaterialRow]:
    """Fetch MasterMaterial rows for import mapping resolution."""
    records = client.fetch_table_records_with_ids(MASTER_MATERIAL_TABLE)
    rows: list[MasterMaterialRow] = []
    for record in records:
        row_id = record.get("id")
        if row_id is None:
            continue
        fields = record.get("fields", {})
        master_material = display_text(fields.get("MasterMaterial"))
        if not master_material:
            continue
        rows.append(
            MasterMaterialRow(
                row_id=int(row_id),
                master_material=master_material,
            )
        )
    if not rows:
        raise GristError("No MasterMaterial rows with MasterMaterial were found.")
    return sorted(rows, key=lambda row: row.master_material.casefold())


def fetch_product_part_toolshop_rows(client: GristClient) -> list[ProductPartToolShopRow]:
    """Fetch existing ProductPartToolShopList rows for duplicate checks."""
    records = client.fetch_table_records_with_ids(PRODUCT_PART_TOOLSHOP_LIST_TABLE)
    rows: list[ProductPartToolShopRow] = []
    for record in records:
        row_id = record.get("id")
        if row_id is None:
            continue
        fields = record.get("fields", {})
        rows.append(
            ProductPartToolShopRow(
                row_id=int(row_id),
                product_part_id=linked_record_id(fields.get("ProductPartName")),
                product_part_name=display_text(fields.get("ProductPartName_ProductPartName")),
                material_row_id=linked_record_id(fields.get("MaterialToCut")),
                material_name=display_text(fields.get("MaterialToCut")),
                length_mm=display_text(fields.get("Length_mm")),
                qty_nos=display_text(fields.get("QtyNos")),
                option_group_1=display_text(fields.get("OptionGroup1")),
                toolshop_part_name=display_text(fields.get("ToolShop_Part_Name")),
                job_remarks=display_text(fields.get("Job_Remarks")),
            )
        )
    return rows


def collect_toolshop_duplicate_matches(
    existing_rows: list[ProductPartToolShopRow],
    import_rows: list[OdsToolShopImportRow],
    product_part_row: ProductPartMasterRow,
) -> list[ToolShopDuplicateMatch]:
    """Return selected ODS rows that already exist in ProductPartToolShopList."""
    existing_by_signature: dict[tuple[Any, ...], list[ProductPartToolShopRow]] = {}
    for row in existing_rows:
        signature = toolshop_row_signature(
            product_part_id=row.product_part_id,
            material_row_id=row.material_row_id,
            length_mm=row.length_mm,
            qty=row.qty_nos,
            option_group_1=row.option_group_1,
            remarks=row.toolshop_part_name,
            job_remarks=row.job_remarks,
        )
        existing_by_signature.setdefault(signature, []).append(row)

    duplicate_matches: list[ToolShopDuplicateMatch] = []
    for row in import_rows:
        signature = toolshop_row_signature(
            product_part_id=product_part_row.row_id,
            material_row_id=row.mapped_grist_material_id,
            length_mm=row.dimension_mm,
            qty=row.qty,
            option_group_1=row.optional_item_group_1,
            remarks=row.remarks,
            job_remarks=row.job_remarks,
        )
        for existing_row in existing_by_signature.get(signature, []):
            duplicate_matches.append(
                ToolShopDuplicateMatch(
                    ods_row=row,
                    existing_row=existing_row,
                )
            )
    return duplicate_matches


def toolshop_row_signature(
    *,
    product_part_id: int | None,
    material_row_id: int | None,
    length_mm: str,
    qty: str,
    option_group_1: str,
    remarks: str,
    job_remarks: str,
) -> tuple[Any, ...]:
    """Build a stable duplicate-detection key for Tool Shop rows."""
    return (
        product_part_id,
        material_row_id,
        normalize_text(length_mm),
        normalize_text(qty),
        normalize_text(option_group_1),
        normalize_text(remarks),
        normalize_text(job_remarks),
    )


def show_toolshop_duplicate_matches(
    console: Console,
    duplicate_matches: list[ToolShopDuplicateMatch],
    product_part_name: str,
) -> None:
    """Render ProductPartToolShopList duplicate matches before create."""
    table = Table(title="Duplicate ProductPartToolShopList Rows", caption=f"{len(duplicate_matches)} match(es)")
    table.add_column("ODS Row", justify="right")
    table.add_column("Target ProductPartName")
    table.add_column("MaterialToCut")
    table.add_column("Length_mm")
    table.add_column("QtyNos")
    table.add_column("OptionGroup1")
    table.add_column("ToolShop_Part_Name")
    table.add_column("Job_Remarks")
    table.add_column("Existing Grist Row", justify="right")
    for match in duplicate_matches:
        table.add_row(
            str(match.ods_row.row_number),
            product_part_name,
            match.ods_row.mapped_grist_material_name or "(blank)",
            match.ods_row.dimension_mm,
            match.ods_row.qty,
            match.ods_row.optional_item_group_1,
            match.ods_row.remarks,
            match.ods_row.job_remarks,
            str(match.existing_row.row_id),
        )
    console.print(table)


def ask_toolshop_duplicate_action(console: Console, duplicate_count: int) -> str:
    """Ask how duplicate ProductPartToolShopList rows should be handled."""
    table = Table(title="Duplicate Tool Shop Import Action")
    table.add_column("Option", justify="right", style="cyan")
    table.add_column("Action")
    table.add_row("1", f"Skip the {duplicate_count} duplicate row(s)")
    table.add_row("2", "Create duplicate rows anyway")
    table.add_row("3", "Cancel")
    console.print(table)
    return Prompt.ask("Choose duplicate action", choices=["1", "2", "3"], default="1")


def show_toolshop_create_plan(
    console: Console,
    rows: list[OdsToolShopImportRow],
    product_part_row: ProductPartMasterRow,
    model_choice: ProductModelChoice,
) -> None:
    """Render the final Tool Shop create plan before writing to Grist."""
    console.print(
        Panel(
            "\n".join(
                [
                    f"ProductModelCode: {model_choice.product_model_code}",
                    f"ProductPartName: {product_part_row.product_part_name}",
                    f"Rows to create: {len(rows)}",
                ]
            ),
            title="Tool Shop Create Plan",
            border_style="yellow",
            padding=(1, 2),
        )
    )
    show_toolshop_import_rows(console, rows, title="ProductPartToolShopList Rows To Create")


def build_toolshop_create_payload(
    row: OdsToolShopImportRow,
    product_part_row: ProductPartMasterRow,
) -> dict[str, dict[str, Any]]:
    """Build one ProductPartToolShopList create payload."""
    fields: dict[str, Any] = {
        "ProductPartName": product_part_row.row_id,
        "Length_mm": row.dimension_mm_value,
        "QtyNos": row.qty_value,
        "OptionGroup1": row.optional_item_group_1,
        "ToolShop_Part_Name": row.remarks,
        "Job_Remarks": row.job_remarks,
    }
    if row.mapped_grist_material_id is not None:
        fields["MaterialToCut"] = row.mapped_grist_material_id
    return {"fields": fields}


def toolshop_verification_product_index(
    client: GristClient,
    model_choice: ProductModelChoice,
) -> int:
    """Return the replay index expected by Tool Shop verification for the selected ProductModelCode."""
    choices = sorted(
        fetch_product_model_config_choices(client).values(),
        key=lambda item: item.product_model_code.casefold(),
    )
    for index, choice in enumerate(choices, start=1):
        if choice.product_model_id == model_choice.product_model_id:
            return index
    raise GristError(
        f"Could not resolve ProductModelCode {model_choice.product_model_code} for Tool Shop verification."
    )


def import_ms_rows_from_ods(
    console: Console,
    client: GristClient,
    workbook: OdsWorkbook,
    app_config: AppConfig,
    material_mapping_path: Path,
) -> None:
    """Create or sync ProductPartMSList rows from ODS Material Cut List rows."""
    sheet_config = app_config.supported_sheets.get(MS_SHEET_NAME)
    if sheet_config is None:
        raise GristError(f"Missing sheet configuration for {MS_SHEET_NAME}.")

    console.print(
        Panel(
            "\n".join(
                [
                    "This workflow creates or syncs ProductPartMSList rows from active ODS Material Cut List rows.",
                    "You will choose one ODS Machine Piece Description group, then decide which ProductPartName in Grist it should belong to.",
                    "If you choose an existing ProductPartName, the tool can either add missing ODS rows only or treat the selected ODS rows as the master list.",
                    "No Grist rows will be deleted. Extra Grist rows can only be marked with Part_Status = Delete.",
                ]
            ),
            title="Add New MS Cut List From ODS",
            border_style="cyan",
            padding=(1, 2),
        )
    )

    material_mapping = load_material_mapping(material_mapping_path)
    master_material_rows = fetch_master_material_rows(client)
    importable_rows, import_issues = collect_ms_import_rows(
        workbook,
        sheet_config,
        material_mapping,
        master_material_rows,
    )
    if import_issues:
        show_ms_import_issues(console, import_issues)
        if importable_rows and not Confirm.ask(
            "Continue with only the Material Cut List ODS rows that have valid material mappings?",
            default=True,
        ):
            console.print("[yellow]Nothing changed.[/yellow]")
            return
    if not importable_rows:
        console.print("[yellow]No eligible Material Cut List ODS rows were available to import.[/yellow]")
        return

    while True:
        selected_machine_piece_desc = choose_ods_ms_machine_piece_desc(console, importable_rows)
        if selected_machine_piece_desc == GO_BACK_LABEL:
            console.print("[yellow]Nothing changed.[/yellow]")
            return

        selected_rows = filter_ms_import_rows_by_machine_piece_desc(importable_rows, selected_machine_piece_desc)
        if not selected_rows:
            console.print("[yellow]No Material Cut List ODS rows matched that Machine Piece Description selection.[/yellow]")
            continue

        show_ms_import_rows(console, selected_rows, title="Material Cut List ODS Rows To Import")
        preview_action = ask_ms_import_preview_action(console, len(selected_rows))
        if preview_action == "1":
            break
        if preview_action == "2":
            console.print("[cyan]Going back to the ODS Machine Piece Description selection.[/cyan]")
            continue
        console.print("[yellow]Nothing changed.[/yellow]")
        return

    product_part_mode = ask_ms_target_product_part_mode(console)
    if product_part_mode == "3":
        console.print("[yellow]Nothing changed.[/yellow]")
        return

    created_new_product_part = False
    if product_part_mode == "1":
        product_part_row = prompt_create_product_part_master_row(console, client)
        if product_part_row is None:
            console.print("[yellow]Nothing changed.[/yellow]")
            return
        created_new_product_part = True
        console.print(
            f"[green]Created ProductPartMaster row:[/green] "
            f"{product_part_row.row_id} - {product_part_row.product_part_name}"
        )
    else:
        product_part_row = choose_product_part_master_row(console, client, preferred_row=None)
        if product_part_row is None:
            console.print("[yellow]Nothing changed.[/yellow]")
            return

    sync_mode = "1"
    if not created_new_product_part:
        sync_mode = ask_ms_existing_product_part_sync_mode(console)
        if sync_mode == "3":
            console.print("[yellow]Nothing changed.[/yellow]")
            return

    existing_rows = filter_ms_rows_by_product_part_and_machine_piece_desc(
        fetch_product_part_ms_rows(client),
        product_part_id=product_part_row.row_id,
        machine_piece_desc=selected_machine_piece_desc,
    )
    planned_actions = plan_ms_sync_actions(
        existing_rows=existing_rows,
        ods_rows=selected_rows,
        sync_mode=sync_mode,
    )
    if not planned_actions:
        console.print("[green]The selected ODS rows already match Grist for this ProductPartName scope.[/green]")
    else:
        show_ms_sync_plan(
            console,
            planned_actions,
            product_part_row=product_part_row,
            machine_piece_desc=selected_machine_piece_desc,
            sync_mode=sync_mode,
        )
        if not Confirm.ask("Apply these ProductPartMSList changes now?", default=True):
            console.print("[yellow]Nothing changed.[/yellow]")
            return
        created_count, updated_count = apply_ms_sync_actions(
            client,
            planned_actions,
            product_part_row=product_part_row,
        )
        console.print(
            f"[green]Completed ProductPartMSList sync:[/green] "
            f"{created_count} row(s) created, {updated_count} row(s) updated."
        )

    if created_new_product_part and Confirm.ask(
        "Assign this new part to ProductModelConfig now?",
        default=True,
    ):
        assign_part_to_config(console, client, preferred_product_part=product_part_row)


def collect_ms_import_rows(
    workbook: OdsWorkbook,
    sheet_config: SheetConfig,
    material_mapping: dict[str, str],
    master_material_rows: list[MasterMaterialRow],
) -> tuple[list[OdsMsImportRow], list[MsImportIssue]]:
    """Return active Material Cut List ODS rows that can be safely imported."""
    sheet = workbook.sheet_view(MS_SHEET_NAME, sheet_config)
    column_map = sheet.column_map()
    machine_piece_index = column_map.index_for("product_part_name")
    material_index = column_map.index_for("material_to_cut")
    dimension_index = column_map.index_for("dimension_to_cut_mm")
    qty_index = column_map.index_for("qty")
    option_group_index = column_map.index_for("optional_item_group_1")
    remarks_index = column_map.index_for("remarks")

    missing_required = [
        label
        for label, index in (
            ("Machine Piece Description", machine_piece_index),
            ("Material to Cut", material_index),
            ("Dimension to Cut (mm)", dimension_index),
            ("Qty", qty_index),
        )
        if index is None
    ]
    if missing_required:
        raise GristError(
            f"{MS_SHEET_NAME}: missing required ODS column(s): {', '.join(missing_required)}."
        )

    master_by_name = {
        normalize_text(row.master_material): row
        for row in master_material_rows
    }
    importable_rows: list[OdsMsImportRow] = []
    issues: list[MsImportIssue] = []
    for row_info in sheet.data_rows():
        decision = sheet.classify_row(row_info.values, column_map)
        if decision.inactive:
            continue

        machine_piece_desc = display_text(sheet.get_value(row_info.values, machine_piece_index)).strip()
        ods_material_used = display_text(sheet.get_value(row_info.values, material_index)).strip()
        if not machine_piece_desc:
            issues.append(
                MsImportIssue(
                    row_number=row_info.number,
                    machine_piece_desc="",
                    ods_material_used=ods_material_used,
                    reason="Machine Piece Description is blank.",
                )
            )
            continue

        mapped_material_name = material_mapping.get(ods_material_used)
        if not mapped_material_name:
            issues.append(
                MsImportIssue(
                    row_number=row_info.number,
                    machine_piece_desc=machine_piece_desc,
                    ods_material_used=ods_material_used,
                    reason="No material mapping was found for this ODS material.",
                )
            )
            continue
        master_material_row = master_by_name.get(normalize_text(mapped_material_name))
        if master_material_row is None:
            issues.append(
                MsImportIssue(
                    row_number=row_info.number,
                    machine_piece_desc=machine_piece_desc,
                    ods_material_used=ods_material_used,
                    reason=f"Mapped Grist material '{mapped_material_name}' was not found in MasterMaterial.",
                )
            )
            continue

        dimension_value = sheet.get_value(row_info.values, dimension_index)
        qty_value = sheet.get_value(row_info.values, qty_index)
        importable_rows.append(
            OdsMsImportRow(
                row_number=row_info.number,
                machine_piece_desc=machine_piece_desc,
                ods_material_used=ods_material_used,
                mapped_grist_material_name=master_material_row.master_material,
                mapped_grist_material_id=master_material_row.row_id,
                dimension_mm=display_text(dimension_value),
                dimension_mm_value=dimension_value,
                qty=display_text(qty_value),
                qty_value=qty_value,
                optional_item_group_1=display_text(sheet.get_value(row_info.values, option_group_index)),
                remarks=display_text(sheet.get_value(row_info.values, remarks_index)),
            )
        )

    return sorted(
        importable_rows,
        key=lambda row: (
            normalize_text(row.machine_piece_desc),
            row.row_number,
        ),
    ), issues


def show_ms_import_issues(console: Console, issues: list[MsImportIssue]) -> None:
    """Render Material Cut List ODS rows that cannot be imported yet."""
    table = Table(title="Material Cut List Import Issues", caption=f"{len(issues)} row(s)")
    table.add_column("ODS Row", justify="right")
    table.add_column("Machine Piece Description")
    table.add_column("ODS Material")
    table.add_column("Reason")
    for issue in issues:
        table.add_row(
            str(issue.row_number),
            issue.machine_piece_desc or BLANKS_LABEL,
            issue.ods_material_used,
            issue.reason,
        )
    console.print(table)


def choose_ods_ms_machine_piece_desc(
    console: Console,
    rows: list[OdsMsImportRow],
) -> str:
    """Prompt for one Machine Piece Description from Material Cut List ODS rows."""
    grouped_counts: dict[str | None, int] = {}
    for row in rows:
        key = None if is_blank(row.machine_piece_desc) else row.machine_piece_desc
        grouped_counts[key] = grouped_counts.get(key, 0) + 1

    values = sorted(
        grouped_counts.keys(),
        key=lambda item: (item is not None, normalize_text(item or "")),
    )
    show_selection_source(
        console,
        "ODS",
        "Selecting one Machine Piece Description group from active Material Cut List rows that passed import checks.",
    )
    table = Table(title="Material Cut List ODS Machine Piece Description")
    table.add_column("Option", justify="right", style="cyan")
    table.add_column("Machine Piece Description")
    table.add_column("Rows", justify="right")
    for index, value in enumerate(values, start=1):
        table.add_row(
            str(index),
            BLANKS_LABEL if value is None else value,
            str(grouped_counts[value]),
        )
    back_option = len(values) + 1
    table.add_row(str(back_option), "Back", "")
    console.print(table)
    selected = Prompt.ask(
        "Choose Machine Piece Description",
        choices=[str(index) for index in range(1, back_option + 1)],
    )
    if int(selected) == back_option:
        return GO_BACK_LABEL
    chosen = values[int(selected) - 1]
    return "" if chosen is None else chosen


def filter_ms_import_rows_by_machine_piece_desc(
    rows: list[OdsMsImportRow],
    machine_piece_desc: str,
) -> list[OdsMsImportRow]:
    """Return Material Cut List import rows for the selected Machine Piece Description."""
    if is_blank(machine_piece_desc):
        return [row for row in rows if is_blank(row.machine_piece_desc)]
    wanted = normalize_text(machine_piece_desc)
    return [row for row in rows if normalize_text(row.machine_piece_desc) == wanted]


def show_ms_import_rows(
    console: Console,
    rows: list[OdsMsImportRow],
    *,
    title: str,
) -> None:
    """Render Material Cut List ODS rows that are about to be imported or synced."""
    table = Table(title=title, caption=f"{len(rows)} row(s)")
    table.add_column("ODS Row", justify="right")
    table.add_column("Machine Piece Description")
    table.add_column("ODS Material")
    table.add_column("Mapped Grist Material")
    table.add_column("Length mm")
    table.add_column("Qty")
    table.add_column("Option Group 1")
    table.add_column("Remarks")
    for row in rows:
        table.add_row(
            str(row.row_number),
            row.machine_piece_desc,
            row.ods_material_used,
            row.mapped_grist_material_name,
            row.dimension_mm,
            row.qty,
            row.optional_item_group_1,
            row.remarks,
        )
    console.print(table)


def ask_ms_import_preview_action(console: Console, row_count: int) -> str:
    """Ask what to do after previewing the Material Cut List ODS rows."""
    table = Table(title="Material Cut List Import Preview Action")
    table.add_column("Option", justify="right", style="cyan")
    table.add_column("Action")
    table.add_row("1", f"Continue with these {row_count} row(s)")
    table.add_row("2", "Go back to Machine Piece Description selection")
    table.add_row("3", "Cancel")
    console.print(table)
    return Prompt.ask("Choose next step", choices=["1", "2", "3"], default="1")


def ask_ms_target_product_part_mode(console: Console) -> str:
    """Ask whether the Material Cut List import should use a new or existing ProductPartName."""
    table = Table(title="Target ProductPartName For MS Cut List Import")
    table.add_column("Option", justify="right", style="cyan")
    table.add_column("Action")
    table.add_row("1", "Enter new ProductPartName")
    table.add_row("2", "Select existing ProductPartName")
    table.add_row("3", "Cancel")
    console.print(table)
    return Prompt.ask("Choose target ProductPartName option", choices=["1", "2", "3"], default="2")


def ask_ms_existing_product_part_sync_mode(console: Console) -> str:
    """Ask how an existing ProductPartName should be synced with the selected ODS rows."""
    table = Table(title="Existing ProductPartName Sync Mode")
    table.add_column("Option", justify="right", style="cyan")
    table.add_column("Action")
    table.add_row("1", "Add missing ODS rows only")
    table.add_row("2", "Treat selected ODS rows as the master list")
    table.add_row("3", "Cancel")
    console.print(table)
    return Prompt.ask("Choose sync mode", choices=["1", "2", "3"], default="1")


def filter_ms_rows_by_product_part_and_machine_piece_desc(
    rows: list[ProductPartMSListRow],
    *,
    product_part_id: int,
    machine_piece_desc: str,
) -> list[ProductPartMSListRow]:
    """Return ProductPartMSList rows matching one ProductPartName and MachinePieceDesc scope."""
    scoped_rows = [row for row in rows if row.current_product_part_id == product_part_id]
    if is_blank(machine_piece_desc):
        return [row for row in scoped_rows if is_blank(row.machine_piece_desc)]
    wanted = normalize_text(machine_piece_desc)
    return [row for row in scoped_rows if normalize_text(row.machine_piece_desc) == wanted]


def plan_ms_sync_actions(
    *,
    existing_rows: list[ProductPartMSListRow],
    ods_rows: list[OdsMsImportRow],
    sync_mode: str,
) -> list[MsSyncAction]:
    """Build ProductPartMSList create/update actions for the selected ODS scope."""
    existing_by_signature: dict[tuple[Any, ...], list[ProductPartMSListRow]] = {}
    for row in existing_rows:
        signature = ms_row_signature(
            material_row_id=row.material_row_id,
            length_mm=row.length_mm,
            qty=row.qty_nos,
            option_group_1=row.option_group_1_temp,
        )
        existing_by_signature.setdefault(signature, []).append(row)

    used_existing_ids: set[int] = set()
    planned_actions: list[MsSyncAction] = []
    for ods_row in ods_rows:
        signature = ms_row_signature(
            material_row_id=ods_row.mapped_grist_material_id,
            length_mm=ods_row.dimension_mm_value,
            qty=ods_row.qty_value,
            option_group_1=ods_row.optional_item_group_1,
        )
        candidates = [row for row in existing_by_signature.get(signature, []) if row.row_id not in used_existing_ids]
        matched_row = choose_best_ms_existing_match(candidates, ods_row)
        if matched_row is None:
            planned_actions.append(
                MsSyncAction(
                    action="Create row",
                    target_status="Active",
                    detail="Missing in Grist for the selected ProductPartName scope.",
                    ods_row=ods_row,
                )
            )
            continue

        used_existing_ids.add(matched_row.row_id)
        change_notes = ms_existing_row_change_notes(matched_row, ods_row)
        if change_notes:
            planned_actions.append(
                MsSyncAction(
                    action="Update existing row",
                    target_status="Modify",
                    detail="; ".join(change_notes),
                    ods_row=ods_row,
                    existing_row=matched_row,
                )
            )
            continue

        if normalize_text(matched_row.part_status) != "active":
            planned_actions.append(
                MsSyncAction(
                    action="Set existing row active",
                    target_status="Active",
                    detail="Row is present in the selected ODS list and will be reactivated.",
                    ods_row=ods_row,
                    existing_row=matched_row,
                )
            )

    if sync_mode == "2":
        for row in existing_rows:
            if row.row_id in used_existing_ids:
                continue
            planned_actions.append(
                MsSyncAction(
                    action="Mark extra row as Delete",
                    target_status="Delete",
                    detail="Row was not found in the selected ODS master list.",
                    existing_row=row,
                )
            )

    return planned_actions


def choose_best_ms_existing_match(
    candidates: list[ProductPartMSListRow],
    ods_row: OdsMsImportRow,
) -> ProductPartMSListRow | None:
    """Return the best existing ProductPartMSList match for one ODS row."""
    if not candidates:
        return None

    wanted_remarks = normalize_text(ods_row.remarks)
    for row in candidates:
        if normalize_text(row.remarks) == wanted_remarks:
            return row
    return candidates[0]


def ms_existing_row_change_notes(
    existing_row: ProductPartMSListRow,
    ods_row: OdsMsImportRow,
) -> list[str]:
    """Return the field-level differences that should produce a Modify action."""
    notes: list[str] = []
    if normalize_text(existing_row.remarks) != normalize_text(ods_row.remarks):
        notes.append(
            f"Remarks: '{existing_row.remarks or '(blank)'}' -> '{ods_row.remarks or '(blank)'}'"
        )
    if normalize_text(existing_row.machine_piece_desc) != normalize_text(ods_row.machine_piece_desc):
        notes.append(
            f"MachinePieceDesc: '{existing_row.machine_piece_desc or '(blank)'}' -> "
            f"'{ods_row.machine_piece_desc or '(blank)'}'"
        )
    return notes


def show_ms_sync_plan(
    console: Console,
    actions: list[MsSyncAction],
    *,
    product_part_row: ProductPartMasterRow,
    machine_piece_desc: str,
    sync_mode: str,
) -> None:
    """Render the ProductPartMSList sync plan before writing to Grist."""
    mode_label = "Add missing ODS rows only" if sync_mode == "1" else "ODS is master list"
    console.print(
        Panel(
            "\n".join(
                [
                    f"ProductPartName: {product_part_row.product_part_name}",
                    f"MachinePieceDesc: {machine_piece_desc or BLANKS_LABEL}",
                    f"Sync mode: {mode_label}",
                    f"Planned changes: {len(actions)}",
                ]
            ),
            title="MS Cut List Sync Plan",
            border_style="yellow",
            padding=(1, 2),
        )
    )
    table = Table(title="Planned ProductPartMSList Changes", caption=f"{len(actions)} action(s)")
    table.add_column("Action")
    table.add_column("Status")
    table.add_column("ODS Row", justify="right")
    table.add_column("Grist Row", justify="right")
    table.add_column("Material")
    table.add_column("Length")
    table.add_column("Qty")
    table.add_column("OptionGroup1")
    table.add_column("Detail")
    for action in actions:
        ods_row = action.ods_row
        existing_row = action.existing_row
        table.add_row(
            action.action,
            action.target_status,
            "" if ods_row is None else str(ods_row.row_number),
            "" if existing_row is None else str(existing_row.row_id),
            (
                ods_row.mapped_grist_material_name
                if ods_row is not None
                else existing_row.material_to_cut if existing_row is not None else ""
            ),
            ods_row.dimension_mm if ods_row is not None else existing_row.length_mm if existing_row is not None else "",
            ods_row.qty if ods_row is not None else existing_row.qty_nos if existing_row is not None else "",
            (
                ods_row.optional_item_group_1
                if ods_row is not None
                else existing_row.option_group_1_temp if existing_row is not None else ""
            ),
            action.detail,
        )
    console.print(table)


def apply_ms_sync_actions(
    client: GristClient,
    actions: list[MsSyncAction],
    *,
    product_part_row: ProductPartMasterRow,
) -> tuple[int, int]:
    """Create and update ProductPartMSList rows for the planned MS sync actions."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    create_payloads: list[dict[str, dict[str, Any]]] = []
    update_payloads: list[dict[str, Any]] = []
    for action in actions:
        if action.action == "Create row":
            assert action.ods_row is not None
            create_payloads.append(
                build_ms_create_payload(
                    action.ods_row,
                    product_part_row=product_part_row,
                    timestamp=timestamp,
                )
            )
            continue

        if action.existing_row is None:
            continue

        fields: dict[str, Any] = {
            "Part_Status": action.target_status,
            "Part_Status_Remark": build_ms_status_remark(
                timestamp=timestamp,
                detail=action.detail,
            ),
        }
        if action.target_status == "Modify" and action.ods_row is not None:
            fields["Remarks"] = action.ods_row.remarks
            fields["MachinePieceDesc"] = action.ods_row.machine_piece_desc
        update_payloads.append(
            {
                "id": action.existing_row.row_id,
                "fields": fields,
            }
        )

    created = client.create_table_records(PRODUCT_PART_MS_LIST_TABLE, create_payloads)
    client.update_table_records(PRODUCT_PART_MS_LIST_TABLE, update_payloads)
    return len(created), len(update_payloads)


def build_ms_create_payload(
    row: OdsMsImportRow,
    *,
    product_part_row: ProductPartMasterRow,
    timestamp: str,
) -> dict[str, dict[str, Any]]:
    """Build one ProductPartMSList create payload."""
    return {
        "fields": {
            "ProductPartName": product_part_row.row_id,
            "MachinePieceDesc": row.machine_piece_desc,
            "MaterialToCut": row.mapped_grist_material_id,
            "Length_mm": row.dimension_mm_value,
            "QtyNos": row.qty_value,
            "OptionGroup1_TEMP": row.optional_item_group_1,
            "Remarks": row.remarks,
            "Part_Status": "Active",
            "Part_Status_Remark": build_ms_status_remark(
                timestamp=timestamp,
                detail="Created from selected ODS Material Cut List rows.",
            ),
        }
    }


def build_ms_status_remark(*, timestamp: str, detail: str) -> str:
    """Build a consistent Part_Status_Remark message for MS sync actions."""
    return f"done by Codex {timestamp}: {detail}"


def ms_row_signature(
    *,
    material_row_id: int | None,
    length_mm: Any,
    qty: Any,
    option_group_1: str,
) -> tuple[Any, ...]:
    """Build the ProductPartMSList row key used for ODS-to-Grist matching."""
    return (
        material_row_id,
        ms_key_value(length_mm),
        ms_key_value(qty),
        normalize_text(option_group_1),
    )


def ms_key_value(value: Any) -> tuple[str, Any]:
    """Return one normalized key component for MS row matching."""
    number = as_decimal(value)
    if number is not None:
        return ("num", number)
    return ("txt", normalize_text(value))


def ask_unresolved_review_action(console: Console, unresolved_count: int) -> str:
    """Ask how the user wants to handle unresolved ODS entries."""
    table = Table(title="Review Unresolved ODS Entries")
    table.add_column("Option", justify="right", style="cyan")
    table.add_column("Action")
    table.add_row("1", f"Review all {unresolved_count} unresolved entries")
    table.add_row("2", "Review and add selected entries")
    table.add_row("3", "Skip for now")
    console.print(table)
    return Prompt.ask("Choose review option", choices=["1", "2", "3"], default="1")


def choose_selected_unresolved_entries(
    console: Console,
    unresolved: list[UnresolvedOdsEntry],
) -> list[UnresolvedOdsEntry] | None:
    """Prompt for one or more unresolved ODS entries to review."""
    table = Table(title="Select Unresolved ODS Entries")
    table.add_column("Option", justify="right", style="cyan")
    table.add_column("ODS Entry")
    table.add_column("Found In")
    table.add_column("Top Suggestion")
    for index, entry in enumerate(unresolved, start=1):
        top_suggestion = entry.suggestions[0].product_part_name if entry.suggestions else ""
        table.add_row(
            str(index),
            entry.ods_value,
            ", ".join(entry.sheets),
            top_suggestion or "-",
        )
    back_option = len(unresolved) + 1
    table.add_row(str(back_option), "Back", "Return to review options", "")
    console.print(table)
    while True:
        selected = Prompt.ask(
            "Choose unresolved ODS entry number(s), comma separated",
            default="",
            show_default=False,
        ).strip()
        if not selected:
            console.print("[yellow]Please enter one or more entry numbers, or choose Back.[/yellow]")
            continue
        if selected == str(back_option):
            return None

        parts = [part.strip() for part in selected.split(",")]
        if any(not part.isdigit() for part in parts):
            console.print("[yellow]Please enter only numbers separated by commas.[/yellow]")
            continue

        option_numbers = [int(part) for part in parts]
        if any(number < 1 or number > len(unresolved) for number in option_numbers):
            console.print(
                f"[yellow]Please choose numbers between 1 and {len(unresolved)}, or {back_option} for Back.[/yellow]"
            )
            continue

        unique_numbers: list[int] = []
        seen_numbers: set[int] = set()
        for number in option_numbers:
            if number in seen_numbers:
                continue
            seen_numbers.add(number)
            unique_numbers.append(number)
        return [unresolved[number - 1] for number in unique_numbers]


def choose_product_model_code_for_part_summary(
    console: Console,
    client: GristClient,
) -> ProductModelChoice | None:
    """Prompt for the ProductModelCode whose part scope should be compared."""
    choices = sorted(
        fetch_product_model_config_choices_with_master_rows(client).values(),
        key=lambda item: item.product_model_code.casefold(),
    )
    if not choices:
        raise GristError("No ProductModelMaster2 rows with ProductModelCode were found.")

    console.print(
        Panel(
            "Please select the ProductModelCode whose configured parts should be used for the ODS vs Grist summary comparison.",
            title="Select Product Model Scope",
            border_style="cyan",
            padding=(1, 2),
        )
    )
    table = Table(title="Product Models")
    table.add_column("Option", justify="right", style="cyan")
    table.add_column("ProductModelCode")
    table.add_column("Description")
    table.add_column("Configured Parts", justify="right")
    for index, choice in enumerate(choices, start=1):
        table.add_row(
            str(index),
            choice.product_model_code,
            choice.description,
            str(len(choice.product_part_names)),
        )
    back_option = len(choices) + 1
    table.add_row(str(back_option), "Back", "Return to Manage Product Part Names", "")
    console.print(table)
    selected = Prompt.ask(
        "Choose ProductModelCode",
        choices=[str(index) for index in range(1, back_option + 1)],
    )
    if int(selected) == back_option:
        return None
    return choices[int(selected) - 1]


def fetch_product_model_config_choices_with_master_rows(
    client: GristClient,
) -> dict[int, ProductModelChoice]:
    """Return ProductModel choices for all master rows with current config scope attached."""
    config_choices_by_id = fetch_product_model_config_choices(client)
    master_records = client.fetch_table_records_with_ids(PRODUCT_MODEL_MASTER_TABLE)
    choices_by_id: dict[int, ProductModelChoice] = {}
    for record in master_records:
        fields = record.get("fields", {})
        row_id = record.get("id")
        if row_id is None:
            continue
        code = display_text(fields.get("ProductModelCode"))
        if not code:
            continue
        existing = config_choices_by_id.get(int(row_id))
        choices_by_id[int(row_id)] = ProductModelChoice(
            product_model_id=int(row_id),
            product_model_code=code,
            description=display_text(fields.get("ProductModelDesc")),
            product_part_names=set() if existing is None else set(existing.product_part_names),
            product_part_ids=set() if existing is None else set(existing.product_part_ids),
        )
    return choices_by_id


def collect_distinct_ods_part_labels(
    workbook: OdsWorkbook,
    sheet_config: SheetConfig,
    spec: ProductPartSummarySheetSpec,
) -> list[OdsPartLabel]:
    """Collect distinct active ODS part labels for a summary sheet."""
    sheet = workbook.sheet_view(spec.sheet_name, sheet_config)
    counts: dict[str, int] = {}
    if spec.use_configured_product_part_name:
        column_map = sheet.column_map()
        label_index = column_map.index_for("product_part_name")
        if label_index is None:
            raise GristError(
                f"{spec.sheet_name}: could not find the configured product part column for summary comparison."
            )
        for row_info in sheet.data_rows():
            if sheet.classify_row(row_info.values, column_map).inactive:
                continue
            label = display_text(sheet.get_value(row_info.values, label_index))
            if is_blank(label):
                continue
            counts[label] = counts.get(label, 0) + 1
    else:
        label_index = find_sheet_header_index(sheet, [PART_CATEGORY_HEADER])
        if label_index is None:
            raise GristError(
                f"{spec.sheet_name}: could not find the '{PART_CATEGORY_HEADER}' column for summary comparison."
            )
        column_map = sheet.column_map()
        for row_info in sheet.data_rows():
            if sheet.classify_row(row_info.values, column_map).inactive:
                continue
            label = display_text(sheet.get_value(row_info.values, label_index))
            if is_blank(label):
                continue
            counts[label] = counts.get(label, 0) + 1

    return [
        OdsPartLabel(value=value, row_count=row_count)
        for value, row_count in sorted(counts.items(), key=lambda item: item[0].casefold())
    ]


def find_sheet_header_index(sheet: SheetView, candidates: list[str]) -> int | None:
    """Find a column index directly from sheet headers."""
    normalized_headers = {
        normalize_header(header): index
        for index, header in enumerate(sheet.header)
        if not is_blank(header)
    }
    for candidate in candidates:
        match = normalized_headers.get(normalize_header(candidate))
        if match is not None:
            return match
    return None


def fetch_scoped_sheet_product_part_names(
    client: GristClient,
    model_choice: ProductModelChoice,
    sheet_config: SheetConfig,
    spec: ProductPartSummarySheetSpec,
) -> set[str]:
    """Return distinct Grist product-part names from the target sheet table in model scope."""
    table_id = sheet_config.grist.table_id
    if not table_id:
        raise GristError(f"{spec.sheet_name}: Grist table is not configured for summary comparison.")
    product_part_field = (
        CNC_PRODUCT_PART_FIELD if spec.sheet_name == "CNC Cut List" else sheet_config.grist.product_part_field
    )
    records = client.fetch_table_records_with_ids(table_id)
    names: set[str] = set()
    for record in records:
        fields = record.get("fields", {})
        product_part_id = linked_record_id(fields.get("ProductPartName"))
        if product_part_id is None or product_part_id not in model_choice.product_part_ids:
            continue
        product_part_name = display_text(fields.get(product_part_field))
        if product_part_name:
            names.add(product_part_name)
    return names


def build_part_comparison_summary(
    *,
    spec: ProductPartSummarySheetSpec,
    ods_labels: list[OdsPartLabel],
    grist_names: set[str],
    master_rows: list[ProductPartMasterRow],
) -> PartComparisonSummary:
    """Build one per-sheet summary from ODS labels and scoped Grist names."""
    all_suggestion_names = list(grist_names)
    master_names = [row.product_part_name for row in master_rows]
    for master_name in master_names:
        if master_name not in grist_names:
            all_suggestion_names.append(master_name)

    results: list[PartComparisonResult] = []
    matched_count = 0
    ambiguous_count = 0
    missing_count = 0
    for ods_label in ods_labels:
        result = compare_ods_label_to_grist_names(ods_label.value, grist_names, all_suggestion_names)
        results.append(result)
        if result.status == "matched":
            matched_count += 1
        elif result.status == "ambiguous":
            ambiguous_count += 1
        else:
            missing_count += 1

    return PartComparisonSummary(
        sheet_name=spec.sheet_name,
        menu_label=spec.menu_label,
        ods_field_label=spec.ods_field_label,
        total_ods_entries=len(ods_labels),
        matched_count=matched_count,
        ambiguous_count=ambiguous_count,
        missing_count=missing_count,
        results=tuple(results),
    )


def compare_ods_label_to_grist_names(
    ods_value: str,
    matched_scope_names: set[str],
    all_suggestion_names: list[str],
) -> PartComparisonResult:
    """Return matched / ambiguous / missing status with ranked suggestions."""
    scoped_suggestions = rank_part_match_suggestions(ods_value, sorted(matched_scope_names))
    if scoped_suggestions:
        top = scoped_suggestions[0]
        second_score = scoped_suggestions[1].score if len(scoped_suggestions) > 1 else -1
        if top.score >= 96:
            return PartComparisonResult(
                ods_value=ods_value,
                status="matched",
                matched_name=top.product_part_name,
                suggestions=tuple(scoped_suggestions[:3]),
            )
        if top.score >= 82 and top.score - second_score >= 10:
            return PartComparisonResult(
                ods_value=ods_value,
                status="matched",
                matched_name=top.product_part_name,
                suggestions=tuple(scoped_suggestions[:3]),
            )
        if top.score >= 62:
            return PartComparisonResult(
                ods_value=ods_value,
                status="ambiguous",
                matched_name="",
                suggestions=tuple(scoped_suggestions[:3]),
            )

    global_suggestions = rank_part_match_suggestions(ods_value, all_suggestion_names)
    if global_suggestions and global_suggestions[0].score >= 58:
        return PartComparisonResult(
            ods_value=ods_value,
            status="ambiguous",
            matched_name="",
            suggestions=tuple(global_suggestions[:3]),
        )

    return PartComparisonResult(
        ods_value=ods_value,
        status="missing",
        matched_name="",
        suggestions=tuple(global_suggestions[:3]),
    )


def rank_part_match_suggestions(ods_value: str, candidate_names: list[str]) -> list[PartMatchSuggestion]:
    """Return candidate names sorted by descending heuristic score."""
    ranked: list[PartMatchSuggestion] = []
    seen: set[str] = set()
    for candidate in candidate_names:
        if candidate in seen:
            continue
        seen.add(candidate)
        score = score_part_name_match(ods_value, candidate)
        if score < 35:
            continue
        ranked.append(PartMatchSuggestion(product_part_name=candidate, score=score))
    return sorted(ranked, key=lambda item: (-item.score, item.product_part_name.casefold()))


def score_part_name_match(ods_value: str, candidate: str) -> int:
    """Heuristic scorer for fuzzy ODS label to Grist product-part-name matching."""
    normalized_ods = normalize_part_match_text(ods_value)
    normalized_candidate = normalize_part_match_text(candidate)
    compact_ods = compact_part_match_text(normalized_ods)
    compact_candidate = compact_part_match_text(normalized_candidate)
    if not compact_ods or not compact_candidate:
        return 0
    if normalized_ods == normalized_candidate or compact_ods == compact_candidate:
        return 100

    ods_tokens = part_match_tokens(normalized_ods)
    candidate_tokens = part_match_tokens(normalized_candidate)
    if not ods_tokens or not candidate_tokens:
        return 0

    common = ods_tokens & candidate_tokens
    if not common:
        sequence_score = int(SequenceMatcher(a=compact_ods, b=compact_candidate).ratio() * 55)
        return sequence_score if sequence_score >= 35 else 0

    numeric_common = numeric_part_tokens(ods_tokens) & numeric_part_tokens(candidate_tokens)
    coverage_ods = len(common) / len(ods_tokens)
    coverage_candidate = len(common) / len(candidate_tokens)
    sequence_score = int(SequenceMatcher(a=compact_ods, b=compact_candidate).ratio() * 15)
    score = int(coverage_ods * 60) + int(coverage_candidate * 25) + sequence_score
    if numeric_common:
        score += 10
    if normalized_ods in normalized_candidate or compact_ods in compact_candidate:
        score += 10
    if any(token in candidate_tokens for token in {"boom", "base", "arm", "chassis", "bucket", "frame"} & ods_tokens):
        score += 5
    return min(score, 100)


def normalize_part_match_text(value: str) -> str:
    """Normalize separators and common unit spellings for fuzzy matching."""
    text = normalize_text(value)
    text = text.replace("_", " ").replace("-", " ").replace("/", " ")
    text = re.sub(r"(\d+(?:\.\d+)?)\s*(?:ft|feet|foot)\b", r"\1 ft", text)
    text = re.sub(r"(\d+(?:\.\d+)?)\s*(?:mm)\b", r"\1 mm", text)
    text = re.sub(r"[^a-z0-9.\s]", " ", text)
    return " ".join(text.split())


def compact_part_match_text(value: str) -> str:
    """Compact a normalized part string to letters and digits only."""
    return re.sub(r"[^a-z0-9]", "", value)


def part_match_tokens(value: str) -> set[str]:
    """Tokenize a normalized part string for overlap scoring."""
    return set(re.findall(r"[a-z0-9]+(?:\.[0-9]+)?", value))


def numeric_part_tokens(tokens: set[str]) -> set[str]:
    """Return the numeric-looking tokens from a token set."""
    return {token for token in tokens if any(char.isdigit() for char in token)}


def render_part_comparison_summaries(
    console: Console,
    model_choice: ProductModelChoice,
    summaries: list[PartComparisonSummary],
) -> None:
    """Render the comparison summary tables for each target sheet."""
    console.print(
        Panel(
            f"[bold]ProductModelCode:[/bold] {model_choice.product_model_code}\n"
            "Comparing distinct active ODS entries against the matching Grist product-part names in scope.",
            title="Comparison Scope",
            border_style="yellow",
            padding=(0, 2),
        )
    )
    overview = Table(title="ODS vs Grist Product Part Summary")
    overview.add_column("Sheet")
    overview.add_column("ODS Field")
    overview.add_column("Distinct ODS Entries", justify="right")
    overview.add_column("Matched", justify="right", style="green")
    overview.add_column("Ambiguous", justify="right", style="yellow")
    overview.add_column("Missing", justify="right", style="red")
    for summary in summaries:
        overview.add_row(
            summary.menu_label,
            summary.ods_field_label,
            str(summary.total_ods_entries),
            str(summary.matched_count),
            str(summary.ambiguous_count),
            str(summary.missing_count),
        )
    console.print(overview)

    for summary in summaries:
        detail_rows = [result for result in summary.results if result.status != "matched"]
        if not detail_rows:
            continue
        table = Table(title=f"{summary.menu_label} Unresolved Entries")
        table.add_column("ODS Entry")
        table.add_column("Status")
        table.add_column("Suggestions")
        for result in detail_rows:
            suggestion_text = ", ".join(
                f"{suggestion.product_part_name} ({suggestion.score})" for suggestion in result.suggestions
            ) or "-"
            table.add_row(result.ods_value, result.status.title(), suggestion_text)
        console.print(table)


def collect_unresolved_ods_entries(
    summaries: list[PartComparisonSummary],
    master_rows: list[ProductPartMasterRow],
) -> list[UnresolvedOdsEntry]:
    """Combine unresolved ODS labels across summary sheets for a single prompt flow."""
    master_names = [row.product_part_name for row in master_rows]
    combined: dict[str, dict[str, Any]] = {}
    for summary in summaries:
        for result in summary.results:
            if result.status == "matched":
                continue
            key = normalize_text(result.ods_value)
            entry = combined.setdefault(
                key,
                {
                    "ods_value": result.ods_value,
                    "sheets": set(),
                    "suggestions": [],
                },
            )
            entry["sheets"].add(summary.menu_label)
            for suggestion in result.suggestions:
                entry["suggestions"].append(suggestion)
            if not entry["suggestions"]:
                for suggestion in rank_part_match_suggestions(result.ods_value, master_names)[:3]:
                    entry["suggestions"].append(suggestion)

    unresolved: list[UnresolvedOdsEntry] = []
    for entry in combined.values():
        suggestions_by_name: dict[str, PartMatchSuggestion] = {}
        for suggestion in entry["suggestions"]:
            existing = suggestions_by_name.get(suggestion.product_part_name)
            if existing is None or suggestion.score > existing.score:
                suggestions_by_name[suggestion.product_part_name] = suggestion
        suggestions = sorted(
            suggestions_by_name.values(),
            key=lambda item: (-item.score, item.product_part_name.casefold()),
        )[:5]
        unresolved.append(
            UnresolvedOdsEntry(
                ods_value=entry["ods_value"],
                sheets=tuple(sorted(entry["sheets"])),
                suggestions=tuple(suggestions),
            )
        )
    return sorted(unresolved, key=lambda item: item.ods_value.casefold())


def prompt_missing_part_resolutions(
    console: Console,
    unresolved: list[UnresolvedOdsEntry],
    master_rows: list[ProductPartMasterRow],
) -> list[MissingPartResolution]:
    """Prompt for a target ProductPartName for each unresolved ODS label."""
    master_by_normalized = {
        normalize_text(row.product_part_name): row
        for row in master_rows
    }
    queued_descriptions_by_target: dict[str, str] = {}
    resolutions: list[MissingPartResolution] = []
    for index, entry in enumerate(unresolved, start=1):
        suggestion_text = "\n".join(
            f"- {suggestion.product_part_name} (score {suggestion.score})"
            for suggestion in entry.suggestions
        ) or "- No close suggestions found"
        console.print(
            Panel(
                "\n".join(
                    [
                        f"ODS entry {index} of {len(unresolved)}",
                        f"ODS label: {entry.ods_value}",
                        f"Found in: {', '.join(entry.sheets)}",
                        "Suggested existing Grist ProductPartName values:",
                        suggestion_text,
                        "Leave ProductPartName blank to skip this entry.",
                        "Type 'skip all' to stop reviewing and keep the entries already queued.",
                    ]
                ),
                title="Resolve ODS Product Part",
                border_style="cyan",
                padding=(1, 2),
            )
        )
        target_name = Prompt.ask(
            "Type the Grist ProductPartName to use for this ODS entry",
            default="",
            show_default=False,
        ).strip()
        if normalize_text(target_name) in {"skip all", "skipall", "/skipall", "/skip-all"}:
            console.print(
                "[yellow]Stopped reviewing unresolved entries. Keeping the product parts already queued.[/yellow]"
            )
            break
        if not target_name:
            continue
        normalized_target = normalize_text(target_name)
        existing_row = master_by_normalized.get(normalized_target)
        if existing_row is not None:
            part_description = existing_row.part_description
            console.print(
                "[cyan]Existing ProductPartMaster match found:[/cyan] "
                f"{existing_row.product_part_name} "
                f"(PartDescription: {existing_row.part_description or '(blank)'})"
            )
        elif normalized_target in queued_descriptions_by_target:
            part_description = queued_descriptions_by_target[normalized_target]
            console.print(
                "[cyan]This ProductPartName is already queued in the current batch.[/cyan] "
                f"Reusing PartDescription: {part_description or '(blank)'}"
            )
        else:
            part_description = Prompt.ask(
                "Enter PartDescription for the new ProductPartMaster row",
                default="",
                show_default=False,
            ).strip()
            queued_descriptions_by_target[normalized_target] = part_description
        resolutions.append(
            MissingPartResolution(
                ods_value=entry.ods_value,
                target_product_part_name=target_name,
                part_description=part_description,
                sheets=entry.sheets,
            )
        )
    return resolutions


def apply_missing_part_resolutions(
    console: Console,
    client: GristClient,
    model_choice: ProductModelChoice,
    master_rows: list[ProductPartMasterRow],
    resolutions: list[MissingPartResolution],
) -> None:
    """Create missing ProductPartMaster rows and ProductModelConfig links."""
    master_by_normalized = {
        normalize_text(row.product_part_name): row
        for row in master_rows
    }
    planned_rows: list[tuple[MissingPartResolution, str, ProductPartMasterRow | None]] = []
    planned_target_names: set[str] = set()
    for resolution in resolutions:
        normalized_target = normalize_text(resolution.target_product_part_name)
        existing = master_by_normalized.get(normalized_target)
        if existing is not None:
            action = "Reuse existing ProductPartMaster"
        elif normalized_target in planned_target_names:
            action = "Create once and reuse in this batch"
        else:
            action = "Create ProductPartMaster"
        planned_target_names.add(normalized_target)
        planned_rows.append((resolution, action, existing))

    table = Table(title="Planned Product Part Updates")
    table.add_column("ODS Entry")
    table.add_column("Sheets")
    table.add_column("Chosen ProductPartName")
    table.add_column("PartDescription")
    table.add_column("Action")
    table.add_column("Assign To Model")
    for resolution, action, existing in planned_rows:
        will_assign = "No"
        if existing is None or existing.row_id not in model_choice.product_part_ids:
            will_assign = "Yes"
        table.add_row(
            resolution.ods_value,
            ", ".join(resolution.sheets),
            resolution.target_product_part_name,
            existing.part_description if existing is not None else resolution.part_description,
            action,
            will_assign,
        )
    console.print(table)
    if not Confirm.ask("Apply these ProductPartMaster / ProductModelConfig changes?", default=True):
        console.print("[yellow]Nothing changed.[/yellow]")
        return

    created_master_rows = 0
    created_config_rows = 0
    for resolution, _, existing in planned_rows:
        master_row = existing or master_by_normalized.get(normalize_text(resolution.target_product_part_name))
        if master_row is None:
            created = client.create_table_records(
                PRODUCT_PART_MASTER_TABLE,
                [
                    {
                        "fields": {
                            "ProductPartName": resolution.target_product_part_name,
                            "PartDescription": resolution.part_description,
                        }
                    }
                ],
            )
            if not created:
                raise GristError("ProductPartMaster row creation did not return a created record.")
            fields = created[0].get("fields", {})
            master_row = ProductPartMasterRow(
                row_id=int(created[0]["id"]),
                product_part_name=display_text(fields.get("ProductPartName")) or resolution.target_product_part_name,
                part_description=display_text(fields.get("PartDescription")),
            )
            master_by_normalized[normalize_text(master_row.product_part_name)] = master_row
            created_master_rows += 1
            console.print(
                f"[green]Created ProductPartMaster row:[/green] "
                f"{master_row.row_id} - {master_row.product_part_name}"
            )

        if master_row.row_id in model_choice.product_part_ids:
            continue

        created = client.create_table_records(
            PRODUCT_MODEL_CONFIG_TABLE,
            [
                {
                    "fields": {
                        "ProductModelCode": model_choice.product_model_id,
                        "ProductPartName": master_row.row_id,
                        "ExistingProduct": "Product Part",
                    }
                }
            ],
        )
        created_config_rows += 1 if created else 0
        model_choice.product_part_ids.add(master_row.row_id)
        model_choice.product_part_names.add(master_row.product_part_name)
        console.print(
            f"[green]Created ProductModelConfig row:[/green] "
            f"{model_choice.product_model_code} -> {master_row.product_part_name}"
        )

    console.print(
        f"[green]Completed:[/green] {created_master_rows} ProductPartMaster row(s) created, "
        f"{created_config_rows} ProductModelConfig row(s) created."
    )


def add_product_part_name(console: Console, client: GristClient) -> None:
    """Create a new ProductPartMaster entry after duplicate checks."""
    created_row = prompt_create_product_part_master_row(console, client)
    if created_row is None:
        console.print("[yellow]Nothing changed.[/yellow]")
        return
    console.print(
        f"[green]Created ProductPartMaster row:[/green] "
        f"{created_row.row_id} - {created_row.product_part_name}"
    )

    if Confirm.ask("Assign this new part to ProductModelConfig now?", default=True):
        assign_part_to_config(console, client, preferred_product_part=created_row)


def prompt_create_product_part_master_row(
    console: Console,
    client: GristClient,
) -> ProductPartMasterRow | None:
    """Prompt for and create one ProductPartMaster row after duplicate checks."""
    master_rows = fetch_product_part_master_rows(client)
    product_part_name = Prompt.ask("Enter ProductPartName").strip()
    if is_blank(product_part_name):
        console.print("[yellow]Nothing added. ProductPartName is required.[/yellow]")
        return None

    duplicate = find_product_part_master_row(master_rows, product_part_name)
    if duplicate is not None:
        console.print(
            f"[yellow]ProductPartName already exists:[/yellow] "
            f"{duplicate.product_part_name} (row id {duplicate.row_id})"
        )
        return None

    part_description = Prompt.ask("Enter PartDescription", default="", show_default=False).strip()
    console.print(
        Panel(
            "\n".join(
                [
                    "Please confirm the new part details before creating the ProductPartMaster entry.",
                    f"ProductPartName: {product_part_name}",
                    f"PartDescription: {part_description or '(blank)'}",
                ]
            ),
            title="Confirm New Part",
            border_style="cyan",
            padding=(1, 2),
        )
    )
    if not Confirm.ask(
        "Create this new part in ProductPartMaster?",
        default=True,
    ):
        return None

    created = client.create_table_records(
        PRODUCT_PART_MASTER_TABLE,
        [
            {
                "fields": {
                    "ProductPartName": product_part_name,
                    "PartDescription": part_description,
                }
            }
        ],
    )
    if not created:
        raise GristError("ProductPartMaster row creation did not return a created record.")

    fields = created[0].get("fields", {})
    return ProductPartMasterRow(
        row_id=int(created[0]["id"]),
        product_part_name=display_text(fields.get("ProductPartName")) or product_part_name,
        part_description=display_text(fields.get("PartDescription")) or part_description,
    )


def assign_part_to_config(
    console: Console,
    client: GristClient,
    preferred_product_part: ProductPartMasterRow | None = None,
) -> None:
    """Create a ProductModelConfig row linking a model code to a product part."""
    model_choice = choose_product_model_code_for_config_assignment(
        console,
        client,
        preferred_product_part_name=(
            None if preferred_product_part is None else preferred_product_part.product_part_name
        ),
    )
    if model_choice is None:
        console.print("[yellow]Nothing changed.[/yellow]")
        return
    product_part_row = (
        preferred_product_part
        if preferred_product_part is not None
        else choose_product_part_master_row(console, client, preferred_row=None)
    )
    if product_part_row is None:
        console.print("[yellow]Nothing changed.[/yellow]")
        return

    if product_part_row.row_id in model_choice.product_part_ids:
        console.print(
            "[yellow]That ProductModelCode already includes this ProductPartName in ProductModelConfig.[/yellow]"
        )
        return

    console.print(f"[cyan]Selected ProductModelCode:[/cyan] {model_choice.product_model_code}")
    console.print(f"[cyan]Selected ProductPartName:[/cyan] {product_part_row.product_part_name}")
    if not Confirm.ask("Create this ProductModelConfig entry?", default=True):
        console.print("[yellow]Nothing changed.[/yellow]")
        return

    created = client.create_table_records(
        PRODUCT_MODEL_CONFIG_TABLE,
        [
            {
                "fields": {
                    "ProductModelCode": model_choice.product_model_id,
                    "ProductPartName": product_part_row.row_id,
                    "ExistingProduct": "Product Part",
                }
            }
        ],
    )
    created_id = created[0].get("id") if created else "?"
    console.print(
        f"[green]Created ProductModelConfig row:[/green] {created_id} "
        f"({model_choice.product_model_code} -> {product_part_row.product_part_name})"
    )


def assign_product_part_name(
    console: Console,
    client: GristClient,
    preferred_product_part: ProductPartMasterRow | None = None,
) -> None:
    """Assign a ProductPartMaster reference to selected ProductPartMSList rows."""
    console.print(
        "[cyan]This workflow updates ProductPartName on rows in ProductPartMSList.[/cyan]"
    )
    console.print(
        "[cyan]First choose how to find the current MS Cut List rows you want to update.[/cyan]"
    )
    all_rows = fetch_product_part_ms_rows(client)
    if not all_rows:
        console.print("[yellow]No ProductPartMSList rows with an existing ProductPartName were found.[/yellow]")
        return

    while True:
        existing_part_choice = choose_existing_ms_product_part_name(console, all_rows)
        if existing_part_choice is None:
            console.print("[yellow]Nothing changed.[/yellow]")
            return

        matching_rows = filter_rows_by_current_product_part_name(all_rows, existing_part_choice.product_part_name)
        if not matching_rows:
            console.print("[yellow]No ProductPartMSList rows matched that ProductPartName.[/yellow]")
            return

        while True:
            console.print(
                f"[cyan]Selected current ProductPartName:[/cyan] {existing_part_choice.product_part_name} "
                f"({existing_part_choice.row_count} row(s) found in ProductPartMSList)"
            )
            console.print(
                "[cyan]Now choose the MachinePieceDesc group inside those MS Cut List rows.[/cyan]"
            )
            machine_piece_desc = choose_machine_piece_desc(
                console,
                matching_rows,
                existing_part_choice.product_part_name,
            )
            if machine_piece_desc == GO_BACK_LABEL:
                console.print("[cyan]Going back to the ProductPartMSList ProductPartName selection.[/cyan]")
                break

            selected_rows = filter_rows_by_machine_piece_desc(matching_rows, machine_piece_desc)
            if not selected_rows:
                console.print("[yellow]No ProductPartMSList rows matched that MachinePieceDesc selection.[/yellow]")
                return

            console.print(
                f"[cyan]Selected current ProductPartName:[/cyan] {existing_part_choice.product_part_name}"
            )
            console.print(
                f"[cyan]Selected MachinePieceDesc:[/cyan] "
                f"{BLANKS_LABEL if machine_piece_desc is None else machine_piece_desc}"
            )
            console.print(
                "[cyan]These are the ProductPartMSList rows that will be updated if you continue.[/cyan]"
            )
            show_product_part_ms_rows(console, selected_rows, title="Rows To Update")
            preview_action = ask_ms_cut_list_preview_action(console, len(selected_rows))
            if preview_action == "1":
                break
            if preview_action == "2":
                console.print("[cyan]Going back to the MachinePieceDesc selection.[/cyan]")
                continue
            console.print("[yellow]Nothing changed.[/yellow]")
            return
        if machine_piece_desc == GO_BACK_LABEL:
            continue
        break

    console.print(
        "[cyan]Next choose the ProductPartMaster entry that should replace the current ProductPartName on these rows.[/cyan]"
    )
    master_choice = choose_product_part_master_row(console, client, preferred_row=preferred_product_part)
    if master_choice is None:
        console.print("[yellow]Nothing changed.[/yellow]")
        return

    rows_needing_change = [
        row for row in selected_rows if row.current_product_part_id != master_choice.row_id
    ]
    if not rows_needing_change:
        console.print(
            "[yellow]All selected rows already point to that ProductPartName. Nothing changed.[/yellow]"
        )
        return

    show_current_value_warning(console, rows_needing_change, master_choice.product_part_name)
    if not Confirm.ask(
        f"Assign '{master_choice.product_part_name}' to {len(rows_needing_change)} row(s)?",
        default=False,
    ):
        console.print("[yellow]Nothing changed.[/yellow]")
        return

    client.update_table_records(
        PRODUCT_PART_MS_LIST_TABLE,
        [
            {
                "id": row.row_id,
                "fields": {
                    "ProductPartName": master_choice.row_id,
                },
            }
            for row in rows_needing_change
        ],
    )
    console.print(
        f"[green]Updated {len(rows_needing_change)} ProductPartMSList row(s) to:[/green] "
        f"{master_choice.product_part_name}"
    )


def fetch_product_part_master_rows(client: GristClient) -> list[ProductPartMasterRow]:
    """Fetch ProductPartMaster rows with the fields needed for selection."""
    records = client.fetch_table_records_with_ids(PRODUCT_PART_MASTER_TABLE)
    rows: list[ProductPartMasterRow] = []
    for record in records:
        fields = record.get("fields", {})
        row_id = record.get("id")
        if row_id is None:
            continue
        product_part_name = display_text(fields.get("ProductPartName"))
        if not product_part_name:
            continue
        rows.append(
            ProductPartMasterRow(
                row_id=int(row_id),
                product_part_name=product_part_name,
                part_description=display_text(fields.get("PartDescription")),
            )
        )
    if not rows:
        raise GristError("No ProductPartMaster rows with ProductPartName were found.")
    return sorted(rows, key=lambda row: row.product_part_name.casefold())


def find_product_part_master_row(
    master_rows: list[ProductPartMasterRow],
    product_part_name: str,
) -> ProductPartMasterRow | None:
    """Return a ProductPartMaster row matching ProductPartName, if any."""
    wanted = normalize_text(product_part_name)
    for row in master_rows:
        if normalize_text(row.product_part_name) == wanted:
            return row
    return None


def choose_product_model_code(console: Console, client: GristClient) -> ProductModelChoice | None:
    """Prompt for a ProductModelConfig code and its associated part names."""
    choices = sorted(
        fetch_product_model_config_choices(client).values(),
        key=lambda item: item.product_model_code.casefold(),
    )
    if not choices:
        raise GristError("No ProductModelConfig rows with ProductModelCode and ProductPartName were found.")
    table = Table(title="ProductModelConfig Codes")
    table.add_column("Option", justify="right", style="cyan")
    table.add_column("ProductModelCode")
    table.add_column("Description")
    table.add_column("Parts", justify="right")
    for index, choice in enumerate(choices, start=1):
        table.add_row(
            str(index),
            choice.product_model_code,
            choice.description,
            str(len(choice.product_part_names)),
        )
    back_option = len(choices) + 1
    table.add_row(str(back_option), "Back", "Return to Manage Product Part Names", "")
    console.print(table)
    selected = Prompt.ask(
        "Choose ProductModelCode",
        choices=[str(index) for index in range(1, back_option + 1)],
    )
    if int(selected) == back_option:
        return None
    return choices[int(selected) - 1]


def choose_product_model_code_for_config_assignment(
    console: Console,
    client: GristClient,
    preferred_product_part_name: str | None = None,
) -> ProductModelChoice | None:
    """Prompt for a ProductModelMaster2 code when creating ProductModelConfig links."""
    config_choices_by_id = fetch_product_model_config_choices(client)
    master_records = client.fetch_table_records_with_ids(PRODUCT_MODEL_MASTER_TABLE)
    choices: list[ProductModelChoice] = []
    for record in master_records:
        fields = record.get("fields", {})
        row_id = record.get("id")
        if row_id is None:
            continue
        code = display_text(fields.get("ProductModelCode"))
        if not code:
            continue

        existing = config_choices_by_id.get(int(row_id))
        choices.append(
            ProductModelChoice(
                product_model_id=int(row_id),
                product_model_code=code,
                description=display_text(fields.get("ProductModelDesc")),
                product_part_names=set() if existing is None else set(existing.product_part_names),
                product_part_ids=set() if existing is None else set(existing.product_part_ids),
            )
        )

    if not choices:
        raise GristError("No ProductModelMaster2 rows with ProductModelCode were found.")

    choices.sort(key=lambda item: item.product_model_code.casefold())
    if preferred_product_part_name:
        console.print(
            Panel(
                f"Please select the ProductModelCode to which the new part "
                f"[bold]{preferred_product_part_name}[/bold] should be assigned.",
                title="Assign New Part To Product Model",
                border_style="cyan",
                padding=(1, 2),
            )
        )
    else:
        console.print(
            Panel(
                "Please select the ProductModelCode that should receive this ProductPartName assignment.",
                title="Assign Part To Product Model",
                border_style="cyan",
                padding=(1, 2),
            )
        )
    table = Table(title="Product Models")
    table.add_column("Option", justify="right", style="cyan")
    table.add_column("ProductModelCode")
    table.add_column("Description")
    table.add_column("Configured Parts", justify="right")
    for index, choice in enumerate(choices, start=1):
        table.add_row(
            str(index),
            choice.product_model_code,
            choice.description,
            str(len(choice.product_part_names)),
        )
    back_option = len(choices) + 1
    table.add_row(str(back_option), "Back", "Return to the previous step", "")
    console.print(table)
    selected = Prompt.ask(
        "Choose ProductModelCode",
        choices=[str(index) for index in range(1, back_option + 1)],
    )
    if int(selected) == back_option:
        return None
    return choices[int(selected) - 1]


def fetch_product_model_config_choices(client: GristClient) -> dict[int, ProductModelChoice]:
    """Return ProductModelConfig-backed choices keyed by ProductModelMaster2 record ID."""
    records = client.fetch_table_records_with_ids(PRODUCT_MODEL_CONFIG_TABLE)
    choices_by_id: dict[int, ProductModelChoice] = {}
    for record in records:
        fields = record.get("fields", {})
        code = display_text(fields.get("ProductModelCode_ProductModelCode2"))
        product_part_name = display_text(fields.get("ProductPartName_ProductPartName"))
        product_model_id = linked_record_id(fields.get("ProductModelCode"))
        product_part_id = linked_record_id(fields.get("ProductPartName"))
        if not code or not product_part_name or product_model_id is None or product_part_id is None:
            continue

        existing = choices_by_id.get(product_model_id)
        if existing is None:
            choices_by_id[product_model_id] = ProductModelChoice(
                product_model_id=product_model_id,
                product_model_code=code,
                description=display_text(fields.get("ProductModelCode_ProductModelDesc")),
                product_part_names={product_part_name},
                product_part_ids={product_part_id},
            )
        else:
            existing.product_part_names.add(product_part_name)
            existing.product_part_ids.add(product_part_id)
    return choices_by_id


def fetch_product_part_ms_rows(client: GristClient) -> list[ProductPartMSListRow]:
    """Fetch ProductPartMSList rows with an existing ProductPartName for selection."""
    records = client.fetch_table_records_with_ids(PRODUCT_PART_MS_LIST_TABLE)
    material_names_by_id = fetch_master_material_names_by_id(client)
    rows: list[ProductPartMSListRow] = []
    for record in records:
        fields = record.get("fields", {})
        current_product_part_name = display_text(fields.get("ProductPartName_ProductPartName"))
        if not current_product_part_name:
            continue
        row_id = record.get("id")
        if row_id is None:
            continue
        material_id = linked_record_id(fields.get("MaterialToCut"))
        rows.append(
            ProductPartMSListRow(
                row_id=int(row_id),
                record_id=display_text(fields.get("RecordID")),
                machine_piece_desc=display_text(fields.get("MachinePieceDesc")),
                material_to_cut=material_names_by_id.get(material_id, display_text(fields.get("MaterialToCut"))),
                length_mm=display_text(fields.get("Length_mm")),
                qty_nos=display_text(fields.get("QtyNos")),
                option_group_1_temp=display_text(fields.get("OptionGroup1_TEMP")),
                remarks=display_text(fields.get("Remarks")),
                current_product_part_name=current_product_part_name,
                current_product_part_id=linked_record_id(fields.get("ProductPartName")),
                material_row_id=material_id,
                part_status=display_text(fields.get("Part_Status")),
                part_status_remark=display_text(fields.get("Part_Status_Remark")),
            )
        )
    return sorted(
        rows,
        key=lambda row: (
            normalize_text(row.current_product_part_name),
            normalize_text(row.machine_piece_desc or BLANKS_LABEL),
            normalize_text(row.record_id),
            normalize_text(row.material_to_cut),
            normalize_text(row.length_mm),
        ),
    )


def fetch_master_material_names_by_id(client: GristClient) -> dict[int, str]:
    """Return MasterMaterial display names keyed by row id."""
    records = client.fetch_table_records_with_ids(MASTER_MATERIAL_TABLE)
    names_by_id: dict[int, str] = {}
    for record in records:
        row_id = record.get("id")
        if row_id is None:
            continue
        fields = record.get("fields", {})
        name = display_text(fields.get("MasterMaterial"))
        if not name:
            continue
        names_by_id[int(row_id)] = name
    return names_by_id


def choose_existing_ms_product_part_name(
    console: Console,
    rows: list[ProductPartMSListRow],
) -> ExistingMsProductPartChoice | None:
    """Prompt for the current ProductPartName scope inside ProductPartMSList."""
    while True:
        action = ask_ms_cut_list_filter_mode(console)
        if action == "3":
            return None

        query = ""
        if action == "1":
            query = Prompt.ask(
                "Search Existing ProductPartName in ProductPartMSList; blank shows all",
                default="",
                show_default=False,
            ).strip()

        matches = search_existing_ms_product_part_names(rows, query)
        if not matches:
            console.print("[yellow]No ProductPartMSList ProductPartName values matched that search.[/yellow]")
            continue
        selected_choice = choose_existing_ms_product_part_name_from_matches(console, matches)
        if selected_choice is None:
            continue
        return selected_choice


def ask_ms_cut_list_filter_mode(console: Console) -> str:
    """Render the ProductPartMSList filter mode menu."""
    table = Table(title="Specify Filter Option from MS Cut List (ProductPartMSList)")
    table.add_column("Option", justify="right", style="cyan")
    table.add_column("Action")
    table.add_row("1", "Search based on Existing ProductPartName field")
    table.add_row("2", "Show all Available ProductPartNames (Sorted)")
    table.add_row("3", "Cancel")
    console.print(table)
    return Prompt.ask("Choose filter option", choices=["1", "2", "3"], default="1")


def search_existing_ms_product_part_names(
    rows: list[ProductPartMSListRow],
    query: str,
) -> list[ExistingMsProductPartChoice]:
    """Return distinct current ProductPartName values from ProductPartMSList."""
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.current_product_part_name] = counts.get(row.current_product_part_name, 0) + 1

    matches = [
        ExistingMsProductPartChoice(product_part_name=name, row_count=count)
        for name, count in counts.items()
        if not query or normalize_text(query) in normalize_text(name)
    ]
    return sorted(matches, key=lambda item: item.product_part_name.casefold())


def choose_existing_ms_product_part_name_from_matches(
    console: Console,
    matches: list[ExistingMsProductPartChoice],
) -> ExistingMsProductPartChoice | None:
    """Prompt for a distinct existing ProductPartName found in ProductPartMSList."""
    show_selection_source(
        console,
        "Grist",
        "Selecting current ProductPartName values from ProductPartMSList.",
    )
    table = Table(title="Available ProductPartNames in ProductPartMSList")
    table.add_column("Option", justify="right", style="cyan")
    table.add_column("Current ProductPartName")
    table.add_column("Rows", justify="right")
    for index, choice in enumerate(matches, start=1):
        table.add_row(str(index), choice.product_part_name, str(choice.row_count))
    back_option = len(matches) + 1
    table.add_row(str(back_option), "Back", "")
    console.print(table)
    selected = Prompt.ask(
        "Choose current ProductPartName",
        choices=[str(index) for index in range(1, back_option + 1)],
    )
    if int(selected) == back_option:
        return None
    return matches[int(selected) - 1]


def filter_rows_by_current_product_part_name(
    rows: list[ProductPartMSListRow],
    product_part_name: str,
) -> list[ProductPartMSListRow]:
    """Return ProductPartMSList rows matching the selected current ProductPartName."""
    wanted = normalize_text(product_part_name)
    return [row for row in rows if normalize_text(row.current_product_part_name) == wanted]


def choose_machine_piece_desc(
    console: Console,
    rows: list[ProductPartMSListRow],
    selection_label: str,
) -> str | None:
    """Prompt for a distinct MachinePieceDesc, including Blanks when present."""
    grouped_counts: dict[str | None, int] = {}
    for row in rows:
        key = None if is_blank(row.machine_piece_desc) else row.machine_piece_desc
        grouped_counts[key] = grouped_counts.get(key, 0) + 1

    if not grouped_counts:
        raise GristError("No MachinePieceDesc values were found for the selected rows.")

    values = sorted(
        grouped_counts.keys(),
        key=lambda item: (item is not None, normalize_text(item or "")),
    )
    show_selection_source(
        console,
        "Grist",
        f"Selecting MachinePieceDesc values from ProductPartMSList for current ProductPartName '{selection_label}'.",
    )
    table = Table(title=f"MachinePieceDesc for {selection_label}")
    table.add_column("Option", justify="right", style="cyan")
    table.add_column("MachinePieceDesc")
    table.add_column("Rows", justify="right")
    for index, value in enumerate(values, start=1):
        table.add_row(
            str(index),
            BLANKS_LABEL if value is None else value,
            str(grouped_counts[value]),
        )
    back_option = len(values) + 1
    table.add_row(str(back_option), "Go back", "Return to ProductPartMSList ProductPartName selection")
    console.print(table)
    selected = Prompt.ask(
        "Choose MachinePieceDesc",
        choices=[str(index) for index in range(1, back_option + 1)],
    )
    if int(selected) == back_option:
        return GO_BACK_LABEL
    return values[int(selected) - 1]


def ask_ms_cut_list_preview_action(console: Console, row_count: int) -> str:
    """Ask what to do after previewing the selected ProductPartMSList rows."""
    table = Table(title="MS Cut List Preview Action")
    table.add_column("Option", justify="right", style="cyan")
    table.add_column("Action")
    table.add_row("1", f"Continue with these {row_count} row(s)")
    table.add_row("2", "Go back to MachinePieceDesc selection")
    table.add_row("3", "Cancel")
    console.print(table)
    return Prompt.ask("Choose next step", choices=["1", "2", "3"], default="1")


def filter_rows_by_machine_piece_desc(
    rows: list[ProductPartMSListRow],
    machine_piece_desc: str | None,
) -> list[ProductPartMSListRow]:
    """Return ProductPartMSList rows matching the selected MachinePieceDesc."""
    if machine_piece_desc is None:
        return [row for row in rows if is_blank(row.machine_piece_desc)]
    wanted = normalize_text(machine_piece_desc)
    return [row for row in rows if normalize_text(row.machine_piece_desc) == wanted]


def show_product_part_ms_rows(
    console: Console,
    rows: list[ProductPartMSListRow],
    *,
    title: str,
) -> None:
    """Render the selected ProductPartMSList rows with the requested preview fields."""
    table = Table(title=title, caption=f"{len(rows)} row(s)")
    table.add_column("RecordID")
    table.add_column("MachinePieceDesc")
    table.add_column("MaterialToCut")
    table.add_column("Length_mm")
    table.add_column("QtyNos")
    table.add_column("OptionGroup1-TEMP")
    table.add_column("Remarks")
    for row in rows:
        table.add_row(
            row.record_id,
            row.machine_piece_desc,
            row.material_to_cut,
            row.length_mm,
            row.qty_nos,
            row.option_group_1_temp,
            row.remarks,
        )
    console.print(table)


def choose_product_part_master_row(
    console: Console,
    client: GristClient,
    *,
    preferred_row: ProductPartMasterRow | None = None,
) -> ProductPartMasterRow | None:
    """Prompt for a ProductPartMaster row using search or a sorted full list."""
    rows = fetch_product_part_master_rows(client)
    rows_by_id = {row.row_id: row for row in rows}
    if preferred_row is not None:
        rows_by_id[preferred_row.row_id] = preferred_row

    while True:
        action = ask_product_part_picker_mode(console, preferred_row is not None)
        if action == "1" and preferred_row is not None:
            return rows_by_id.get(preferred_row.row_id, preferred_row)
        if action == "2" and preferred_row is not None:
            query = Prompt.ask(
                "Search ProductPartName or PartDescription; blank shows all",
                default="",
                show_default=False,
            ).strip()
            matches = search_product_part_master_rows(rows, query)
            if not matches:
                console.print("[yellow]No ProductPartMaster rows matched that search.[/yellow]")
                continue
            selected_row = choose_product_part_master_row_from_matches(console, matches)
            if selected_row is None:
                continue
            return selected_row
        if action == "3" and preferred_row is not None:
            selected_row = choose_product_part_master_row_from_matches(console, rows)
            if selected_row is None:
                continue
            return selected_row
        if action == "2" and preferred_row is None:
            selected_row = choose_product_part_master_row_from_matches(console, rows)
            if selected_row is None:
                continue
            return selected_row
        if action == "1" and preferred_row is None:
            query = Prompt.ask(
                "Search ProductPartName or PartDescription; blank shows all",
                default="",
                show_default=False,
            ).strip()
            matches = search_product_part_master_rows(rows, query)
            if not matches:
                console.print("[yellow]No ProductPartMaster rows matched that search.[/yellow]")
                continue
            selected_row = choose_product_part_master_row_from_matches(console, matches)
            if selected_row is None:
                continue
            return selected_row
        return None


def ask_product_part_picker_mode(console: Console, has_preferred_row: bool) -> str:
    """Render the product-part picker mode menu."""
    table = Table(title="Select ProductPartName")
    table.add_column("Option", justify="right", style="cyan")
    table.add_column("Action")
    if has_preferred_row:
        table.add_row("1", "Use newly created part")
        table.add_row("2", "Search ProductPartName / PartDescription")
        table.add_row("3", "Show all (sorted)")
        table.add_row("4", "Cancel")
        console.print(table)
        return Prompt.ask("Choose an option", choices=["1", "2", "3", "4"], default="1")

    table.add_row("1", "Search ProductPartName / PartDescription")
    table.add_row("2", "Show all (sorted)")
    table.add_row("3", "Cancel")
    console.print(table)
    return Prompt.ask("Choose an option", choices=["1", "2", "3"], default="1")


def search_product_part_master_rows(
    rows: list[ProductPartMasterRow],
    query: str,
) -> list[ProductPartMasterRow]:
    """Return ProductPartMaster rows matching ProductPartName or PartDescription."""
    normalized_query = normalize_text(query)
    if not normalized_query:
        return rows
    return [
        row
        for row in rows
        if normalized_query in normalize_text(row.product_part_name)
        or normalized_query in normalize_text(row.part_description)
    ]


def choose_product_part_master_row_from_matches(
    console: Console,
    rows: list[ProductPartMasterRow],
) -> ProductPartMasterRow | None:
    """Prompt for a ProductPartMaster row from a prepared list."""
    show_selection_source(
        console,
        "Grist",
        "Selecting ProductPartName values from ProductPartMaster.",
    )
    table = Table(title="ProductPartMaster")
    table.add_column("Option", justify="right", style="cyan")
    table.add_column("ProductPartName")
    table.add_column("PartDescription")
    for index, row in enumerate(rows, start=1):
        table.add_row(str(index), row.product_part_name, row.part_description)
    back_option = len(rows) + 1
    table.add_row(str(back_option), "Back", "Return to ProductPartName selection")
    console.print(table)
    selected = Prompt.ask(
        "Choose ProductPartName",
        choices=[str(index) for index in range(1, back_option + 1)],
    )
    if int(selected) == back_option:
        return None
    return rows[int(selected) - 1]


def show_current_value_warning(
    console: Console,
    rows: list[ProductPartMSListRow],
    new_product_part_name: str,
) -> None:
    """Show the current ProductPartName values before overwriting them."""
    counts: dict[str, int] = {}
    for row in rows:
        label = row.current_product_part_name or "(blank)"
        counts[label] = counts.get(label, 0) + 1

    table = Table(title="Current ProductPartName Values")
    table.add_column("Current Value")
    table.add_column("Rows", justify="right")
    for current_value, count in sorted(counts.items(), key=lambda item: item[0].casefold()):
        table.add_row(current_value, str(count))
    console.print(table)
    console.print(f"[yellow]New ProductPartName to assign:[/yellow] {new_product_part_name}")


def linked_record_id(value: Any) -> int | None:
    """Return a linked Grist record ID from a reference field value."""
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

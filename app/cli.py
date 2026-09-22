"""Typer CLI and interactive command flow."""

from __future__ import annotations

from pathlib import Path
import json
import logging
import os
import webbrowser

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

from app import __app_name__, __version__
from app.config import DEFAULT_CONFIG_PATH, DEFAULT_MATERIAL_MAPPING_PATH, load_app_config
from app.exceptions import CostingAppError, GristPermissionError, GristValidationError, GristWorkspaceSelectionRequired
from app.grist_admin import SAFARI_DOCUMENT_NAME, GristAdminClient, ensure_safari_document
from app.catalog_import import import_catalog
from app.catalog_grist import sync_catalog_to_grist
from app.grist import GristClient
from app.schema import FOUNDATION_TABLES, apply_schema, plan_schema
from app.logging_config import setup_logging
from app.material_mapping_manager import manage_material_mapping
from app.product_part_name_manager import manage_product_part_names
from app.state import LastInputs, load_last_inputs, normalize_ods_filename, save_last_inputs
from app.verification import verify_sheet_with_grist
from app.versioning import create_new_version
from app.workbook import OdsWorkbook

console = Console()
logger = logging.getLogger(__name__)
SHEET_MENU_LABELS = {
    "5. Material Cut List Price": "Material Cut List",
}
app = typer.Typer(
    name=__app_name__,
    help="Process LibreOffice Calc .ods product costing files.",
    invoke_without_command=True,
    no_args_is_help=False,
)


@app.callback()
def callback(
    ctx: typer.Context,
    version: bool = typer.Option(False, "--version", help="Show application version."),
) -> None:
    """Run the default interactive workflow when no subcommand is supplied."""
    if version:
        console.print(f"{__app_name__} {__version__}")
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        interactive_run(DEFAULT_CONFIG_PATH, DEFAULT_MATERIAL_MAPPING_PATH)


@app.command()
def run(
    config_path: Path = typer.Option(DEFAULT_CONFIG_PATH, "--config", help="Sheet configuration YAML."),
    material_mapping_path: Path = typer.Option(
        DEFAULT_MATERIAL_MAPPING_PATH,
        "--material-mapping",
        help="ODS-to-Grist material mapping YAML.",
    ),
) -> None:
    """Run the main interactive menu."""
    interactive_run(config_path, material_mapping_path)


@app.command("materials")
def materials_command(
    material_mapping_path: Path = typer.Option(
        DEFAULT_MATERIAL_MAPPING_PATH,
        "--material-mapping",
        help="ODS-to-Grist material mapping YAML.",
    ),
) -> None:
    """Manage ODS-to-Grist material mappings without loading an ODS workbook."""
    setup_logging()
    try:
        manage_material_mapping(console, material_mapping_path)
    except CostingAppError as exc:
        logger.exception("Material mapping manager error")
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc


@app.command("safari-setup")
def safari_setup_command(
    workspace_id: str | None = typer.Option(None, "--workspace-id", help="Explicit writable Grist workspace ID."),
    apply: bool = typer.Option(False, "--apply", help="Create/reuse the validated document. Plan is the default."),
    schema_apply: bool = typer.Option(False, "--schema-apply", help="Apply the reviewed foundation schema after document validation."),
    yes: bool = typer.Option(False, "--yes", help="Confirm the explicit apply action without an interactive prompt."),
) -> None:
    """Plan or apply guarded Safari Manufacturing document/schema setup."""
    setup_logging()
    try:
        client = GristAdminClient.from_environment()
        selected_workspace_id = workspace_id or os.getenv("SAFARI_MANUFACTURING_GRIST_WORKSPACE_ID", "").strip() or None
        legacy_doc_id = os.getenv("GRIST_DOC_ID", "").strip()
        writable_workspaces = client.discover_writable_workspaces()
        selected_workspace = next((item for item in writable_workspaces if item.id == selected_workspace_id), None) if selected_workspace_id else (writable_workspaces[0] if len(writable_workspaces) == 1 else None)
        workspace_choices = ", ".join(f"{item.name} ({item.id}, org {item.organization_id})" for item in writable_workspaces) or "none"
        console.print(Panel(
            f"Base URL: {client.base_url}\n"
            f"Organization: {selected_workspace.organization_id if selected_workspace else '(selection required)'}\n"
            f"Workspace: {selected_workspace.name + ' (' + selected_workspace.id + ')' if selected_workspace else '(selection required)'}\n"
            f"Writable choices: {workspace_choices}\n"
            f"Document: {SAFARI_DOCUMENT_NAME}\n"
            f"Legacy document guard: {legacy_doc_id or '(not configured)'}\n"
            f"Mode: {'APPLY' if apply else 'PLAN ONLY'}",
            title="Safari Manufacturing setup",
            border_style="cyan",
        ))
        if schema_apply and not apply:
            raise GristValidationError("--schema-apply requires --apply; run without --schema-apply to inspect a schema plan.")
        if selected_workspace is None:
            if selected_workspace_id:
                raise GristValidationError("The explicitly selected workspace is not accessible and writable.")
            if len(writable_workspaces) > 1:
                choices = ", ".join(f"{item.name} ({item.id}, organization {item.organization_id})" for item in writable_workspaces)
                raise GristWorkspaceSelectionRequired(f"Select SAFARI_MANUFACTURING_GRIST_WORKSPACE_ID; writable workspace discovery returned {len(writable_workspaces)} choices: {choices}")
            raise GristPermissionError("The authenticated Grist account has no writable workspace.")
        if (apply or schema_apply) and not yes and not Confirm.ask("Proceed with the explicit Safari Manufacturing setup action?", default=False):
            console.print("Cancelled; no Grist mutation was attempted.")
            return
        result = ensure_safari_document(client, workspace_id=selected_workspace.id, apply=apply, legacy_doc_id=legacy_doc_id)
        console.print(f"Workspace: {result.workspace.name} ({result.workspace.id})")
        console.print(f"Document action: {result.action}")
        if result.document is None:
            console.print("Plan: one Safari Manufacturing document would be created; rerun with --apply.")
            console.print(f"Schema plan: deferred until document creation; {len(FOUNDATION_TABLES)} foundation tables are defined.")
            return
        console.print(f"Organization: {result.workspace.organization_id}")
        console.print(f"Validated document: {result.document.name} ({result.document.id})")
        schema_plan = plan_schema(client, result.document.id, workspace_id=result.workspace.id, document_name=result.document.name, legacy_doc_id=legacy_doc_id)
        console.print(f"Schema plan {schema_plan.schema_version}: {len(schema_plan.create_tables)} tables to create, {len(schema_plan.add_columns)} tables to extend, {len(schema_plan.update_columns)} tables to update.")
        if schema_apply:
            apply_schema(client, schema_plan, legacy_doc_id=legacy_doc_id)
            console.print("Schema applied to the independently revalidated Safari Manufacturing document.")
        else:
            console.print("Schema was not applied; use --apply --schema-apply after reviewing the plan.")
        if apply:
            local_config = Path("config") / "safari_manufacturing.local.json"
            local_config.parent.mkdir(parents=True, exist_ok=True)
            local_config.write_text(json.dumps({"baseUrl": client.base_url, "organizationId": result.workspace.organization_id, "workspaceId": result.workspace.id, "workspaceName": result.workspace.name, "documentId": result.document.id, "documentName": result.document.name}, indent=2), encoding="utf-8")
            console.print(f"Validated document settings saved to ignored local config: {local_config}")
        console.print("Store SAFARI_MANUFACTURING_GRIST_DOC_ID in ignored deployment configuration; it is not written to source control.")
    except GristWorkspaceSelectionRequired as exc:
        logger.error("Safari Manufacturing setup requires explicit workspace selection")
        console.print(f"[yellow]Workspace selection required:[/yellow] {exc}")
        console.print("Set SAFARI_MANUFACTURING_GRIST_WORKSPACE_ID to one of the listed IDs and rerun. No Grist mutation was attempted.")
        raise typer.Exit(code=2) from exc
    except CostingAppError as exc:
        logger.exception("Safari Manufacturing setup failed")
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc


@app.command("safari-catalog")
def safari_catalog_command(
    source: Path = typer.Option(..., "--source", exists=True, dir_okay=False, help="Canonical Product-ProductModelNo-ModelCode.ods path."),
    output: Path | None = typer.Option(None, "--output", help="Optional JSON catalog/import-plan report path."),
    grist_plan: bool = typer.Option(False, "--grist-plan", help="Compare the catalog with the explicitly configured Safari Manufacturing document."),
    apply: bool = typer.Option(False, "--apply", help="Apply the reviewed identity upsert to Safari Manufacturing Grist."),
    yes: bool = typer.Option(False, "--yes", help="Confirm the Grist catalog import without an interactive prompt."),
) -> None:
    """Read and report the canonical identity catalog without mutating its ODS source."""
    try:
        result = import_catalog(source)
        report = result.to_dict()
        console.print(f"Catalog plan: {len(result.products)} products, {len(result.models)} models, {len(result.codes)} codes, {len(result.issues)} issue(s).")
        if grist_plan or apply:
            client = GristClient.from_safari_environment()
            plan = sync_catalog_to_grist(client, result)
            report["gristPlan"] = plan.to_dict()
            console.print(f"Safari Grist plan for {plan.document_id}: {sum(plan.creates.values())} creates, {sum(plan.updates.values())} updates; idempotent={plan.idempotent}.")
            for change in plan.changes[:20]:
                console.print(f"{change['action']} {change['table']}: {change['identity']}")
            if len(plan.changes) > 20:
                console.print(f"... {len(plan.changes) - 20} additional reviewed changes.")
            if apply:
                if not plan.idempotent and not yes and not Confirm.ask("Apply this identity catalog plan to the validated Safari Manufacturing document?", default=False):
                    console.print("Cancelled; no Grist mutation was attempted.")
                    return
                applied = sync_catalog_to_grist(client, result, apply=True, expected_plan=plan)
                console.print(f"Catalog import status: {'already applied' if applied.idempotent else 'applied'}.")
        if output:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
            console.print(f"Report: {output.resolve()}")
        else:
            for issue in result.issues[:10]:
                console.print(f"[yellow]{issue.issue_type}[/yellow] row {issue.source_row}: {issue.message}")
        console.print("The source ODS was not modified.")
        if not apply:
            console.print("No Grist record was modified.")
    except CostingAppError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc


def interactive_run(config_path: Path, material_mapping_path: Path) -> None:
    """Run the main interactive workflow with concrete paths."""
    setup_logging()
    try:
        app_config = load_app_config(config_path)
        last_inputs = load_last_inputs()
        folder, source_file = ask_source_selection(last_inputs.folder, last_inputs.source_file)
        source_path = folder / source_file
        workbook, workbook_mtime = load_workbook_from_disk(source_path)
        previous_folder = Path(last_inputs.folder).expanduser() if last_inputs.folder else None
        previous_source_file = normalize_ods_filename(last_inputs.source_file) if last_inputs.source_file else ""
        if previous_folder == folder and previous_source_file == source_file:
            save_last_inputs(
                folder,
                source_file,
                verification_sheet_index=last_inputs.verification_sheet_index,
                verification_filter_mode=last_inputs.verification_filter_mode,
                verification_product_model_index=last_inputs.verification_product_model_index,
                verification_manual_product_part_names=last_inputs.verification_manual_product_part_names,
                verification_scope_state=last_inputs.verification_scope_state,
                verification_scope_mode=last_inputs.verification_scope_mode,
                verification_specific_options=last_inputs.verification_specific_options,
                verification_cnc_dxf_filter_state=last_inputs.verification_cnc_dxf_filter_state,
                verification_cnc_dxf_filter_mode=last_inputs.verification_cnc_dxf_filter_mode,
                verification_cnc_dxf_excluded_options=last_inputs.verification_cnc_dxf_excluded_options,
            )
        else:
            save_last_inputs(folder, source_file)

        while True:
            choice = ask_main_menu()
            if choice == "1":
                workbook, workbook_mtime = reload_workbook_if_changed(
                    source_path=source_path,
                    workbook=workbook,
                    workbook_mtime=workbook_mtime,
                )
                while True:
                    sheet_name, sheet_index = ask_supported_sheet(app_config.supported_sheets.keys())
                    if sheet_name is None:
                        break
                    verification_run = verify_sheet_with_grist(
                        console=console,
                        workbook=workbook,
                        app_config=app_config,
                        sheet_name=sheet_name,
                        material_mapping_path=material_mapping_path,
                    )
                    if verification_run is None:
                        continue
                    break
                if sheet_name is None or verification_run is None:
                    continue
                (
                    result,
                    csv_path,
                    json_path,
                    html_path,
                    filter_mode,
                    product_index,
                    manual_product_part_names,
                    scope_state,
                    cnc_dxf_filter_state,
                ) = verification_run
                save_last_inputs(
                    folder,
                    source_file,
                    verification_sheet_index=sheet_index,
                    verification_filter_mode=filter_mode,
                    verification_product_model_index=product_index,
                    verification_manual_product_part_names=manual_product_part_names,
                    verification_scope_state=scope_state,
                    verification_cnc_dxf_filter_state=cnc_dxf_filter_state,
                )
                console.print(f"[green]HTML report:[/green] {html_path}")
                console.print(f"[green]CSV report:[/green] {csv_path}")
                console.print(f"[green]JSON report:[/green] {json_path}")
                open_report_in_browser(html_path)
            elif choice == "2":
                version_workbook = OdsWorkbook.load(source_path)
                output_path, report_path = create_new_version(
                    console=console,
                    workbook=version_workbook,
                    app_config=app_config,
                    source_path=source_path,
                )
                console.print(f"[green]New ODS file written:[/green] {output_path}")
                console.print(f"[green]Remap report:[/green] {report_path}")
            elif choice == "3":
                manage_material_mapping(console, material_mapping_path)
            elif choice == "4":
                workbook, workbook_mtime = load_workbook_from_disk(source_path, reason="Reloaded workbook")
            elif choice == "5":
                folder = ask_folder(str(folder), prompt_reuse=False)
                source_file = ask_source_file(source_file, prompt_reuse=False)
                source_path = folder / source_file
                workbook, workbook_mtime = load_workbook_from_disk(source_path)
                save_last_inputs(folder, source_file)
            elif choice == "6":
                last_inputs = load_last_inputs()
                folder_path = Path(last_inputs.folder).expanduser()
                source_path = folder_path / last_inputs.source_file
                if not folder_path.exists() or not folder_path.is_dir() or not source_path.exists():
                    console.print("[red]Previous folder or file not found. Falling back to interactive mode.[/red]")
                    continue
                if not has_previous_verification_options(last_inputs):
                    console.print("[yellow]No previous verification options are saved yet. Run option 1 once first.[/yellow]")
                    continue
                sheet_names = list(app_config.supported_sheets.keys())
                if last_inputs.verification_sheet_index > len(sheet_names):
                    console.print("[yellow]The saved sheet option is no longer valid. Run option 1 once first.[/yellow]")
                    continue
                folder = folder_path
                source_file = last_inputs.source_file
                workbook, workbook_mtime = load_workbook_from_disk(source_path)
                sheet_name, _ = ask_supported_sheet(sheet_names, replay_index=last_inputs.verification_sheet_index)
                verification_run = verify_sheet_with_grist(
                    console=console,
                    workbook=workbook,
                    app_config=app_config,
                    sheet_name=sheet_name,
                    material_mapping_path=material_mapping_path,
                    replay_filter_mode=last_inputs.verification_filter_mode,
                    replay_product_index=last_inputs.verification_product_model_index,
                    replay_manual_product_part_names=last_inputs.verification_manual_product_part_names,
                    replay_scope_state=last_inputs.verification_scope_state,
                    replay_scope_mode=last_inputs.verification_scope_mode,
                    replay_options=last_inputs.verification_specific_options,
                    replay_cnc_dxf_filter_state=last_inputs.verification_cnc_dxf_filter_state,
                    replay_cnc_dxf_filter_mode=last_inputs.verification_cnc_dxf_filter_mode,
                    replay_cnc_dxf_excluded_options=last_inputs.verification_cnc_dxf_excluded_options,
                )
                if verification_run is None:
                    continue
                (
                    result,
                    csv_path,
                    json_path,
                    html_path,
                    _,
                    _,
                    _,
                    _,
                    _,
                ) = verification_run
                console.print(f"[green]HTML report:[/green] {html_path}")
                console.print(f"[green]CSV report:[/green] {csv_path}")
                console.print(f"[green]JSON report:[/green] {json_path}")
                open_report_in_browser(html_path)
            elif choice == "7":
                manage_product_part_names(console, workbook, app_config, material_mapping_path)
            else:
                console.print("Goodbye.")
                raise typer.Exit()
    except CostingAppError as exc:
        logger.exception("Application error")
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc


def load_workbook_from_disk(source_path: Path, *, reason: str = "Loaded workbook") -> tuple[OdsWorkbook, int]:
    """Load the selected ODS workbook and return its last saved timestamp."""
    workbook = OdsWorkbook.load(source_path)
    workbook_mtime = source_path.stat().st_mtime_ns
    console.print(f"[green]{reason}:[/green] {source_path}")
    return workbook, workbook_mtime


def reload_workbook_if_changed(
    *,
    source_path: Path,
    workbook: OdsWorkbook,
    workbook_mtime: int,
) -> tuple[OdsWorkbook, int]:
    """Reload the workbook when the saved ODS file has changed on disk."""
    current_mtime = source_path.stat().st_mtime_ns
    if current_mtime == workbook_mtime:
        return workbook, workbook_mtime
    console.print("[yellow]Detected saved changes in ODS. Reloading workbook before verification.[/yellow]")
    return load_workbook_from_disk(source_path, reason="Reloaded workbook")


def open_report_in_browser(report_path: Path) -> None:
    """Open a generated HTML report in the default browser."""
    try:
        webbrowser.open(report_path.resolve().as_uri(), new=1, autoraise=True)
    except Exception as exc:  # pragma: no cover - defensive only
        logger.warning("Could not open report in browser: %s", exc)
        console.print(f"[yellow]Could not open report automatically:[/yellow] {report_path}")


def ask_source_selection(previous_folder: str = "", previous_source_file: str = "") -> tuple[Path, str]:
    """Prompt once to reuse the previous folder+file pair, or ask for both."""
    previous = Path(previous_folder).expanduser() if previous_folder else None
    previous_source = normalize_ods_filename(previous_source_file) if previous_source_file else ""
    if previous and previous.exists() and previous.is_dir() and previous_source:
        previous_path = previous / previous_source
        if previous_path.exists() and previous_path.is_file():
            if Confirm.ask(
                f"Reuse previous folder and source file '{previous}' + '{previous_source}'?",
                default=True,
            ):
                return previous, previous_source

    folder = ask_folder(previous_folder, prompt_reuse=False)
    source_file = ask_source_file(previous_source_file, prompt_reuse=False)
    return folder, source_file


def ask_folder(previous_folder: str = "", *, prompt_reuse: bool = True) -> Path:
    """Prompt for an existing folder path."""
    previous = Path(previous_folder).expanduser() if previous_folder else None
    if prompt_reuse and previous and previous.exists() and previous.is_dir():
        if Confirm.ask(f"Reuse previous folder '{previous}'?", default=True):
            return previous

    while True:
        raw = Prompt.ask("Folder path", default=str(Path.cwd())).strip().strip('"')
        path = Path(raw).expanduser()
        if path.exists() and path.is_dir():
            return path
        console.print(f"[red]Folder not found:[/red] {path}")


def ask_source_file(previous_source_file: str = "", *, prompt_reuse: bool = True) -> str:
    """Prompt for a source filename, defaulting the extension to .ods."""
    if prompt_reuse and previous_source_file:
        previous = normalize_ods_filename(previous_source_file)
        if Confirm.ask(f"Reuse previous source file '{previous}'?", default=True):
            return previous

    while True:
        raw = Prompt.ask("Source .ods file name", default=previous_source_file, show_default=bool(previous_source_file))
        source_file = normalize_ods_filename(raw)
        if source_file != ".ods":
            return source_file
        console.print("[red]Please enter a source file name.[/red]")


def ask_main_menu() -> str:
    """Render and prompt the main menu."""
    table = Table(title="Main Menu")
    table.add_column("Option", justify="right", style="cyan")
    table.add_column("Action")
    table.add_row("1", "Verify with Grist")
    table.add_row("2", "Create New Version")
    table.add_row("3", "Manage Material Mapping")
    table.add_row("4", "Reload ODS File")
    table.add_row("5", "Change Folder/Source File")
    table.add_row("6", "Run All with Previous Options")
    table.add_row("7", "Manage Product Part Names")
    table.add_row("8", "Exit")
    console.print(table)
    return Prompt.ask("Choose an option", choices=["1", "2", "3", "4", "5", "6", "7", "8"])


def has_previous_verification_options(last_inputs: LastInputs) -> bool:
    """Return True when a complete previous verification selection is available."""
    return (
        last_inputs.verification_sheet_index > 0
        and (
            (
                last_inputs.verification_filter_mode in {1, 3}
                and bool(last_inputs.verification_manual_product_part_names)
            )
            or (
                last_inputs.verification_filter_mode == 2
                and last_inputs.verification_product_model_index > 0
            )
        )
        and (
            bool(last_inputs.verification_scope_state)
            or (
                last_inputs.verification_scope_mode > 0
                and (
                    last_inputs.verification_scope_mode != 2
                    or bool(last_inputs.verification_specific_options)
                )
            )
        )
    )


def ask_supported_sheet(sheet_names: object, replay_index: int = 0) -> tuple[str | None, int]:
    """Prompt for sheet selection, or return replay sheet if index provided."""
    names = list(sheet_names)
    if replay_index > 0 and replay_index <= len(names):
        return names[replay_index - 1], replay_index

    console.print(
        Panel(
            "Select the sheet from the ODS file that should be used for verification against Grist.",
            title="Verify With Grist",
            border_style="cyan",
            padding=(1, 2),
        )
    )
    table = Table(title="Supported Sheets")
    table.add_column("Option", justify="right", style="cyan")
    table.add_column("ODS Sheet To Verify", style="bold")
    for i, name in enumerate(names, 1):
        table.add_row(str(i), SHEET_MENU_LABELS.get(name, name))
    back_option = len(names) + 1
    table.add_row(str(back_option), "Back")
    console.print(table)
    choice = Prompt.ask(
        "Choose a sheet",
        choices=[str(i) for i in range(1, back_option + 1)],
        default="1",
    )
    if int(choice) == back_option:
        return None, 0
    index = int(choice)
    return names[index - 1], index

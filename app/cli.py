"""Typer CLI and interactive command flow."""

from __future__ import annotations

from pathlib import Path
import logging
import webbrowser

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

from app import __app_name__, __version__
from app.config import DEFAULT_CONFIG_PATH, DEFAULT_MATERIAL_MAPPING_PATH, load_app_config
from app.exceptions import CostingAppError
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

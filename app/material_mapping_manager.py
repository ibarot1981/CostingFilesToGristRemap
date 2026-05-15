"""Interactive utilities for maintaining ODS-to-Grist material mappings."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import shutil
from typing import Any

import yaml
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

from app.exceptions import ConfigError, GristError
from app.grist import GristClient
from app.utils import display_text, is_blank, normalize_text


MAX_ROWS_TO_SHOW = 80


@dataclass(frozen=True)
class MaterialMappingDocument:
    """Loaded material mapping YAML plus non-material metadata."""

    metadata: dict[str, Any]
    materials: dict[str, str]


@dataclass(frozen=True)
class MasterMaterialIndex:
    """Searchable Grist MasterMaterial values."""

    names: set[str]
    alternates: dict[str, str]

    def contains_name(self, value: str) -> bool:
        """Return True when value is an exact MasterMaterial name."""
        return normalize_text(value) in self.names

    def alternate_suggestion(self, value: str) -> str:
        """Return the MasterMaterial name for an AlternateSize match, if any."""
        return self.alternates.get(normalize_text(value), "")


def manage_material_mapping(console: Console, material_mapping_path: Path) -> None:
    """Run the interactive material mapping maintenance menu."""
    console.print(
        Panel(
            "\n".join(
                [
                    "Maintain config/material_mapping.yaml without opening the file by hand.",
                    "Tips:",
                    "- Search before adding to avoid duplicate ODS material names.",
                    "- The Grist value should normally match MasterMaterial.MasterMaterial exactly.",
                    "- A backup is created next to the YAML file before every save.",
                    "- Blank input keeps the old value when editing.",
                ]
            ),
            title="Material Mapping Manager",
            border_style="cyan",
        )
    )

    while True:
        choice = ask_mapping_menu(console)
        if choice == "1":
            list_mappings(console, material_mapping_path)
        elif choice == "2":
            add_mapping(console, material_mapping_path)
        elif choice == "3":
            edit_mapping(console, material_mapping_path)
        elif choice == "4":
            delete_mapping(console, material_mapping_path)
        elif choice == "5":
            validate_mappings(console, material_mapping_path)
        else:
            return


def ask_mapping_menu(console: Console) -> str:
    """Render the material mapping menu and return the selected option."""
    table = Table(title="Material Mapping Menu")
    table.add_column("Option", justify="right", style="cyan")
    table.add_column("Action")
    table.add_row("1", "List/Search mappings")
    table.add_row("2", "Add new mapping")
    table.add_row("3", "Edit existing mapping")
    table.add_row("4", "Delete mapping")
    table.add_row("5", "Validate mappings against Grist MasterMaterial")
    table.add_row("6", "Back")
    console.print(table)
    return Prompt.ask("Choose an option", choices=["1", "2", "3", "4", "5", "6"], default="1")


def list_mappings(console: Console, material_mapping_path: Path) -> None:
    """Show mappings matching an optional search term."""
    document = load_mapping_document(material_mapping_path)
    query = Prompt.ask(
        "Search ODS or Grist material text; blank shows the first mappings",
        default="",
        show_default=False,
    ).strip()
    matches = search_mappings(document.materials, query)
    show_mapping_table(console, matches, title="Material Mappings", query=query)


def add_mapping(console: Console, material_mapping_path: Path) -> None:
    """Prompt for and save a new material mapping."""
    document = load_mapping_document(material_mapping_path)
    ods_material = Prompt.ask("ODS material name").strip()
    if is_blank(ods_material):
        console.print("[yellow]Nothing added. ODS material name is required.[/yellow]")
        return

    existing_value = document.materials.get(ods_material)
    if existing_value is not None:
        console.print(f"[yellow]This ODS material already maps to:[/yellow] {existing_value}")
        if not Confirm.ask("Overwrite this mapping?", default=False):
            console.print("[yellow]Nothing changed.[/yellow]")
            return

    grist_material = Prompt.ask("Grist MasterMaterial name").strip()
    if is_blank(grist_material):
        console.print("[yellow]Nothing added. Grist material name is required.[/yellow]")
        return

    maybe_validate_single_material(console, grist_material)
    document.materials[ods_material] = grist_material
    backup_path = save_mapping_document(material_mapping_path, document)
    console.print(f"[green]Saved mapping:[/green] {ods_material} -> {grist_material}")
    console.print(f"[green]Backup written:[/green] {backup_path}")


def edit_mapping(console: Console, material_mapping_path: Path) -> None:
    """Prompt for an existing mapping and update its key or value."""
    document = load_mapping_document(material_mapping_path)
    selected = choose_mapping(console, document.materials, "Search mapping to edit")
    if selected is None:
        return

    old_ods, old_grist = selected
    console.print(f"[cyan]Current mapping:[/cyan] {old_ods} -> {old_grist}")
    new_ods = Prompt.ask("New ODS material name", default=old_ods).strip()
    new_grist = Prompt.ask("New Grist MasterMaterial name", default=old_grist).strip()
    if is_blank(new_ods) or is_blank(new_grist):
        console.print("[yellow]Nothing changed. Both ODS and Grist values are required.[/yellow]")
        return
    if new_ods == old_ods and new_grist == old_grist:
        console.print("[yellow]Nothing changed.[/yellow]")
        return

    duplicate_value = document.materials.get(new_ods)
    if new_ods != old_ods and duplicate_value is not None:
        console.print(f"[yellow]Target ODS name already maps to:[/yellow] {duplicate_value}")
        if not Confirm.ask("Overwrite that existing mapping?", default=False):
            console.print("[yellow]Nothing changed.[/yellow]")
            return

    maybe_validate_single_material(console, new_grist)
    document.materials.pop(old_ods, None)
    document.materials[new_ods] = new_grist
    backup_path = save_mapping_document(material_mapping_path, document)
    console.print(f"[green]Updated mapping:[/green] {new_ods} -> {new_grist}")
    console.print(f"[green]Backup written:[/green] {backup_path}")


def delete_mapping(console: Console, material_mapping_path: Path) -> None:
    """Prompt for an existing mapping and delete it after confirmation."""
    document = load_mapping_document(material_mapping_path)
    selected = choose_mapping(console, document.materials, "Search mapping to delete")
    if selected is None:
        return

    ods_material, grist_material = selected
    console.print(f"[cyan]Selected mapping:[/cyan] {ods_material} -> {grist_material}")
    if not Confirm.ask("Delete this mapping?", default=False):
        console.print("[yellow]Nothing changed.[/yellow]")
        return

    document.materials.pop(ods_material, None)
    backup_path = save_mapping_document(material_mapping_path, document)
    console.print(f"[green]Deleted mapping for:[/green] {ods_material}")
    console.print(f"[green]Backup written:[/green] {backup_path}")


def validate_mappings(console: Console, material_mapping_path: Path) -> None:
    """Validate all mapped Grist material names against MasterMaterial."""
    document = load_mapping_document(material_mapping_path)
    index = fetch_master_material_index(console)

    missing: list[tuple[str, str, str]] = []
    target_to_sources: dict[str, list[str]] = {}
    for ods_material, grist_material in sorted_mappings(document.materials):
        normalized_target = normalize_text(grist_material)
        target_to_sources.setdefault(normalized_target, []).append(ods_material)
        if not index.contains_name(grist_material):
            missing.append((ods_material, grist_material, index.alternate_suggestion(grist_material)))

    duplicate_targets = [
        (document.materials[sources[0]], sources)
        for sources in target_to_sources.values()
        if len(sources) > 1
    ]

    summary = Table(title="Material Mapping Validation")
    summary.add_column("Metric", style="cyan")
    summary.add_column("Count", justify="right", style="bold")
    summary.add_row("Total mappings", str(len(document.materials)))
    summary.add_row("Valid Grist material names", str(len(document.materials) - len(missing)))
    summary.add_row("Grist names not found", str(len(missing)))
    summary.add_row("Grist names used by multiple ODS materials", str(len(duplicate_targets)))
    console.print(summary)

    if missing:
        table = Table(title="Grist Material Names Not Found")
        table.add_column("No.", justify="right", style="cyan")
        table.add_column("ODS Material")
        table.add_column("Mapped Grist Material")
        table.add_column("AlternateSize Suggestion")
        for index_number, (ods_material, grist_material, suggestion) in enumerate(missing[:MAX_ROWS_TO_SHOW], 1):
            table.add_row(str(index_number), ods_material, grist_material, suggestion)
        console.print(table)
        if len(missing) > MAX_ROWS_TO_SHOW:
            console.print(f"[yellow]Showing first {MAX_ROWS_TO_SHOW} of {len(missing)} missing values.[/yellow]")

    if duplicate_targets:
        table = Table(title="Repeated Grist Material Targets")
        table.add_column("Grist Material")
        table.add_column("ODS Material Names")
        for grist_material, ods_sources in duplicate_targets[:MAX_ROWS_TO_SHOW]:
            table.add_row(grist_material, "; ".join(sorted(ods_sources, key=str.casefold)))
        console.print(table)
        if len(duplicate_targets) > MAX_ROWS_TO_SHOW:
            console.print(f"[yellow]Showing first {MAX_ROWS_TO_SHOW} of {len(duplicate_targets)} repeated targets.[/yellow]")


def choose_mapping(
    console: Console,
    materials: dict[str, str],
    prompt_text: str,
) -> tuple[str, str] | None:
    """Search and select a single mapping."""
    query = Prompt.ask(prompt_text, default="", show_default=False).strip()
    matches = search_mappings(materials, query)
    if not matches:
        console.print("[yellow]No mappings matched your search.[/yellow]")
        return None
    if len(matches) > MAX_ROWS_TO_SHOW:
        console.print(
            f"[yellow]{len(matches)} mappings matched. Showing the first {MAX_ROWS_TO_SHOW}; "
            "use a narrower search if needed.[/yellow]"
        )
        matches = matches[:MAX_ROWS_TO_SHOW]

    show_mapping_table(console, matches, title="Matching Material Mappings", query=query, include_numbers=True)
    back_option = len(matches) + 1
    console.print(f"[cyan]{back_option}[/cyan] Back")
    choice = Prompt.ask("Choose mapping number", choices=[str(i) for i in range(1, back_option + 1)])
    if int(choice) == back_option:
        return None
    return matches[int(choice) - 1]


def show_mapping_table(
    console: Console,
    mappings: list[tuple[str, str]],
    *,
    title: str,
    query: str,
    include_numbers: bool = False,
) -> None:
    """Render material mappings in a Rich table."""
    display_mappings = mappings[:MAX_ROWS_TO_SHOW]
    caption = f"{len(mappings)} match(es)"
    if query:
        caption += f" for '{query}'"

    table = Table(title=title, caption=caption)
    if include_numbers:
        table.add_column("No.", justify="right", style="cyan")
    table.add_column("ODS Material")
    table.add_column("Grist Material")
    for index, (ods_material, grist_material) in enumerate(display_mappings, 1):
        row = [ods_material, grist_material]
        if include_numbers:
            row.insert(0, str(index))
        table.add_row(*row)
    console.print(table)
    if len(mappings) > MAX_ROWS_TO_SHOW:
        console.print(f"[yellow]Showing first {MAX_ROWS_TO_SHOW} of {len(mappings)} mappings.[/yellow]")


def search_mappings(materials: dict[str, str], query: str) -> list[tuple[str, str]]:
    """Return sorted mappings matching query in either ODS or Grist text."""
    normalized_query = normalize_text(query)
    mappings = sorted_mappings(materials)
    if not normalized_query:
        return mappings
    return [
        (ods_material, grist_material)
        for ods_material, grist_material in mappings
        if normalized_query in normalize_text(ods_material)
        or normalized_query in normalize_text(grist_material)
    ]


def sorted_mappings(materials: dict[str, str]) -> list[tuple[str, str]]:
    """Return mappings sorted by ODS material name."""
    return sorted(materials.items(), key=lambda item: item[0].casefold())


def maybe_validate_single_material(console: Console, grist_material: str) -> None:
    """Optionally validate one Grist material value before saving."""
    if not Confirm.ask("Validate this Grist material name against MasterMaterial now?", default=True):
        return

    try:
        index = fetch_master_material_index(console)
    except (ConfigError, GristError) as exc:
        console.print(f"[yellow]Could not validate with Grist:[/yellow] {exc}")
        console.print("[yellow]You can still save now and run full validation later.[/yellow]")
        return

    if index.contains_name(grist_material):
        console.print("[green]Grist material found in MasterMaterial.[/green]")
        return

    suggestion = index.alternate_suggestion(grist_material)
    if suggestion:
        console.print(
            "[yellow]This value was found in AlternateSize, not MasterMaterial.[/yellow] "
            f"Suggested MasterMaterial: {suggestion}"
        )
    else:
        console.print("[yellow]This Grist material name was not found in MasterMaterial.[/yellow]")


def fetch_master_material_index(console: Console) -> MasterMaterialIndex:
    """Fetch MasterMaterial names and AlternateSize suggestions from Grist."""
    console.print("[cyan]Fetching Grist MasterMaterial values...[/cyan]")
    client = GristClient.from_environment()
    records = client.fetch_table_records("MasterMaterial")
    names: set[str] = set()
    alternates: dict[str, str] = {}

    for fields in records:
        master_name = display_text(fields.get("MasterMaterial"))
        if master_name:
            names.add(normalize_text(master_name))

        alternate_value = display_text(fields.get("AlternateSize"))
        if alternate_value and master_name:
            alternates.setdefault(normalize_text(alternate_value), master_name)

    if not names:
        raise GristError("No MasterMaterial.MasterMaterial values were found in Grist.")
    return MasterMaterialIndex(names=names, alternates=alternates)


def load_mapping_document(path: Path) -> MaterialMappingDocument:
    """Load material_mapping.yaml while preserving top-level metadata."""
    if not path.exists():
        raise ConfigError(f"Material mapping file not found: {path}")
    try:
        data: Any = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise ConfigError("Material mapping YAML must be a mapping.")
    raw_materials = data.get("materials", {})
    if not isinstance(raw_materials, dict):
        raise ConfigError("material_mapping.yaml must contain a 'materials' mapping.")

    metadata = {str(key): value for key, value in data.items() if key != "materials"}
    materials = {
        str(key).strip(): str(value).strip()
        for key, value in raw_materials.items()
        if not is_blank(key) and not is_blank(value)
    }
    return MaterialMappingDocument(metadata=metadata, materials=materials)


def save_mapping_document(path: Path, document: MaterialMappingDocument) -> Path:
    """Back up and atomically save a material mapping document."""
    path.parent.mkdir(parents=True, exist_ok=True)
    backup_path = create_mapping_backup(path)
    payload = {
        **document.metadata,
        "materials": dict(sorted_mappings(document.materials)),
    }
    temp_path = path.with_name(f"{path.name}.tmp")
    try:
        temp_path.write_text(
            yaml.safe_dump(payload, sort_keys=False, allow_unicode=False, default_flow_style=False),
            encoding="utf-8",
        )
        temp_path.replace(path)
    except OSError as exc:
        if temp_path.exists():
            temp_path.unlink()
        raise ConfigError(f"Could not save material mapping file {path}: {exc}") from exc
    return backup_path


def create_mapping_backup(path: Path) -> Path:
    """Create a timestamped backup beside the mapping file."""
    if not path.exists():
        return path
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = path.with_name(f"{path.stem}.backup_{timestamp}{path.suffix}")
    suffix = 1
    while backup_path.exists():
        backup_path = path.with_name(f"{path.stem}.backup_{timestamp}_{suffix}{path.suffix}")
        suffix += 1
    try:
        shutil.copy2(path, backup_path)
    except OSError as exc:
        raise ConfigError(f"Could not create material mapping backup {backup_path}: {exc}") from exc
    return backup_path

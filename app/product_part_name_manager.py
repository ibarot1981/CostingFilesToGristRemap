"""Interactive utilities for maintaining product part names in Grist."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rich.console import Console
from rich.prompt import Confirm, Prompt
from rich.table import Table

from app.exceptions import GristError
from app.grist import GristClient
from app.utils import display_text, is_blank, normalize_text


PRODUCT_PART_MASTER_TABLE = "ProductPartMaster"
PRODUCT_PART_MS_LIST_TABLE = "ProductPartMSList"
PRODUCT_MODEL_CONFIG_TABLE = "ProductModelConfig"
BLANKS_LABEL = "Blanks"
SHOW_ALL_LABEL = "__SHOW_ALL__"


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


def manage_product_part_names(console: Console) -> None:
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
        else:
            return


def ask_product_part_name_menu(console: Console) -> str:
    """Render the Product Part Name manager menu."""
    table = Table(title="Manage Product Part Names")
    table.add_column("Option", justify="right", style="cyan")
    table.add_column("Action")
    table.add_row("1", "Add new Product Part Name")
    table.add_row("2", "Assign part to config")
    table.add_row("3", "Assign ProductPartName")
    table.add_row("4", "Back")
    console.print(table)
    return Prompt.ask("Choose an option", choices=["1", "2", "3", "4"], default="1")


def add_product_part_name(console: Console, client: GristClient) -> None:
    """Create a new ProductPartMaster entry after duplicate checks."""
    master_rows = fetch_product_part_master_rows(client)
    product_part_name = Prompt.ask("Enter ProductPartName").strip()
    if is_blank(product_part_name):
        console.print("[yellow]Nothing added. ProductPartName is required.[/yellow]")
        return

    duplicate = find_product_part_master_row(master_rows, product_part_name)
    if duplicate is not None:
        console.print(
            f"[yellow]ProductPartName already exists:[/yellow] "
            f"{duplicate.product_part_name} (row id {duplicate.row_id})"
        )
        return

    part_description = Prompt.ask("Enter PartDescription", default="", show_default=False).strip()
    if not Confirm.ask(
        f"Create ProductPartMaster entry '{product_part_name}'?",
        default=True,
    ):
        console.print("[yellow]Nothing changed.[/yellow]")
        return

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
    created_row = ProductPartMasterRow(
        row_id=int(created[0]["id"]),
        product_part_name=display_text(fields.get("ProductPartName")) or product_part_name,
        part_description=display_text(fields.get("PartDescription")) or part_description,
    )
    console.print(
        f"[green]Created ProductPartMaster row:[/green] "
        f"{created_row.row_id} - {created_row.product_part_name}"
    )

    if Confirm.ask("Assign this new part to ProductModelConfig now?", default=True):
        assign_part_to_config(console, client, preferred_product_part=created_row)


def assign_part_to_config(
    console: Console,
    client: GristClient,
    preferred_product_part: ProductPartMasterRow | None = None,
) -> None:
    """Create a ProductModelConfig row linking a model code to a product part."""
    model_choice = choose_product_model_code(console, client)
    product_part_row = choose_product_part_master_row(console, client, preferred_row=preferred_product_part)
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
    """Assign a ProductPartMaster reference to matching ProductPartMSList rows."""
    model_choice = choose_product_model_code(console, client)
    matching_rows = fetch_product_part_ms_rows_for_model(client, model_choice.product_part_names)
    if not matching_rows:
        console.print("[yellow]No ProductPartMSList rows matched that ProductModelCode.[/yellow]")
        return

    machine_piece_desc = choose_machine_piece_desc(console, matching_rows, model_choice.product_model_code)
    selected_rows = filter_rows_by_machine_piece_desc(matching_rows, machine_piece_desc)
    if not selected_rows:
        console.print("[yellow]No ProductPartMSList rows matched that MachinePieceDesc selection.[/yellow]")
        return

    console.print(
        f"[cyan]Selected ProductModelCode:[/cyan] {model_choice.product_model_code} "
        f"({len(model_choice.product_part_names)} configured part names)"
    )
    console.print(
        f"[cyan]Selected MachinePieceDesc:[/cyan] "
        f"{BLANKS_LABEL if machine_piece_desc is None else machine_piece_desc}"
    )
    show_product_part_ms_rows(console, selected_rows, title="Rows To Update")
    if not Confirm.ask(f"Continue with these {len(selected_rows)} row(s)?", default=True):
        console.print("[yellow]Nothing changed.[/yellow]")
        return

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


def choose_product_model_code(console: Console, client: GristClient) -> ProductModelChoice:
    """Prompt for a ProductModelConfig code and its associated part names."""
    records = client.fetch_table_records_with_ids(PRODUCT_MODEL_CONFIG_TABLE)
    by_code: dict[str, ProductModelChoice] = {}
    for record in records:
        fields = record.get("fields", {})
        code = display_text(fields.get("ProductModelCode_ProductModelCode2"))
        product_part_name = display_text(fields.get("ProductPartName_ProductPartName"))
        product_model_id = linked_record_id(fields.get("ProductModelCode"))
        product_part_id = linked_record_id(fields.get("ProductPartName"))
        if not code or not product_part_name or product_model_id is None or product_part_id is None:
            continue

        existing = by_code.get(code)
        if existing is None:
            by_code[code] = ProductModelChoice(
                product_model_id=product_model_id,
                product_model_code=code,
                description=display_text(fields.get("ProductModelCode_ProductModelDesc")),
                product_part_names={product_part_name},
                product_part_ids={product_part_id},
            )
        else:
            existing.product_part_names.add(product_part_name)
            existing.product_part_ids.add(product_part_id)

    if not by_code:
        raise GristError("No ProductModelConfig rows with ProductModelCode and ProductPartName were found.")

    choices = sorted(by_code.values(), key=lambda item: item.product_model_code.casefold())
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
    console.print(table)
    selected = Prompt.ask(
        "Choose ProductModelCode",
        choices=[str(index) for index in range(1, len(choices) + 1)],
    )
    return choices[int(selected) - 1]


def fetch_product_part_ms_rows_for_model(
    client: GristClient,
    product_part_names: set[str],
) -> list[ProductPartMSListRow]:
    """Fetch ProductPartMSList rows belonging to the selected model code scope."""
    wanted = {normalize_text(name) for name in product_part_names}
    records = client.fetch_table_records_with_ids(PRODUCT_PART_MS_LIST_TABLE)
    rows: list[ProductPartMSListRow] = []
    for record in records:
        fields = record.get("fields", {})
        current_product_part_name = display_text(fields.get("ProductPartName_ProductPartName"))
        if normalize_text(current_product_part_name) not in wanted:
            continue
        row_id = record.get("id")
        if row_id is None:
            continue
        rows.append(
            ProductPartMSListRow(
                row_id=int(row_id),
                record_id=display_text(fields.get("RecordID")),
                machine_piece_desc=display_text(fields.get("MachinePieceDesc")),
                material_to_cut=display_text(fields.get("MaterialToCut")),
                length_mm=display_text(fields.get("Length_mm")),
                qty_nos=display_text(fields.get("QtyNos")),
                option_group_1_temp=display_text(fields.get("OptionGroup1_TEMP")),
                remarks=display_text(fields.get("Remarks")),
                current_product_part_name=current_product_part_name,
                current_product_part_id=linked_record_id(fields.get("ProductPartName")),
            )
        )
    return sorted(
        rows,
        key=lambda row: (
            normalize_text(row.machine_piece_desc or BLANKS_LABEL),
            normalize_text(row.record_id),
            normalize_text(row.material_to_cut),
            normalize_text(row.length_mm),
        ),
    )


def choose_machine_piece_desc(
    console: Console,
    rows: list[ProductPartMSListRow],
    product_model_code: str,
) -> str | None:
    """Prompt for a distinct MachinePieceDesc, including Blanks when present."""
    grouped_counts: dict[str | None, int] = {}
    for row in rows:
        key = None if is_blank(row.machine_piece_desc) else row.machine_piece_desc
        grouped_counts[key] = grouped_counts.get(key, 0) + 1

    if not grouped_counts:
        raise GristError("No MachinePieceDesc values were found for the selected ProductModelCode.")

    values = sorted(
        grouped_counts.keys(),
        key=lambda item: (item is not None, normalize_text(item or "")),
    )
    table = Table(title=f"MachinePieceDesc for {product_model_code}")
    table.add_column("Option", justify="right", style="cyan")
    table.add_column("MachinePieceDesc")
    table.add_column("Rows", justify="right")
    for index, value in enumerate(values, start=1):
        table.add_row(
            str(index),
            BLANKS_LABEL if value is None else value,
            str(grouped_counts[value]),
        )
    console.print(table)
    selected = Prompt.ask(
        "Choose MachinePieceDesc",
        choices=[str(index) for index in range(1, len(values) + 1)],
    )
    return values[int(selected) - 1]


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
            return choose_product_part_master_row_from_matches(console, matches)
        if action == "3" and preferred_row is not None:
            return choose_product_part_master_row_from_matches(console, rows)
        if action == "2" and preferred_row is None:
            return choose_product_part_master_row_from_matches(console, rows)
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
            return choose_product_part_master_row_from_matches(console, matches)
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
) -> ProductPartMasterRow:
    """Prompt for a ProductPartMaster row from a prepared list."""
    table = Table(title="ProductPartMaster")
    table.add_column("Option", justify="right", style="cyan")
    table.add_column("ProductPartName")
    table.add_column("PartDescription")
    for index, row in enumerate(rows, start=1):
        table.add_row(str(index), row.product_part_name, row.part_description)
    console.print(table)
    selected = Prompt.ask(
        "Choose ProductPartName",
        choices=[str(index) for index in range(1, len(rows) + 1)],
    )
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

"""Grist verification workflow and comparison logic."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.prompt import Confirm, Prompt
from rich.table import Table

from app.config import load_material_mapping
from app.config_models import AppConfig, SheetConfig
from app.cnc_dxf_review import review_and_update_cnc_dxf_paths
from app.exceptions import ConfigError, GristError
from app.grist import GristClient
from app.reports import ReportWriter, report_dir_for_source, show_count_summary
from app.utils import as_decimal, display_text, is_blank, normalize_text, values_equal
from app.workbook import ColumnMap, OdsWorkbook, RowInfo, SheetView


@dataclass
class CompareRecord:
    """Normalized row or Grist record used for comparison."""

    key: tuple[str, ...]
    display_key: str
    values: dict[str, Any]
    source_row: int | None = None
    grist_row_id: Any = None


@dataclass(frozen=True)
class MaterialResolution:
    """Resolved material values used for matching and reporting."""

    original_name: str
    compared_name: str
    match_type: str


@dataclass(frozen=True)
class MasterMaterialIndex:
    """MasterMaterial lookup data used by verification."""

    id_to_resolution: dict[Any, MaterialResolution]
    name_to_resolution: dict[str, MaterialResolution]
    comparable_materials: dict[str, str]


@dataclass
class VerificationResult:
    """Verification output grouped into reportable categories."""

    active_ods_entries: int = 0
    active_grist_entries: int = 0
    matched: list[dict[str, Any]] = field(default_factory=list)
    only_in_ods: list[dict[str, Any]] = field(default_factory=list)
    only_in_grist: list[dict[str, Any]] = field(default_factory=list)
    mismatched: list[dict[str, Any]] = field(default_factory=list)
    missing_material_mapping: list[dict[str, Any]] = field(default_factory=list)
    ignored_option_scope_rows: list[dict[str, Any]] = field(default_factory=list)
    ignored_inactive_rows: list[dict[str, Any]] = field(default_factory=list)
    duplicate_ods_keys: list[dict[str, Any]] = field(default_factory=list)
    duplicate_grist_keys: list[dict[str, Any]] = field(default_factory=list)
    ods_row_accounting: list[dict[str, Any]] = field(default_factory=list)
    grist_tally_updates: list[dict[str, Any]] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        """Return category counts in the order required by the CLI."""
        return {
            "matched": len(self.matched),
            "only in ODS": len(self.only_in_ods),
            "only in Grist": len(self.only_in_grist),
            "mismatched": len(self.mismatched),
            "missing material mapping": len(self.missing_material_mapping),
            "ignored option scope rows": len(self.ignored_option_scope_rows),
            "ignored/inactive rows": len(self.ignored_inactive_rows),
            "duplicate ODS keys": len(self.duplicate_ods_keys),
            "duplicate Grist keys": len(self.duplicate_grist_keys),
            "ODS row accounting": len(self.ods_row_accounting),
            "Grist tally updates": len(self.grist_tally_updates),
        }

    def match_band_summary(self) -> dict[str, Any]:
        """Return source-side match health used by the HTML report band."""
        total_entries = self.active_ods_entries + self.active_grist_entries
        matched_entries = min(len(self.matched) * 2, total_entries)
        mismatched_entries = max(total_entries - matched_entries, 0)
        mismatch_percent = 0.0 if total_entries == 0 else mismatched_entries / total_entries * 100
        return {
            "active_ods_entries": self.active_ods_entries,
            "active_grist_entries": self.active_grist_entries,
            "total_entries": total_entries,
            "matched_pairs": len(self.matched),
            "matched_entries": matched_entries,
            "mismatched_entries": mismatched_entries,
            "mismatch_percent": round(mismatch_percent, 2),
        }

    def detail_rows(self) -> list[dict[str, Any]]:
        """Flatten categories into CSV-friendly rows."""
        rows: list[dict[str, Any]] = []
        for category in [
            "matched",
            "only_in_ods",
            "only_in_grist",
            "mismatched",
            "missing_material_mapping",
            "ignored_option_scope_rows",
            "ignored_inactive_rows",
            "duplicate_ods_keys",
            "duplicate_grist_keys",
            "ods_row_accounting",
            "grist_tally_updates",
        ]:
            category_rows = list(getattr(self, category))
            if category == "ods_row_accounting":
                category_rows.sort(key=row_number_sort_key)
            for item in category_rows:
                rows.append({"category": category, **item})
        return rows


def row_number_sort_key(row: dict[str, Any]) -> tuple[int, str]:
    """Sort report rows by row_number while keeping missing values last."""
    row_number = row.get("row_number")
    try:
        return int(row_number), ""
    except (TypeError, ValueError):
        return 10**12, display_text(row_number)


@dataclass(frozen=True)
class VerificationFilters:
    """User-selected filters for narrowing Grist verification records."""

    product_part_names: set[str]
    product_filter_label: str


@dataclass(frozen=True)
class OdsOptionScope:
    """User-selected ODS option scope for verification rows."""

    mode: str
    selected_options: tuple[str, ...] = ()
    include_blanks: bool = False
    options_discovered: tuple[str, ...] = ()

    def includes(self, option_value: Any) -> bool:
        """Return True when an ODS option value is inside the selected scope."""
        if self.mode == "all_options":
            return True
        if is_blank(option_value):
            return self.include_blanks
        if self.mode == "only_blanks":
            return False
        selected = {normalize_text(option) for option in self.selected_options}
        return normalize_text(option_value) in selected

    def label(self) -> str:
        """Return a concise display label for prompts and reports."""
        if self.mode == "all_options":
            return "All options"
        if self.mode == "only_blanks":
            return "BLANKS"
        parts = []
        if self.include_blanks:
            parts.append("BLANKS")
        parts.extend(self.selected_options)
        return " + ".join(parts) if parts else "No ODS rows selected"

    def metadata(self) -> dict[str, Any]:
        """Return report metadata for the selected scope."""
        return {
            "ods_option_scope_mode": self.mode,
            "selected_ods_options": list(self.selected_options),
            "blanks_included": self.include_blanks,
            "options_discovered": list(self.options_discovered),
            "ods_option_scope_label": self.label(),
        }


@dataclass(frozen=True)
class CncDxfReviewOptionFilter:
    """User-selected option-group exclusions for CNC DXF review only."""

    mode: str
    excluded_options: tuple[str, ...] = ()
    exclude_blanks: bool = False
    options_discovered: tuple[str, ...] = ()

    def includes(self, option_value: Any) -> bool:
        """Return True when a row should remain in the CNC DXF review set."""
        if self.mode == "all_rows":
            return True
        if self.mode == "only_blanks":
            return is_blank(option_value)
        if is_blank(option_value):
            return not self.exclude_blanks
        excluded = {normalize_text(option) for option in self.excluded_options}
        return normalize_text(option_value) not in excluded

    def label(self) -> str:
        """Return a concise display label for prompts and logs."""
        if self.mode == "all_rows":
            return "All option groups"
        if self.mode == "only_blanks":
            return "Only BLANKS/common rows"
        parts = []
        if self.exclude_blanks:
            parts.append("BLANKS")
        parts.extend(self.excluded_options)
        return f"Exclude: {', '.join(parts)}" if parts else "All option groups"


def verify_sheet_with_grist(
    *,
    console: Console,
    workbook: OdsWorkbook,
    app_config: AppConfig,
    sheet_name: str,
    material_mapping_path: Path,
    replay_filter_mode: int = 0,
    replay_product_index: int = 0,
    replay_manual_product_part_names: list[str] | None = None,
    replay_scope_mode: int = 0,
    replay_options: list[int] | None = None,
    replay_cnc_dxf_filter_mode: int = 0,
    replay_cnc_dxf_excluded_options: list[int] | None = None,
) -> tuple[VerificationResult, Path, Path, Path, int, int, list[str], int, list[int], int, list[int]]:
    """Compare a configured ODS sheet with its configured Grist table."""
    sheet_config = app_config.supported_sheets[sheet_name]
    grist_config = sheet_config.grist
    if not grist_config.enabled or not grist_config.table_id:
        raise ConfigError(f"{sheet_name} does not have Grist verification configured yet.")

    client = GristClient.from_environment()
    filters, filter_mode, product_index, manual_product_part_names = ask_verification_filters(
        console,
        client,
        replay_filter_mode,
        replay_product_index,
        replay_manual_product_part_names,
    )
    ods_option_scope, scope_mode, options_list = ask_ods_option_scope(console, workbook, app_config, replay_scope_mode, replay_options)
    console.print(f"[cyan]Loading material mapping from[/cyan] {material_mapping_path}")
    material_mapping = load_material_mapping(material_mapping_path)
    console.print(f"[cyan]Fetching Grist table[/cyan] {grist_config.table_id}")
    grist_records = client.fetch_table_records_with_ids(grist_config.table_id)
    master_material_records = client.fetch_table_records_with_ids("MasterMaterial")
    master_material_index = build_master_material_index(
        master_material_records,
        comparable_field=grist_config.comparable_material_field,
    )

    sheet = workbook.sheet_view(sheet_name, sheet_config)
    last_value_row_number = sheet.last_non_empty_row_number()
    result = compare_sheet_to_grist(
        sheet,
        sheet_config,
        material_mapping,
        grist_records,
        master_material_index=master_material_index,
        filters=filters,
        ods_option_scope=ods_option_scope,
        last_value_row_number=last_value_row_number,
    )

    console.print(f"[cyan]Last row with any value:[/cyan] {last_value_row_number}")
    verification_counts = result.counts()
    verification_counts.pop("Grist tally updates", None)
    show_count_summary(console, f"Verification Summary: {sheet_name}", verification_counts)
    cnc_dxf_filter_mode = 0
    cnc_dxf_excluded_options: list[int] = []
    if Confirm.ask("Update TallyWithODS in Grist for this verification result?", default=False):
        tally_rows = update_grist_tally(
            client=client,
            table_id=grist_config.table_id,
            tally_field=grist_config.tally_field,
            sheet_config=sheet_config,
            grist_records=grist_records,
            filters=filters,
            result=result,
        )
        result.grist_tally_updates.extend(tally_rows)
        show_tally_update_summary(console, tally_rows)
        if sheet_name == "CNC Cut List":
            filtered_grist_records = [
                raw_record
                for raw_record in grist_records
                if grist_record_passes_filters(split_grist_record(raw_record)[1], filters, sheet_config)
            ]
            dxf_filter, cnc_dxf_filter_mode, cnc_dxf_excluded_options = ask_cnc_dxf_review_option_filter(
                console,
                result.only_in_ods,
                replay_filter_mode=replay_cnc_dxf_filter_mode,
                replay_options=replay_cnc_dxf_excluded_options,
            )
            dxf_review_rows, skipped_dxf_review_rows = filter_cnc_dxf_review_rows(result.only_in_ods, dxf_filter)
            if skipped_dxf_review_rows:
                console.print(
                    "[yellow]CNC DXF review option filter:[/yellow] "
                    f"{dxf_filter.label()} | skipped {len(skipped_dxf_review_rows)} row(s)."
                )
            dxf_json_path, dxf_outcome = review_and_update_cnc_dxf_paths(
                console=console,
                workbook_path=workbook.path,
                client=client,
                grist_records=filtered_grist_records,
                ods_only_rows=dxf_review_rows,
                product_part_choices=sorted(filters.product_part_names),
                report_base_dir=app_config.reports_dir,
            )
            console.print(f"[green]DXF review JSON:[/green] {dxf_json_path}")
            if dxf_outcome is None:
                console.print("[yellow]DXF review was skipped because there were no linked CNC parts in scope.[/yellow]")
            elif dxf_outcome.action == "cancelled":
                console.print("[yellow]DXF review was cancelled. No DXF File Path updates were saved.[/yellow]")
            else:
                console.print(
                    "[green]DXF File Path updates saved:[/green] "
                    f"{dxf_outcome.updated_rows} updated, {dxf_outcome.cleared_rows} cleared, "
                    f"{dxf_outcome.unchanged_rows} unchanged, {dxf_outcome.skipped_rows} skipped, "
                    f"{dxf_outcome.created_rows} ProductPartCNCList rows created."
                )
    else:
        console.print("[yellow]TallyWithODS was left unchanged in Grist.[/yellow]")

    writer = ReportWriter(report_dir_for_source(app_config.reports_dir, workbook.path))
    report_payload = {
        "source_file": str(workbook.path),
        "sheet": sheet_name,
        "grist_table": grist_config.table_id,
        "sheet_stats": {
            "header_row": sheet_config.header_row,
            "last_value_row_number": last_value_row_number,
            "total_rows_loaded": len(sheet.rows),
            "trailing_blank_rows_after_last_value": max(len(sheet.rows) - last_value_row_number, 0),
        },
        "filters": {
            "product_filter": filters.product_filter_label,
            "product_part_names": sorted(filters.product_part_names),
            **ods_option_scope.metadata(),
        },
        "counts": result.counts(),
        "match_band": result.match_band_summary(),
        "details": result.detail_rows(),
    }
    json_path = writer.write_json(
        f"verify_{sheet_name}_summary",
        report_payload,
    )
    csv_path = writer.write_csv(f"verify_{sheet_name}_details", result.detail_rows())
    html_path = writer.write_verification_html(f"verify_{sheet_name}_report", report_payload)
    return (
        result,
        csv_path,
        json_path,
        html_path,
        filter_mode,
        product_index,
        manual_product_part_names,
        scope_mode,
        options_list,
        cnc_dxf_filter_mode,
        cnc_dxf_excluded_options,
    )


def compare_sheet_to_grist(
    sheet: SheetView,
    sheet_config: SheetConfig,
    material_mapping: dict[str, str],
    grist_records: list[dict[str, Any]],
    *,
    master_material_index: MasterMaterialIndex | None = None,
    filters: VerificationFilters | None = None,
    ods_option_scope: OdsOptionScope | None = None,
    last_value_row_number: int | None = None,
) -> VerificationResult:
    """Compare active ODS rows with Grist records."""
    grist_config = sheet_config.grist
    if not grist_config.key_fields:
        raise GristError("Grist verification config must define key_fields.")

    column_map = sheet.column_map()
    result = VerificationResult()
    ods_records: dict[tuple[str, ...], list[CompareRecord]] = {}
    master_material_index = master_material_index or MasterMaterialIndex({}, {}, {})
    mapped_grist_materials = mapped_material_targets(
        material_mapping,
        master_material_index.comparable_materials,
    )
    effective_last_value_row_number = (
        sheet.last_non_empty_row_number() if last_value_row_number is None else last_value_row_number
    )

    for row_info in sheet.data_rows():
        decision = sheet.classify_row(row_info.values, column_map)
        if decision.inactive:
            if is_trailing_blank_row(row_info, effective_last_value_row_number):
                continue
            report_values = ods_report_values(
                sheet=sheet,
                row=row_info.values,
                column_map=column_map,
                sheet_config=sheet_config,
            )
            reason = "; ".join(decision.reasons)
            result.ignored_inactive_rows.append(
                {
                    "sheet": sheet.name,
                    "row_number": row_info.number,
                    "reason": reason,
                    **report_values,
                }
            )
            add_ods_row_accounting(
                result,
                sheet=sheet.name,
                row_number=row_info.number,
                outcome="ignored/inactive",
                key=ods_key_display_from_row(sheet, row_info.values, column_map, sheet_config),
                reason=reason,
                values=report_values,
            )
            continue

        raw_option_value = ods_option_value(row_info.values, sheet, column_map)
        if ods_option_scope is not None and not ods_option_scope.includes(raw_option_value):
            report_values = ods_report_values(
                sheet=sheet,
                row=row_info.values,
                column_map=column_map,
                sheet_config=sheet_config,
            )
            option_text = option_scope_value_text(raw_option_value)
            reason = option_scope_reason(raw_option_value, ods_option_scope)
            result.ignored_option_scope_rows.append(
                {
                    "sheet": sheet.name,
                    "row_number": row_info.number,
                    "option_value": option_text,
                    "selected_option_scope": ods_option_scope.label(),
                    "reason": reason,
                    **report_values,
                }
            )
            add_ods_row_accounting(
                result,
                sheet=sheet.name,
                row_number=row_info.number,
                outcome="ignored/out of selected option scope",
                key=ods_key_display_from_row(sheet, row_info.values, column_map, sheet_config),
                reason=reason,
                values={
                    **report_values,
                    "option_value": option_text,
                    "selected_option_scope": ods_option_scope.label(),
                },
            )
            continue

        result.active_ods_entries += 1
        record = ods_compare_record(
            sheet=sheet,
            row_info=row_info,
            column_map=column_map,
            sheet_config=sheet_config,
            material_mapping=material_mapping,
            comparable_materials=master_material_index.comparable_materials,
            material_name_to_resolution=master_material_index.name_to_resolution,
        )
        if record is None:
            material_field = grist_config.material_field or "material_to_cut"
            material_index = column_map.index_for(material_field)
            report_values = ods_report_values(
                sheet=sheet,
                row=row_info.values,
                column_map=column_map,
                sheet_config=sheet_config,
            )
            result.missing_material_mapping.append(
                {
                    "source": "ODS",
                    "sheet": sheet.name,
                    "row_number": row_info.number,
                    "material_name": display_text(sheet.get_value(row_info.values, material_index)),
                    "ods_material": display_text(sheet.get_value(row_info.values, material_index)),
                    "compared_material": "",
                    "material_match_type": "missing material mapping",
                    "reason": "No material mapping found for ODS material value.",
                    **report_values,
                }
            )
            add_ods_row_accounting(
                result,
                sheet=sheet.name,
                row_number=row_info.number,
                outcome="missing material mapping",
                key=ods_key_display_from_row(sheet, row_info.values, column_map, sheet_config),
                reason="No material mapping found for ODS material value.",
                values=report_values,
            )
            continue
        ods_records.setdefault(record.key, []).append(record)

    grist_by_key: dict[tuple[str, ...], list[CompareRecord]] = {}
    for raw_record in grist_records:
        grist_row_id, fields = split_grist_record(raw_record)
        if not grist_record_passes_filters(fields, filters, sheet_config):
            continue
        result.active_grist_entries += 1
        record = grist_compare_record(
            fields=fields,
            sheet_config=sheet_config,
            material_id_to_resolution=master_material_index.id_to_resolution,
            grist_row_id=grist_row_id,
        )
        if record is not None:
            if grist_config.material_field and not grist_material_has_ods_mapping(
                record.values.get("compared_material"),
                mapped_grist_materials,
            ):
                result.missing_material_mapping.append(
                    {
                        "source": "Grist",
                        "grist_row_id": grist_row_id,
                        "material_name": display_text(record.values.get("grist_material")),
                        "grist_material": display_text(record.values.get("grist_material")),
                        "compared_material": display_text(record.values.get("compared_material")),
                        "material_match_type": display_text(record.values.get("material_match_type")),
                        "reason": "No ODS material mapping currently points to this Grist material value.",
                        **record_report_values(record, sheet_config),
                    }
                )
                continue
            grist_by_key.setdefault(record.key, []).append(record)

    duplicate_ods_contexts = duplicate_contexts_by_key(ods_records)
    duplicate_grist_contexts = duplicate_contexts_by_key(grist_by_key)

    matched_ods_rows: set[int] = set()
    matched_grist_ids: set[Any] = set()
    fallback_ambiguous_ods_rows: set[int] = set()
    fallback_ambiguous_grist_ids: set[Any] = set()

    for key, ods_record_list in ods_records.items():
        grist_record_list = grist_by_key.get(key)
        if grist_record_list is None:
            continue
        if len(ods_record_list) != 1 or len(grist_record_list) != 1:
            continue
        ods_record = ods_record_list[0]
        grist_record = grist_record_list[0]
        add_match_result(
            result,
            sheet_name=sheet.name,
            ods_record=ods_record,
            grist_record=grist_record,
            sheet_config=sheet_config,
            match_strategy="strict key",
        )
        if ods_record.source_row is not None:
            matched_ods_rows.add(ods_record.source_row)
        matched_grist_ids.add(grist_record.grist_row_id)

    for key in sorted(set(ods_records) & set(grist_by_key), key=lambda item: "|".join(item)):
        ods_by_context = records_by_duplicate_context(ods_records[key])
        grist_by_context = records_by_duplicate_context(grist_by_key[key])
        for context in sorted(set(ods_by_context) & set(grist_by_context)):
            if context in duplicate_ods_contexts.get(key, set()):
                continue
            if context in duplicate_grist_contexts.get(key, set()):
                continue
            ods_candidates = [
                record
                for record in ods_by_context[context]
                if record.source_row not in matched_ods_rows
            ]
            grist_candidates = [
                record
                for record in grist_by_context[context]
                if record.grist_row_id not in matched_grist_ids
            ]
            if len(ods_candidates) != 1 or len(grist_candidates) != 1:
                continue
            ods_record = ods_candidates[0]
            grist_record = grist_candidates[0]
            add_match_result(
                result,
                sheet_name=sheet.name,
                ods_record=ods_record,
                grist_record=grist_record,
                sheet_config=sheet_config,
                match_strategy="strict key with product part context",
            )
            if ods_record.source_row is not None:
                matched_ods_rows.add(ods_record.source_row)
            matched_grist_ids.add(grist_record.grist_row_id)

    fallback_ods_records = [
        record
        for records in ods_records.values()
        for record in records
        if record.source_row not in matched_ods_rows
    ]
    fallback_grist_records = [
        record
        for records in grist_by_key.values()
        for record in records
        if record.grist_row_id not in matched_grist_ids
    ]
    if is_toolshop_comparable_fallback_enabled(sheet.name):
        fallback_ods_by_key = build_toolshop_fallback_index(fallback_ods_records)
        fallback_grist_by_key = build_toolshop_fallback_index(fallback_grist_records)
        for fallback_key in sorted(set(fallback_ods_by_key) & set(fallback_grist_by_key), key=lambda item: "|".join(item)):
            ods_candidates = fallback_ods_by_key[fallback_key]
            grist_candidates = fallback_grist_by_key[fallback_key]
            fallback_display_key = toolshop_fallback_display_key(ods_candidates[0])
            if len(ods_candidates) == 1 and len(grist_candidates) == 1:
                ods_record = ods_candidates[0]
                grist_record = grist_candidates[0]
                add_match_result(
                    result,
                    sheet_name=sheet.name,
                    ods_record=ods_record,
                    grist_record=grist_record,
                    sheet_config=sheet_config,
                    match_strategy="toolshop comparable material fallback",
                    fallback_key=fallback_display_key,
                )
                if ods_record.source_row is not None:
                    matched_ods_rows.add(ods_record.source_row)
                matched_grist_ids.add(grist_record.grist_row_id)
                continue

            if len(ods_candidates) > 1:
                fallback_ambiguous_ods_rows.update(
                    record.source_row for record in ods_candidates if record.source_row is not None
                )
                result.duplicate_ods_keys.append(
                    {
                        "key": fallback_display_key,
                        "fallback_key": fallback_display_key,
                        "match_strategy": "toolshop comparable material fallback",
                        "reason": "fallback key matched multiple ODS rows",
                        "count": len(ods_candidates),
                        "rows": ", ".join(str(record.source_row) for record in ods_candidates),
                    }
                )
            if len(grist_candidates) > 1:
                fallback_ambiguous_grist_ids.update(record.grist_row_id for record in grist_candidates)
                result.duplicate_grist_keys.append(
                    {
                        "key": fallback_display_key,
                        "fallback_key": fallback_display_key,
                        "match_strategy": "toolshop comparable material fallback",
                        "reason": "fallback key matched multiple Grist rows",
                        "count": len(grist_candidates),
                        "grist_row_ids": ", ".join(display_text(record.grist_row_id) for record in grist_candidates),
                        "product_parts": ", ".join(
                            sorted({display_text(record.values.get("product_part_name")) for record in grist_candidates})
                        ),
                    }
                )

    unresolved_duplicate_ods_rows: set[int] = set()
    for key in sorted(duplicate_ods_contexts, key=lambda item: "|".join(item)):
        context_records = records_by_duplicate_context(ods_records[key])
        for context in sorted(duplicate_ods_contexts[key]):
            records = [
                record
                for record in context_records[context]
                if record.source_row not in matched_ods_rows
                and record.source_row not in fallback_ambiguous_ods_rows
            ]
            if not records:
                continue
            unresolved_duplicate_ods_rows.update(
                record.source_row for record in records if record.source_row is not None
            )
            result.duplicate_ods_keys.append(
                {
                    "key": records[0].display_key,
                    "match_strategy": "strict key",
                    "count": len(records),
                    "rows": ", ".join(str(record.source_row) for record in records),
                    "product_part_name": duplicate_context_display(records[0]),
                }
            )

    unresolved_duplicate_grist_ids: set[Any] = set()
    for key in sorted(duplicate_grist_contexts, key=lambda item: "|".join(item)):
        context_records = records_by_duplicate_context(grist_by_key[key])
        for context in sorted(duplicate_grist_contexts[key]):
            records = [
                record
                for record in context_records[context]
                if record.grist_row_id not in matched_grist_ids
                and record.grist_row_id not in fallback_ambiguous_grist_ids
            ]
            if not records:
                continue
            unresolved_duplicate_grist_ids.update(record.grist_row_id for record in records)
            result.duplicate_grist_keys.append(
                {
                    "key": records[0].display_key,
                    "match_strategy": "strict key",
                    "count": len(records),
                    "grist_row_ids": ", ".join(display_text(record.grist_row_id) for record in records),
                    "product_parts": ", ".join(
                        sorted({display_text(record.values.get("product_part_name")) for record in records})
                    ),
                }
            )

    for key in sorted(set(duplicate_ods_contexts) | set(duplicate_grist_contexts), key=lambda item: "|".join(item)):
        if key not in ods_records:
            continue
        for record in ods_records[key]:
            if record.source_row in matched_ods_rows:
                continue
            if record.source_row in fallback_ambiguous_ods_rows:
                continue
            context = duplicate_context(record)
            outcomes = []
            if context in duplicate_ods_contexts.get(key, set()):
                outcomes.append("duplicate ODS key")
            if context in duplicate_grist_contexts.get(key, set()):
                outcomes.append("duplicate Grist key")
            if not outcomes:
                continue
            add_ods_row_accounting(
                result,
                sheet=sheet.name,
                row_number=record.source_row,
                outcome="; ".join(outcomes),
                key=record.display_key,
                values=record_report_values(record, sheet_config),
            )

    for record in fallback_ods_records:
        if record.source_row not in fallback_ambiguous_ods_rows:
            continue
        add_ods_row_accounting(
            result,
            sheet=sheet.name,
            row_number=record.source_row,
            outcome="duplicate fallback key",
            key=record.display_key,
            reason="Tool Shop fallback key matched multiple rows.",
            values={
                **record_report_values(record, sheet_config),
                "fallback_key": toolshop_fallback_display_key(record),
                "match_strategy": "toolshop comparable material fallback",
            },
        )

    for key, ods_record_list in ods_records.items():
        for ods_record in ods_record_list:
            if ods_record.source_row in matched_ods_rows:
                continue
            if ods_record.source_row in unresolved_duplicate_ods_rows:
                continue
            if ods_record.source_row in fallback_ambiguous_ods_rows:
                continue
            if duplicate_context(ods_record) in duplicate_grist_contexts.get(key, set()):
                continue
            result.only_in_ods.append(
                {
                    "sheet": sheet.name,
                    "row_number": ods_record.source_row,
                    "key": ods_record.display_key,
                    "qty": display_text(ods_record.values.get("qty")),
                    **record_report_values(ods_record, sheet_config),
                }
            )
            add_ods_row_accounting(
                result,
                sheet=sheet.name,
                row_number=ods_record.source_row,
                outcome="only in ODS",
                key=ods_record.display_key,
                values=record_report_values(ods_record, sheet_config),
            )

    for key, grist_records_for_key in grist_by_key.items():
        for grist_record in grist_records_for_key:
            if grist_record.grist_row_id in matched_grist_ids:
                continue
            if grist_record.grist_row_id in unresolved_duplicate_grist_ids:
                continue
            if grist_record.grist_row_id in fallback_ambiguous_grist_ids:
                continue
            if duplicate_context(grist_record) in duplicate_ods_contexts.get(key, set()):
                continue
            result.only_in_grist.append(
                {
                    "grist_row_id": grist_record.grist_row_id,
                    "key": grist_record.display_key,
                    **record_report_values(grist_record, sheet_config),
                }
            )

    return result


def update_grist_tally(
    *,
    client: GristClient,
    table_id: str,
    tally_field: str,
    sheet_config: SheetConfig,
    grist_records: list[dict[str, Any]],
    filters: VerificationFilters,
    result: VerificationResult,
) -> list[dict[str, Any]]:
    """Set TallyWithODS in Grist for the current filtered verification scope."""
    product_part_field = grist_product_part_field_name(sheet_config)
    matched_ids = {
        row.get("grist_row_id")
        for row in result.matched
        if row.get("grist_row_id") is not None
    }
    tally_rows: list[dict[str, Any]] = []
    updates: list[dict[str, Any]] = []
    seen_ids: set[Any] = set()

    for raw_record in grist_records:
        grist_row_id, fields = split_grist_record(raw_record)
        if grist_row_id is None or grist_row_id in seen_ids:
            continue
        if not grist_record_passes_filters(fields, filters, sheet_config):
            continue

        seen_ids.add(grist_row_id)
        should_tally = grist_row_id in matched_ids
        previous_value = fields.get(tally_field)
        updates.append({"id": grist_row_id, "fields": {tally_field: should_tally}})
        tally_rows.append(
            {
                "grist_row_id": grist_row_id,
                "product_part_name": display_text(fields.get(product_part_field)),
                "previous_tally_with_ods": yes_no(previous_value),
                "new_tally_with_ods": yes_no(should_tally),
                "reason": "matched ODS row" if should_tally else "no matched ODS row in current verification scope",
            }
        )

    for batch in chunks(updates, 100):
        client.update_table_records(table_id, batch)
    return tally_rows


def show_tally_update_summary(console: Console, tally_rows: list[dict[str, Any]]) -> None:
    """Print a compact tally update summary."""
    yes_count = sum(1 for row in tally_rows if row.get("new_tally_with_ods") == "Yes")
    no_count = sum(1 for row in tally_rows if row.get("new_tally_with_ods") == "No")
    table = Table(title="Grist TallyWithODS Updates")
    table.add_column("New Value", style="cyan")
    table.add_column("Count", justify="right", style="bold")
    table.add_row("Yes", str(yes_count))
    table.add_row("No", str(no_count))
    table.add_row("Total", str(len(tally_rows)))
    console.print(table)


def is_trailing_blank_row(row_info: RowInfo, last_value_row_number: int) -> bool:
    """Return True for blank rows after the sheet's last row containing any value."""
    return row_info.number > last_value_row_number and all(is_blank(value) for value in row_info.values)


def add_ods_row_accounting(
    result: VerificationResult,
    *,
    sheet: str,
    row_number: int | None,
    outcome: str,
    key: str = "",
    grist_row_id: Any = None,
    reason: str = "",
    values: dict[str, Any] | None = None,
) -> None:
    """Append one audit row showing how an ODS row was accounted for."""
    row = {
        "sheet": sheet,
        "row_number": row_number,
        "outcome": outcome,
        "key": key,
    }
    if grist_row_id is not None:
        row["grist_row_id"] = grist_row_id
    if reason:
        row["reason"] = reason
    row.update(values or {})
    result.ods_row_accounting.append(row)


def record_report_values(record: CompareRecord, sheet_config: SheetConfig) -> dict[str, str]:
    """Return configured descriptive fields from a normalized comparison record."""
    values = {
        field_name: display_text(record.values.get(field_name))
        for field_name in sheet_config.grist.report_fields
    }
    for field_name in ["ods_material", "grist_material", "compared_material", "material_match_type"]:
        if not is_blank(record.values.get(field_name)):
            values[field_name] = display_text(record.values.get(field_name))
    return values


def matched_report_values(
    ods_record: CompareRecord,
    grist_record: CompareRecord,
    sheet_config: SheetConfig,
) -> dict[str, str]:
    """Return report values for a matched ODS/Grist pair."""
    values = record_report_values(ods_record, sheet_config)
    for field_name in ["grist_material", "compared_material"]:
        if not is_blank(grist_record.values.get(field_name)):
            values[field_name] = display_text(grist_record.values.get(field_name))
    values["material_match_type"] = combined_material_match_type(ods_record, grist_record)
    return values


def add_match_result(
    result: VerificationResult,
    *,
    sheet_name: str,
    ods_record: CompareRecord,
    grist_record: CompareRecord,
    sheet_config: SheetConfig,
    match_strategy: str,
    fallback_key: str = "",
) -> None:
    """Append matched or mismatched report rows for one ODS/Grist pair."""
    report_values = matched_report_values(ods_record, grist_record, sheet_config)
    report_values["match_strategy"] = match_strategy
    if fallback_key:
        report_values["fallback_key"] = fallback_key

    mismatches = []
    for field_name in sheet_config.grist.compare_fields:
        if not values_equal(ods_record.values.get(field_name), grist_record.values.get(field_name)):
            mismatches.append(
                {
                    "field": field_name,
                    "ods_value": display_text(ods_record.values.get(field_name)),
                    "grist_value": display_text(grist_record.values.get(field_name)),
                }
            )

    if mismatches:
        for mismatch in mismatches:
            result.mismatched.append(
                {
                    "sheet": sheet_name,
                    "row_number": ods_record.source_row,
                    "grist_row_id": grist_record.grist_row_id,
                    "key": ods_record.display_key,
                    **report_values,
                    "grist_product_part_name": display_text(
                        grist_record.values.get("grist_product_part_name") or grist_record.values.get("product_part_name")
                    ),
                    **mismatch,
                }
            )
        add_ods_row_accounting(
            result,
            sheet=sheet_name,
            row_number=ods_record.source_row,
            outcome="mismatched",
            key=ods_record.display_key,
            grist_row_id=grist_record.grist_row_id,
            values=report_values,
        )
        return

    result.matched.append(
        {
            "sheet": sheet_name,
            "row_number": ods_record.source_row,
            "grist_row_id": grist_record.grist_row_id,
            "key": ods_record.display_key,
            **report_values,
            "grist_product_part_name": display_text(
                grist_record.values.get("grist_product_part_name") or grist_record.values.get("product_part_name")
            ),
        }
    )
    add_ods_row_accounting(
        result,
        sheet=sheet_name,
        row_number=ods_record.source_row,
        outcome="matched",
        key=ods_record.display_key,
        grist_row_id=grist_record.grist_row_id,
        values=report_values,
    )


def combined_material_match_type(ods_record: CompareRecord, grist_record: CompareRecord) -> str:
    """Return the most informative material match type for a matched pair."""
    grist_match_type = display_text(grist_record.values.get("material_match_type"))
    ods_match_type = display_text(ods_record.values.get("material_match_type"))
    if grist_match_type == "comparable material class" or ods_match_type == "comparable material class":
        return "comparable material class"
    return grist_match_type or ods_match_type


def ods_report_values(
    *,
    sheet: SheetView,
    row: list[Any],
    column_map: ColumnMap,
    sheet_config: SheetConfig,
) -> dict[str, str]:
    """Return configured descriptive fields directly from an ODS row."""
    values = {}
    for field_name in sheet_config.grist.report_fields:
        index = column_map.index_for(field_name)
        values[field_name] = display_text(sheet.get_value(row, index))
    return values


def ods_option_value(row: list[Any], sheet: SheetView, column_map: ColumnMap) -> Any:
    """Return the ODS optional item group value from a row, if present."""
    return sheet.get_value(row, column_map.index_for("optional_item_group_1"))


def option_scope_value_text(option_value: Any) -> str:
    """Return display text for option scope reporting."""
    if is_blank(option_value):
        return "<blank>"
    return display_text(option_value)


def option_scope_reason(option_value: Any, scope: OdsOptionScope) -> str:
    """Explain why a row fell outside the selected option scope."""
    if is_blank(option_value):
        return "blank/common row is outside selected ODS option scope"
    return f"option group '{display_text(option_value)}' is outside selected ODS option scope"


def ods_key_display_from_row(
    sheet: SheetView,
    row: list[Any],
    column_map: ColumnMap,
    sheet_config: SheetConfig,
) -> str:
    """Build a best-effort display key directly from raw ODS row values."""
    values = [
        key_display_value(sheet.get_value(row, column_map.index_for(field_name)))
        for field_name in sheet_config.grist.key_fields
    ]
    return " | ".join(values)


def ask_verification_filters(
    console: Console,
    client: GristClient,
    replay_filter_mode: int = 0,
    replay_product_index: int = 0,
    replay_manual_product_part_names: list[str] | None = None,
) -> tuple[VerificationFilters, int, int, list[str]]:
    """Ask how Grist rows should be filtered before comparison."""
    replay_manual_names = {
        name.strip()
        for name in replay_manual_product_part_names or []
        if name.strip()
    }
    if replay_filter_mode == 1 and replay_manual_names:
        return VerificationFilters(
            product_part_names=replay_manual_names,
            product_filter_label="manual product part names",
        ), replay_filter_mode, 0, sorted(replay_manual_names)

    if replay_filter_mode == 2 and replay_product_index > 0:
        names, label, product_index = choose_product_model_code(console, client, replay_index=replay_product_index)
        return VerificationFilters(
            product_part_names=names,
            product_filter_label=label,
        ), replay_filter_mode, product_index, []

    table = Table(title="Grist Product Part Filter")
    table.add_column("Option", justify="right", style="cyan")
    table.add_column("Filter")
    table.add_row("1", "Enter product part name(s)")
    table.add_row("2", "Use ProductModelConfig")
    console.print(table)
    filter_choice = Prompt.ask("Choose filter mode", choices=["1", "2"], default="2")
    filter_mode = int(filter_choice)

    if filter_choice == "1":
        raw_names = Prompt.ask("Enter ProductPartName value(s), comma-separated").strip()
        names = {name.strip() for name in raw_names.split(",") if name.strip()}
        if not names:
            raise ConfigError("At least one product part name is required for manual filter mode.")
        label = "manual product part names"
        product_index = 0
        manual_product_part_names = sorted(names)
    else:
        names, label, product_index = choose_product_model_code(console, client)
        manual_product_part_names = []

    return VerificationFilters(
        product_part_names=names,
        product_filter_label=label,
    ), filter_mode, product_index, manual_product_part_names


def choose_product_model_code(console: Console, client: GristClient, replay_index: int = 0) -> tuple[set[str], str, int]:
    """Ask for a ProductModelConfig code and return its product part names."""
    records = client.fetch_table_records("ProductModelConfig")
    by_code: dict[str, dict[str, Any]] = {}
    for fields in records:
        code = display_text(fields.get("ProductModelCode_ProductModelCode2"))
        part_name = display_text(fields.get("ProductPartName_ProductPartName"))
        if not code or not part_name:
            continue
        item = by_code.setdefault(
            code,
            {
                "description": display_text(fields.get("ProductModelCode_ProductModelDesc")),
                "parts": set(),
            },
        )
        item["parts"].add(part_name)

    if not by_code:
        raise GristError("No ProductModelConfig rows with model codes and product part names were found.")

    codes = sorted(by_code.keys(), key=str.casefold)
    if replay_index > 0 and replay_index <= len(codes):
        code = codes[replay_index - 1]
        return set(by_code[code]["parts"]), f"ProductModelConfig ProductModelCode={code}", replay_index

    table = Table(title="ProductModelConfig Codes")
    table.add_column("Option", justify="right", style="cyan")
    table.add_column("ProductModelCode")
    table.add_column("Description")
    table.add_column("Parts", justify="right")
    for index, code in enumerate(codes, start=1):
        table.add_row(
            str(index),
            code,
            by_code[code]["description"],
            str(len(by_code[code]["parts"])),
        )
    console.print(table)
    choice = Prompt.ask("Choose ProductModelCode", choices=[str(i) for i in range(1, len(codes) + 1)])
    index = int(choice)
    code = codes[index - 1]
    return set(by_code[code]["parts"]), f"ProductModelConfig ProductModelCode={code}", index


def ask_ods_option_scope(console: Console, workbook: OdsWorkbook, app_config: AppConfig, replay_scope_mode: int = 0, replay_options: list[int] | None = None) -> tuple[OdsOptionScope, int, list[int]]:
    """Ask which ODS option-group rows should participate in verification."""
    discovered_options = discover_ods_option_values(workbook, app_config)
    if replay_scope_mode == 1:
        scope = OdsOptionScope(
            mode="only_blanks",
            include_blanks=True,
            options_discovered=tuple(discovered_options),
        )
        console.print(f"[cyan]ODS option scope:[/cyan] {scope.label()}")
        return scope, 1, []
    if replay_scope_mode == 3:
        scope = OdsOptionScope(
            mode="all_options",
            selected_options=tuple(discovered_options),
            include_blanks=True,
            options_discovered=tuple(discovered_options),
        )
        console.print(f"[cyan]ODS option scope:[/cyan] {scope.label()}")
        return scope, 3, []
    if replay_scope_mode == 2 and replay_options is not None:
        selected_options, include_blanks = replay_specific_ods_options(replay_options, discovered_options)
        scope = OdsOptionScope(
            mode="specific_options",
            selected_options=tuple(selected_options),
            include_blanks=include_blanks,
            options_discovered=tuple(discovered_options),
        )
        console.print(f"[cyan]ODS option scope:[/cyan] {scope.label()}")
        return scope, 2, replay_options

    table = Table(title="ODS Option Scope")
    table.add_column("Option", justify="right", style="cyan")
    table.add_column("Scope")
    table.add_column("Rows Included")
    table.add_row("1", "Only blanks", "Only common/shared ODS rows with blank option group")
    table.add_row("2", "Specific options", "Selected option values, optionally plus BLANKS/common rows")
    table.add_row("3", "All options", "Every ODS row regardless of option group")
    console.print(table)
    scope_choice = Prompt.ask("Choose ODS option scope", choices=["1", "2", "3"], default="1")
    scope_mode = int(scope_choice)

    if scope_choice == "1":
        scope = OdsOptionScope(
            mode="only_blanks",
            include_blanks=True,
            options_discovered=tuple(discovered_options),
        )
        console.print(f"[cyan]ODS option scope:[/cyan] {scope.label()}")
        return scope, 1, []

    if scope_choice == "3":
        scope = OdsOptionScope(
            mode="all_options",
            selected_options=tuple(discovered_options),
            include_blanks=True,
            options_discovered=tuple(discovered_options),
        )
        console.print(f"[cyan]ODS option scope:[/cyan] {scope.label()}")
        return scope, 3, []

    selected_options, include_blanks, options_list = ask_specific_ods_options(console, discovered_options)
    scope = OdsOptionScope(
        mode="specific_options",
        selected_options=tuple(selected_options),
        include_blanks=include_blanks,
        options_discovered=tuple(discovered_options),
    )
    console.print(f"[cyan]ODS option scope:[/cyan] {scope.label()}")
    return scope, 2, options_list


def ask_specific_ods_options(console: Console, discovered_options: list[str]) -> tuple[list[str], bool, list[int]]:
    """Ask for one or more discovered ODS option values, with BLANKS available."""
    table = Table(title="Discovered ODS Options")
    table.add_column("Option", justify="right", style="cyan")
    table.add_column("Value")
    entries: list[str | None] = [None, *discovered_options]
    table.add_row("1", "BLANKS/common rows")
    for index, option in enumerate(discovered_options, start=2):
        table.add_row(str(index), option)
    console.print(table)

    if not discovered_options:
        console.print("[yellow]No nonblank ODS option values were discovered in supported sheets.[/yellow]")

    valid_choices = {str(index) for index in range(1, len(entries) + 1)}
    while True:
        raw = Prompt.ask("Choose ODS option number(s), comma-separated", default="1").strip()
        selected_numbers = [part.strip() for part in raw.split(",") if part.strip()]
        invalid = [part for part in selected_numbers if part not in valid_choices]
        if invalid:
            console.print(f"[red]Invalid option number(s):[/red] {', '.join(invalid)}")
            continue
        if not selected_numbers:
            console.print("[red]Select at least one ODS option scope entry.[/red]")
            continue
        selected_indexes = []
        for number in selected_numbers:
            index = int(number) - 1
            if index not in selected_indexes:
                selected_indexes.append(index)
        include_blanks = 0 in selected_indexes
        selected_options = [
            option
            for index in selected_indexes
            if (option := entries[index]) is not None
        ]
        options_list = [i + 1 for i in selected_indexes]  # 1-based
        return selected_options, include_blanks, options_list


def discover_cnc_dxf_review_option_values(ods_only_rows: list[dict[str, Any]]) -> list[str]:
    """Return distinct nonblank option-group values present in CNC ODS-only rows."""
    values = {
        display_text(row.get("optional_item_group_1"))
        for row in ods_only_rows
        if not is_blank(row.get("optional_item_group_1"))
    }
    return sorted(values, key=str.casefold)


def replay_cnc_dxf_excluded_options(
    replay_options: list[int],
    discovered_options: list[str],
) -> tuple[list[str], bool, list[int]]:
    """Resolve replayed CNC DXF exclusion choices back to option values."""
    entries: list[str | None] = [None, *discovered_options]
    selected_indexes: list[int] = []
    for option in replay_options:
        index = option - 1
        if 0 <= index < len(entries) and index not in selected_indexes:
            selected_indexes.append(index)
    exclude_blanks = 0 in selected_indexes
    selected_options = [
        option
        for index in selected_indexes
        if (option := entries[index]) is not None
    ]
    return selected_options, exclude_blanks, [index + 1 for index in selected_indexes]


def ask_cnc_dxf_excluded_options(console: Console, discovered_options: list[str]) -> tuple[list[str], bool, list[int]]:
    """Ask which option groups should be excluded from CNC DXF review."""
    table = Table(title="CNC DXF Review Option Groups To Exclude")
    table.add_column("Option", justify="right", style="cyan")
    table.add_column("Value")
    entries: list[str | None] = [None, *discovered_options]
    table.add_row("1", "BLANKS/common rows")
    for index, option in enumerate(discovered_options, start=2):
        table.add_row(str(index), option)
    console.print(table)

    valid_choices = {str(index) for index in range(1, len(entries) + 1)}
    while True:
        raw = Prompt.ask("Choose option group number(s) to exclude, comma-separated", default="1").strip()
        selected_numbers = [part.strip() for part in raw.split(",") if part.strip()]
        invalid = [part for part in selected_numbers if part not in valid_choices]
        if invalid:
            console.print(f"[red]Invalid option number(s):[/red] {', '.join(invalid)}")
            continue
        if not selected_numbers:
            console.print("[red]Select at least one option-group entry to exclude.[/red]")
            continue
        selected_indexes: list[int] = []
        for number in selected_numbers:
            index = int(number) - 1
            if index not in selected_indexes:
                selected_indexes.append(index)
        exclude_blanks = 0 in selected_indexes
        selected_options = [
            option
            for index in selected_indexes
            if (option := entries[index]) is not None
        ]
        options_list = [index + 1 for index in selected_indexes]
        return selected_options, exclude_blanks, options_list


def ask_cnc_dxf_review_option_filter(
    console: Console,
    ods_only_rows: list[dict[str, Any]],
    replay_filter_mode: int = 0,
    replay_options: list[int] | None = None,
) -> tuple[CncDxfReviewOptionFilter, int, list[int]]:
    """Ask which ODS option groups should be excluded from CNC DXF review only."""
    discovered_options = discover_cnc_dxf_review_option_values(ods_only_rows)

    if replay_filter_mode == 1:
        scope = CncDxfReviewOptionFilter(
            mode="all_rows",
            options_discovered=tuple(discovered_options),
        )
        console.print(f"[cyan]CNC DXF review option filter:[/cyan] {scope.label()}")
        return scope, 1, []
    if replay_filter_mode == 3:
        scope = CncDxfReviewOptionFilter(
            mode="only_blanks",
            exclude_blanks=False,
            options_discovered=tuple(discovered_options),
        )
        console.print(f"[cyan]CNC DXF review option filter:[/cyan] {scope.label()}")
        return scope, 3, []
    if replay_filter_mode == 2 and replay_options is not None:
        selected_options, exclude_blanks, options_list = replay_cnc_dxf_excluded_options(replay_options, discovered_options)
        scope = CncDxfReviewOptionFilter(
            mode="exclude_specific",
            excluded_options=tuple(selected_options),
            exclude_blanks=exclude_blanks,
            options_discovered=tuple(discovered_options),
        )
        console.print(f"[cyan]CNC DXF review option filter:[/cyan] {scope.label()}")
        return scope, 2, options_list

    table = Table(title="CNC DXF Review Option Filter")
    table.add_column("Option", justify="right", style="cyan")
    table.add_column("Scope")
    table.add_column("Rows Included In DXF Review")
    table.add_row("1", "All option groups", "All CNC ODS-only rows")
    table.add_row("2", "Exclude specific option groups", "All rows except selected option groups")
    table.add_row("3", "Only blanks/common rows", "Only blank/common CNC ODS-only rows")
    console.print(table)
    filter_choice = Prompt.ask("Choose CNC DXF review option filter", choices=["1", "2", "3"], default="1")
    filter_mode = int(filter_choice)

    if filter_choice == "1":
        scope = CncDxfReviewOptionFilter(
            mode="all_rows",
            options_discovered=tuple(discovered_options),
        )
        console.print(f"[cyan]CNC DXF review option filter:[/cyan] {scope.label()}")
        return scope, 1, []

    if filter_choice == "3":
        scope = CncDxfReviewOptionFilter(
            mode="only_blanks",
            exclude_blanks=False,
            options_discovered=tuple(discovered_options),
        )
        console.print(f"[cyan]CNC DXF review option filter:[/cyan] {scope.label()}")
        return scope, 3, []

    selected_options, exclude_blanks, options_list = ask_cnc_dxf_excluded_options(console, discovered_options)
    scope = CncDxfReviewOptionFilter(
        mode="exclude_specific",
        excluded_options=tuple(selected_options),
        exclude_blanks=exclude_blanks,
        options_discovered=tuple(discovered_options),
    )
    console.print(f"[cyan]CNC DXF review option filter:[/cyan] {scope.label()}")
    return scope, 2, options_list


def filter_cnc_dxf_review_rows(
    ods_only_rows: list[dict[str, Any]],
    option_filter: CncDxfReviewOptionFilter,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split CNC ODS-only rows into included and skipped sets for DXF review."""
    included: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for row in ods_only_rows:
        if option_filter.includes(row.get("optional_item_group_1")):
            included.append(row)
        else:
            skipped.append(row)
    return included, skipped


def replay_specific_ods_options(replay_options: list[int], discovered_options: list[str]) -> tuple[list[str], bool]:
    """Replay specific ODS options selection."""
    entries: list[str | None] = [None, *discovered_options]
    selected_indexes = [i - 1 for i in replay_options if 1 <= i <= len(entries)]
    include_blanks = 0 in selected_indexes
    selected_options = [
        option
        for index in selected_indexes
        if (option := entries[index]) is not None
    ]
    return selected_options, include_blanks


def discover_ods_option_values(workbook: OdsWorkbook, app_config: AppConfig) -> list[str]:
    """Collect distinct nonblank optional item groups from all configured ODS sheets."""
    values: dict[str, str] = {}
    for sheet_name, sheet_config in app_config.supported_sheets.items():
        if sheet_name not in workbook.sheets:
            continue
        sheet = workbook.sheet_view(sheet_name, sheet_config)
        column_map = sheet.column_map()
        option_index = column_map.index_for("optional_item_group_1")
        if option_index is None:
            continue
        for row_info in sheet.data_rows():
            option_value = display_text(sheet.get_value(row_info.values, option_index))
            if option_value:
                values.setdefault(normalize_text(option_value), option_value)
    return sorted(values.values(), key=str.casefold)


def grist_record_passes_filters(
    fields: dict[str, Any],
    filters: VerificationFilters | None,
    sheet_config: SheetConfig | None = None,
) -> bool:
    """Return True when a Grist row passes selected user filters."""
    if grist_record_is_excluded(fields, sheet_config):
        return False
    if filters is not None:
        product_part_field = grist_product_part_field_name(sheet_config)
        if display_text(fields.get(product_part_field)) not in filters.product_part_names:
            return False
    return True


def grist_record_is_excluded(fields: dict[str, Any], sheet_config: SheetConfig | None) -> bool:
    """Return True when a Grist row is excluded by configured field values."""
    if sheet_config is None:
        return False
    for field_name, excluded_values in sheet_config.grist.excluded_record_values.items():
        excluded = {normalize_text(value) for value in excluded_values}
        if normalize_text(fields.get(field_name)) in excluded:
            return True
    return False


def split_grist_record(record: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
    """Return Grist record ID and fields from raw or fields-only records."""
    fields = record.get("fields")
    if isinstance(fields, dict):
        return record.get("id"), fields
    return record.get("id"), record


def grist_product_part_field_name(sheet_config: SheetConfig | None) -> str:
    """Return the Grist field used for product-part filtering and reporting."""
    if sheet_config is None:
        return "ProductPartName_ProductPartName"
    return sheet_config.grist.product_part_field


def build_master_material_index(
    records: list[dict[str, Any]],
    *,
    comparable_field: str,
) -> MasterMaterialIndex:
    """Build material ID and comparable-class lookups from MasterMaterial records."""
    id_to_resolution: dict[Any, MaterialResolution] = {}
    name_to_resolution: dict[str, MaterialResolution] = {}
    comparable_materials: dict[str, str] = {}
    for record in records:
        grist_row_id, fields = split_grist_record(record)
        master_name = display_text(fields.get("MasterMaterial"))
        comparable_name = display_text(fields.get(comparable_field))
        if comparable_name:
            compared_name = comparable_name
            match_type = "comparable material class"
            comparable_materials.setdefault(normalize_text(comparable_name), comparable_name)
        else:
            compared_name = master_name
            match_type = "exact material match"
        id_to_resolution[grist_row_id] = MaterialResolution(
            original_name=master_name,
            compared_name=compared_name,
            match_type=match_type,
        )
        if master_name:
            name_to_resolution.setdefault(normalize_text(master_name), id_to_resolution[grist_row_id])
    return MasterMaterialIndex(
        id_to_resolution=id_to_resolution,
        name_to_resolution=name_to_resolution,
        comparable_materials=comparable_materials,
    )


def mapped_material_targets(
    material_mapping: dict[str, str],
    comparable_materials: dict[str, str],
) -> set[str]:
    """Return normalized material comparison names reachable from ODS data."""
    targets = {normalize_text(value) for value in material_mapping.values() if not is_blank(value)}
    targets.update(comparable_materials.keys())
    return targets


def grist_material_has_ods_mapping(value: Any, mapped_grist_materials: set[str]) -> bool:
    """Return True when a Grist material name is present as a mapping target."""
    return normalize_text(value) in mapped_grist_materials


def is_toolshop_comparable_fallback_enabled(sheet_name: str) -> bool:
    """Return True for the Tool Shop-only comparable material fallback."""
    return sheet_name == "Tool Shop Items"


def build_toolshop_fallback_index(records: list[CompareRecord]) -> dict[tuple[str, ...], list[CompareRecord]]:
    """Index Tool Shop comparable-material records by the fallback key."""
    index: dict[tuple[str, ...], list[CompareRecord]] = {}
    for record in records:
        fallback_key = toolshop_fallback_key(record)
        if fallback_key is None:
            continue
        index.setdefault(fallback_key, []).append(record)
    return index


def toolshop_fallback_key(record: CompareRecord) -> tuple[str, ...] | None:
    """Return fallback key that ignores dimension for comparable Tool Shop materials."""
    fallback_material = toolshop_fallback_material(record)
    if fallback_material is None:
        return None
    required_fields = ["toolshop_part_name", "qty"]
    if any(is_blank(record.values.get(field_name)) for field_name in required_fields):
        return None
    return (
        key_value(fallback_material),
        key_value(record.values.get("toolshop_part_name")),
        key_value(record.values.get("qty")),
        key_value(record.values.get("optional_item_group_1")),
    )


def toolshop_fallback_display_key(record: CompareRecord) -> str:
    """Return display text for the Tool Shop fallback key."""
    fallback_material = toolshop_fallback_material(record)
    return " | ".join(
        [
            key_display_value(fallback_material),
            key_display_value(record.values.get("toolshop_part_name")),
            key_display_value(record.values.get("qty")),
            key_display_value(record.values.get("optional_item_group_1")),
        ]
    )


def toolshop_fallback_material(record: CompareRecord) -> Any | None:
    """Return the material bucket used for Tool Shop fallback matching."""
    if record.source_row is not None and not is_blank(record.values.get("ods_material")):
        return record.values.get("ods_material")
    if display_text(record.values.get("material_match_type")) == "comparable material class":
        return record.values.get("compared_material")
    return None


def duplicate_context(record: CompareRecord) -> str:
    """Return the non-key context used to decide whether repeated keys are true duplicates."""
    return key_value(record.values.get("grist_product_part_name") or record.values.get("product_part_name"))


def duplicate_context_display(record: CompareRecord) -> str:
    """Return a display label for the duplicate-disambiguation context."""
    return key_display_value(record.values.get("grist_product_part_name") or record.values.get("product_part_name"))


def records_by_duplicate_context(records: list[CompareRecord]) -> dict[str, list[CompareRecord]]:
    """Group records with the same comparison key by product-part context."""
    by_context: dict[str, list[CompareRecord]] = {}
    for record in records:
        by_context.setdefault(duplicate_context(record), []).append(record)
    return by_context


def duplicate_contexts_by_key(
    records_by_key: dict[tuple[str, ...], list[CompareRecord]],
) -> dict[tuple[str, ...], set[str]]:
    """Return key/context groups that still contain more than one source row."""
    duplicates: dict[tuple[str, ...], set[str]] = {}
    for key, records in records_by_key.items():
        contexts = {
            context
            for context, context_records in records_by_duplicate_context(records).items()
            if len(context_records) > 1
        }
        if contexts:
            duplicates[key] = contexts
    return duplicates


def chunks(values: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    """Split a list into API-friendly batches."""
    return [values[index : index + size] for index in range(0, len(values), size)]


def ods_compare_record(
    *,
    sheet: SheetView,
    row_info: RowInfo,
    column_map: ColumnMap,
    sheet_config: SheetConfig,
    material_mapping: dict[str, str],
    comparable_materials: dict[str, str],
    material_name_to_resolution: dict[str, MaterialResolution],
) -> CompareRecord | None:
    """Build a normalized comparison record from an ODS row."""
    grist_config = sheet_config.grist
    values: dict[str, Any] = {}
    fields_needed = set(grist_config.key_fields) | set(grist_config.compare_fields) | set(grist_config.report_fields)

    for field_name in fields_needed:
        index = column_map.index_for(field_name)
        value = sheet.get_value(row_info.values, index)
        if grist_config.material_field and field_name == grist_config.material_field:
            if is_blank(value):
                return None
            ods_material = display_text(value)
            mapped = material_mapping.get(ods_material)
            comparable_material = comparable_materials.get(normalize_text(ods_material))
            if mapped:
                mapped_resolution = material_name_to_resolution.get(normalize_text(mapped))
                if mapped_resolution is not None:
                    value = mapped_resolution.compared_name
                    values["grist_material"] = mapped_resolution.original_name
                    values["material_match_type"] = mapped_resolution.match_type
                else:
                    value = mapped
                    values["material_match_type"] = "exact material mapping"
            elif comparable_material:
                value = comparable_material
                values["material_match_type"] = "comparable material class"
            else:
                return None
            values["ods_material"] = ods_material
            values["compared_material"] = value
        values[field_name] = value

    key = tuple(key_value(values.get(field_name)) for field_name in grist_config.key_fields)
    display_key = " | ".join(key_display_value(values.get(field_name)) for field_name in grist_config.key_fields)
    return CompareRecord(key=key, display_key=display_key, values=values, source_row=row_info.number)


def grist_compare_record(
    fields: dict[str, Any],
    sheet_config: SheetConfig,
    material_id_to_resolution: dict[Any, MaterialResolution],
    grist_row_id: Any = None,
) -> CompareRecord | None:
    """Build a normalized comparison record from Grist record fields."""
    grist_config = sheet_config.grist
    product_part_field = grist_product_part_field_name(sheet_config)
    values: dict[str, Any] = {}
    fields_needed = set(grist_config.key_fields) | set(grist_config.compare_fields) | set(grist_config.report_fields)
    for field_name in fields_needed:
        grist_field = grist_config.ods_to_grist_fields.get(field_name, field_name)
        value = fields.get(grist_field)
        if grist_config.material_field and field_name == grist_config.material_field:
            resolution = material_id_to_resolution.get(
                value,
                MaterialResolution(
                    original_name=display_text(value),
                    compared_name=display_text(value),
                    match_type="exact material match",
                ),
            )
            values["grist_material"] = resolution.original_name
            values["compared_material"] = resolution.compared_name
            values["material_match_type"] = resolution.match_type
            value = resolution.compared_name
        values[field_name] = value

    values["grist_product_part_name"] = fields.get(product_part_field)

    allowed_blank_fields = set(grist_config.allow_blank_key_fields)
    required_key_fields = [
        field_name for field_name in grist_config.key_fields if field_name not in allowed_blank_fields
    ]
    if any(is_blank(values.get(field_name)) for field_name in required_key_fields):
        return None
    key = tuple(key_value(values.get(field_name)) for field_name in grist_config.key_fields)
    display_key = " | ".join(key_display_value(values.get(field_name)) for field_name in grist_config.key_fields)
    return CompareRecord(key=key, display_key=display_key, values=values, grist_row_id=grist_row_id)


def key_value(value: Any) -> str:
    """Normalize key values, preserving numeric equivalence."""
    number = as_decimal(value)
    if number is not None:
        return format(number.normalize(), "f")
    return normalize_text(value)


def key_display_value(value: Any) -> str:
    """Return a display value for key fields, making blank key parts visible."""
    if is_blank(value):
        return "<blank>"
    return display_text(value)


def yes_no(value: Any) -> str:
    """Display Grist toggle values as Yes/No."""
    return "Yes" if value is True else "No"

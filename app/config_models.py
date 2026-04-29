"""Pydantic models for sheet and application configuration."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class InactiveRules(BaseModel):
    """Config-driven rules for deciding whether a row is active."""

    blank_rows: bool = True
    required_any_fields: list[str] = Field(default_factory=list)
    qty_fields: list[str] = Field(default_factory=list)
    inactive_flag_fields: list[str] = Field(default_factory=list)
    inactive_values: list[str] = Field(
        default_factory=lambda: [
            "no",
            "n",
            "false",
            "0",
            "inactive",
            "not in use",
            "unused",
            "delete",
            "deleted",
        ]
    )


class CostingRules(BaseModel):
    """Config for removing or blanking costing data during version creation."""

    fields: list[str] = Field(default_factory=list)
    mode: Literal["blank", "remove"] = "blank"


class GristVerificationConfig(BaseModel):
    """Config for comparing a sheet with a Grist table."""

    enabled: bool = False
    table_id: str | None = None
    product_part_field: str = "ProductPartName_ProductPartName"
    key_fields: list[str] = Field(default_factory=list)
    allow_blank_key_fields: list[str] = Field(default_factory=list)
    compare_fields: list[str] = Field(default_factory=list)
    report_fields: list[str] = Field(default_factory=list)
    excluded_record_values: dict[str, list[str]] = Field(default_factory=dict)
    ods_to_grist_fields: dict[str, str] = Field(default_factory=dict)
    material_field: str | None = None
    comparable_material_field: str = "ODSComparableMaterial"
    tally_field: str = "TallyWithODS"


class SheetConfig(BaseModel):
    """Configuration for a supported ODS sheet."""

    header_row: int
    aliases: dict[str, list[str]] = Field(default_factory=dict)
    part_name_fields: list[str] = Field(default_factory=list)
    inactive_rules: InactiveRules = Field(default_factory=InactiveRules)
    costing: CostingRules = Field(default_factory=CostingRules)
    grist: GristVerificationConfig = Field(default_factory=GristVerificationConfig)


class AppConfig(BaseModel):
    """Top-level application configuration."""

    reports_dir: str = "reports"
    output_dir: str = "output"
    supported_sheets: dict[str, SheetConfig]

"""Small persisted CLI state for remembered prompts."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import logging

import yaml

logger = logging.getLogger(__name__)

DEFAULT_STATE_PATH = Path("config/last_inputs.yaml")


@dataclass
class LastInputs:
    """Previously used source folder and filename."""

    folder: str = ""
    source_file: str = ""
    verification_sheet_index: int = 0
    verification_filter_mode: int = 0
    verification_product_model_index: int = 0
    verification_manual_product_part_names: list[str] = field(default_factory=list)
    verification_scope_state: dict[str, Any] = field(default_factory=dict)
    verification_scope_mode: int = 0
    verification_specific_options: list[int] = field(default_factory=list)
    verification_cnc_dxf_filter_state: dict[str, Any] = field(default_factory=dict)
    verification_cnc_dxf_filter_mode: int = 0
    verification_cnc_dxf_excluded_options: list[int] = field(default_factory=list)


def clean_cnc_dxf_filter_scope_state(value: Any) -> dict[str, Any]:
    """Return a normalized saved CNC DXF filter scope dictionary."""
    if not isinstance(value, dict):
        return {}
    selected_values = value.get("selected_values") or []
    if not isinstance(selected_values, list):
        selected_values = []
    cleaned: dict[str, Any] = {}
    field_name = str(value.get("field") or "").strip()
    mode = str(value.get("mode") or "").strip()
    if field_name:
        cleaned["field"] = field_name
    if mode:
        cleaned["mode"] = mode
    cleaned["selected_values"] = [str(item).strip() for item in selected_values if str(item).strip()]
    cleaned["include_blanks"] = bool(value.get("include_blanks"))
    return cleaned


def clean_cnc_dxf_filter_state(value: Any) -> dict[str, Any]:
    """Return a normalized saved CNC DXF combined filter dictionary."""
    if not isinstance(value, dict):
        return {}
    cleaned: dict[str, Any] = {"version": int(value.get("version") or 2)}
    primary = clean_cnc_dxf_filter_scope_state(value.get("primary"))
    secondary = clean_cnc_dxf_filter_scope_state(value.get("secondary"))
    if primary:
        cleaned["primary"] = primary
    if secondary:
        cleaned["secondary"] = secondary
    return cleaned if "primary" in cleaned else {}


def clean_ods_scope_state(value: Any) -> dict[str, Any]:
    """Return a normalized saved ODS verification scope dictionary."""
    if not isinstance(value, dict):
        return {}
    cleaned: dict[str, Any] = {"version": int(value.get("version") or 2)}
    primary = clean_cnc_dxf_filter_scope_state(value.get("primary"))
    secondary = clean_cnc_dxf_filter_scope_state(value.get("secondary"))
    if primary:
        cleaned["primary"] = primary
    if secondary:
        cleaned["secondary"] = secondary
    return cleaned if "primary" in cleaned else {}


def load_last_inputs(path: Path = DEFAULT_STATE_PATH) -> LastInputs:
    """Load remembered CLI inputs, ignoring corrupt or missing state."""
    if not path.exists():
        return LastInputs()
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # pragma: no cover - defensive only
        logger.warning("Could not read remembered inputs from %s: %s", path, exc)
        return LastInputs()
    if not isinstance(data, dict):
        return LastInputs()
    verification_specific_options = data.get("verification_specific_options") or []
    if not isinstance(verification_specific_options, list):
        verification_specific_options = []
    verification_cnc_dxf_filter_state = clean_cnc_dxf_filter_state(data.get("verification_cnc_dxf_filter_state"))
    verification_cnc_dxf_excluded_options = data.get("verification_cnc_dxf_excluded_options") or []
    if not isinstance(verification_cnc_dxf_excluded_options, list):
        verification_cnc_dxf_excluded_options = []
    verification_manual_product_part_names = data.get("verification_manual_product_part_names") or []
    if not isinstance(verification_manual_product_part_names, list):
        verification_manual_product_part_names = []
    verification_scope_state = clean_ods_scope_state(data.get("verification_scope_state"))
    return LastInputs(
        folder=str(data.get("folder") or ""),
        source_file=str(data.get("source_file") or ""),
        verification_sheet_index=int(data.get("verification_sheet_index") or 0),
        verification_filter_mode=int(data.get("verification_filter_mode") or 0),
        verification_product_model_index=int(data.get("verification_product_model_index") or 0),
        verification_manual_product_part_names=[
            str(name).strip() for name in verification_manual_product_part_names if str(name).strip()
        ],
        verification_scope_state=verification_scope_state,
        verification_scope_mode=int(data.get("verification_scope_mode") or 0),
        verification_specific_options=[
            int(option) for option in verification_specific_options if str(option).isdigit()
        ],
        verification_cnc_dxf_filter_state=verification_cnc_dxf_filter_state,
        verification_cnc_dxf_filter_mode=int(data.get("verification_cnc_dxf_filter_mode") or 0),
        verification_cnc_dxf_excluded_options=[
            int(option) for option in verification_cnc_dxf_excluded_options if str(option).isdigit()
        ],
    )


def save_last_inputs(
    folder: Path,
    source_file: str,
    path: Path = DEFAULT_STATE_PATH,
    verification_sheet_index: int = 0,
    verification_filter_mode: int = 0,
    verification_product_model_index: int = 0,
    verification_manual_product_part_names: list[str] | None = None,
    verification_scope_state: dict[str, Any] | None = None,
    verification_scope_mode: int = 0,
    verification_specific_options: list[int] | None = None,
    verification_cnc_dxf_filter_state: dict[str, Any] | None = None,
    verification_cnc_dxf_filter_mode: int = 0,
    verification_cnc_dxf_excluded_options: list[int] | None = None,
) -> None:
    """Persist the last successful source folder and filename."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        data: dict[str, Any] = {
            "folder": str(folder),
            "source_file": source_file,
            "verification_sheet_index": verification_sheet_index,
            "verification_filter_mode": verification_filter_mode,
            "verification_product_model_index": verification_product_model_index,
            "verification_manual_product_part_names": verification_manual_product_part_names or [],
            "verification_scope_state": clean_ods_scope_state(verification_scope_state or {}),
            "verification_scope_mode": verification_scope_mode,
            "verification_specific_options": verification_specific_options or [],
            "verification_cnc_dxf_filter_state": clean_cnc_dxf_filter_state(
                verification_cnc_dxf_filter_state or {}
            ),
            "verification_cnc_dxf_filter_mode": verification_cnc_dxf_filter_mode,
            "verification_cnc_dxf_excluded_options": verification_cnc_dxf_excluded_options or [],
        }
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    except Exception as exc:  # pragma: no cover - defensive only
        logger.warning("Could not save remembered inputs to %s: %s", path, exc)


def normalize_ods_filename(value: str) -> str:
    """Return a source filename, assuming .ods when no extension is entered."""
    cleaned = value.strip().strip('"')
    if cleaned.casefold().endswith(".ods"):
        return cleaned
    return f"{cleaned}.ods"

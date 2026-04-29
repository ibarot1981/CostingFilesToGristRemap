"""Configuration loading helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from app.config_models import AppConfig
from app.exceptions import ConfigError


DEFAULT_CONFIG_PATH = Path("config") / "sheet_config.yaml"
DEFAULT_MATERIAL_MAPPING_PATH = Path("config") / "material_mapping.yaml"


def load_app_config(path: Path = DEFAULT_CONFIG_PATH) -> AppConfig:
    """Load and validate the sheet configuration YAML file."""
    if not path.exists():
        raise ConfigError(f"Sheet config file not found: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return AppConfig.model_validate(data)
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {path}: {exc}") from exc
    except ValidationError as exc:
        raise ConfigError(f"Invalid sheet config in {path}: {exc}") from exc


def load_material_mapping(path: Path = DEFAULT_MATERIAL_MAPPING_PATH) -> dict[str, str]:
    """Load ODS-to-Grist material name mappings from YAML."""
    if not path.exists():
        raise ConfigError(f"Material mapping file not found: {path}")
    try:
        data: Any = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {path}: {exc}") from exc

    if isinstance(data, dict) and "materials" in data:
        data = data["materials"]
    if not isinstance(data, dict):
        raise ConfigError("Material mapping must be a mapping or contain a 'materials' mapping.")
    return {str(key).strip(): str(value).strip() for key, value in data.items()}

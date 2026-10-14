"""Conversions for values in Grist's normal cell representation."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any


def canonical_grist_json_cell(value: Any) -> str:
    """Serialize an object as a deterministic primitive Grist cell value.

    Grist REST cells cannot contain a bare JSON object or array. Storing
    normalized evidence as canonical JSON text keeps the Any cell a valid
    primitive while preserving a stable representation for retries.
    """
    if not isinstance(value, dict):
        raise TypeError("Canonical Grist JSON cells require an object value.")
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def decode_grist_any_object(value: Any) -> dict[str, Any] | None:
    """Read an object stored in an Any cell, including Grist typed objects.

    Current mapping writes use canonical JSON text. This reader also accepts
    previously supported raw fixture dictionaries and Grist's tagged ``O``
    object representation, with nested ``O``/``L`` values decoded. Invalid
    or non-object cells return ``None`` so callers can fail closed.
    """
    def decode_nested(item: Any) -> Any:
        if isinstance(item, dict):
            return {str(key): decode_nested(nested) for key, nested in item.items()}
        if isinstance(item, list):
            if item and item[0] == "O":
                if len(item) != 2 or not isinstance(item[1], dict):
                    raise ValueError("Invalid Grist object cell.")
                return decode_nested(item[1])
            if item and item[0] == "L":
                return [decode_nested(nested) for nested in item[1:]]
            return [decode_nested(nested) for nested in item]
        return item

    try:
        if isinstance(value, str):
            value = json.loads(value)
        if isinstance(value, list) and (not value or value[0] != "O"):
            return None
        decoded = decode_nested(value)
        if not isinstance(decoded, dict):
            return None
        canonical_grist_json_cell(decoded)
    except (TypeError, ValueError):
        return None
    return decoded


def datetime_text(value: Any) -> str:
    """Convert Grist epoch-second DateTime cells to domain ISO-8601 text."""
    if isinstance(value, (list, tuple)) and len(value) > 1 and value[0] == "D":
        value = value[1]
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            return str(value)
    text = "" if value is None else str(value).strip()
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.isoformat()


def grist_datetime(value: Any) -> float | None:
    """Encode a domain timestamp using Grist's normal epoch-second format."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    text = datetime_text(value)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"Invalid DateTime value for Grist: {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def grist_list(values: Any) -> list[Any]:
    """Encode an Any-cell list using Grist's normal object representation."""
    if not isinstance(values, (list, tuple)):
        raise TypeError("Grist list cells require a list or tuple of values.")
    return ["L", *values]

"""Conversions for values in Grist's normal cell representation."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


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

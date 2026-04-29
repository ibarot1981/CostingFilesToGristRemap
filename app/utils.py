"""Small normalization and value conversion helpers."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any


def normalize_header(value: Any) -> str:
    """Normalize a column header for alias matching."""
    return " ".join(str(value or "").strip().casefold().split())


def normalize_text(value: Any) -> str:
    """Normalize text for stable comparison and key construction."""
    return " ".join(str(value or "").strip().casefold().split())


def display_text(value: Any) -> str:
    """Return a user-facing string without noisy Python None values."""
    if value is None:
        return ""
    return str(value).strip()


def is_blank(value: Any) -> bool:
    """Return True when a spreadsheet cell should be treated as blank."""
    if value is None:
        return True
    return str(value).strip() == ""


def as_decimal(value: Any) -> Decimal | None:
    """Convert spreadsheet-looking numeric values to Decimal when possible."""
    if is_blank(value):
        return None
    if isinstance(value, Decimal):
        return value
    text = str(value).strip().replace(",", "")
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return None


def is_zero_or_blank(value: Any) -> bool:
    """Return True for blank values or values that parse as numeric zero."""
    if is_blank(value):
        return True
    number = as_decimal(value)
    return number is not None and number == 0


def values_equal(left: Any, right: Any) -> bool:
    """Compare ODS and Grist values with sensible numeric handling."""
    left_number = as_decimal(left)
    right_number = as_decimal(right)
    if left_number is not None and right_number is not None:
        return left_number == right_number
    return normalize_text(left) == normalize_text(right)


def slugify(value: str) -> str:
    """Make a conservative file-name slug for reports."""
    chars = []
    for char in value.strip().lower():
        if char.isalnum():
            chars.append(char)
        elif char in {" ", "-", "_", "."}:
            chars.append("_")
    slug = "".join(chars).strip("_")
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug or "report"

"""Pure ODS material resolution shared by CLI comparison and pilot ingestion."""

from __future__ import annotations

from dataclasses import dataclass

from app.utils import normalize_text


@dataclass(frozen=True)
class MaterialMatch:
    display_name: str
    canonical_name: str | None
    status: str


def resolve_material(
    display_name: str,
    mappings: dict[str, str],
    *,
    alternates: dict[str, str] | None = None,
    option_scope: str | None = None,
) -> MaterialMatch:
    """Resolve explicit aliases without silently merging similar material names.

    ``option_scope`` is carried by callers as line identity, never by the
    material master. AlternateSize is a suggestion until explicitly mapped.
    """
    del option_scope
    value = display_name.strip()
    mapped = mappings.get(value)
    if mapped:
        return MaterialMatch(value, mapped, "reviewed_alias")
    if value in mappings.values():
        return MaterialMatch(value, value, "exact_canonical")
    alternate = (alternates or {}).get(normalize_text(value))
    if alternate:
        return MaterialMatch(value, alternate, "alternate_size_proposal")
    return MaterialMatch(value, None, "unmatched")

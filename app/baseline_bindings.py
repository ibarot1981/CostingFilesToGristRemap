"""Read-only overlays for audited normalization of accepted baseline rows."""
from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from app.part_identity import request_fingerprint


def _fields(row: dict[str, Any]) -> dict[str, Any]:
    value = row.get("fields")
    return value if isinstance(value, dict) else row


def effective_baseline_overrides(request_rows: list[dict[str, Any]], part_id: str, revision_id: int) -> dict[int, dict[str, Any]]:
    """Return only completed, keyed technical corrections for this exact revision."""
    overrides: dict[int, dict[str, Any]] = {}
    for row in request_rows:
        fields = _fields(row)
        if (str(fields.get("RequestType") or "") != "part_baseline_effective_binding"
                or str(fields.get("Status") or "").casefold() != "published"
                or str(fields.get("EntityUUID") or "") != str(part_id)
                or not str(fields.get("RequestKey") or "").strip()
                or not str(fields.get("RequestFingerprint") or "").strip()):
            continue
        result = fields.get("Result")
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except (TypeError, ValueError):
                continue
        if (not isinstance(result, dict) or result.get("schema") != "part-baseline-effective-binding-v1"
                or str(result.get("partId") or "") != str(part_id)
                or result.get("applied") is not True):
            continue
        actor, reason = str(result.get("actor") or "").strip(), str(result.get("reason") or "").strip()
        preview_fingerprint = str(result.get("previewFingerprint") or "").strip()
        if (not actor or not reason or not preview_fingerprint
                or str(fields.get("RequestFingerprint")) != request_fingerprint([part_id, preview_fingerprint, actor, reason])):
            continue
        try:
            result_revision_id = int(result.get("revisionId"))
        except (TypeError, ValueError):
            continue
        if result_revision_id != int(revision_id):
            continue
        for binding in result.get("bindings", []):
            try:
                detail_id = int(binding.get("detailId"))
                if int(binding.get("revisionId")) != int(revision_id):
                    continue
            except (AttributeError, TypeError, ValueError):
                continue
            if detail_id in overrides:
                # Conflicting completed repairs fail closed in readers.
                return {}
            overrides[detail_id] = deepcopy(binding.get("fields") or {})
    return overrides


def apply_effective_baseline_override(detail: dict[str, Any], overrides: dict[int, dict[str, Any]]) -> dict[str, Any]:
    copied = deepcopy(detail)
    try:
        detail_id = int(copied.get("id"))
    except (TypeError, ValueError):
        return copied
    patch = overrides.get(detail_id)
    if not patch:
        return copied
    if isinstance(copied.get("fields"), dict):
        copied["fields"] = {**copied["fields"], **deepcopy(patch)}
    else:
        copied.update(deepcopy(patch))
    return copied

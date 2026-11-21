"""Resolve cost-entry age without conflating it with effective or transaction dates."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.grist_types import datetime_text


def _first(fields: dict[str, Any], names: tuple[str, ...]) -> Any:
    return next((fields.get(name) for name in names if fields.get(name) not in (None, "")), None)


def _timestamp(value: Any) -> str | None:
    if value in (None, "") or isinstance(value, (list, dict, bool)):
        return None
    try:
        text = datetime_text(value)
        datetime.fromisoformat(text.replace("Z", "+00:00"))
        return text
    except (TypeError, ValueError, OverflowError):
        text = str(value).strip()
        for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y", "%d-%m-%y"):
            try:
                return datetime.strptime(text[:10] if fmt.endswith("%Y") and "/" not in fmt and "." not in fmt else text, fmt).date().isoformat()
            except ValueError:
                continue
        return None


def cost_date_evidence(fields: dict[str, Any], *, source_kind: str) -> dict[str, str | None]:
    """Return distinct event, effective and transaction dates plus the chosen age basis.

    Generic UpdatedAt is treated as a cost event only for an immutable rate-log row,
    where the row itself records the rate event. A master record's metadata update
    is never allowed to make its rate look newly entered.
    """
    effective = _timestamp(_first(fields, ("EffectiveAt", "RateEffectiveAt", "EffectiveDate", "RateDate")))
    transaction = _timestamp(_first(fields, ("TransactionAt", "TransactionDate", "PurchaseDate")))

    if source_kind == "rate_log":
        recorded_names = ("RecordedAt", "CreatedAt", "EntryAt", "DateOfEntry", "EntryDate", "EnteredAt")
        updated_names = ("RateUpdatedAt", "PriceUpdatedAt", "UpdatedAt", "LastUpdatedAt")
    elif source_kind == "rate_master":
        recorded_names = ("RateRecordedAt", "PriceRecordedAt", "CostRecordedAt")
        updated_names = ("RateUpdatedAt", "PriceUpdatedAt", "CostUpdatedAt")
    elif source_kind == "purchase_record":
        recorded_names = ("RecordedAt", "CreatedAt", "EntryAt")
        updated_names = ("CostUpdatedAt", "PurchaseRateUpdatedAt")
    elif source_kind == "process_rate":
        recorded_names = ("RecordedAt", "CreatedAt", "EntryAt", "RateRecordedAt")
        updated_names = ("RateUpdatedAt", "CostUpdatedAt")
    else:
        recorded_names, updated_names = (), ()

    recorded = _timestamp(_first(fields, recorded_names))
    updated = _timestamp(_first(fields, updated_names))
    if updated:
        source_date, basis = updated, "update"
    elif recorded:
        source_date, basis = recorded, "entry"
    elif effective:
        source_date, basis = effective, "effective"
    elif transaction:
        source_date, basis = transaction, "transaction"
    else:
        source_date, basis = None, "unknown"

    return {"effectiveAt": effective, "transactionAt": transaction, "rateSourceDate": source_date,
        "rateDateBasis": basis, "sourceRecordedAt": recorded, "sourceUpdatedAt": updated}

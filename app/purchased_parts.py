"""Deterministic latest-actual-purchase resolver for canonical purchased Parts."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import math
from typing import Any


RATE_POLICY = "net merchandise per costing UOM; discount deducted; tax, freight and other charges excluded"
ELIGIBLE_STATUSES = {"posted", "completed"}
ELIGIBLE_TYPES = {"actual_purchase", "invoice", "receipt", "purchase"}


def _ref(value: Any) -> str:
    if value in (None, "", 0):
        return ""
    if isinstance(value, list) and len(value) > 2 and value[0] == "R":
        return str(value[2]) if value[2] not in (None, 0, "0") else ""
    return str(value or "")


def _date(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        result = value
    elif value in (None, ""):
        return None
    elif isinstance(value, (list, tuple)) and len(value) > 1 and value[0] == "D":
        return _date(value[1])
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            result = datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    else:
        try:
            result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _conversion_factor(conversions: list[dict[str, Any]], from_value: str, to_value: str) -> float | None:
    if from_value.strip().casefold() == to_value.strip().casefold():
        return 1.0
    candidates = [row for row in conversions if str(row.get("Status", "")).casefold() == "approved"
                  and str(row.get("FromUOM", "")).casefold() == from_value.casefold()
                  and str(row.get("ToUOM", "")).casefold() == to_value.casefold()]
    if len(candidates) != 1:
        return None
    try:
        value = float(candidates[0].get("Factor"))
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and value > 0 else None


def _currency_factor(conversions: list[dict[str, Any]], source: str, target: str, transaction_at: datetime) -> float | None:
    if source.strip().upper() == target.strip().upper():
        return 1.0
    candidates = []
    for row in conversions:
        rate_date = _date(row.get("RateDate"))
        if (str(row.get("Status", "")).casefold() == "approved"
                and str(row.get("FromCurrency", "")).upper() == source.upper()
                and str(row.get("ToCurrency", "")).upper() == target.upper()
                and rate_date and rate_date <= transaction_at and str(row.get("EvidenceReference", "")).strip()):
            candidates.append((rate_date, row))
    if not candidates:
        return None
    most_recent_date = max(date for date, _ in candidates)
    latest = [row for date, row in candidates if date == most_recent_date]
    if len(latest) != 1:
        return None
    try:
        value = float(latest[0].get("Rate"))
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and value > 0 else None


def resolve_purchased_part_rate(specification: dict[str, Any], records: list[dict[str, Any]], *,
                                unit_conversions: list[dict[str, Any]] | None = None,
                                currency_conversions: list[dict[str, Any]] | None = None,
                                as_of: str | datetime | None = None) -> dict[str, Any]:
    """Select the newest eligible purchase and refuse silent fallback.

    Accepted records are immutable Grist field dictionaries. `as_of` filters on
    transaction date; record-entry time never influences which purchase wins.
    The initial price basis deducts explicit discounts and excludes tax, freight
    and other charges pending a separately approved landed-cost policy.
    """
    units = unit_conversions or []
    currencies = currency_conversions or []
    as_of_date = _date(as_of) if as_of is not None else None
    if as_of not in (None, "") and as_of_date is None:
        return {"status": "review_required", "rate": None, "reason": "The costing as-of date is invalid.", "ratePolicy": RATE_POLICY}
    spec_key = str(specification.get("SpecificationKey") or "")
    spec_id = str(specification.get("recordId") or "")
    spec_part = _ref(specification.get("ProductPart"))
    revision_ref = _ref(specification.get("PartRevision"))
    costing_uom = str(specification.get("CostingUOM") or "").strip()
    costing_currency = str(specification.get("CostingCurrency") or "").strip().upper()
    eligible = []
    all_for_spec = []
    for source in records:
        row = source.get("fields", source)
        if (_ref(row.get("PartPurchaseSpecification")) != spec_id
                and str(row.get("PartPurchaseSpecificationKey") or "") != spec_key):
            continue
        if spec_part and _ref(row.get("ProductPart")) != spec_part:
            continue
        if revision_ref and _ref(row.get("PartRevision")) != revision_ref:
            continue
        eligible_status = str(row.get("Status", "")).casefold() in ELIGIBLE_STATUSES
        eligible_type = str(row.get("RecordType", "")).casefold() in ELIGIBLE_TYPES
        is_correction = (str(row.get("RecordType", "")).casefold() in {"return", "reversal", "void", "correction"}
                         or bool(row.get("ReversesRecord")) or bool(row.get("SupersedesRecord")))
        transaction_at = _date(row.get("TransactionAt"))
        if eligible_status and (eligible_type or is_correction) and not transaction_at:
            return {"status": "review_required", "rate": None, "reason": "An eligible purchase or correction is missing a valid transaction date/time, so effective purchase history cannot be established.", "purchaseRecordId": str(source.get("id") or row.get("PurchaseRecordKey") or ""), "ratePolicy": RATE_POLICY}
        if not transaction_at or (as_of_date and transaction_at > as_of_date):
            continue
        material = {**row, "_record_id": str(source.get("id") or row.get("PurchaseRecordKey") or ""), "_transaction_at": transaction_at}
        all_for_spec.append(material)
        if eligible_status and eligible_type:
            eligible.append(material)
    if not eligible:
        return {"status": "unavailable", "rate": None, "reason": "No eligible actual purchase exists on or before the selected as-of date.", "asOf": as_of_date.isoformat() if as_of_date else None, "ratePolicy": RATE_POLICY}

    # Honor effective reversals/supersessions only as of the calculation date.
    alias_targets: dict[str, set[str]] = {}
    for row in all_for_spec:
        for alias in (row.get("PurchaseRecordKey"), row.get("TransactionKey")):
            if alias:
                alias_targets.setdefault(str(alias), set()).add(row["_record_id"])
        alias_targets.setdefault(row["_record_id"], set()).add(row["_record_id"])
    index = {row["_record_id"]: row for row in eligible}
    invalidated: set[str] = set()
    for row in all_for_spec:
        reversal_type = str(row.get("RecordType", "")).casefold() in {"return", "reversal", "void", "correction"}
        raw_reference = row.get("ReversesRecord") or row.get("SupersedesRecord")
        if not reversal_type and not raw_reference:
            continue
        # Corrections only become effective after they are posted/completed and
        # their transaction date is within the requested as-of window.
        if str(row.get("Status", "")).casefold() not in ELIGIBLE_STATUSES:
            continue
        reference_value = _ref(raw_reference) or str(raw_reference or "").strip()
        targets = alias_targets.get(reference_value, set())
        if len(targets) != 1:
            return {"status": "review_required", "rate": None,
                    "reason": "An effective posted correction does not identify exactly one purchase transaction in the same Part specification.",
                    "purchaseRecordId": row["_record_id"], "ratePolicy": RATE_POLICY}
        reference = next(iter(targets))
        original = index.get(reference)
        if not original:
            return {"status": "review_required", "rate": None,
                    "reason": "An effective posted correction references evidence that is not an eligible purchase for the same Part revision and specification.",
                    "purchaseRecordId": row["_record_id"], "ratePolicy": RATE_POLICY}
        original_part, correction_part = _ref(original.get("ProductPart")), _ref(row.get("ProductPart"))
        if (original_part and correction_part and original_part != correction_part
                or _ref(original.get("PartPurchaseSpecification")) not in (None, _ref(row.get("PartPurchaseSpecification")))
                or _ref(original.get("PartRevision")) not in (None, _ref(row.get("PartRevision")))
                or original.get("_record_id") == row.get("_record_id")):
            return {"status": "review_required", "rate": None,
                    "reason": "Correction and referenced purchase do not share one Part, specification, revision and distinct transaction.",
                    "purchaseRecordId": row["_record_id"], "ratePolicy": RATE_POLICY}
        invalidated.add(reference)
    eligible = [row for row in eligible if row["_record_id"] not in invalidated]
    if not eligible:
        return {"status": "unavailable", "rate": None, "reason": "All eligible purchase evidence is reversed or superseded as of this date.", "asOf": as_of_date.isoformat() if as_of_date else None, "ratePolicy": RATE_POLICY}

    # Duplicate source transaction keys with different evidence are conflicts;
    # exact repeats collapse to one evidence row.
    by_transaction: dict[str, dict[str, Any]] = {}
    for row in eligible:
        transaction_key = str(row.get("TransactionKey") or "").strip()
        line_key = str(row.get("TransactionLineKey") or "").strip()
        vendor = _ref(row.get("Vendor"))
        if not transaction_key or not line_key:
            return {"status": "review_required", "rate": None, "reason": "Actual purchase lacks a stable transaction and line identity.", "purchaseRecordId": row["_record_id"], "ratePolicy": RATE_POLICY}
        key = "|".join((vendor, transaction_key.casefold(), line_key.casefold()))
        evidence = {k: v for k, v in row.items() if not k.startswith("_") and k not in {
            "PurchaseRecordKey", "RecordedAt", "Actor", "Reason", "RequestKey", "RequestFingerprint"}}
        previous = by_transaction.get(key)
        if previous is not None and json.dumps({k: v for k, v in previous.items() if k != "_record_id"}, sort_keys=True, default=str) != json.dumps(evidence, sort_keys=True, default=str):
            return {"status": "conflict", "rate": None, "reason": "The same vendor transaction line has conflicting duplicate evidence.", "transactionKey": transaction_key, "ratePolicy": RATE_POLICY}
        if previous is None:
            by_transaction[key] = {**evidence, "_record_id": row["_record_id"]}
    eligible = []
    for row in by_transaction.values():
        row["_transaction_at"] = _date(row.get("TransactionAt"))
        eligible.append(row)
    latest_at = max(row["_transaction_at"] for row in eligible)
    latest = [row for row in eligible if row["_transaction_at"] == latest_at]

    priced = []
    for row in latest:
        try:
            quantity = float(row.get("Quantity"))
            amount = float(row.get("ExtendedAmount"))
            discount = float(row.get("DiscountAmount") or 0)
        except (TypeError, ValueError):
            quantity, amount, discount = 0.0, 0.0, 0.0
        source_uom = str(row.get("QuantityUOM") or "").strip()
        rate_basis_uom = str(row.get("RateBasisUOM") or source_uom).strip()
        source_currency = str(row.get("Currency") or "").strip().upper()
        uom_factor = _conversion_factor(units, source_uom, costing_uom)
        currency_factor = _currency_factor(currencies, source_currency, costing_currency, row["_transaction_at"])
        if quantity <= 0 or amount < 0 or discount < 0 or discount > amount or not costing_uom or not costing_currency:
            return {"status": "review_required", "rate": None, "reason": "Latest purchase has invalid amount/quantity or the specification lacks a costing UOM/currency.", "purchaseRecordId": row["_record_id"], "ratePolicy": RATE_POLICY}
        if rate_basis_uom.casefold() != source_uom.casefold():
            return {"status": "review_required", "rate": None, "reason": "Latest purchase rate basis does not match its captured purchase quantity UOM.", "purchaseRecordId": row["_record_id"], "ratePolicy": RATE_POLICY}
        if uom_factor is None:
            return {"status": "review_required", "rate": None, "reason": f"Latest purchase UOM {source_uom or '(missing)'} has no approved conversion to {costing_uom}.", "purchaseRecordId": row["_record_id"], "ratePolicy": RATE_POLICY}
        if currency_factor is None:
            return {"status": "review_required", "rate": None, "reason": f"Latest purchase currency {source_currency or '(missing)'} has no approved dated conversion to {costing_currency}.", "purchaseRecordId": row["_record_id"], "ratePolicy": RATE_POLICY}
        normalized_quantity = quantity * uom_factor
        base_unit_rate = (amount * currency_factor) / normalized_quantity
        discount_per_unit = (discount * currency_factor) / normalized_quantity
        unit_rate = base_unit_rate - discount_per_unit
        if not math.isfinite(unit_rate) or normalized_quantity <= 0:
            return {"status": "review_required", "rate": None, "reason": "Latest purchase could not be normalized safely.", "purchaseRecordId": row["_record_id"], "ratePolicy": RATE_POLICY}
        priced.append((unit_rate, row, normalized_quantity, source_uom, source_currency, base_unit_rate, discount_per_unit))
    rates = {round(item[0], 12) for item in priced}
    if len(rates) > 1:
        return {"status": "conflict", "rate": None, "reason": "Different eligible purchases share the latest transaction timestamp; an audited resolution is required.", "transactionAt": latest_at.isoformat(), "purchaseRecordIds": [item[1]["_record_id"] for item in priced], "ratePolicy": RATE_POLICY}
    # Identical equal-time prices have equivalent cost results. Pick a stable
    # evidence key solely to make the immutable snapshot repeatable.
    price, row, normalized_quantity, source_uom, source_currency, base_unit_rate, discount_per_unit = sorted(priced, key=lambda item: item[1]["_record_id"])[0]
    return {"status": "available", "rate": price, "currency": costing_currency, "uom": costing_uom,
            "baseUnitPrice": base_unit_rate, "discountPerUnit": discount_per_unit,
            "purchaseRecordId": row["_record_id"], "vendorId": _ref(row.get("Vendor")),
            "transactionAt": row["_transaction_at"].isoformat(), "purchaseQuantity": float(row.get("Quantity")),
            "purchaseUOM": source_uom, "normalizedQuantity": normalized_quantity,
            "sourceCurrency": source_currency, "ratePolicy": RATE_POLICY,
            "excludedCharges": {"tax": row.get("TaxAmount", 0), "freight": row.get("FreightAmount", 0), "other": row.get("OtherCharges", 0)},
            "asOf": as_of_date.isoformat() if as_of_date else None, "reason": "Latest eligible actual purchase selected by transaction date."}

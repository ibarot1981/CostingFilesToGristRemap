from __future__ import annotations

import unittest

from app.purchased_parts import resolve_purchased_part_rate


SPEC = {
    "recordId": "41",
    "SpecificationKey": "spec:part-1:purchase",
    "PartRevision": 8,
    "CostingUOM": "each",
    "CostingCurrency": "INR",
}


def purchase(record_id: int, vendor: int, transaction: str, when: str, amount: float, **overrides):
    fields = {
        "PurchaseRecordKey": f"purchase:{record_id}",
        "PartPurchaseSpecification": 41,
        "PartPurchaseSpecificationKey": SPEC["SpecificationKey"],
        "PartRevision": 8,
        "Vendor": vendor,
        "TransactionKey": transaction,
        "TransactionLineKey": "1",
        "TransactionAt": when,
        "RecordedAt": "2030-01-01T00:00:00Z",
        "RecordType": "invoice",
        "Status": "posted",
        "Quantity": 1,
        "QuantityUOM": "each",
        "RateBasisUOM": "each",
        "Currency": "INR",
        "ExtendedAmount": amount,
        "DiscountAmount": 0,
        "TaxAmount": 100,
        "FreightAmount": 50,
        "OtherCharges": 25,
    }
    fields.update(overrides)
    return {"id": record_id, "fields": fields}


class PurchasedPartRateTests(unittest.TestCase):
    def resolve(self, records, **kwargs):
        return resolve_purchased_part_rate(SPEC, records, **kwargs)

    def test_latest_actual_purchase_across_vendors_and_historical_as_of(self):
        a = purchase(1, 101, "A-100", "2026-01-01T10:00:00Z", 10)
        b = purchase(2, 202, "B-200", "2026-02-01T10:00:00Z", 14)
        current = self.resolve([a, b])
        historical = self.resolve([a, b], as_of="2026-01-15T00:00:00Z")
        self.assertEqual((current["status"], current["vendorId"], current["rate"]), ("available", "202", 14))
        self.assertEqual((historical["status"], historical["purchaseRecordId"], historical["rate"]), ("available", "1", 10))
        self.assertEqual(SPEC["SpecificationKey"], "spec:part-1:purchase")  # rate changes do not change Part/spec identity

    def test_recorded_later_but_backdated_purchase_does_not_win(self):
        old_date = purchase(1, 101, "A-100", "2026-01-01T10:00:00Z", 10)
        late_entry = purchase(2, 202, "B-200", "2025-12-01T10:00:00Z", 5, RecordedAt="2026-03-01T00:00:00Z")
        result = self.resolve([old_date, late_entry])
        self.assertEqual(result["purchaseRecordId"], "1")
        self.assertEqual(result["rate"], 10)

    def test_grist_datetime_epoch_values_are_supported(self):
        row = purchase(1, 101, "A-100", "2026-01-01T00:00:00Z", 10)
        row["fields"]["TransactionAt"] = 1767225600
        result = self.resolve([row])
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["purchaseRecordId"], "1")

    def test_quotes_drafts_voids_and_returns_do_not_set_or_preserve_rate(self):
        original = purchase(1, 101, "A-100", "2026-01-01T10:00:00Z", 10)
        excluded = [
            purchase(2, 202, "B-quote", "2026-02-01T10:00:00Z", 1, RecordType="quote", Status="posted"),
            purchase(3, 202, "B-draft", "2026-03-01T10:00:00Z", 2, Status="draft"),
            purchase(4, 202, "B-void", "2026-04-01T10:00:00Z", 3, Status="void"),
        ]
        self.assertEqual(self.resolve([original, *excluded])["rate"], 10)
        returned = purchase(5, 101, "A-return", "2026-05-01T10:00:00Z", 10, RecordType="return", ReversesRecord=1)
        self.assertEqual(self.resolve([original, returned])["status"], "unavailable")

    def test_identical_transaction_replay_collapses_and_conflicting_replay_blocks(self):
        first = purchase(1, 101, "INV-1", "2026-01-01T10:00:00Z", 10)
        replay = purchase(2, 101, "INV-1", "2026-01-01T10:00:00Z", 10, PurchaseRecordKey="purchase:different-request")
        self.assertEqual(self.resolve([first, replay])["rate"], 10)
        conflict = purchase(3, 101, "INV-1", "2026-01-01T10:00:00Z", 11)
        self.assertEqual(self.resolve([first, conflict])["status"], "conflict")

    def test_equal_latest_timestamp_with_different_rates_requires_resolution(self):
        rows = [purchase(1, 101, "A-1", "2026-01-01T10:00:00Z", 10),
                purchase(2, 202, "B-1", "2026-01-01T10:00:00Z", 12)]
        self.assertEqual(self.resolve(rows)["status"], "conflict")

    def test_latest_incomparable_uom_blocks_fallback_and_explicit_factor_prices_it(self):
        old = purchase(1, 101, "A-1", "2026-01-01T10:00:00Z", 10)
        latest = purchase(2, 202, "B-1", "2026-02-01T10:00:00Z", 30,
                          Quantity=2, QuantityUOM="pack", RateBasisUOM="pack")
        self.assertEqual(self.resolve([old, latest])["status"], "review_required")
        conversion = [{"Status": "approved", "FromUOM": "pack", "ToUOM": "each", "Factor": 10}]
        result = self.resolve([old, latest], unit_conversions=conversion)
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["rate"], 1.5)
        self.assertEqual(result["normalizedQuantity"], 20)

    def test_currency_requires_explicit_dated_conversion(self):
        row = purchase(1, 101, "A-1", "2026-02-01T10:00:00Z", 2, Currency="USD")
        self.assertEqual(self.resolve([row])["status"], "review_required")
        conversions = [{"Status": "approved", "FromCurrency": "USD", "ToCurrency": "INR", "Rate": 80,
                       "RateDate": "2026-01-15T00:00:00Z", "EvidenceReference": "approved source"}]
        result = self.resolve([row], currency_conversions=conversions)
        self.assertEqual(result["rate"], 160)

    def test_reversal_by_record_key_invalidates_the_original(self):
        original = purchase(1, 101, "A-1", "2026-01-01T10:00:00Z", 10)
        reversal = purchase(2, 101, "A-2", "2026-02-01T10:00:00Z", 10,
                            RecordType="correction", SupersedesRecord="purchase:1")
        self.assertEqual(self.resolve([original, reversal])["status"], "unavailable")

    def test_only_posted_effective_reversals_apply_within_as_of_window(self):
        original = purchase(1, 101, "A-1", "2026-01-01T10:00:00Z", 10)
        draft_return = purchase(2, 101, "A-2", "2026-02-01T10:00:00Z", 10,
                                RecordType="return", Status="draft", ReversesRecord=1)
        voided_return = purchase(3, 101, "A-3", "2026-02-02T10:00:00Z", 10,
                                 RecordType="return", Status="void", ReversesRecord=1)
        future_return = purchase(4, 101, "A-4", "2026-04-01T10:00:00Z", 10,
                                 RecordType="return", Status="posted", ReversesRecord=1)
        self.assertEqual(self.resolve([original, draft_return, voided_return])["rate"], 10)
        self.assertEqual(self.resolve([original, future_return], as_of="2026-03-01T00:00:00Z")["rate"], 10)
        self.assertEqual(self.resolve([original, future_return])["status"], "unavailable")

    def test_effective_correction_with_wrong_reference_is_review_required(self):
        original = purchase(1, 101, "A-1", "2026-01-01T10:00:00Z", 10)
        corrupt = purchase(2, 101, "A-2", "2026-02-01T10:00:00Z", 10,
                           RecordType="correction", Status="completed", SupersedesRecord="missing-row")
        self.assertEqual(self.resolve([original, corrupt])["status"], "review_required")

    def test_missing_history_and_invalid_dates_are_explicit(self):
        self.assertEqual(self.resolve([])["status"], "unavailable")
        row = purchase(1, 101, "A-1", "not-a-date", 10)
        self.assertEqual(self.resolve([row])["status"], "review_required")
        self.assertEqual(self.resolve([], as_of="not-a-date")["status"], "review_required")

    def test_tax_freight_and_other_charges_are_excluded_from_net_merchandise_rate(self):
        row = purchase(1, 101, "A-1", "2026-01-01T10:00:00Z", 100, DiscountAmount=10, Quantity=2)
        result = self.resolve([row])
        self.assertEqual(result["rate"], 45)
        self.assertEqual(result["baseUnitPrice"], 50)
        self.assertEqual(result["discountPerUnit"], 5)
        self.assertEqual(result["excludedCharges"], {"tax": 100, "freight": 50, "other": 25})


if __name__ == "__main__":
    unittest.main()

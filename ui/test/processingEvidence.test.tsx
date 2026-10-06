import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { ProcessingEvidence } from "../src/CostingReviewView";
import type { CostingReview } from "../src/types";
afterEach(cleanup);
it("shows physical gates separately from nonblocking prices and names each blank Part row", () => {
  const evidence: NonNullable<CostingReview['processing_evidence']> = {
    sourceHash: 'hash', observedAt: 'now', requiredSheets: [], missingRequiredSheets: ['Spares Detail'],
    sheetCoverage: [], physicalComparisonAvailable: true,
    physicalComparisons: [{ sheet: 'CNC', row: 4, field: 'qty', previous: 2, current: 3, matches: false, basis: 'exact quantity' }],
    structuralDifferences: [], pricingDifferences: [{}],
    partRowsRequiringReview: [{ sheet: 'Tool Shop Items', row: 5, description: '', blankDescription: true }],
    summary: { present: true, extractedItemCount: 2, optionGroups: ['Motor'], configurationStatus: 'review_required' },
    missingRateEvidence: [{ material: 'Steel', status: 'missing' }], completionAvailable: false,
    pendingPolicies: ['Reviewed Part assignments'],
  };
  render(<ProcessingEvidence evidence={evidence}/>);
  expect(screen.getByText(/Missing required sheets: Spares Detail/)).toBeTruthy();
  expect(screen.getByText(/Price and final cost-total differences.*do not block processing/)).toBeTruthy();
  expect(screen.getByText('Tool Shop Items #5')).toBeTruthy();
  expect(screen.getByText('exact quantity')).toBeTruthy();
  expect(screen.getByText(/Completion is unavailable/)).toBeTruthy();
  expect(screen.queryByRole('button', { name: /processed/i })).toBeNull();
});

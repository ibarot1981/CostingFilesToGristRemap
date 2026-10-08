import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { LiveCostView } from "../src/LiveCostView";
import type { Product } from "../src/types";

const mocks = vi.hoisted(() => ({
  models: vi.fn(), codes: vi.fn(), partsForConfiguration: vi.fn(), costingConfiguration: vi.fn(), saveCostingConfiguration: vi.fn(),
  liveCost: vi.fn(), costSnapshots: vi.fn(), saveCostSnapshot: vi.fn(), costSnapshot: vi.fn(), compareCosts: vi.fn(),
  costPolicy: vi.fn(), saveCostPolicy: vi.fn(), recordProcessRate: vi.fn(),
}));
vi.mock("../src/api", () => ({ api: mocks }));

const products = [{ id: "p", name: "Product" }] as Product[];
const part = { id: "part-1", partNumber: "SM-P-000001", name: "S1K — Bearing", engineeringRevision: "A" };
const live = { status: "complete", generation: "live-generation-1", inputFingerprint: "input-fingerprint-1", currency: "INR", costBasis: "current configured cost",
  totalCost: 100, knownSubtotal: 100, lineCount: 1, availableLineCount: 1, configuration: { id: 10, revisionId: 11, revisionKey: "configuration:1", version: 1 },
  parts: [{ occurrencePath: "selection:slot-a", partNumber: part.partNumber, name: part.name, engineeringRevision: "A" }],
  lines: [{ lineKey: "selection:slot-a|purchase:spec-1", stableOccurrencePath: "selection:slot-a", partOccurrencePath: "selection:slot-a", sourceType: "purchased_part",
    sourceKey: "purchase-spec:spec-1", sourceRecordId: "40", sourceRevisionId: "11", description: "Bearing", costCategory: "purchased_part", quantity: 1,
    quantityUOM: "each", rate: 100, rateUOM: "each", currency: "INR", netCost: 100, status: "available", costPolicy: "actual purchase" }],
  rateEvidence: [{ lineKey: "selection:slot-a|purchase:spec-1", sourceType: "purchased_part", evidenceReference: "purchase:2" }], warnings: [] };

const configured = { status: "configured", configuration: { id: 10, revisionId: 11, revisionKey: "configuration:1", version: 1, currency: "INR",
  selections: [{ selectionIdentity: "slot-a", partId: part.id, partName: part.name, quantity: 1, uom: "each", sourcingRoute: "buy", label: "Main bearing" }] } };
const snapshot = { snapshot: { id: 90, SnapshotKey: "snapshot-a", PublicationStatus: "complete", TotalCost: 100, Currency: "INR", CreatedBy: "reviewer", Notes: "reviewed" },
  parts: [{ id: 91, PartOccurrenceKey: "snapshot-a:selection:slot-a", StableOccurrencePath: "selection:slot-a", ProductPart: 30, PartNumber: part.partNumber,
    FrozenName: part.name, EngineeringRevision: "A", MetadataVersionNumber: 1, EffectiveQuantity: 1, QuantityUOM: "each" }],
  lines: [{ id: 92, SnapshotLineKey: "snapshot-a:line-a", StableOccurrencePath: "selection:slot-a", SourceType: "purchased_part", SourceKey: "purchase-spec:spec-1",
    Description: "Bearing", Quantity: 1, QuantityUOM: "each", Rate: 100, RateUOM: "each", Currency: "INR", NetCost: 100 }],
  rateEvidence: [{ id: 93, SnapshotLine: 92, SourceTable: "PartPurchaseRecord", SourceRecordId: "71", EvidenceReference: "purchase:2" }], lineCount: 1, lineOffset: 0, lineLimit: 100 };

async function selectCode() {
  fireEvent.change(screen.getByRole("combobox", { name: "Product" }), { target: { value: "p" } });
  await screen.findByRole("option", { name: /MODEL/ });
  fireEvent.change(screen.getByRole("combobox", { name: "Product Model" }), { target: { value: "m" } });
  await screen.findByRole("option", { name: /CODE/ });
  fireEvent.change(screen.getByRole("combobox", { name: "Model Code" }), { target: { value: "c" } });
}

beforeEach(() => {
  localStorage.clear();
  Object.values(mocks).forEach(mock => mock.mockReset());
  vi.stubGlobal("crypto", { randomUUID: () => "live-cost-test-key" });
  mocks.models.mockResolvedValue([{ id: "m", model_number: "MODEL", name: "Model" }]);
  mocks.codes.mockResolvedValue({ active: [{ id: "c", code: "CODE", description: "Code" }] });
  mocks.partsForConfiguration.mockResolvedValue({ items: [part] });
  mocks.costingConfiguration.mockResolvedValue(configured);
  mocks.saveCostingConfiguration.mockResolvedValue(configured);
  mocks.liveCost.mockResolvedValue(live);
  mocks.costSnapshots.mockResolvedValue({ items: [{ snapshotKey: "snapshot-a", capturedAt: "2026-10-01T00:00:00Z", label: "October baseline", notes: "reviewed",
    currency: "INR", totalCost: 100, createdBy: "reviewer", partRowCount: 1, lineRowCount: 1 }], total: 1, offset: 0, limit: 25 });
  mocks.saveCostSnapshot.mockResolvedValue(snapshot);
  mocks.costSnapshot.mockResolvedValue(snapshot);
  mocks.compareCosts.mockResolvedValue({ status: "comparable", left: "snapshot-a", right: "Live", leftTotal: 100, rightTotal: 100, currency: "INR",
    difference: 0, percentDifference: 0, quantityImpact: 0, rateImpact: 0, structuralImpact: 0, reconciliationResidual: 0,
    lines: [{ matchKey: "slot-a", classification: ["unchanged"], quantityImpact: 0, rateImpact: 0, structuralImpact: 0, netDelta: 0 }] });
  mocks.costPolicy.mockResolvedValue({ policy: "manual", source: "system default", due: false, snapshotNowAvailable: true, automaticSave: false });
  mocks.saveCostPolicy.mockResolvedValue({});
  mocks.recordProcessRate.mockResolvedValue({});
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it("keeps viewing read-only, saves a deliberate snapshot, then opens only frozen detail", async () => {
  render(<LiveCostView products={products}/>);
  await selectCode();
  await screen.findByText(/Complete current calculation/);
  expect(mocks.liveCost).toHaveBeenCalledWith("c");
  expect(mocks.saveCostSnapshot).not.toHaveBeenCalled();
  expect((screen.getByRole("button", { name: "Save Cost Snapshot Now" }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.change(screen.getByLabelText("Save reason"), { target: { value: "Reviewed estimate" } });
  fireEvent.click(screen.getByRole("button", { name: "Save Cost Snapshot Now" }));
  await waitFor(() => expect(mocks.saveCostSnapshot).toHaveBeenCalledTimes(1));
  const [, payload, requestKey] = mocks.saveCostSnapshot.mock.calls[0];
  expect(requestKey).toBe("live-cost-test-key");
  expect(payload.liveResult.generation).toBe("live-generation-1");
  expect(payload.reason).toBe("Reviewed estimate");
  expect(localStorage.getItem("safari-cost-snapshot-pending:c")).toBeNull();

  const liveCalls = mocks.liveCost.mock.calls.length;
  fireEvent.click(await screen.findByRole("button", { name: "Open frozen detail" }));
  await screen.findByRole("heading", { name: "Frozen snapshot detail" });
  expect(await screen.findByText(/PartPurchaseRecord\/71/)).toBeTruthy();
  expect(mocks.costSnapshot).toHaveBeenCalledWith("snapshot-a", 0, 100);
  expect(mocks.liveCost.mock.calls.length).toBe(liveCalls);
  const afterSavedRefresh = mocks.liveCost.mock.calls.length;
  fireEvent.click(screen.getByRole("button", { name: "Open frozen detail" }));
  await waitFor(() => expect(mocks.costSnapshot).toHaveBeenCalledTimes(2));
  expect(mocks.liveCost.mock.calls.length).toBe(afterSavedRefresh);
});

it("creates an explicit configuration revision before a current cost becomes saveable", async () => {
  mocks.costingConfiguration.mockResolvedValueOnce({ status: "missing", configuration: null });
  mocks.liveCost.mockResolvedValueOnce({ status: "incomplete", generation: "missing", inputFingerprint: "empty", totalCost: null,
    knownSubtotal: 0, parts: [], lines: [], rateEvidence: [], warnings: [{ code: "COST_CONFIGURATION_REQUIRED", message: "Select canonical Parts." }] });
  render(<LiveCostView products={products}/>);
  await selectCode();
  await screen.findByText("No configuration saved");
  fireEvent.click(screen.getByRole("button", { name: "Add Part occurrence" }));
  await screen.findByRole("option", { name: /SM-P-000001/ });
  const partSelect = screen.getByRole("combobox", { name: "Canonical Part" });
  fireEvent.change(partSelect, { target: { value: part.id } });
  fireEvent.change(screen.getByLabelText("Configuration reason"), { target: { value: "Approved per-code selection" } });
  fireEvent.click(screen.getByRole("button", { name: "Save configuration revision" }));
  await waitFor(() => expect(mocks.saveCostingConfiguration).toHaveBeenCalledTimes(1));
  expect(mocks.saveCostingConfiguration.mock.calls[0][0]).toBe("c");
  expect(mocks.saveCostingConfiguration.mock.calls[0][1].selections).toHaveLength(1);
  expect(mocks.saveCostingConfiguration.mock.calls[0][1].selections[0]).toMatchObject({ partId: part.id, quantity: 1, uom: "each" });
});

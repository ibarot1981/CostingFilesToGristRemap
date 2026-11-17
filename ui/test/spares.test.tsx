import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { SparesMasterView } from "../src/SparesMasterView";

const savedSpare = {
  recordId: 31, spareKey: "spare-31", spareCode: "SM-S-000001", description: "Pilot washer", category: "Washer", unit: "EA",
  sourcingType: "unclassified", status: "active", version: 1, createdAt: 1794914044.624793, updatedAt: 1794914044.624793,
  createdBy: "qa", updatedBy: "qa", legacyAliases: ["W-OLD"], sourceCosts: [], sourceEvidence: [], history: [],
};

afterEach(() => { cleanup(); sessionStorage.clear(); vi.unstubAllGlobals(); });

it("creates a canonical spare with retained legacy code and exposes it in the searchable list", async () => {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    if (url.pathname === "/api/spares/sources") return new Response(JSON.stringify({ items: [], total: 0 }), { status: 200 });
    if (url.pathname === "/api/spares" && init?.method === "POST") return new Response(JSON.stringify(savedSpare), { status: 200 });
    if (url.pathname === "/api/spares" && url.searchParams.get("search")) return new Response(JSON.stringify({ items: [savedSpare], total: 1 }), { status: 200 });
    if (url.pathname === "/api/spares") return new Response(JSON.stringify({ items: [], total: 0 }), { status: 200 });
    if (url.pathname === "/api/spares/spare-31") return new Response(JSON.stringify(savedSpare), { status: 200 });
    throw new Error(`Unexpected request ${url.pathname}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  vi.stubGlobal("crypto", { randomUUID: () => "create-spare-request" });

  render(<SparesMasterView/>);
  fireEvent.click(await screen.findByRole("button", { name: /New spare/ }));
  fireEvent.change(screen.getByLabelText("Description"), { target: { value: "Pilot washer" } });
  fireEvent.change(screen.getByLabelText(/Legacy Item Code aliases/), { target: { value: "W-OLD" } });
  fireEvent.change(screen.getByLabelText("Reason for change"), { target: { value: "Reviewed pilot identity" } });
  fireEvent.click(screen.getByRole("button", { name: "Create in Grist" }));

  expect(await screen.findByText("SM-S-000001")).toBeTruthy();
  expect(await screen.findByText("W-OLD")).toBeTruthy();
  expect((await screen.findAllByText(/17 Nov 2026/)).length).toBe(2);
  expect(screen.queryByText(/1794914044/)).toBeNull();
  const write = fetchMock.mock.calls.find(([input, init]) => new URL(String(input), "http://localhost").pathname === "/api/spares" && init?.method === "POST");
  expect(write?.[1]?.headers).toMatchObject({ "Idempotency-Key": "create-spare-request" });
  const payload = JSON.parse(String(write?.[1]?.body));
  expect(payload).toMatchObject({ description: "Pilot washer", legacyAliases: ["W-OLD"], sourcingType: "unclassified" });

  fireEvent.change(screen.getByLabelText(/Search code, legacy code/), { target: { value: "W-OLD" } });
  await waitFor(() => expect(fetchMock.mock.calls.some(([input]) => new URL(String(input), "http://localhost").searchParams.get("search") === "W-OLD")).toBe(true));
});

it("requires preview acknowledgement and applies the exact reviewed source fingerprint", async () => {
  const preview = {
    previewFingerprint: "preview-1", sourceHashes: { associatedWorkbook: "workbook-hash", templateMaster: "template-hash", associationContext: "association-hash" },
    sourceLabels: { associatedWorkbook: "Pilot.ods", templateMaster: "Template/Spares List - Master.ods" },
    counts: { creates: 1, updates: 0, noops: 0, conflicts: 0, unresolvedIdentities: 1, missingCostDates: 1, futureCostDates: 0, compositionEvidence: 1, unresolvedEvidenceRows: 1 },
    identityRows: [{ legacyCode: "W-NEW", sourceRow: 8, action: "create", description: "New washer", sourcingTypeCandidate: null, resolution: "new_unique_source_identity" }],
    identityOffset: 0, identityLimit: 100, identityTotal: 1, evidenceOffset: 0, evidenceLimit: 50,
    evidenceTotals: { sourceEvidence: 1, costEvidence: 1, compositionEvidence: 1 },
    sourceEvidenceRows: [{ key: "src-1", evidenceType: "Source metadata", sourceFile: "Pilot.ods", sheetName: "SparesDB", sourceRow: 8, sourceCell: "A8", legacyCode: "W-NEW", resolutionStatus: "resolved_unique_source_identity" }],
    costEvidenceRows: [{ key: "cost-1", costKind: "source_purchase_price", sourceFile: "Pilot.ods", sheetName: "SparesDB", sourceRow: 8, sourceCell: "E8", sourceDateISO: "", sourceDateMeaning: "source date missing", dateBasis: "missing", formula: "=1+2", cachedValue: 3 }],
    compositionEvidenceRows: [{ key: "composition-1", sourceKind: "SparesDetail", sourceFile: "Pilot.ods", sheetName: "Spares Detail", sourceRow: 12, sourceIdentity: "W-NEW", sourceLineCode: "MCL-5", manufacturingLineCode: "MCL-5", nestedLegacySpareCode: "W-OLD", nestedReferenceStatus: "pending" }],
    reviewRequired: true, readOnly: true,
  };
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    if (url.pathname === "/api/spares/sources") return new Response(JSON.stringify({ items: [{ path: "Pilot.ods", fileName: "Pilot.ods", product: "Safari", model: "S1" }], total: 1 }), { status: 200 });
    if (url.pathname === "/api/spares/import-preview") return new Response(JSON.stringify(preview), { status: 200 });
    if (url.pathname === "/api/spares/import-apply") return new Response(JSON.stringify({ status: "complete", counts: preview.counts, requestKey: "import-request-1" }), { status: 200 });
    if (url.pathname === "/api/spares") return new Response(JSON.stringify({ items: [], total: 0 }), { status: 200 });
    throw new Error(`Unexpected request ${url.pathname}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  vi.stubGlobal("crypto", { randomUUID: () => "import-request-1" });

  render(<SparesMasterView/>);
  await waitFor(() => expect((screen.getByLabelText("Associated costing workbook") as HTMLSelectElement).value).toBe("Pilot.ods"));
  fireEvent.click(await screen.findByRole("button", { name: "Build read-only preview" }));
  await waitFor(() => expect(fetchMock.mock.calls.some(([input]) => new URL(String(input), "http://localhost").pathname === "/api/spares/import-preview")).toBe(true));
  if (screen.queryByRole("alert")) throw new Error(`Preview request failed: ${screen.queryByRole("alert")?.textContent}`);
  expect(await screen.findByText(/Preview preview-1/)).toBeTruthy();
  expect(screen.getByText(/Formula: =1\+2/)).toBeTruthy();
  expect(screen.getByText(/Manufacturing line: MCL-5/)).toBeTruthy();
  const apply = screen.getByRole("button", { name: "Apply reviewed import in Grist" }) as HTMLButtonElement;
  expect(apply.disabled).toBe(true);
  fireEvent.click(screen.getByLabelText(/I reviewed the identity decisions/));
  fireEvent.click(apply);

  expect(await screen.findByText(/Reviewed source import completed/)).toBeTruthy();
  const write = fetchMock.mock.calls.find(([input]) => new URL(String(input), "http://localhost").pathname === "/api/spares/import-apply");
  expect(write?.[1]?.headers).toMatchObject({ "Idempotency-Key": "import-request-1" });
  expect(JSON.parse(String(write?.[1]?.body))).toMatchObject({ path: "Pilot.ods", previewFingerprint: "preview-1" });
});

it("retains the exact import key while Grist reports a matching request is still applying", async () => {
  const sourceHashes = { associatedWorkbook: "workbook-hash", templateMaster: "template-hash", associationContext: "association-hash" };
  const preview = {
    previewFingerprint: "preview-retry", sourceHashes,
    sourceLabels: { associatedWorkbook: "Pilot.ods", templateMaster: "Template/Spares List - Master.ods" },
    counts: { creates: 0, updates: 0, noops: 1, conflicts: 0, unresolvedIdentities: 0, sourceEvidence: 0, costEvidence: 0, compositionEvidence: 0 },
    identityRows: [], identityOffset: 0, identityLimit: 100, identityTotal: 0,
    sourceEvidenceRows: [], costEvidenceRows: [], compositionEvidenceRows: [], evidenceOffset: 0, evidenceLimit: 50,
    evidenceTotals: { sourceEvidence: 0, costEvidence: 0, compositionEvidence: 0 }, reviewRequired: true, readOnly: true,
  };
  let applyCalls = 0;
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    if (url.pathname === "/api/spares/sources") return new Response(JSON.stringify({ items: [{ path: "Pilot.ods", fileName: "Pilot.ods", product: "Safari", model: "S1" }], total: 1 }), { status: 200 });
    if (url.pathname === "/api/spares/import-preview") return new Response(JSON.stringify(preview), { status: 200 });
    if (url.pathname === "/api/spares/import-apply") {
      applyCalls += 1;
      if (applyCalls === 1) return new Response(JSON.stringify({ detail: { code: "SPARE_REQUEST_CONFLICT", message: "A partial retry needs reconciliation." } }), { status: 409 });
      return new Response(JSON.stringify({ status: "complete", requestKey: "import-retry-key", idempotent: true }), { status: 200 });
    }
    if (url.pathname === "/api/spares/import-status/import-retry-key") return new Response(JSON.stringify({ status: "applying", previewFingerprint: "preview-retry", sourceHashes, progress: { stage: "identities" } }), { status: 200 });
    if (url.pathname === "/api/spares") return new Response(JSON.stringify({ items: [], total: 0 }), { status: 200 });
    throw new Error(`Unexpected request ${url.pathname}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  vi.stubGlobal("crypto", { randomUUID: () => "import-retry-key" });

  render(<SparesMasterView/>);
  fireEvent.click(await screen.findByRole("button", { name: "Build read-only preview" }));
  expect(await screen.findByText(/Preview preview-retry/)).toBeTruthy();
  fireEvent.click(screen.getByLabelText(/I reviewed the identity decisions/));
  fireEvent.click(screen.getByRole("button", { name: "Apply reviewed import in Grist" }));

  expect(await screen.findByText(/Status: applying/)).toBeTruthy();
  const pending = JSON.parse(sessionStorage.getItem("spares:import-write-attempt") || "null");
  expect(pending).toMatchObject({ key: "import-retry-key", previewFingerprint: "preview-retry", sourceHashes });
  fireEvent.click(screen.getByRole("button", { name: "Resume same reviewed import" }));

  expect(await screen.findByText(/Reviewed source import completed/)).toBeTruthy();
  const writes = fetchMock.mock.calls.filter(([input]) => new URL(String(input), "http://localhost").pathname === "/api/spares/import-apply");
  expect(writes).toHaveLength(2);
  expect(writes.map(([, init]) => (init?.headers as Record<string, string>)["Idempotency-Key"])).toEqual(["import-retry-key", "import-retry-key"]);
  expect(sessionStorage.getItem("spares:import-write-attempt")).toBeNull();
});

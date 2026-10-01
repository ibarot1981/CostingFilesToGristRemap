import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ReconciliationView } from "../src/ReconciliationView";
import type { Product, ReconciliationIssue } from "../src/types";

const mocks = vi.hoisted(() => ({
  reconciliationIssues: vi.fn(), directoryMappings: vi.fn(), reconciliationIssue: vi.fn(), issueAction: vi.fn(),
  sourceRevisionPreview: vi.fn(), sourceRevisionApply: vi.fn(), identityCleanupPreview: vi.fn(), identityCleanupApply: vi.fn(),
  proposeDirectoryMapping: vi.fn(), directoryMappingAction: vi.fn(), models: vi.fn(), reconciliationScan: vi.fn(),
}));
vi.mock("../src/api", () => ({ api: mocks }));
afterEach(() => { cleanup(); vi.clearAllMocks(); });

const issue: ReconciliationIssue = {
  id: "issue:1", fingerprint: "fingerprint", issue_type: "changed_file", severity: "high", message: "Source workbook hash changed",
  source_file: "S1KHF/Local/model.ods", source_path: "S1KHF/Local/model.ods", entity_type: "CostingFile", entity_id: "file:model",
  costing_file_id: "file:model", source_cell: "", detected_facts: { stored: { sha256: "old" }, current: { sha256: "new" } },
  proposed_resolution: { action: "accept_source_revision" }, status: "open", assigned_owner: null,
  first_seen_at: "2026-09-23T00:00:00+00:00", last_seen_at: "2026-09-23T00:00:00+00:00", version: 2,
  resolution_action: "", resolution_reason: "",
};

describe("Reconciliation review workflow", () => {
  it("supports keyboard reachable issue selection and required-reason lifecycle actions", async () => {
    mocks.reconciliationIssues.mockResolvedValue({ total: 1, items: [issue] });
    mocks.directoryMappings.mockResolvedValue({ total: 0, items: [] });
    mocks.reconciliationIssue.mockResolvedValue({ issue, observations: [], associationHistory: [], relatedFileHistories: [], auditTrail: [] });
    mocks.issueAction.mockResolvedValue({ issue: { ...issue, status: "deferred", version: 3 } });
    mocks.reconciliationScan.mockResolvedValue({ detected: 0, materialized: 0 });

    render(<ReconciliationView products={[{ id: "p1", name: "Safari" } as Product]}/>);
    const row = await screen.findByRole("button", { name: /Source workbook hash changed/ });
    expect(row.tagName).toBe("BUTTON");
    row.focus();
    expect(document.activeElement).toBe(row);
    fireEvent.keyDown(row, { key: "Enter" });
    const detailsHeading = await screen.findByRole("heading", { name: "Issue details" });
    await waitFor(() => expect(document.activeElement).toBe(detailsHeading));

    const defer = screen.getByRole("button", { name: "Defer" });
    fireEvent.click(defer);
    expect(await screen.findByText("Enter a reason before changing issue status.")).toBeTruthy();
    fireEvent.change(screen.getByPlaceholderText("Required for status changes"), { target: { value: "Waiting for owner review" } });
    fireEvent.click(defer);
    await waitFor(() => expect(mocks.issueAction).toHaveBeenCalledWith("issue:1", "defer", expect.objectContaining({ expectedVersion: 2, reason: "Waiting for owner review" }), expect.any(String)));
  });

  it("shows the active code owner history and opens it for a guided supersede review", async () => {
    const duplicate: ReconciliationIssue = { ...issue, id: "issue:duplicate", issue_type: "duplicate_code_ownership", message: "CODE-A has two active owners", entity_type: "ProductModelCode", entity_id: "code-a" };
    const onOpenFile = vi.fn();
    mocks.reconciliationIssues.mockResolvedValue({ total: 1, items: [duplicate] });
    mocks.directoryMappings.mockResolvedValue({ total: 0, items: [] });
    mocks.reconciliationIssue.mockResolvedValue({
      issue: duplicate, observations: [], associationHistory: [], auditTrail: [],
      relatedFileHistories: [{ fileId: "file:owner.ods", relativePath: "S1KHF/Local/owner.ods", history: [{ association: { active: true, actor: "Irshad", reason: "Pilot mapping", created_at: "2026-09-20" }, codes: [{ code: "CODE-A" }] }] }],
    });

    render(<ReconciliationView products={[{ id: "p1", name: "Safari" } as Product]} onOpenFile={onOpenFile}/>);
    fireEvent.click(await screen.findByRole("button", { name: /CODE-A has two active owners/ }));
    expect(await screen.findByText(/active owner and its exact code\/audit history/)).toBeTruthy();
    expect(screen.getByText(/Current owner · 2026-09-20/)).toBeTruthy();
    expect(screen.getByText(/codes: CODE-A/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Review current owner in Costing Explorer" }));
    expect(onOpenFile).toHaveBeenCalledWith("S1KHF/Local/owner.ods");
  });
});

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ModelCodeExplorer } from "../src/ModelCodeExplorer";
const mocks = vi.hoisted(() => ({ models: vi.fn(), codes: vi.fn(), codeRecords: vi.fn(), costingReview: vi.fn() }));
vi.mock("../src/api", () => ({ api: mocks }));
vi.mock("../src/CostingReviewView", () => ({ CostingReviewView: ({ selectedPath }: { selectedPath: string }) => <p>Source reconciliation: {selectedPath}</p> }));
afterEach(() => { cleanup(); vi.clearAllMocks(); });
beforeEach(() => {
  mocks.models.mockResolvedValue([{ id: "m", model_number: "MODEL", name: "" }]);
  mocks.codes.mockResolvedValue({ active: [{ id: "c", code: "CODE" }, { id: "other", code: "OTHER" }] });
});
const stored = (code = "CODE") => ({ status: "stored", code: { code }, source: { name: "pilot.ods", relative_path: "missing/pilot.ods" }, readAt: "now", baseline: { snapshotKey: "accepted", sourceHash: "source-hash", acceptedAt: "then" }, total: 1, items: [{ observation: { sheet: "CNC", row: 4, part_display_name: "Plate" }, mapping: { status: "mapped" }, master: { process_type: "cnc" }, detail: { quantity: 2 } }] });
async function selectCode() {
  fireEvent.change(screen.getByRole("combobox", { name: "Product", exact: true }), { target: { value: "p" } });
  await screen.findByRole("option", { name: "MODEL" });
  fireEvent.change(screen.getByRole("combobox", { name: "Product Model", exact: true }), { target: { value: "m" } });
  await screen.findByRole("option", { name: "CODE" });
  fireEvent.change(screen.getByRole("combobox", { name: "Model Code", exact: true }), { target: { value: "c" } });
}
describe("Model Code stored navigation", () => {
  it("reads Grist records and exposes source reconciliation only on demand", async () => {
    mocks.codeRecords.mockResolvedValue(stored());
    const open = vi.fn();
    render(<ModelCodeExplorer products={[{ id: "p", name: "Product" }]} onOpenFile={open} />);
    await selectCode();
    expect(await screen.findByText(/Stored Grist records/)).toBeTruthy();
    expect(screen.getByText(/current ODS revision has not been checked/)).toBeTruthy();
    expect(mocks.costingReview).not.toHaveBeenCalled();
    expect(screen.queryByText(/Source reconciliation:/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Open source reconciliation" }));
    expect(screen.getByText("Source reconciliation: missing/pilot.ods")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Open source file" }));
    expect(open).toHaveBeenCalledWith("missing/pilot.ods");
  });
  it("discards a late response from the previous code", async () => {
    let finish!: (data: unknown) => void;
    mocks.codeRecords.mockImplementation((id: string) => id === "c" ? new Promise(resolve => { finish = resolve; }) : Promise.resolve(stored("OTHER")));
    render(<ModelCodeExplorer products={[{ id: "p", name: "Product" }]} onOpenFile={() => {}} />);
    await selectCode();
    fireEvent.change(screen.getByRole("combobox", { name: "Model Code", exact: true }), { target: { value: "other" } });
    await screen.findByText(/Shared source-file evidence for OTHER/);
    finish(stored());
    await waitFor(() => expect(screen.queryByText(/Shared source-file evidence for CODE/)).toBeNull());
  });
});

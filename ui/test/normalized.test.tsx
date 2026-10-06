import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { NormalizedView } from "../src/NormalizedView";

const mocks = vi.hoisted(() => ({ normalized: vi.fn() }));
vi.mock("../src/api", () => ({ api: mocks }));
afterEach(() => { cleanup(); vi.clearAllMocks(); });

describe("S1KHF normalized inspection", () => {
  it("labels an unaccepted projection and filters historical rows", async () => {
    mocks.normalized.mockResolvedValue({
      baseline: { accepted: false, snapshotKey: "local-unaccepted:h", sourceMatchesBaseline: null },
      persistence: "projection_only", total: 1,
      reconciliation: { unresolved_mappings: 1, differences: [] }, exceptions: [{ code: "DUPLICATE_COMPOSITE" }],
      items: [{ mapping: { status: "ambiguous", master_key: "line:1" },
        observation: { sheet: "CNC Cut List", row: 23, status: "historical", part_display_name: "Plate", cells: { qty: { cell: "E23", formula: "of:=1" } }, cached_cost: null },
        master: { process_type: "cnc", key: "line:1" }, revision: { key: "rev:1", previous_key: null },
        detail: { material_display_name: "Steel", activity_kind: null }, audit: [{ actor: "system" }] }],
    });
    render(<NormalizedView selectedPath="S1KHF/Local/model.ods" />);
    expect(await screen.findByText(/Local source projection, awaiting Safari baseline/)).toBeTruthy();
    fireEvent.change(screen.getByRole("textbox", { name: "status" }), { target: { value: "historical" } });
    await waitFor(() => expect(mocks.normalized).toHaveBeenLastCalledWith("S1KHF/Local/model.ods", expect.objectContaining({ status: "historical" })));
    fireEvent.click(screen.getByRole("button", { name: "CNC Cut List #23" }));
    expect(screen.getByText(/of:=1/)).toBeTruthy();
  });
});

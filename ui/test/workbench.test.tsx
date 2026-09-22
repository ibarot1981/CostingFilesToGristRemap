import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App, AssociationPanel, MappedView, PreviewPanel } from "../src/App";
import type { ComponentProps } from "react";

const apiMocks = vi.hoisted(() => ({
  summary: vi.fn(), products: vi.fn(), tree: vi.fn(), files: vi.fn(), associations: vi.fn(), models: vi.fn(),
  codes: vi.fn(), inspect: vi.fn(), preview: vi.fn(), validateAssociation: vi.fn(),
  saveAssociation: vi.fn(), mappedFiles: vi.fn(),
}));

vi.mock("../src/api", () => ({ api: apiMocks }));

type PanelProps = ComponentProps<typeof AssociationPanel>;

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

beforeEach(() => {
  Object.values(apiMocks).forEach((mock) => mock.mockReset());
  apiMocks.summary.mockResolvedValue({ root: "C:/costing", file_count: 1 });
  apiMocks.products.mockResolvedValue([{ id: "product-1", name: "Mini Crane" }]);
  apiMocks.tree.mockResolvedValue({ items: [{ id: "pilot.ods", name: "pilot.ods", type: "file", relative_path: "pilot.ods", extension: ".ods" }] });
  apiMocks.files.mockResolvedValue({ total: 0, items: [], source: "filesystem" });
  apiMocks.associations.mockResolvedValue({ schemaAvailable: false, writeEnabled: false, adapter: "in-memory", items: [] });
  apiMocks.models.mockResolvedValue([{ id: "model-1", product_id: "product-1", model_number: "S1KHF", name: "", codes: [] }]);
  apiMocks.codes.mockResolvedValue({ active: [{ id: "code-1", code: "S1KHFELP", description: "Local electric" }], legacy: [], conflicts: [] });
  apiMocks.inspect.mockResolvedValue({ id: "pilot.ods", name: "pilot.ods", type: "file", relative_path: "pilot.ods", extension: ".ods", content_hash: "preview-hash" });
  apiMocks.preview.mockResolvedValue({ path: "pilot.ods", sheets: ["Summary"], sheet: "Summary", startRow: 1, totalRows: 1, totalColumns: 1, truncatedColumns: false, rows: [["Cost"]], cells: [[{ value: "Cost", kind: "value" }]] });
  apiMocks.validateAssociation.mockResolvedValue({ valid: true, errors: [], warnings: [], proposal: { expectedVersion: 3, expectedHash: "preview-hash" } });
  apiMocks.saveAssociation.mockRejectedValueOnce(new Error("temporary response timeout")).mockResolvedValue({ idempotent: true });
});

function panelProps(): PanelProps {
  return {
    selectedFile: { id: "pilot.ods", name: "pilot.ods", type: "file", relative_path: "pilot.ods", extension: ".ods" },
    previewing: false,
    products: [{ id: "product-1", name: "Mini Crane" }],
    models: [{ id: "model-1", product_id: "product-1", model_number: "S1KHF", name: "", codes: [] }],
    codes: [{ id: "code-1", code: "S1KHFELP", description: "Local electric" }],
    productId: "product-1",
    modelId: "model-1",
    selectedCodes: ["code-1"],
    reason: "",
    supersede: false,
    validation: { valid: false, errors: [], warnings: [] },
    requestKeyReady: false,
    busy: false,
    setReason: vi.fn(),
    setSupersede: vi.fn(),
    loadModels: vi.fn(),
    loadCodes: vi.fn(),
    toggleCode: vi.fn(),
    validate: vi.fn(),
    save: vi.fn(),
  };
}

describe("Costing Explorer association workbench", () => {
  it("keeps a floating horizontal scrollbar and supports full-page preview", () => {
    render(<PreviewPanel
      selectedFile={{ id: "pilot.ods", name: "pilot.ods", type: "file", relative_path: "pilot.ods", extension: ".ods" }}
      preview={{ path: "pilot.ods", sheets: ["Summary"], sheet: "Summary", startRow: 1, totalRows: 2, totalColumns: 4, truncatedColumns: true, rows: [["A", "B", "C", "D"], ["1", "2", "3", "4"]], cells: [] }}
      previewing={false}
      chooseSheet={vi.fn()}
      summary={{ root: "C:/costing", file_count: 1 }}
    />);

    expect(screen.getByRole("region", { name: "Workbook horizontal scroll" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Expand workbook preview" }));
    expect(screen.getByRole("dialog", { name: "Expanded workbook preview" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Exit full-page workbook preview" })).toBeTruthy();

    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.getByRole("button", { name: "Expand workbook preview" })).toBeTruthy();
  });

  it("searches nested ODS paths and renders their folder ancestry", async () => {
    apiMocks.files.mockResolvedValue({ total: 1, source: "filesystem", items: [{ id: "S1KHF/Local/deep-only.ods", name: "deep-only.ods", type: "file", relative_path: "S1KHF/Local/deep-only.ods", extension: ".ods", candidate_classification: "costing_candidate" }] });
    render(<App />);

    fireEvent.change(await screen.findByRole("textbox", { name: "Search ODS costing files" }), { target: { value: "deep-only" } });
    expect(await screen.findByRole("button", { name: /deep-only\.ods/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: "S1KHF" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Local" })).toBeTruthy();
    expect(screen.getByRole("status").textContent).toContain("1 ODS file match");
    expect(apiMocks.files).toHaveBeenCalledWith("deep-only");
  });

  it("shows and filters files with conflicting active code owners", async () => {
    apiMocks.tree.mockResolvedValue({ items: [
      { id: "conflict.ods", name: "conflict.ods", type: "file", relative_path: "conflict.ods", extension: ".ods", mapping_status: "conflict" },
      { id: "mapped.ods", name: "mapped.ods", type: "file", relative_path: "mapped.ods", extension: ".ods", mapping_status: "mapped" },
    ] });
    render(<App />);

    await screen.findByRole("button", { name: /conflict\.ods/ });
    expect(document.querySelector(".status-badge.conflict")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Filter by mapping status"), { target: { value: "conflict" } });

    expect(screen.getByRole("button", { name: /conflict\.ods/ })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /mapped\.ods/ })).toBeNull();
  });

  it("gates Save on successful validation and wires cascading choices", () => {
    const props = panelProps();
    const { rerender } = render(<AssociationPanel {...props} />);

    const validate = screen.getByRole("button", { name: "Validate" }) as HTMLButtonElement;
    const save = screen.getByRole("button", { name: "Save association" }) as HTMLButtonElement;
    expect(validate.disabled).toBe(false);
    expect(save.disabled).toBe(true);

    fireEvent.change(screen.getByLabelText("Product"), { target: { value: "product-1" } });
    fireEvent.change(screen.getByLabelText("Product Model"), { target: { value: "model-1" } });
    fireEvent.click(screen.getByRole("checkbox", { name: /S1KHFELP/ }));
    expect(props.loadModels).toHaveBeenCalledWith("product-1");
    expect(props.loadCodes).toHaveBeenCalledWith("model-1");
    expect(props.toggleCode).toHaveBeenCalledWith("code-1");

    rerender(<AssociationPanel {...props} validation={{ valid: true, errors: [], warnings: [] }} requestKeyReady={true} />);
    const validatedSave = screen.getByRole("button", { name: "Save association" }) as HTMLButtonElement;
    expect(validatedSave.disabled).toBe(false);
    fireEvent.click(validatedSave);
    expect(props.save).toHaveBeenCalledOnce();
  });

  it("sends validation concurrency tokens and reuses its request key after a timeout", async () => {
    vi.stubGlobal("crypto", { randomUUID: () => "stable-request-key" });
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: /pilot\.ods/ }));
    fireEvent.change(await screen.findByLabelText("Product"), { target: { value: "product-1" } });
    fireEvent.change(await screen.findByLabelText("Product Model"), { target: { value: "model-1" } });
    fireEvent.click(await screen.findByRole("checkbox", { name: /S1KHFELP/ }));
    fireEvent.click(screen.getByRole("button", { name: "Validate" }));

    await vi.waitFor(() => expect(apiMocks.validateAssociation).toHaveBeenCalledOnce());
    expect(apiMocks.validateAssociation.mock.calls[0][0].expectedHash).toBe("preview-hash");

    const saveButton = await screen.findByRole("button", { name: "Save association" });
    await vi.waitFor(() => expect((saveButton as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(saveButton);
    await screen.findByText("temporary response timeout");
    expect((saveButton as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(saveButton);

    await screen.findByText("Association saved and queued for read-only processing.");
    expect(apiMocks.saveAssociation).toHaveBeenCalledTimes(2);
    for (const [payload, requestKey] of apiMocks.saveAssociation.mock.calls) {
      expect(payload.expectedVersion).toBe(3);
      expect(payload.expectedHash).toBe("preview-hash");
      expect(requestKey).toBe("stable-request-key");
    }
  });
});

describe("Mapped Files review", () => {
  it("opens ownership and association history from the keyboard", () => {
    const row = {
      file: { id: "file:pilot.ods", name: "pilot.ods", relative_path: "S1KHF/pilot.ods", modified_at: "2026-09-18T00:00:00+00:00" },
      product: { name: "Mini Crane" },
      model: { model_number: "S1KHF" },
      codes: [{ code: "S1KHFELP" }],
      association: { id: "association-1", version: 1 },
      history: [{ association: { active: true, created_at: "2026-09-18T12:00:00+00:00", actor: "tester", reason: "pilot mapping" }, codes: [{ code: "S1KHFELP" }], auditEvents: [{ id: "event-1", event_type: "file_association_saved", actor: "tester", occurred_at: "2026-09-18T12:00:00+00:00", reason: "pilot mapping" }] }],
      processingBatch: { id: "batch-1", source_file: "S1KHF/pilot.ods", parser_version: "association-0.1", started_at: "2026-09-18T12:00:01+00:00", status: "queued", outcome: "read-only processing queued" },
      reviewStatus: "mapped" as const,
      issues: [],
      lastObservedAt: "2026-09-18T12:01:00+00:00",
      lastObservedChange: { modifiedAt: "2026-09-18T00:00:00+00:00", observedAt: "2026-09-18T12:01:00+00:00", source: "observation" },
    };
    const onSelect = vi.fn();
    render(<MappedView rows={[row]} onSelect={onSelect} />);

    const fileRow = screen.getByRole("row", { name: "Open details for S1KHF/pilot.ods" });
    fireEvent.keyDown(fileRow, { key: "Enter" });

    expect(screen.getByText("Association history")).toBeTruthy();
    expect(screen.getAllByText(/pilot mapping/)).toHaveLength(2);
    expect(screen.getByText("queued")).toBeTruthy();
    expect(screen.getByText("Codes: S1KHFELP")).toBeTruthy();
    expect(screen.getByText(/Audit: file_association_saved/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Close details" }));
    expect(screen.queryByText("Association history")).toBeNull();
  });
});

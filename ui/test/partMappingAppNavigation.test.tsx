import {cleanup, fireEvent, render, screen, waitFor, within} from "@testing-library/react";
import {afterEach, beforeEach, expect, it, vi} from "vitest";
import {App} from "../src/App";

const apiMocks = vi.hoisted(() => ({
  summary: vi.fn(), products: vi.fn(), tree: vi.fn(), associations: vi.fn(),
  fileAssociation: vi.fn(), inspect: vi.fn(), preview: vi.fn(), processingState: vi.fn(),
  partMappings: vi.fn(), searchParts: vi.fn(), savePartMappings: vi.fn(), partBaselineReview: vi.fn(),
  partScopeTargets: vi.fn(), parts: vi.fn(), partNamePreview: vi.fn(), createManagedPart: vi.fn(), partDetails: vi.fn(),
}));
vi.mock("../src/api", () => ({api: apiMocks}));

const existingParts = [1, 2, 3].map(number => ({
  id: `part-${number}`, partNumber: `SM-P-${String(number).padStart(6, "0")}`,
  name: `Safari Manufacturing — Existing ${number}`, description: `Existing ${number}`, variant: "Standard",
  engineeringRevision: "A", selectable: true, duplicateName: false, outOfScopeCodes: [],
}));
const newPart = {
  id: "part-created", partNumber: "SM-P-000004", name: "Safari Manufacturing — Mapping Return",
  description: "Mapping Return", variant: "Standard", scope: "global", scopeTargetId: "global",
  scopeTarget: "Safari Manufacturing", engineeringRevision: "A", metadataVersion: 1,
  status: "active", aliases: [], legacy: false, selectable: true, duplicateName: false, outOfScopeCodes: [],
};

function makeGroup(index: number, overrides: Record<string, unknown> = {}) {
  const key = `mapping-${index}`;
  const sheet = "5. Material Cut List Price";
  return {
    key, mappingPolicyVersion: "part-source-labels-v2", evidenceFingerprint: `evidence-${key}`, sheet,
    labelField: "product_part_name", description: `Item ${index}`, blankDescription: false,
    rows: [{sheet, row: index + 10, fields: {material_to_cut: "MS Plate", dimension_to_cut_mm: "3 x 25", qty: "2", product_part_name: `Item ${index}`},
      sourceHeaders: {material_to_cut: "Material to Cut", dimension_to_cut_mm: "Dimension to Cut (mm)", qty: "Quantity Nos", product_part_name: "Plate Part to Cut"},
      sourceHeaderCells: {material_to_cut: "B8"}, sourceCells: {material_to_cut: {cell: `B${index + 10}`}}, headerRow: 8,
      availableFields: ["material_to_cut", "dimension_to_cut_mm", "qty", "product_part_name"]}],
    part: null, reviewed: false, version: 0, ...overrides,
  };
}

function makeDetail(groups = [1, 2, 3, 4].map(index => makeGroup(index)), overrides: Record<string, unknown> = {}) {
  return {
    sourceHash: "workbook-hash", associationKey: "association-key", associationVersion: 3, version: 0,
    schemaAvailable: true, legacyHistoryAvailable: true, mappingPolicyVersion: "part-source-labels-v2",
    sourceSheets: {"5. Material Cut List Price": {present: true, status: "ok", labelField: "product_part_name",
      labelHeader: "Machine Piece Description", labelHeaderCell: "A8", ambiguousLabelHeaders: [], headerRow: 8}},
    parts: [], groups, unresolvedGroups: groups.filter((group: any) => !group.reviewed).length, history: [], ...overrides,
  };
}

let persistedDetail: ReturnType<typeof makeDetail>;

beforeEach(() => {
  Object.values(apiMocks).forEach(mock => mock.mockReset());
  vi.stubGlobal("crypto", {randomUUID: () => "app-navigation-request"});
  sessionStorage.clear();
  window.history.replaceState(null, "", "#files");
  persistedDetail = makeDetail();

  apiMocks.summary.mockResolvedValue({root: "C:/costing", file_count: 1});
  apiMocks.products.mockResolvedValue([{id: "product-1", name: "Safari 1000"}]);
  apiMocks.tree.mockResolvedValue({items: [{id: "pilot.ods", name: "pilot.ods", type: "file", relative_path: "pilot.ods", extension: ".ods"}]});
  apiMocks.associations.mockResolvedValue({schemaAvailable: true, writeEnabled: true, adapter: "grist", items: []});
  apiMocks.fileAssociation.mockResolvedValue({fileId: "file:pilot.ods", current: null, product: null, model: null, codes: [], history: [], mappingStatus: "unmapped", processingBatch: null});
  apiMocks.inspect.mockResolvedValue({id: "pilot.ods", name: "pilot.ods", type: "file", relative_path: "pilot.ods", extension: ".ods", content_hash: "workbook-hash"});
  apiMocks.preview.mockResolvedValue({path: "pilot.ods", sheets: ["5. Material Cut List Price"], sheet: "5. Material Cut List Price", startRow: 1, totalRows: 1, totalColumns: 1, truncatedColumns: false, rows: [["Cost"]], cells: [[{value: "Cost", kind: "value"}]]});
  apiMocks.processingState.mockResolvedValue({state: "associated", effectiveState: "associated", version: 0, sourceHash: "workbook-hash", sourceChanged: false, schemaAvailable: true, stateBasis: "association", associationKey: "association-key", associationVersion: 3, history: []});

  apiMocks.partMappings.mockImplementation(async () => structuredClone(persistedDetail));
  apiMocks.searchParts.mockImplementation(async (query: string) => {
    const choices = query === newPart.id ? [newPart] : [...existingParts, newPart];
    return {items: choices, total: choices.length, offset: 0, limit: 30, hasMore: false};
  });
  apiMocks.savePartMappings.mockImplementation(async (payload: any) => {
    const nextVersion = Number(payload.expectedVersion) + 1;
    const savedKeys = Object.keys(payload.decisions);
    const groups = (persistedDetail.groups as any[]).map(group => {
      if (!savedKeys.includes(group.key)) return group;
      const partId = payload.decisions[group.key];
      const part = [...existingParts, newPart].find(item => item.id === partId) || null;
      return {...group, part, reviewed: Boolean(partId), explicitlyUnassigned: !partId, version: nextVersion};
    });
    persistedDetail = {...persistedDetail, version: nextVersion, groups, unresolvedGroups: groups.filter((group: any) => !group.reviewed).length};
    return {savedRows: savedKeys.length, version: nextVersion, idempotent: false, sourceHash: payload.expectedHash,
      associationKey: payload.expectedAssociationKey, associationVersion: payload.expectedAssociationVersion,
      assignments: Object.fromEntries(savedKeys.map(key => [key, {actionType: "initial_assignment", partId: payload.decisions[key], requiresUserReason: false}])), confirmedRows: []};
  });
  apiMocks.partBaselineReview.mockResolvedValue({});
  apiMocks.partScopeTargets.mockResolvedValue({scopes: [
    {id: "global", label: "Global", target: {id: "global", label: "Safari Manufacturing", shortcode: "SM"}},
    {id: "product", label: "Product", targets: [{id: "product-1", label: "Safari 1000", shortcode: "S1K"}]},
    {id: "product_model", label: "Product Model", targets: []},
    {id: "model_code", label: "Model Code", targets: []},
  ]});
  apiMocks.parts.mockImplementation(async () => ({items: [newPart], legacyItems: []}));
  apiMocks.partNamePreview.mockResolvedValue({name: newPart.name, available: true, collision: []});
  apiMocks.createManagedPart.mockResolvedValue({part: newPart, idempotent: false});
  apiMocks.partDetails.mockResolvedValue({part: newPart, manufacturingBaseline: {status: "not_established"}, intendedSharing: {items: []},
    processLines: {items: []}, components: {items: []}, purchases: {items: []}, drawings: {items: []}, usedIn: {items: []}});
});

afterEach(() => {cleanup(); vi.unstubAllGlobals();});

async function expandGroup(index: number) {
  const toggle = screen.getByRole("button", {name: new RegExp(`source evidence · Item ${index}`, "i")});
  if (toggle.getAttribute("aria-expanded") === "false") fireEvent.click(toggle);
  return toggle;
}

async function chooseExistingPart(groupIndex: number, partIndex: number) {
  const combo = screen.getByRole("combobox", {name: `Part assignment for Item ${groupIndex}`});
  fireEvent.focus(combo);
  fireEvent.click(await screen.findByRole("option", {name: new RegExp(existingParts[partIndex].partNumber!)}));
}

function expectPendingChanges(count: number) {
  const summary = screen.getByRole("region", {name: "Save Part mapping drafts"});
  expect(within(summary).getByText(`${count} unsaved group${count === 1 ? "" : "s"}`)).toBeTruthy();
}

it("keeps four App-level mapping choices through Part creation and navigation, then saves one and the remaining three", async () => {
  render(<App/>);
  fireEvent.click(await screen.findByRole("button", {name: /pilot\.ods/}));
  await screen.findByText("No saved association");
  fireEvent.click(screen.getByRole("button", {name: "Part Mapping"}));
  await screen.findByRole("heading", {name: "Part Mapping"});
  await screen.findByText("Showing 4 of 4 source groups");

  await chooseExistingPart(1, 0);
  await chooseExistingPart(2, 1);
  await chooseExistingPart(3, 2);
  expectPendingChanges(3);

  const fourth = screen.getByRole("combobox", {name: "Part assignment for Item 4"}).closest(".part-source-group")!;
  expect(fourth.classList.contains("is-collapsed")).toBe(true);
  fireEvent.click(within(fourth).getByRole("button", {name: "Create Part"}));
  const createDialog = await screen.findByRole("dialog", {name: "Create and assign a Part"});
  fireEvent.change(within(createDialog).getByLabelText("Name derives from"), {target: {value: "global"}});
  fireEvent.change(within(createDialog).getByLabelText("What is the Part called?"), {target: {value: "Mapping Return"}});
  fireEvent.change(within(createDialog).getByLabelText("Why is this Part needed?"), {target: {value: "Integrated mapping navigation test"}});
  await screen.findByText("Generated name · server validated");
  fireEvent.click(within(createDialog).getByRole("button", {name: "Create and assign"}));
  await waitFor(() => expect(screen.queryByRole("dialog", {name: "Create and assign a Part"})).toBeNull());
  expect(fourth.classList.contains("is-collapsed")).toBe(true);
  expect(within(fourth).getByRole("combobox", {name: "Part assignment for Item 4"})).toHaveProperty("value", expect.stringContaining("SM-P-000004"));
  expectPendingChanges(4);
  expect(apiMocks.createManagedPart).toHaveBeenCalledOnce();
  expect(apiMocks.savePartMappings).not.toHaveBeenCalled();

  const firstToggle = await expandGroup(1);
  fireEvent.click(within(firstToggle.closest(".part-source-group")!).getByRole("button", {name: "Save", exact: true}));
  await waitFor(() => expect(within(firstToggle.closest(".part-source-group")!).getByText("Saved")).toBeTruthy());
  expect(apiMocks.savePartMappings).toHaveBeenCalledTimes(1);
  expect(apiMocks.savePartMappings.mock.calls[0][0].decisions).toEqual({"mapping-1": "part-1"});
  expectPendingChanges(3);

  fireEvent.click(screen.getByRole("button", {name: "Collapse all displayed"}));
  await waitFor(() => expect(firstToggle.getAttribute("aria-expanded")).toBe("false"));
  for (const index of [2, 3, 4]) {
    expect(screen.getByRole("button", {name: new RegExp(`source evidence · Item ${index}`, "i")}).getAttribute("aria-expanded")).toBe("false");
  }

  fireEvent.click(screen.getByRole("button", {name: "Parts", exact: true}));
  await screen.findByRole("heading", {name: "Parts"});
  expect(await screen.findByText("Parts explorer")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", {name: "Part Mapping"}));
  await screen.findByText("Showing 4 of 4 source groups");
  await waitFor(() => expectPendingChanges(3));
  expect(apiMocks.savePartMappings).toHaveBeenCalledTimes(1);
  for (const index of [2, 3, 4]) {
    const toggle = screen.getByRole("button", {name: new RegExp(`source evidence · Item ${index}`, "i")});
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
  }
  for (const [index, partNumber] of [[2, "SM-P-000002"], [3, "SM-P-000003"], [4, "SM-P-000004"]] as const) {
    const toggle = screen.getByRole("button", {name: new RegExp(`source evidence · Item ${index}`, "i")});
    const assignment = toggle.closest(".part-source-group")?.querySelector<HTMLInputElement>("input[role=combobox]");
    expect(assignment?.value).toContain(partNumber);
  }

  fireEvent.click(screen.getByRole("button", {name: "Save pending mappings"}));
  const dialog = await screen.findByRole("dialog", {name: "Save all pending mappings?"});
  expect(within(dialog).getByText("5. Material Cut List Price · Item 2")).toBeTruthy();
  expect(within(dialog).getByText("5. Material Cut List Price · Item 3")).toBeTruthy();
  expect(within(dialog).getByText("5. Material Cut List Price · Item 4")).toBeTruthy();
  fireEvent.click(within(dialog).getByRole("button", {name: "Confirm save 3 groups"}));
  await waitFor(() => expect(apiMocks.savePartMappings).toHaveBeenCalledTimes(2));
  expect(apiMocks.savePartMappings.mock.calls[1][0].decisions).toEqual({"mapping-2": "part-2", "mapping-3": "part-3", "mapping-4": "part-created"});
  expect(apiMocks.savePartMappings.mock.calls[1][0].expectedVersion).toBe(1);
  await waitFor(() => expectPendingChanges(0));
}, 20000);

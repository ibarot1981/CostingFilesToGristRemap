import {cleanup, fireEvent, render, screen, waitFor, within} from "@testing-library/react";
import {afterEach, beforeEach, expect, it, vi} from "vitest";
import {App} from "../src/App";

const apiMocks = vi.hoisted(() => ({
  summary: vi.fn(), products: vi.fn(), tree: vi.fn(), associations: vi.fn(),
  fileAssociation: vi.fn(), inspect: vi.fn(), preview: vi.fn(), processingState: vi.fn(),
  partMappings: vi.fn(), searchParts: vi.fn(), savePartMappings: vi.fn(), partBaselineReview: vi.fn(),
  partScopeTargets: vi.fn(), parts: vi.fn(), partNamePreview: vi.fn(), createManagedPart: vi.fn(), partDetails: vi.fn(), mappedFiles: vi.fn(),
  comparePartBaseline: vi.fn(), partManufacturingComparison: vi.fn(), decidePartManufacturingComparison: vi.fn(),
}));
vi.mock("../src/api", () => ({api: apiMocks}));

const existingParts = [1, 2, 3].map(number => ({
  id: `part-${number}`, partNumber: `SM-P-${String(number).padStart(6, "0")}`,
  name: `Safari Manufacturing — Existing ${number}`, description: `Existing ${number}`, variant: "Standard",
  engineeringRevision: "A", selectable: true, duplicateName: false, outOfScopeCodes: [], aliases: [],
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
    rows: [{sheet, row: index + 10, lineKey: `${key}:line:${index + 10}`, semanticEvidence: {fingerprint: `semantic-${key}`},
      fields: {material_to_cut: "MS Plate", dimension_to_cut_mm: "3 x 25", qty: "2", product_part_name: `Item ${index}`},
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

function mappedGroup(index: number, part = existingParts[0]) {
  const group = makeGroup(index, {part, reviewed: true});
  return {...group, rows: group.rows.map(row => ({...row, part, reviewed: true}))};
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
    const lineAssignments: Record<string, any> = {};
    for (const [groupKey, partId] of Object.entries(payload.decisions) as [string, string | null][]) {
      const sourceGroup = (persistedDetail.groups as any[]).find(group => group.key === groupKey);
      for (const row of sourceGroup?.rows || []) if (row.lineKey) lineAssignments[row.lineKey] = {
        actionType: partId ? "initial_assignment" : "clear_assignment", partId: partId || null,
        reason: partId ? "Initial Part assignment" : payload.reasons?.[groupKey] || "Assignment was made in error", requiresUserReason: false,
      };
    }
    for (const [lineKey, partId] of Object.entries(payload.lineDecisions || {}) as [string, string | null][]) lineAssignments[lineKey] = {
      actionType: partId ? "initial_assignment" : "clear_assignment", partId: partId || null,
      reason: payload.lineReasons?.[lineKey] || "Initial Part assignment", requiresUserReason: false,
    };
    const groups = (persistedDetail.groups as any[]).map(group => {
      const rows = group.rows.map((row: any) => {
        const assignment = lineAssignments[row.lineKey];
        if (!assignment) return row;
        const part = [...existingParts, newPart].find(item => item.id === assignment.partId) || null;
        return {...row, part, reviewed: true, explicitlyUnassigned: !part};
      });
      if (!rows.some((row: any, index: number) => row !== group.rows[index])) return group;
      const mapped = rows.filter((row: any) => row.part);
      const partIds = new Set(mapped.map((row: any) => row.part.id));
      const reviewed = rows.every((row: any) => row.reviewed);
      return {...group, rows, part: reviewed && mapped.length === rows.length && partIds.size === 1 ? mapped[0].part : null,
        reviewed, explicitlyUnassigned: reviewed && mapped.length === 0,
        assignmentSummary: {partCount: partIds.size, mappedLineCount: mapped.length, unassignedLineCount: rows.length - mapped.length}, version: nextVersion};
    });
    persistedDetail = {...persistedDetail, version: nextVersion, groups, unresolvedGroups: groups.filter((group: any) => !group.reviewed).length};
    return {savedRows: Object.keys(lineAssignments).length, version: nextVersion, idempotent: false, sourceHash: payload.expectedHash,
      associationKey: payload.expectedAssociationKey, associationVersion: payload.expectedAssociationVersion,
      assignments: Object.fromEntries(savedKeys.map(key => [key, {actionType: "initial_assignment", partId: payload.decisions[key], requiresUserReason: false}])),
      lineAssignments, confirmedRows: []};
  });
  apiMocks.partBaselineReview.mockResolvedValue({});
  apiMocks.mappedFiles.mockResolvedValue({items: []});
  apiMocks.comparePartBaseline.mockResolvedValue({});
  apiMocks.partManufacturingComparison.mockResolvedValue({});
  apiMocks.decidePartManufacturingComparison.mockResolvedValue({});
  apiMocks.partScopeTargets.mockResolvedValue({scopes: [
    {id: "global", label: "Global", target: {id: "global", label: "Safari Manufacturing", shortcode: "SM"}},
    {id: "product", label: "Product", targets: [{id: "product-1", label: "Safari 1000", shortcode: "S1K"}]},
    {id: "product_model", label: "Product Model", targets: []},
    {id: "model_code", label: "Model Code", targets: []},
  ]});
  apiMocks.parts.mockImplementation(async () => ({items: [newPart], legacyItems: []}));
  apiMocks.partNamePreview.mockResolvedValue({name: newPart.name, available: true, collision: []});
  apiMocks.createManagedPart.mockResolvedValue({part: newPart, idempotent: false});
  apiMocks.partDetails.mockImplementation(async (id: string) => {
    const selectedPart = [...existingParts, newPart].find(item => item.id === id) || newPart;
    return {part: selectedPart, manufacturingBaseline: {status: "not_established"}, intendedSharing: {items: []},
      processLines: {items: []}, components: {items: []}, purchases: {items: []}, drawings: {items: []}, usedIn: {items: []}};
  });
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
  expect(summary.textContent).toContain(`${count} group${count === 1 ? "" : "s"} · ${count} unsaved line${count === 1 ? "" : "s"}`);
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
  fireEvent.click(within(firstToggle.closest(".part-source-group")!).getByRole("button", {name: "Save group", exact: true}));
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
  const dialog = await screen.findByRole("dialog", {name: "Save pending line assignments?"});
  expect(within(dialog).getByText("5. Material Cut List Price · Item 2")).toBeTruthy();
  expect(within(dialog).getByText("5. Material Cut List Price · Item 3")).toBeTruthy();
  expect(within(dialog).getByText("5. Material Cut List Price · Item 4")).toBeTruthy();
  fireEvent.click(within(dialog).getByRole("button", {name: "Confirm save 3 lines"}));
  await waitFor(() => expect(apiMocks.savePartMappings).toHaveBeenCalledTimes(2));
  expect(apiMocks.savePartMappings.mock.calls[1][0].decisions).toEqual({"mapping-2": "part-2", "mapping-3": "part-3", "mapping-4": "part-created"});
  expect(apiMocks.savePartMappings.mock.calls[1][0].expectedVersion).toBe(1);
  await waitFor(() => expectPendingChanges(0));
}, 20000);

it("keeps cached Parts and Mapping separate and preserves a view-only Part return without saving drafts", async () => {
  persistedDetail = makeDetail([makeGroup(1), makeGroup(2, {sheet:"Tool Shop Items", labelField:"product_part_name"})]);
  apiMocks.parts.mockResolvedValue({items:existingParts, legacyItems:[]});
  render(<App/>);
  fireEvent.click(await screen.findByRole("button", {name:/pilot\.ods/}));
  await screen.findByText("No saved association");
  fireEvent.click(screen.getByRole("button", {name:"Part Mapping"}));
  await screen.findByText("Showing 2 of 2 source groups");

  const search = screen.getByLabelText("Search descriptions or source fields");
  fireEvent.change(search, {target:{value:"Item 1"}});
  fireEvent.change(screen.getByLabelText("Source sheet"), {target:{value:"mcl"}});
  const toggle = screen.getByRole("button", {name:/source evidence · Item 1/i});
  fireEvent.click(toggle);
  await chooseExistingPart(1, 0);
  expectPendingChanges(1);
  expect(apiMocks.savePartMappings).not.toHaveBeenCalled();

  const mappingWrapper = Array.from(document.querySelectorAll<HTMLElement>(".app-cached-view"))
    .find(node => Boolean(node.querySelector(".part-mapping-shell")))!;
  const partsBeforeVisit = document.querySelector<HTMLElement>(".app-cached-view .parts-page");
  expect(mappingWrapper.hidden).toBe(false);
  expect(mappingWrapper.hasAttribute("inert")).toBe(false);
  expect(partsBeforeVisit).toBeNull();

  const group = toggle.closest(".part-source-group")!;
  fireEvent.click(within(group).getByRole("button", {name:"View Part"}));
  await screen.findByText("Part number");
  const partsWrapper = Array.from(document.querySelectorAll<HTMLElement>(".app-cached-view"))
    .find(node => Boolean(node.querySelector(".parts-page")))!;
  expect(partsWrapper.hidden).toBe(false);
  expect(mappingWrapper.hidden).toBe(true);
  expect(mappingWrapper.getAttribute("aria-hidden")).toBe("true");
  expect(mappingWrapper.hasAttribute("inert")).toBe(true);
  expect(getComputedStyle(mappingWrapper).display).toBe("none");
  expect(mappingWrapper.querySelector("input[role=combobox]")?.tabIndex).toBe(0);

  fireEvent.click(screen.getByRole("button", {name:"Return to Part Mapping"}));
  await screen.findByText("Showing 1 of 2 source groups");
  await waitFor(() => expectPendingChanges(1));
  expect((screen.getByLabelText("Search descriptions or source fields") as HTMLInputElement).value).toBe("Item 1");
  expect((screen.getByLabelText("Source sheet") as HTMLSelectElement).value).toBe("mcl");
  expect(screen.getByRole("button", {name:/source evidence · Item 1/i}).getAttribute("aria-expanded")).toBe("true");
  expect((screen.getByRole("combobox", {name:"Part assignment for Item 1"}) as HTMLInputElement).value).toContain("SM-P-000001");
  expect(partsWrapper.hidden).toBe(true);
  expect(partsWrapper.getAttribute("aria-hidden")).toBe("true");
  expect(partsWrapper.hasAttribute("inert")).toBe(true);
  expect(getComputedStyle(partsWrapper).display).toBe("none");
  expect(apiMocks.savePartMappings).not.toHaveBeenCalled();

  for (let index = 0; index < 3; index += 1) {
    fireEvent.click(screen.getByRole("button", {name:"Parts", exact:true}));
    await screen.findByRole("heading", {name:"Parts"});
    expect(partsWrapper.hidden).toBe(false);
    expect(mappingWrapper.hidden).toBe(true);
    fireEvent.click(screen.getByRole("button", {name:"Part Mapping"}));
    await screen.findByText("Showing 1 of 2 source groups");
    expect(mappingWrapper.hidden).toBe(false);
    expect(partsWrapper.hidden).toBe(true);
  }
  expectPendingChanges(1);
  expect(apiMocks.savePartMappings).not.toHaveBeenCalled();
}, 20000);

it("keeps cross-workbook replacement drafts on B after return selection is consumed and preserves A's separate draft", async () => {
  const pathA = "Browser-QA.ods";
  const pathB = "Browser-QA-2.ods";
  const groupB = mappedGroup(1);
  const detailA = makeDetail([mappedGroup(1), makeGroup(2)], {
    sourceHash: "hash-A", associationKey: "association-A", associationVersion: 4, version: 3, parts: [existingParts[0]],
  });
  const detailB = makeDetail([groupB, makeGroup(2)], {
    sourceHash: "hash-B", associationKey: "association-B", associationVersion: 7, version: 5, parts: [existingParts[0]],
  });
  const comparison = {
    comparison: {ComparisonKey: "comparison-B", Status: "complete", WorkbookName: pathB, SourceHash: "incoming-B", BaselineSourceHash: "accepted-hash"},
    families: [],
    differences: [{DifferenceKey: "difference-B", DifferenceType: "proposed_modification", Status: "pending_review", Family: "mcl",
      SourceSheet: "5. Material Cut List Price", SourceRow: 11, MappingGroupKey: groupB.key, IncomingObservation: 801,
      IncomingValues: "{}", BaselineValues: "{}", FieldDifferences: "[]"}],
    decisions: [], proposals: [],
  };
  const review = {
    baselineStatus: "established",
    source: {fileId: pathB, workbookPath: pathB, sourceHash: "hash-B", associationKey: "association-B", associationVersion: 7,
      mappingVersion: 5, mappingPolicyVersion: "part-source-labels-v2"},
    families: {mcl: {family: "mcl", sheet: "5. Material Cut List Price", sourceEvidenceStatus: "ok", mappedGroupCount: 1,
      mappedLineCount: 1, requirementCount: 1, applicabilityStatus: "applicable", completenessConfirmed: true},
      toolshop: {family: "toolshop", sheet: "Tool Shop Items", sourceEvidenceStatus: "ok", mappedGroupCount: 0, mappedLineCount: 0,
        requirementCount: 0, applicabilityStatus: "not_applicable", completenessConfirmed: true},
      cnc: {family: "cnc", sheet: "CNC Cut List", sourceEvidenceStatus: "ok", mappedGroupCount: 0, mappedLineCount: 0,
        requirementCount: 0, applicabilityStatus: "not_applicable", completenessConfirmed: true}},
    existingContent: {processRequirements: 0, components: 0, purchaseSpecifications: 0, requiresAppendConfirmation: false},
  };
  const files = [pathA, pathB].map(path => ({id: path, name: path, type: "file", relative_path: path, extension: ".ods"}));
  apiMocks.tree.mockResolvedValue({items: files});
  apiMocks.fileAssociation.mockImplementation(async (path: string) => ({fileId: `file:${path}`, current: null, product: null, model: null,
    codes: [], history: [], mappingStatus: "unmapped", processingBatch: null}));
  apiMocks.inspect.mockImplementation(async (path: string) => ({id: path, name: path, type: "file", relative_path: path, extension: ".ods",
    content_hash: path === pathA ? "hash-A" : "hash-B"}));
  apiMocks.preview.mockImplementation(async (path: string) => ({path, sheets: ["5. Material Cut List Price"], sheet: "5. Material Cut List Price",
    startRow: 1, totalRows: 1, totalColumns: 1, truncatedColumns: false, rows: [["Cost"]], cells: [[{value: "Cost", kind: "value"}]]}));
  apiMocks.partMappings.mockImplementation(async (path: string) => structuredClone(path === pathA ? detailA : detailB));
  apiMocks.parts.mockResolvedValue({items: [existingParts[0], existingParts[1]], legacyItems: []});
  apiMocks.partDetails.mockResolvedValue({part: {...existingParts[0], identityKind: "part", scope: "global", scopeTarget: "Safari Manufacturing",
    metadataVersion: 1, status: "active", actor: "QA", createdAt: "2026-10-16", reason: "QA"},
    manufacturingBaseline: {status: "established", baseline: {WorkbookName: pathB, WorkbookPath: pathB, SourceHash: "accepted-hash"}, requirements: [], families: []},
    mappedLines: {status: "available", items: [{assignmentStatus: "current", sourceStatus: "readable", workbookPath: pathB,
      key: "saved-line-B", sheet: "5. Material Cut List Price", row: 11, description: "Item 1", sourceHash: "hash-B"}]},
    intendedSharing: {items: []}, processLines: {items: []}, components: {items: []}, purchases: {items: []}, drawings: {items: []}, usedIn: {items: []}});
  apiMocks.mappedFiles.mockResolvedValue({items: [{file: {relative_path: pathB}, association: {product_name: "Safari 1000", model_name: "Model 1000"}, issues: []}]});
  apiMocks.partBaselineReview.mockResolvedValue(review);
  apiMocks.comparePartBaseline.mockResolvedValue(comparison);
  apiMocks.partManufacturingComparison.mockResolvedValue(comparison);
  apiMocks.decidePartManufacturingComparison.mockResolvedValue({
    ...comparison,
    replacementMapping: {groupKey: groupB.key, lineKey: groupB.rows[0].lineKey,
      lineEvidenceFingerprint: groupB.rows[0].semanticEvidence.fingerprint},
  });
  apiMocks.searchParts.mockImplementation(async (query: string) => {
    if (query === existingParts[1].id) return {items: [existingParts[1]], total: 1, offset: 0, limit: 30, hasMore: false};
    return {items: [...existingParts, newPart], total: 4, offset: 0, limit: 30, hasMore: false};
  });

  render(<App/>);
  fireEvent.click(await screen.findByRole("button", {name: pathA}));
  await screen.findByText("No saved association");
  fireEvent.click(screen.getByRole("button", {name: "Part Mapping"}));
  await screen.findByText("Showing 2 of 2 source groups");

  await chooseExistingPart(2, 1);
  expectPendingChanges(1);
  await expandGroup(1);
  const mappedPartGroup = screen.getByRole("button", {name: /source evidence · Item 1/i}).closest(".part-source-group")!;
  fireEvent.click(within(mappedPartGroup).getByRole("button", {name: "View Part"}));
  await screen.findByRole("heading", {name: "Parts"});
  await screen.findByRole("heading", {name: existingParts[0].name});
  await waitFor(() => expect((screen.getByLabelText("Source workbook for Part baseline review") as HTMLSelectElement).value).toBe(pathB));
  fireEvent.click(screen.getByRole("button", {name: "Review this Part in the selected workbook"}));
  await screen.findByRole("heading", {name: "Review manufacturing baselines"});
  await screen.findByRole("heading", {name: existingParts[0].name});
  await screen.findByRole("button", {name: "Compare incoming workbook"});
  fireEvent.click(screen.getByRole("button", {name: "Compare incoming workbook"}));
  await screen.findByRole("heading", {name: /Workbook comparison · complete/i});
  fireEvent.change(screen.getByLabelText("Decision reason"), {target: {value: "Reviewed explicit replacement for the changed incoming line"}});
  fireEvent.change(screen.getByLabelText("Replacement canonical Part"), {target: {value: existingParts[1].name}});
  const replacement = await within(await screen.findByRole("listbox")).findByRole("option", {name: /SM-P-000002/});
  fireEvent.click(replacement);
  fireEvent.click(screen.getByRole("button", {name: "Use a different Part"}));

  await screen.findByRole("heading", {name: "Part Mapping"});
  await screen.findByText("Showing 2 of 2 source groups");
  await waitFor(() => expectPendingChanges(1));
  expect(screen.getByText(pathB, {selector: ".part-workbook"})).toBeTruthy();
  const bGroupToggle = screen.getByRole("button", {name: /source evidence · Item 1/i});
  const bGroup = bGroupToggle.closest(".part-source-group")!;
  fireEvent.click(bGroupToggle);
  fireEvent.click(within(bGroup).getByRole("button", {name: "Expand source details"}));
  expect(bGroup.querySelector(".part-line-assignment-cell strong")?.textContent).toContain("SM-P-000002 · Safari Manufacturing — Existing 2");
  expect(apiMocks.partMappings).toHaveBeenCalledWith(pathB);

  fireEvent.click(within(bGroup.querySelector(".part-line-assignment-cell")!).getByRole("button", {name: "View"}));
  await screen.findByRole("heading", {name: "Parts"});
  fireEvent.click(screen.getByRole("button", {name: "Return to Part Mapping"}));
  await screen.findByRole("heading", {name: "Part Mapping"});
  await screen.findByText("Showing 2 of 2 source groups");
  await waitFor(() => expectPendingChanges(1));
  expect(screen.getByText(pathB, {selector: ".part-workbook"})).toBeTruthy();

  fireEvent.click(screen.getByRole("button", {name: "Parts", exact: true}));
  await screen.findByRole("heading", {name: "Parts"});
  fireEvent.click(screen.getByRole("button", {name: "Part Mapping"}));
  await screen.findByText("Showing 2 of 2 source groups");
  await waitFor(() => expectPendingChanges(1));
  expect(screen.getByText(pathB, {selector: ".part-workbook"})).toBeTruthy();
  const returnedBGroup = screen.getByRole("button", {name: /source evidence · Item 1/i}).closest(".part-source-group")!;
  expect(returnedBGroup.querySelector(".part-line-assignment-cell strong")?.textContent).toContain("SM-P-000002 · Safari Manufacturing — Existing 2");

  fireEvent.click(screen.getByRole("button", {name: "Files", exact: true}));
  fireEvent.click(screen.getByRole("button", {name: pathA}));
  await screen.findByText("No saved association");
  fireEvent.click(screen.getByRole("button", {name: "Part Mapping"}));
  await screen.findByText("Showing 2 of 2 source groups");
  await waitFor(() => expectPendingChanges(1));
  const aGroup1 = screen.getByRole("button", {name: /source evidence · Item 1/i}).closest(".part-source-group")!;
  const aGroup2 = screen.getByRole("button", {name: /source evidence · Item 2/i}).closest(".part-source-group")!;
  const aGroupToggle = screen.getByRole("button", {name: /source evidence · Item 1/i});
  if (aGroupToggle.getAttribute("aria-expanded") === "false") fireEvent.click(aGroupToggle);
  fireEvent.click(within(aGroup1).getByRole("button", {name: "Expand source details"}));
  expect(aGroup1.querySelector(".part-line-assignment-cell strong")?.textContent).toContain("SM-P-000001 · Safari Manufacturing — Existing 1");
  expect(aGroup2.querySelector<HTMLInputElement>("input[role=combobox]")?.value).toContain("SM-P-000002");
  expect(apiMocks.savePartMappings).not.toHaveBeenCalled();
}, 30000);

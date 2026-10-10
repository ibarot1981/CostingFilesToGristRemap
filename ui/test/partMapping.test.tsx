import {act, cleanup, fireEvent, render, screen, waitFor, within} from "@testing-library/react";
import {afterEach, beforeEach, expect, it, vi} from "vitest";
import {ApiError} from "../src/api";
import {PartMappingView} from "../src/PartMappingView";

const mocks = vi.hoisted(() => ({partMappings: vi.fn(), searchParts: vi.fn(), savePartMappings: vi.fn(), partScopeTargets: vi.fn(), fileAssociation: vi.fn(), partNamePreview: vi.fn(), createManagedPart: vi.fn(), maintainPartShortcode: vi.fn(), parts: vi.fn(),
  partBaselineReview: vi.fn(), establishPartBaseline: vi.fn(), comparePartBaseline: vi.fn(), decidePartManufacturingComparison: vi.fn()}));
vi.mock("../src/api", async () => ({...(await vi.importActual<typeof import("../src/api")>("../src/api")), api: mocks}));

const chassis = {id:"part-1", partNumber:"SM-P-000001", name:"S1KHF — Chassis", description:"Chassis", variant:"Standard", engineeringRevision:"A", selectable:true, duplicateName:false, outOfScopeCodes:[]};
const bracket = {id:"part-2", partNumber:"SM-P-000002", name:"S1KHF — Bracket", description:"Bracket", variant:"Folded", engineeringRevision:"A", selectable:true, duplicateName:false, outOfScopeCodes:[]};
const group = (key = "mcl-shaft", sheet = "5. Material Cut List Price", description = "Shaft", row = 10, overrides = {}) => ({
  key, mappingPolicyVersion:"part-source-labels-v2", evidenceFingerprint:`evidence-${key}`, sheet,
  labelField:sheet === "CNC Cut List" ? "part_category" : "product_part_name", description, blankDescription:false,
  rows:[{sheet,row,fields:{material_to_cut:"MS Plate",dimension_to_cut_mm:"3 x 25",qty:"2",optional_item_group_1:"HF",in_use:"Yes",product_part_name:"Plate 1",part_category:"Frame"},
    sourceHeaders:{material_to_cut:"Material to Cut",dimension_to_cut_mm:"Dimension to Cut (mm)",qty:"Quantity Nos",optional_item_group_1:"Optional Item Group 1",in_use:"In Use",product_part_name:"Plate Part to Cut",part_category:"Part Category"},
    sourceHeaderCells:{material_to_cut:"B8",part_category:"C8"},sourceCells:{material_to_cut:{cell:`B${row}`},dimension_to_cut_mm:{cell:`C${row}`},qty:{cell:`D${row}`}},headerRow:8,
    availableFields:["material_to_cut","dimension_to_cut_mm","qty","optional_item_group_1","in_use","product_part_name","part_category"]}],
  part:null, reviewed:false, version:0, ...overrides,
});
const detail = (groups = [group()], overrides = {}) => ({sourceHash:"hash1", associationKey:"assoc1", associationVersion:2,
  version:0, schemaAvailable:true, legacyHistoryAvailable:true, mappingPolicyVersion:"part-source-labels-v2",
  sourceSheets:{"5. Material Cut List Price":{present:true,status:"ok",labelField:"product_part_name",labelHeader:"Machine Piece Description",labelHeaderCell:"A8",ambiguousLabelHeaders:[],headerRow:8},
    "CNC Cut List":{present:true,status:"ok",labelField:"part_category",labelHeader:"Part Category",labelHeaderCell:"C8",ambiguousLabelHeaders:[],headerRow:8}},
  parts:[], groups, unresolvedGroups:groups.filter(item => !item.reviewed).length, history:[], ...overrides});
const partScopeTargets = (modelShortcode: string | null) => ({scopes:[
  {id:"global",label:"Global",target:{id:"global",label:"Safari Manufacturing",shortcode:"SM"}},
  {id:"product",label:"Product",targets:[{id:"product-1",label:"Safari 1000",shortcode:"S1K"}]},
  {id:"product_model",label:"Product Model",targets:[{id:"model-1",parentId:"product-1",label:"Safari 1000 HF",shortcode:modelShortcode}]},
  {id:"model_code",label:"Model Code",targets:[{id:"code-1",parentId:"model-1",label:"S1KHFELP"}]},
]});
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(done => { resolve = done; });
  return {promise, resolve};
}
beforeEach(() => {
  Object.values(mocks).forEach(mock => mock.mockReset());
  vi.stubGlobal("crypto", {randomUUID: () => "request-key"});
  sessionStorage.clear();
  localStorage.removeItem("part-mapping:context-open");
  mocks.partMappings.mockResolvedValue(detail());
  mocks.searchParts.mockResolvedValue({items:[chassis,bracket],total:2,offset:0,limit:30,hasMore:false});
  mocks.savePartMappings.mockImplementation(async (payload:any) => ({savedRows:Object.keys(payload.decisions).length,
    version:payload.expectedVersion + 1,idempotent:false,sourceHash:payload.expectedHash,
    associationKey:payload.expectedAssociationKey,associationVersion:payload.expectedAssociationVersion,
    assignments:Object.fromEntries(Object.entries(payload.decisions).map(([key,partId]) => [key,{
      actionType:partId ? "initial_assignment" : "clear_assignment",partId:partId || null,
      reason:partId ? "Initial Part assignment" : "Assignment was made in error",requiresUserReason:!partId,
    }])),confirmedRows:[]}));
  mocks.partBaselineReview.mockResolvedValue({});
  mocks.establishPartBaseline.mockResolvedValue({baselineStatus:"established"});
  mocks.comparePartBaseline.mockResolvedValue({});
  mocks.decidePartManufacturingComparison.mockResolvedValue({});
  mocks.partScopeTargets.mockResolvedValue({scopes:[
    {id:"global",label:"Global",target:{id:"global",label:"Safari Manufacturing",shortcode:"SM"}},
    {id:"product",label:"Product",targets:[{id:"product-1",label:"Safari 1000",shortcode:"S1K"}]},
    {id:"product_model",label:"Product Model",targets:[{id:"model-1",parentId:"product-1",label:"Safari 1000 HF",shortcode:"S1KHF"}]},
    {id:"model_code",label:"Model Code",targets:[{id:"code-1",parentId:"model-1",label:"S1KHFELP"}]},
  ]});
  mocks.fileAssociation.mockResolvedValue({current:{product_id:"product-1",model_id:"model-1"},product:{id:"product-1"},model:{id:"model-1"},codes:[{id:"code-1"}]});
  mocks.partNamePreview.mockImplementation(async (_scope:string, _target:string, description:string, variant:string) => ({name:`S1KHF — ${description}${variant ? ` — ${variant}` : ""}`,available:true,collision:[]}));
  mocks.createManagedPart.mockResolvedValue({part:{...chassis,id:"created-part",partNumber:"SM-P-000003",name:"S1KHF — Shaft"}});
  mocks.maintainPartShortcode.mockResolvedValue({shortcode:"S1KHF",version:1});
  mocks.parts.mockResolvedValue({items:[chassis],legacyItems:[]});
});
afterEach(() => {cleanup(); vi.unstubAllGlobals();});

async function expandGroup(label = "Shaft") {
  const toggle = await screen.findByRole("button", {name:new RegExp(`source evidence · ${label}`, "i")});
  if (toggle?.getAttribute("aria-expanded") === "false") fireEvent.click(toggle);
}
async function choosePart(label = "Shaft", index = 0) {
  const combo = await screen.findByRole("combobox", {name:`Part assignment for ${label}`});
  fireEvent.focus(combo);
  const partOption = await screen.findByRole("option", {name:new RegExp(index === 0 ? "SM-P-000001" : "SM-P-000002")});
  fireEvent.click(partOption);
}

it("uses a sheet-specific label and shows bounded source values with workbook provenance", async () => {
  mocks.partMappings.mockResolvedValue(detail([group("cnc-frame","CNC Cut List","Frame",42)]));
  render(<PartMappingView path="pilot.ods"/>);
  expect(await screen.findByText("Frame")).toBeTruthy();
  expect(screen.getByText(/Part Category from Part Category at C8/)).toBeTruthy();
  expect(screen.queryByRole("columnheader", {name:"Material to Cut"})).toBeNull();
  const groupToggle = screen.getByRole("button", {name:/Expand source evidence · Frame/});
  expect(groupToggle.getAttribute("aria-expanded")).toBe("false");
  fireEvent.click(groupToggle);
  fireEvent.click(screen.getByRole("button", {name:"Expand source details"}));
  expect(screen.getByRole("columnheader", {name:"Material to Cut"})).toBeTruthy();
  expect(screen.getByRole("columnheader", {name:"Plate Part to Cut"})).toBeTruthy();
  expect(screen.getByText("Plate 1")).toBeTruthy();
  expect(screen.getByText("MS Plate")).toBeTruthy();
  expect(screen.getByText("row 42 · headers row 8")).toBeTruthy();
});

it("saves only one group, retries the identical request, and reloads saved state", async () => {
  mocks.savePartMappings.mockRejectedValueOnce(new Error("response lost"));
  render(<PartMappingView path="pilot.ods"/>);
  await choosePart();
  expect(screen.queryByRole("textbox", {name:/Reason for mapping change/})).toBeNull();
  fireEvent.click(screen.getByRole("button", {name:"Save"}));
  await screen.findByRole("alert");
  expect((screen.getByRole("combobox", {name:"Part assignment for Shaft"}) as HTMLInputElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole("button", {name:"Retry save"}));
  expect(await screen.findByText("Saved")).toBeTruthy();
  expect(mocks.savePartMappings.mock.calls[1]).toEqual(mocks.savePartMappings.mock.calls[0]);
  expect(mocks.savePartMappings.mock.calls[0][0]).toMatchObject({path:"pilot.ods",expectedHash:"hash1",expectedVersion:0,
    expectedAssociationKey:"assoc1",expectedAssociationVersion:2,decisions:{"mcl-shaft":"part-1"},reasons:{}});
});

it("keeps hidden drafts and names every pending group in batch confirmation", async () => {
  const second = group("tool-bracket","Tool Shop Items","Bracket",18);
  mocks.partMappings.mockResolvedValue(detail([group(),second]));
  render(<PartMappingView path="pilot.ods"/>);
  await choosePart("Shaft",0);
  await choosePart("Bracket",1);
  fireEvent.change(screen.getByLabelText("Source sheet"), {target:{value:"toolshop"}});
  expect(screen.queryByText("Shaft")).toBeNull();
  fireEvent.click(screen.getByRole("button", {name:"Save pending mappings"}));
  const dialog = await screen.findByRole("dialog", {name:"Save all pending mappings?"});
  expect(within(dialog).getByText("5. Material Cut List Price · Shaft")).toBeTruthy();
  expect(within(dialog).getByText("Tool Shop Items · Bracket")).toBeTruthy();
  fireEvent.click(within(dialog).getByRole("button", {name:"Confirm save 2 groups"}));
  await waitFor(() => expect(mocks.savePartMappings).toHaveBeenCalledTimes(1));
  expect(mocks.savePartMappings.mock.calls[0][0].decisions).toEqual({"mcl-shaft":"part-1","tool-bracket":"part-2"});
});

it("opens contextual creation without leaving Mapping and assigns the created Part as an unsaved draft", async () => {
  mocks.partMappings.mockResolvedValue(detail([group(),group("tool-bracket","Tool Shop Items","Bracket",18)]));
  const view = render(<PartMappingView path="pilot.ods"/>);
  await choosePart();
  await choosePart("Bracket",1);
  fireEvent.change(screen.getByLabelText("Search descriptions or source fields"), {target:{value:"MS Plate"}});
  fireEvent.change(screen.getByLabelText("Source sheet"), {target:{value:"mcl"}});
  const table = document.querySelector<HTMLElement>(".part-source-table")!;
  table.scrollTop = 37;
  fireEvent.scroll(table);
  fireEvent.click(document.querySelector<HTMLButtonElement>(".part-context-desktop-toggle")!);
  expect(document.querySelector(".part-context-panel")?.classList.contains("closed")).toBe(true);
  const reloadCount = mocks.partMappings.mock.calls.length;
  fireEvent.click(screen.getByRole("button", {name:"Create Part"}));
  const dialog = await screen.findByRole("dialog", {name:"Create and assign a Part"});
  expect(within(dialog).getByText("Workbook · pilot.ods")).toBeTruthy();
  expect(within(dialog).getByText("Sheet · 5. Material Cut List Price")).toBeTruthy();
  expect((within(dialog).getByLabelText("What is the Part called?") as HTMLInputElement).value).toBe("Shaft");
  expect((within(dialog).getByLabelText("Scope target") as HTMLSelectElement).value).toBe("model-1");
  expect(within(dialog).queryByLabelText(/Select workbook/i)).toBeNull();
  fireEvent.change(within(dialog).getByLabelText("Why is this Part needed?"), {target:{value:"Canonical Part for mapping review"}});
  await waitFor(() => expect(within(dialog).getByText("S1KHF — Shaft")).toBeTruthy());
  fireEvent.click(within(dialog).getByRole("button", {name:"Create and assign"}));
  await waitFor(() => expect((screen.getByRole("combobox", {name:"Part assignment for Shaft"}) as HTMLInputElement).value).toContain("SM-P-000003"));
  expect(mocks.createManagedPart).toHaveBeenCalledTimes(1);
  expect(mocks.partMappings).toHaveBeenCalledTimes(reloadCount);
  expect(screen.getByText("Unsaved")).toBeTruthy();
  expect(screen.getByText(/created in Grist and added as an unsaved mapping selection/)).toBeTruthy();
  expect(JSON.parse(sessionStorage.getItem("part-mapping:pilot.ods") || "{}")).toMatchObject({decisions:{"mcl-shaft":"created-part","tool-bracket":"part-2"},query:"MS Plate",sheetFilter:"mcl",scrollTop:37});
  expect(table.scrollTop).toBe(37);
  expect(document.querySelector(".part-context-panel")?.classList.contains("closed")).toBe(true);
  expect(screen.queryByRole("combobox", {name:"Part assignment for Bracket"})).toBeNull();
  view.unmount();
});

it("restores an uncertain contextual creation with the exact Grist request and group context", async () => {
  mocks.createManagedPart.mockReset()
    .mockRejectedValueOnce(new Error("response lost after submit"))
    .mockResolvedValueOnce({part:{...chassis,id:"restored-created",partNumber:"SM-P-000004",name:"S1KHF — Shaft"}});
  const view = render(<PartMappingView path="pilot.ods"/>);
  fireEvent.click(await screen.findByRole("button", {name:"Create Part"}));
  let dialog = await screen.findByRole("dialog", {name:"Create and assign a Part"});
  fireEvent.change(within(dialog).getByLabelText("Why is this Part needed?"), {target:{value:"Recover the uncertain creation"}});
  await waitFor(() => expect(within(dialog).getByText("S1KHF — Shaft")).toBeTruthy());
  fireEvent.click(within(dialog).getByRole("button", {name:"Create and assign"}));
  await within(dialog).findByRole("alert");
  const original = mocks.createManagedPart.mock.calls[0];
  const savedAttempt = JSON.parse(sessionStorage.getItem("part-mapping:create-attempt") || "{}");
  expect(savedAttempt).toMatchObject({key:original[1],payload:original[0],origin:{workbookPath:"pilot.ods",groupKey:"mcl-shaft",sourceHash:"hash1",evidenceFingerprint:"evidence-mcl-shaft"}});
  expect(within(dialog).getByRole("button", {name:"Close Part creation dialog"})).toHaveProperty("disabled", true);
  fireEvent.keyDown(document, {key:"Escape"});
  expect(screen.getByRole("dialog", {name:"Create and assign a Part"})).toBeTruthy();
  view.unmount();

  const restored = render(<PartMappingView path="pilot.ods"/>);
  dialog = await screen.findByRole("dialog", {name:"Create and assign a Part"});
  fireEvent.click(await within(dialog).findByRole("button", {name:"Retry same creation request"}));
  await waitFor(() => expect((screen.getByRole("combobox", {name:"Part assignment for Shaft"}) as HTMLInputElement).value).toContain("SM-P-000004"));
  expect(mocks.createManagedPart).toHaveBeenCalledTimes(2);
  expect(mocks.createManagedPart.mock.calls[1]).toEqual(original);
  expect(mocks.partMappings).toHaveBeenCalledTimes(2);
  expect(JSON.parse(sessionStorage.getItem("part-mapping:pilot.ods") || "{}")).toMatchObject({decisions:{"mcl-shaft":"restored-created"}});
  restored.unmount();
});

it("refuses to apply the created Part when the originating workbook changes", async () => {
  const view = render(<PartMappingView path="pilot.ods"/>);
  fireEvent.click(await screen.findByRole("button", {name:"Create Part"}));
  const dialog = await screen.findByRole("dialog", {name:"Create and assign a Part"});
  view.rerender(<PartMappingView path="different.ods"/>);
  fireEvent.change(within(dialog).getByLabelText("Why is this Part needed?"), {target:{value:"Keep the created identity"}});
  await waitFor(() => expect(within(dialog).getByText("S1KHF — Shaft")).toBeTruthy());
  fireEvent.click(within(dialog).getByRole("button", {name:"Create and assign"}));
  await waitFor(() => expect(screen.getByText(/originating workbook or source group changed, so it was not assigned/)).toBeTruthy());
  expect(mocks.createManagedPart).toHaveBeenCalledTimes(1);
  expect(screen.getByText("0 unsaved groups")).toBeTruthy();
});

it("preserves a published Part without assigning it when the originating group evidence changes", async () => {
  mocks.partMappings.mockReset()
    .mockResolvedValueOnce(detail())
    .mockResolvedValueOnce(detail([group("mcl-shaft","5. Material Cut List Price","Shaft",10,{evidenceFingerprint:"changed-evidence"})]));
  render(<PartMappingView path="pilot.ods"/>);
  fireEvent.click(await screen.findByRole("button", {name:"Create Part"}));
  const dialog = await screen.findByRole("dialog", {name:"Create and assign a Part"});
  fireEvent.click(screen.getByRole("button", {name:"Reload review"}));
  await waitFor(() => expect(mocks.partMappings).toHaveBeenCalledTimes(2));
  fireEvent.change(within(dialog).getByLabelText("Why is this Part needed?"), {target:{value:"Preserve identity after source evidence changes"}});
  await waitFor(() => expect(within(dialog).getByText("S1KHF — Shaft")).toBeTruthy());
  fireEvent.click(within(dialog).getByRole("button", {name:"Create and assign"}));
  await waitFor(() => expect(screen.getByText(/exists in Grist\. The originating workbook or source group changed, so it was not assigned/)).toBeTruthy());
  expect(mocks.createManagedPart).toHaveBeenCalledTimes(1);
  expect(screen.getByText("0 unsaved groups")).toBeTruthy();
  expect(mocks.savePartMappings).not.toHaveBeenCalled();
});

it.each([
  ["workbook path", "different.ods", detail()],
  ["source hash", "pilot.ods", detail([group()], {sourceHash:"hash2"})],
  ["association identity", "pilot.ods", detail([group()], {associationKey:"assoc2"})],
  ["association version", "pilot.ods", detail([group()], {associationVersion:3})],
  ["source group identity", "pilot.ods", detail([group("replacement-group")])],
  ["source evidence fingerprint", "pilot.ods", detail([group("mcl-shaft","5. Material Cut List Price","Shaft",10,{evidenceFingerprint:"changed-evidence"})])],
])("withholds assignment when %s changes while canonical creation is pending", async (_change, refreshedPath, refreshedDetail) => {
  const pending = deferred<{part: typeof chassis}>();
  mocks.partMappings.mockReset().mockResolvedValueOnce(detail()).mockResolvedValueOnce(refreshedDetail);
  mocks.createManagedPart.mockReturnValue(pending.promise);
  const view = render(<PartMappingView path="pilot.ods"/>);
  fireEvent.click(await screen.findByRole("button", {name:"Create Part"}));
  const dialog = await screen.findByRole("dialog", {name:"Create and assign a Part"});
  fireEvent.change(within(dialog).getByLabelText("Why is this Part needed?"), {target:{value:"Preserve identity while source evidence refreshes"}});
  await waitFor(() => expect(within(dialog).getByText("S1KHF — Shaft")).toBeTruthy());
  fireEvent.click(within(dialog).getByRole("button", {name:"Create and assign"}));
  await waitFor(() => expect(mocks.createManagedPart).toHaveBeenCalledTimes(1));
  if (refreshedPath !== "pilot.ods") view.rerender(<PartMappingView path={refreshedPath}/>);
  else fireEvent.click(screen.getByRole("button", {name:"Reload review"}));
  await waitFor(() => expect(mocks.partMappings).toHaveBeenCalledTimes(2));
  await waitFor(() => expect(screen.queryByText(/Refreshing workbook and saved mapping evidence/)).toBeNull());
  await act(async () => { pending.resolve({part:{...chassis,id:"created-after-refresh",partNumber:"SM-P-000010",name:"S1KHF — Shaft"}}); await pending.promise; });
  await waitFor(() => expect(screen.getByText(/exists in Grist\. The originating workbook or source group changed, so it was not assigned/)).toBeTruthy());
  expect(screen.getByText("0 unsaved groups")).toBeTruthy();
  expect(mocks.savePartMappings).not.toHaveBeenCalled();
  expect(sessionStorage.getItem("part-mapping:create-attempt")).toBeNull();
});

it("withholds a colliding Part when its lookup resolves after group evidence changed", async () => {
  const pending = deferred<{items: Array<typeof chassis>}>();
  mocks.partNamePreview.mockResolvedValue({name:"S1KHF — Shaft",available:false,collision:[{source:"canonical",id:"part-1",number:"SM-P-000001",status:"active"}]});
  mocks.partMappings.mockReset()
    .mockResolvedValueOnce(detail())
    .mockResolvedValueOnce(detail([group("mcl-shaft","5. Material Cut List Price","Shaft",10,{evidenceFingerprint:"changed-evidence"})]));
  mocks.searchParts.mockReturnValue(pending.promise);
  render(<PartMappingView path="pilot.ods"/>);
  fireEvent.click(await screen.findByRole("button", {name:"Create Part"}));
  const dialog = await screen.findByRole("dialog", {name:"Create and assign a Part"});
  await waitFor(() => expect(within(dialog).getByText("S1KHF — Shaft")).toBeTruthy());
  fireEvent.click(within(dialog).getByRole("button", {name:/Use existing Part/}));
  await waitFor(() => expect(mocks.searchParts).toHaveBeenCalledTimes(1));
  fireEvent.click(screen.getByRole("button", {name:"Reload review"}));
  await waitFor(() => expect(mocks.partMappings).toHaveBeenCalledTimes(2));
  await waitFor(() => expect(screen.queryByText(/Refreshing workbook and saved mapping evidence/)).toBeNull());
  await act(async () => { pending.resolve({items:[chassis]}); await pending.promise; });
  await waitFor(() => expect(screen.getByText(/is selectable, but the originating source context changed/)).toBeTruthy());
  expect(screen.getByText("0 unsaved groups")).toBeTruthy();
  expect(mocks.savePartMappings).not.toHaveBeenCalled();
});

it("allows a contextual creation to be corrected after a structured pre-write rejection", async () => {
  vi.stubGlobal("crypto", {randomUUID: vi.fn().mockReturnValueOnce("rejected-request").mockReturnValueOnce("corrected-request")});
  mocks.createManagedPart.mockReset()
    .mockRejectedValueOnce(new ApiError("PART_NAME_PREVIEW_STALE: refresh the name", 409, "PART_NAME_PREVIEW_STALE", "safe_to_edit"))
    .mockResolvedValueOnce({part:{...chassis,id:"corrected-part",partNumber:"SM-P-000011",name:"S1KHF — Shaft corrected"}});
  render(<PartMappingView path="pilot.ods"/>);
  fireEvent.click(await screen.findByRole("button", {name:"Create Part"}));
  const dialog = await screen.findByRole("dialog", {name:"Create and assign a Part"});
  const description = within(dialog).getByLabelText("What is the Part called?") as HTMLInputElement;
  fireEvent.change(within(dialog).getByLabelText("Why is this Part needed?"), {target:{value:"Correct a rejected request"}});
  await waitFor(() => expect(within(dialog).getByText("S1KHF — Shaft")).toBeTruthy());
  fireEvent.click(within(dialog).getByRole("button", {name:"Create and assign"}));
  await within(dialog).findByRole("alert");
  expect(description.disabled).toBe(false);
  expect(within(dialog).getByRole("button", {name:"Cancel before creation"})).toHaveProperty("disabled", false);
  const firstCall = mocks.createManagedPart.mock.calls[0];
  fireEvent.change(description, {target:{value:"Shaft corrected"}});
  await waitFor(() => expect(within(dialog).getByText("S1KHF — Shaft corrected")).toBeTruthy());
  fireEvent.click(within(dialog).getByRole("button", {name:"Create and assign"}));
  await waitFor(() => expect(screen.getByText("Unsaved")).toBeTruthy());
  expect(mocks.createManagedPart).toHaveBeenCalledTimes(2);
  expect(mocks.createManagedPart.mock.calls[1][0]).toMatchObject({...firstCall[0],description:"Shaft corrected",expectedName:"S1KHF — Shaft corrected"});
  expect(mocks.createManagedPart.mock.calls[1][1]).not.toBe(firstCall[1]);
});

it("allows contextual creation to be cancelled after a structured pre-write rejection", async () => {
  mocks.createManagedPart.mockRejectedValueOnce(new ApiError("Correct the Part fields", 422, "PART_INPUT_INVALID", "safe_to_edit"));
  render(<PartMappingView path="pilot.ods"/>);
  fireEvent.click(await screen.findByRole("button", {name:"Create Part"}));
  const dialog = await screen.findByRole("dialog", {name:"Create and assign a Part"});
  fireEvent.change(within(dialog).getByLabelText("Why is this Part needed?"), {target:{value:"Cancel after rejection"}});
  await waitFor(() => expect(within(dialog).getByText("S1KHF — Shaft")).toBeTruthy());
  fireEvent.click(within(dialog).getByRole("button", {name:"Create and assign"}));
  await within(dialog).findByRole("alert");
  fireEvent.click(within(dialog).getByRole("button", {name:"Cancel before creation"}));
  expect(screen.queryByRole("dialog", {name:"Create and assign a Part"})).toBeNull();
  expect(sessionStorage.getItem("part-mapping:create-attempt")).toBeNull();
});

it("refreshes a missing shortcode in the mounted contextual form and preserves origin and intended sharing", async () => {
  mocks.partScopeTargets.mockReset().mockResolvedValueOnce(partScopeTargets(null)).mockResolvedValueOnce(partScopeTargets("HFNEW"));
  mocks.maintainPartShortcode.mockReset().mockResolvedValue({shortcode:"HFNEW",version:1});
  mocks.partNamePreview.mockReset().mockResolvedValue({name:"HFNEW — Shaft updated — Forged",available:true,collision:[]});
  const pendingCreate = deferred<{part: typeof chassis}>();
  mocks.createManagedPart.mockReturnValue(pendingCreate.promise);
  render(<PartMappingView path="pilot.ods"/>);
  fireEvent.click(await screen.findByRole("button", {name:"Create Part"}));
  const dialog = await screen.findByRole("dialog", {name:"Create and assign a Part"});
  const description = within(dialog).getByLabelText("What is the Part called?") as HTMLInputElement;
  await waitFor(() => expect(description.disabled).toBe(false));
  await waitFor(() => expect((within(dialog).getByLabelText("Scope target") as HTMLSelectElement).value).toBe("model-1"));
  expect(within(dialog).getByText("Workbook · pilot.ods")).toBeTruthy();
  expect(within(dialog).getByText("Sheet · 5. Material Cut List Price")).toBeTruthy();
  const intendedCode = within(dialog).getByRole("checkbox", {name:/S1KHFELP/}) as HTMLInputElement;
  await waitFor(() => expect(intendedCode.checked).toBe(true));
  fireEvent.change(description, {target:{value:"Shaft updated"}});
  fireEvent.change(within(dialog).getByLabelText("What distinguishes this design?"), {target:{value:"Forged"}});
  fireEvent.change(within(dialog).getByLabelText("Why is this Part needed?"), {target:{value:"Preserve workbook context after prefix maintenance"}});
  fireEvent.change(await within(dialog).findByLabelText("Shortcode"), {target:{value:"HFNEW"}});
  fireEvent.change(within(dialog).getByLabelText("Why is this shortcode being set?"), {target:{value:"Maintain the missing model prefix"}});
  fireEvent.click(within(dialog).getByRole("button", {name:"Save shortcode"}));
  await waitFor(() => expect(mocks.maintainPartShortcode).toHaveBeenCalledTimes(1));
  await waitFor(() => expect(within(dialog).queryByText(/Resolve the missing shortcode/)).toBeNull());
  await within(dialog).findByText("HFNEW — Shaft updated — Forged");
  expect((within(dialog).getByLabelText("What is the Part called?") as HTMLInputElement).value).toBe("Shaft updated");
  expect((within(dialog).getByLabelText("What distinguishes this design?") as HTMLInputElement).value).toBe("Forged");
  expect((within(dialog).getByRole("checkbox", {name:/S1KHFELP/}) as HTMLInputElement).checked).toBe(true);
  expect(within(dialog).getByText("Workbook · pilot.ods")).toBeTruthy();
  fireEvent.click(within(dialog).getByRole("button", {name:"Create and assign"}));
  await waitFor(() => expect(mocks.createManagedPart).toHaveBeenCalledTimes(1));
  const retainedAttempt = JSON.parse(sessionStorage.getItem("part-mapping:create-attempt") || "{}");
  expect(retainedAttempt).toMatchObject({key:"request-key",origin:{workbookPath:"pilot.ods",groupKey:"mcl-shaft",sourceHash:"hash1",associationKey:"assoc1",associationVersion:2,evidenceFingerprint:"evidence-mcl-shaft"}});
  expect(mocks.createManagedPart.mock.calls[0][0]).toMatchObject({scope:"product_model",targetId:"model-1",description:"Shaft updated",variant:"Forged",expectedName:"HFNEW — Shaft updated — Forged",intendedModelCodeIds:["code-1"]});
  await act(async () => { pendingCreate.resolve({part:{...chassis,id:"shortcode-created",partNumber:"SM-P-000012",name:"HFNEW — Shaft updated — Forged"}}); await pendingCreate.promise; });
  await waitFor(() => expect(screen.getByText("Unsaved")).toBeTruthy());
  expect(screen.getByText(/created in Grist and added as an unsaved mapping selection/)).toBeTruthy();
  expect(mocks.savePartMappings).not.toHaveBeenCalled();
});

it("recovers contextual shortcode maintenance after remount with its exact request and workbook origin", async () => {
  mocks.partScopeTargets.mockReset().mockResolvedValueOnce(partScopeTargets(null)).mockResolvedValueOnce(partScopeTargets("HFNEW"));
  mocks.maintainPartShortcode.mockReset()
    .mockRejectedValueOnce(new Error("shortcode response was lost after the write"))
    .mockResolvedValueOnce({shortcode:"HFNEW",version:1});
  mocks.partNamePreview.mockResolvedValue({name:"HFNEW — Shaft restored — Forged",available:true,collision:[]});
  render(<PartMappingView path="pilot.ods"/>);
  fireEvent.click(await screen.findByRole("button", {name:"Create Part"}));
  const dialog = await screen.findByRole("dialog", {name:"Create and assign a Part"});
  const description = within(dialog).getByLabelText("What is the Part called?") as HTMLInputElement;
  await waitFor(() => expect(description.disabled).toBe(false));
  await waitFor(() => expect((within(dialog).getByLabelText("Scope target") as HTMLSelectElement).value).toBe("model-1"));
  fireEvent.change(description, {target:{value:"Shaft restored"}});
  fireEvent.change(within(dialog).getByLabelText("What distinguishes this design?"), {target:{value:"Forged"}});
  fireEvent.change(within(dialog).getByLabelText("Why is this Part needed?"), {target:{value:"Restore the same contextual capture"}});
  fireEvent.change(await within(dialog).findByLabelText("Shortcode"), {target:{value:"HFNEW"}});
  fireEvent.change(within(dialog).getByLabelText("Why is this shortcode being set?"), {target:{value:"Recover maintained model prefix"}});
  fireEvent.click(within(dialog).getByRole("button", {name:"Save shortcode"}));
  await within(dialog).findByRole("alert");
  const original = mocks.maintainPartShortcode.mock.calls[0];
  const saved = JSON.parse(sessionStorage.getItem("part-mapping:shortcode-attempt") || "{}");
  expect(saved).toMatchObject({key:original[1],payload:original[0],origin:{workbookPath:"pilot.ods",groupKey:"mcl-shaft",sourceHash:"hash1",associationKey:"assoc1",associationVersion:2,evidenceFingerprint:"evidence-mcl-shaft"},
    formState:{description:"Shaft restored",variant:"Forged",reason:"Restore the same contextual capture"}});
  cleanup();

  render(<PartMappingView path="pilot.ods"/>);
  const restoredDialog = await screen.findByRole("dialog", {name:"Create and assign a Part"});
  await waitFor(() => expect((within(restoredDialog).getByLabelText("What is the Part called?") as HTMLInputElement).value).toBe("Shaft restored"));
  expect(within(restoredDialog).getByText("Workbook · pilot.ods")).toBeTruthy();
  expect((within(restoredDialog).getByLabelText("What distinguishes this design?") as HTMLInputElement).value).toBe("Forged");
  const retry = within(restoredDialog).getByRole("button", {name:"Retry shortcode save"});
  await waitFor(() => expect(retry).toHaveProperty("disabled", false));
  fireEvent.click(retry);
  await waitFor(() => expect(mocks.maintainPartShortcode).toHaveBeenCalledTimes(2));
  expect(mocks.maintainPartShortcode.mock.calls[1]).toEqual(original);
  await within(restoredDialog).findByText("HFNEW — Shaft restored — Forged");
  expect(sessionStorage.getItem("part-mapping:shortcode-attempt")).toBeNull();
});

it("offers a selectable colliding Part as an unsaved assignment", async () => {
  mocks.partNamePreview.mockResolvedValue({name:"S1KHF — Shaft",available:false,collision:[{source:"canonical",id:"part-1",number:"SM-P-000001",status:"active"}]});
  render(<PartMappingView path="pilot.ods"/>);
  fireEvent.click(await screen.findByRole("button", {name:"Create Part"}));
  const dialog = await screen.findByRole("dialog", {name:"Create and assign a Part"});
  await waitFor(() => expect(within(dialog).getByText("S1KHF — Shaft")).toBeTruthy());
  fireEvent.click(within(dialog).getByRole("button", {name:/Use existing Part/}));
  await waitFor(() => expect((screen.getByRole("combobox", {name:"Part assignment for Shaft"}) as HTMLInputElement).value).toContain("SM-P-000001"));
  expect(mocks.createManagedPart).not.toHaveBeenCalled();
  expect(screen.getByText("Unsaved")).toBeTruthy();
  expect(mocks.savePartMappings).not.toHaveBeenCalled();
});

it("traps dialog focus and returns focus to the initiating group on safe cancellation", async () => {
  render(<PartMappingView path="pilot.ods"/>);
  const opener = await screen.findByRole("button", {name:"Create Part"});
  fireEvent.click(opener);
  const dialog = await screen.findByRole("dialog", {name:"Create and assign a Part"});
  await waitFor(() => expect(dialog.contains(document.activeElement)).toBe(true));
  const focusable = Array.from(dialog.querySelectorAll<HTMLElement>("button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled])"));
  expect(focusable.length).toBeGreaterThan(1);
  focusable[0].focus();
  fireEvent.keyDown(document, {key:"Tab",shiftKey:true});
  expect(document.activeElement).toBe(focusable[focusable.length - 1]);
  fireEvent.keyDown(document, {key:"Escape"});
  await waitFor(() => expect(screen.queryByRole("dialog", {name:"Create and assign a Part"})).toBeNull());
  await waitFor(() => expect(document.activeElement).toBe(opener));
  expect(mocks.createManagedPart).not.toHaveBeenCalled();
});

it("keeps assignment and Save actionable while source evidence stays collapsed", async () => {
  render(<PartMappingView path="pilot.ods"/>);
  await choosePart();
  const card = screen.getByRole("combobox", {name:"Part assignment for Shaft"}).closest(".part-source-group")!;
  expect(card.classList.contains("is-collapsed")).toBe(true);
  fireEvent.click(within(card).getByRole("button", {name:"Create Part"}));
  fireEvent.click(await screen.findByRole("button", {name:"Cancel before creation"}));
  expect(card.classList.contains("is-collapsed")).toBe(true);
  fireEvent.click(within(card).getByRole("button", {name:"Save"}));
  await waitFor(() => expect(mocks.savePartMappings).toHaveBeenCalledTimes(1));
  expect(card.classList.contains("is-collapsed")).toBe(true);
  expect(within(card).getByRole("button", {name:/Expand source evidence/}).getAttribute("aria-expanded")).toBe("false");
  expect((card.querySelector(".part-source-details-heading button") as HTMLButtonElement).textContent).toBe("Expand source details");
});

it("rejects a Part returned from Parts when that source group changed", async () => {
  mocks.searchParts.mockResolvedValue({items:[chassis],total:1});
  render(<PartMappingView path="pilot.ods" returnSelection={{path:"pilot.ods",groupKey:"mcl-shaft",partId:"part-1",mode:"select",sourceHash:"hash1",associationKey:"assoc1",associationVersion:2,evidenceFingerprint:"old-evidence"}}/>);
  expect(await screen.findByText("The source group changed while Parts was open. Review the refreshed group before assigning a Part.")).toBeTruthy();
  await expandGroup("Shaft");
  expect((screen.getByRole("combobox", {name:"Part assignment for Shaft"}) as HTMLInputElement).value).toBe("No Part selected");
});

it("holds stale selections until the refreshed source is explicitly reviewed", async () => {
  mocks.partMappings.mockResolvedValueOnce(detail()).mockResolvedValueOnce(detail([group("mcl-shaft","5. Material Cut List Price","Shaft",10,{evidenceFingerprint:"changed-evidence"})],{sourceHash:"hash2"}));
  mocks.savePartMappings.mockRejectedValueOnce(new Error("PART_REVIEW_STALE: The workbook changed; reload the review"));
  render(<PartMappingView path="pilot.ods"/>);
  await choosePart();
  fireEvent.click(screen.getByRole("button", {name:"Save"}));
  await screen.findByRole("alert");
  fireEvent.click(screen.getByRole("button", {name:"Review current source"}));
  expect(await screen.findByRole("region", {name:"Drafts held for source review"})).toBeTruthy();
  expect(screen.getByText(/previous choice: SM-P-000001/)).toBeTruthy();
  expect((screen.getByRole("combobox", {name:"Part assignment for Shaft"}) as HTMLInputElement).value).toBe("No Part selected");
  fireEvent.click(screen.getByRole("button", {name:"Reapply after reviewing current rows"}));
  expect((screen.getByRole("combobox", {name:"Part assignment for Shaft"}) as HTMLInputElement).value).toContain("SM-P-000001");
  expect(mocks.savePartMappings).toHaveBeenCalledTimes(1);
});

it("holds drafts for explicit review when a normal reload finds changed source evidence", async () => {
  mocks.partMappings.mockResolvedValueOnce(detail()).mockResolvedValueOnce(detail(
    [group("mcl-shaft","5. Material Cut List Price","Shaft",10,{evidenceFingerprint:"changed-evidence"})], {sourceHash:"hash2"}));
  render(<PartMappingView path="pilot.ods"/>);
  await choosePart();
  fireEvent.click(screen.getByRole("button", {name:"Reload review"}));
  expect(await screen.findByRole("region", {name:"Drafts held for source review"})).toBeTruthy();
  expect(screen.getByText(/Pending choices were held for explicit review/)).toBeTruthy();
  expect((screen.getByRole("combobox", {name:"Part assignment for Shaft"}) as HTMLInputElement).value).toBe("No Part selected");
  expect(mocks.savePartMappings).not.toHaveBeenCalled();
});

it("supports keyboard navigation in the searchable Part combobox", async () => {
  render(<PartMappingView path="pilot.ods"/>);
  await expandGroup("Shaft");
  const combo = await screen.findByRole("combobox", {name:"Part assignment for Shaft"});
  fireEvent.focus(combo);
  await screen.findByRole("option", {name:/SM-P-000002/});
  const popover = screen.getByRole("listbox", {name:"Matching canonical Parts"});
  expect(popover.closest(".part-source-table")).toBeNull();
  expect(getComputedStyle(popover).position).toBe("fixed");
  fireEvent.keyDown(combo, {key:"ArrowDown"});
  fireEvent.keyDown(combo, {key:"Enter"});
  await waitFor(() => expect((combo as HTMLInputElement).value).toContain("SM-P-000002"));
});

it("uses a focus-managed overlay context drawer at narrow widths", async () => {
  const originalWidth = window.innerWidth;
  const originalPreference = localStorage.getItem("part-mapping:context-open");
  localStorage.removeItem("part-mapping:context-open");
  Object.defineProperty(window, "innerWidth", {configurable:true,value:390});
  try {
    render(<PartMappingView path="pilot.ods"/>);
    await waitFor(() => expect(document.querySelector(".part-mapping-shell")?.classList.contains("narrow")).toBe(true));
    const show = await waitFor(() => {
      const toggle = document.querySelector<HTMLButtonElement>(".part-context-mobile-toggle");
      expect(toggle?.textContent).toBe("Show supporting information");
      return toggle!;
    });
    fireEvent.click(show);
    const panel = document.querySelector(".part-context-panel")!;
    await waitFor(() => expect(panel.classList.contains("open")).toBe(true));
    expect(screen.getByRole("button", {name:"Close Mapping context panel"})).toBeTruthy();
    fireEvent.keyDown(document, {key:"Escape"});
    await waitFor(() => expect(panel.classList.contains("closed")).toBe(true));
    await waitFor(() => expect(document.activeElement).toBe(show));
  } finally {
    Object.defineProperty(window, "innerWidth", {configurable:true,value:originalWidth});
    if (originalPreference === null) localStorage.removeItem("part-mapping:context-open");
    else localStorage.setItem("part-mapping:context-open", originalPreference);
  }
});

it("keeps collapsed-group and source-detail expansion per workbook across reloads", async () => {
  const first = render(<PartMappingView path="pilot.ods"/>);
  await expandGroup("Shaft");
  fireEvent.click(screen.getByRole("button", {name:"Expand source details"}));
  await waitFor(() => expect(JSON.parse(sessionStorage.getItem("part-mapping:pilot.ods") || "{}")).toMatchObject({
    expandedGroups:{"mcl-shaft":true},expandedSourceDetails:{"mcl-shaft":true}}));
  first.unmount();
  render(<PartMappingView path="pilot.ods"/>);
  const groupToggle = await screen.findByRole("button", {name:/source evidence · Shaft/});
  await waitFor(() => expect(groupToggle.getAttribute("aria-expanded")).toBe("true"));
  expect(screen.getByRole("button", {name:"Show key fields"})).toBeTruthy();
});

it("does not show or select stale search results while a newer query is loading", async () => {
  let resolveShaft!: (value: any) => void;
  let resolveBracket!: (value: any) => void;
  mocks.searchParts.mockImplementation((query:string) => {
    if (!query) return Promise.resolve({items:[chassis],total:1});
    if (query === "shaft") return new Promise(resolve => { resolveShaft = resolve; });
    if (query === "bracket") return new Promise(resolve => { resolveBracket = resolve; });
    return Promise.resolve({items:[],total:0});
  });
  render(<PartMappingView path="pilot.ods"/>);
  await expandGroup("Shaft");
  const combo = await screen.findByRole("combobox", {name:"Part assignment for Shaft"});
  fireEvent.focus(combo);
  expect(await screen.findByRole("option", {name:/SM-P-000001/})).toBeTruthy();
  fireEvent.change(combo, {target:{value:"shaft"}});
  expect(screen.getByRole("status").textContent).toContain("Searching");
  expect(screen.queryByRole("option", {name:/SM-P-000001/})).toBeNull();
  fireEvent.keyDown(combo, {key:"Enter"});
  expect(screen.queryByText("Unsaved")).toBeNull();
  await waitFor(() => expect(mocks.searchParts.mock.calls.some(call => call[0] === "shaft")).toBe(true));
  fireEvent.change(combo, {target:{value:"bracket"}});
  await waitFor(() => expect(mocks.searchParts.mock.calls.some(call => call[0] === "bracket")).toBe(true));
  await act(async () => resolveBracket({items:[bracket],total:1}));
  expect(await screen.findByRole("option", {name:/SM-P-000002/})).toBeTruthy();
  await act(async () => resolveShaft({items:[chassis],total:1}));
  expect(screen.queryByRole("option", {name:/SM-P-000001/})).toBeNull();
  fireEvent.keyDown(combo, {key:"Enter"});
  await waitFor(() => expect((combo as HTMLInputElement).value).toContain("SM-P-000002"));
});

it("establishes Rev A requirements only after explicit source-family confirmations", async () => {
  const saved = group("mcl-shaft","5. Material Cut List Price","Shaft",10,{part:chassis,reviewed:true,version:1});
  mocks.partMappings.mockResolvedValue(detail([saved],{version:1,unresolvedGroups:0}));
  const review = {baselineStatus:"not_established",source:{fileId:"pilot.ods",workbookPath:"pilot.ods",sourceHash:"hash1",associationKey:"assoc1",associationVersion:2,mappingVersion:1,mappingPolicyVersion:"part-source-labels-v2"},
    families:{mcl:{sheet:"5. Material Cut List Price",sourceEvidenceStatus:"ok",mappedGroupCount:1,requirementCount:1,applicabilityStatus:"unconfirmed"},
      toolshop:{sheet:"Tool Shop Items",sourceEvidenceStatus:"ok",mappedGroupCount:0,requirementCount:0,applicabilityStatus:"unconfirmed"},
      cnc:{sheet:"CNC Cut List",sourceEvidenceStatus:"ok",mappedGroupCount:0,requirementCount:0,applicabilityStatus:"unconfirmed"}},
    existingContent:{processRequirements:0,components:0,purchaseSpecifications:0,requiresAppendConfirmation:false}};
  mocks.partBaselineReview.mockResolvedValueOnce(review).mockResolvedValue({...review,baselineStatus:"established"});
  mocks.establishPartBaseline.mockResolvedValue({baselineStatus:"established",engineeringRevision:"A",finalizationRequired:true});
  render(<PartMappingView path="pilot.ods"/>);
  fireEvent.click(await screen.findByRole("button", {name:/Manufacturing baseline & workbook comparison/}));
  fireEvent.click(await screen.findByRole("button", {name:"Review this workbook"}));
  await waitFor(() => expect(document.querySelectorAll(".part-baseline-family")).toHaveLength(3));
  const familyCards = document.querySelectorAll(".part-baseline-family");
  fireEvent.change(within(familyCards[0]).getByLabelText("Applicability"), {target:{value:"applicable"}});
  fireEvent.click(within(familyCards[0]).getByLabelText("All applicable rows for this Part are mapped and complete"));
  for (const index of [1,2]) {
    fireEvent.change(within(familyCards[index]).getByLabelText("Applicability"), {target:{value:"not_applicable"}});
    fireEvent.click(within(familyCards[index]).getByLabelText("I confirm this source family does not apply"));
  }
  fireEvent.click(screen.getByRole("button", {name:"Establish Part baseline"}));
  await waitFor(() => expect(mocks.establishPartBaseline).toHaveBeenCalledTimes(1));
  expect(mocks.establishPartBaseline.mock.calls[0][1]).toMatchObject({expectedHash:"hash1",expectedMappingVersion:1,
    sourceFamilies:{mcl:{status:"applicable",confirmedComplete:true},toolshop:{status:"not_applicable",confirmedComplete:true},cnc:{status:"not_applicable",confirmedComplete:true}}});
  expect(await screen.findByText(/Initial manufacturing baseline is published in Grist/)).toBeTruthy();
  expect(mocks.savePartMappings).not.toHaveBeenCalled();
});

it("requires an explicit baseline-line choice before resolving an ambiguous material correspondence", async () => {
  const saved = group("mcl-shaft", "5. Material Cut List Price", "Shaft", 10, {part:chassis, reviewed:true, version:1});
  mocks.partMappings.mockResolvedValue(detail([saved], {version:1, unresolvedGroups:0}));
  const review = {baselineStatus:"established",source:{fileId:"pilot.ods",workbookPath:"pilot.ods",sourceHash:"hash1",associationKey:"assoc1",associationVersion:2,mappingVersion:1,mappingPolicyVersion:"part-source-labels-v2"},
    families:{mcl:{sheet:"5. Material Cut List Price",sourceEvidenceStatus:"ok",mappedGroupCount:1,requirementCount:1,applicabilityStatus:"unconfirmed"},
      toolshop:{sheet:"Tool Shop Items",sourceEvidenceStatus:"ok",mappedGroupCount:0,requirementCount:0,applicabilityStatus:"unconfirmed"},
      cnc:{sheet:"CNC Cut List",sourceEvidenceStatus:"ok",mappedGroupCount:0,requirementCount:0,applicabilityStatus:"unconfirmed"}},
    existingContent:{processRequirements:1,components:0,purchaseSpecifications:0,requiresAppendConfirmation:false}};
  const comparison = {comparison:{ComparisonKey:"compare-1",Status:"complete",WorkbookName:"pilot.ods",SourceHash:"hash1",BaselineSourceHash:"baseline-hash",MatchCount:0,AdditionCount:0,ModificationCount:0,DeletionCount:0,AmbiguousCount:1},
    families:[],decisions:[],proposals:[],evidenceWarnings:[],differences:[
      {DifferenceKey:"amb-incoming",DifferenceType:"ambiguous_correspondence",Family:"mcl",Status:"pending_review",MappingGroupKey:"mcl-shaft",SourceSheet:"5. Material Cut List Price",SourceRow:10,
        CandidateBaselineLines:'["line-1"]',IncomingObservation:99,IncomingValues:'{"physical":{"material":"Aluminum","dimension":"3 x 25","quantity":"2","item_name":"Plate 1"}}',SourceCellEvidence:"C10"},
      {DifferenceKey:"amb-baseline",DifferenceType:"ambiguous_correspondence",Family:"mcl",Status:"pending_review",BaselineRequirementKey:"line-1",BaselineRevisionLine:14,
        BaselineValues:'{"physical":{"material":"Steel","dimension":"3 x 25","quantity":"2","item_name":"Plate 1"}}'}]};
  mocks.partBaselineReview.mockResolvedValue(review);
  mocks.comparePartBaseline.mockResolvedValue(comparison);
  mocks.decidePartManufacturingComparison.mockResolvedValue({...comparison,differences:[{...comparison.differences[0],Status:"matched"}]});
  render(<PartMappingView path="pilot.ods"/>);
  fireEvent.click(await screen.findByRole("button", {name:/Manufacturing baseline & workbook comparison/}));
  fireEvent.click(await screen.findByRole("button", {name:"Review this workbook"}));
  await waitFor(() => expect(document.querySelectorAll(".part-baseline-family")).toHaveLength(3));
  const familyCards = document.querySelectorAll(".part-baseline-family");
  fireEvent.change(within(familyCards[0]).getByLabelText("Applicability"), {target:{value:"applicable"}});
  fireEvent.click(within(familyCards[0]).getByLabelText("All applicable rows for this Part are mapped and complete"));
  for (const index of [1,2]) {
    fireEvent.change(within(familyCards[index]).getByLabelText("Applicability"), {target:{value:"not_applicable"}});
    fireEvent.click(within(familyCards[index]).getByLabelText("I confirm this source family does not apply"));
  }
  fireEvent.click(screen.getByRole("button", {name:"Compare incoming workbook"}));
  await screen.findByText("Workbook comparison · complete");
  fireEvent.change(screen.getByLabelText("Decision reason"), {target:{value:"Confirmed material was substituted"}});
  const matchSelector = screen.getByLabelText("Match this incoming row to a baseline requirement") as HTMLSelectElement;
  fireEvent.change(matchSelector, {target:{value:"line-1"}});
  fireEvent.click(screen.getByRole("button", {name:"Match to baseline requirement"}));
  await waitFor(() => expect(mocks.decidePartManufacturingComparison).toHaveBeenCalledTimes(1));
  expect(mocks.decidePartManufacturingComparison.mock.calls[0][0]).toBe("compare-1");
  expect(mocks.decidePartManufacturingComparison.mock.calls[0][1]).toMatchObject({action:"match_correspondence",differenceKeys:["amb-incoming"],baselineRequirementKey:"line-1",reason:"Confirmed material was substituted"});
});

it("keeps saved assignments distinct from an explicit audited clear", async () => {
  const saved = group("mcl-shaft","5. Material Cut List Price","Shaft",10,{part:chassis,reviewed:true,version:1});
  mocks.partMappings.mockResolvedValue(detail([saved],{version:1,unresolvedGroups:0}));
  render(<PartMappingView path="pilot.ods"/>);
  await screen.findByText("Saved");
  await choosePart("Shaft");
  fireEvent.click(screen.getByRole("button", {name:"Clear saved Part…"}));
  expect(screen.getByText("Unsaved")).toBeTruthy();
  expect((screen.getByRole("combobox", {name:"Part assignment for Shaft"}) as HTMLInputElement).value).toBe("No Part selected");
  fireEvent.change(screen.getByLabelText("Reason for mapping change · Shaft"), {target:{value:"Assignment was made in error"}});
  fireEvent.click(screen.getByRole("button", {name:"Save"}));
  await waitFor(() => expect(mocks.savePartMappings).toHaveBeenCalledTimes(1));
  expect(mocks.savePartMappings.mock.calls[0][0].decisions).toEqual({"mcl-shaft":""});
});

it("shows a compact reason only when replacing an existing mapping", async () => {
  const saved = group("mcl-shaft","5. Material Cut List Price","Shaft",10,{part:chassis,reviewed:true,version:1});
  mocks.partMappings.mockResolvedValue(detail([saved],{version:1,unresolvedGroups:0}));
  render(<PartMappingView path="pilot.ods"/>);
  await screen.findByText("Saved");
  expect(screen.queryByRole("textbox", {name:/Reason for mapping change/})).toBeNull();
  await choosePart("Shaft",1);
  expect(screen.getByLabelText("Reason for mapping change · Shaft")).toBeTruthy();
  expect((screen.getByRole("button", {name:"Save"}) as HTMLButtonElement).disabled).toBe(true);
});

it("retracts the context panel without unmounting an unresolved baseline publication", async () => {
  const saved = group("mcl-shaft","5. Material Cut List Price","Shaft",10,{part:chassis,reviewed:true,version:1});
  mocks.partMappings.mockResolvedValue(detail([saved],{version:1,unresolvedGroups:0}));
  mocks.partBaselineReview.mockResolvedValue({baselineStatus:"not_established",source:{fileId:"pilot.ods",workbookPath:"pilot.ods",sourceHash:"hash1",associationKey:"assoc1",associationVersion:2,mappingVersion:1,mappingPolicyVersion:"part-source-labels-v2"},
    families:{mcl:{sheet:"5. Material Cut List Price",sourceEvidenceStatus:"ok",mappedGroupCount:1,requirementCount:1,applicabilityStatus:"unconfirmed"},
      toolshop:{sheet:"Tool Shop Items",sourceEvidenceStatus:"ok",mappedGroupCount:0,requirementCount:0,applicabilityStatus:"unconfirmed"},
      cnc:{sheet:"CNC Cut List",sourceEvidenceStatus:"ok",mappedGroupCount:0,requirementCount:0,applicabilityStatus:"unconfirmed"}},
    existingContent:{processRequirements:0,components:0,purchaseSpecifications:0,requiresAppendConfirmation:false}});
  mocks.establishPartBaseline.mockRejectedValueOnce(new Error("response lost"));
  render(<PartMappingView path="pilot.ods"/>);
  fireEvent.click(await screen.findByRole("button", {name:/Manufacturing baseline & workbook comparison/}));
  const reviewButton = screen.getByRole("button", {name:"Review this workbook"});
  await waitFor(() => expect(reviewButton).toHaveProperty("disabled", false));
  fireEvent.click(reviewButton);
  await waitFor(() => expect(document.querySelectorAll(".part-baseline-family")).toHaveLength(3));
  const families = document.querySelectorAll(".part-baseline-family");
  fireEvent.change(within(families[0]).getByLabelText("Applicability"), {target:{value:"applicable"}});
  fireEvent.click(within(families[0]).getByLabelText("All applicable rows for this Part are mapped and complete"));
  for (const index of [1,2]) {
    fireEvent.change(within(families[index]).getByLabelText("Applicability"), {target:{value:"not_applicable"}});
    fireEvent.click(within(families[index]).getByLabelText("I confirm this source family does not apply"));
  }
  fireEvent.click(screen.getByRole("button", {name:"Establish Part baseline"}));
  await screen.findByRole("alert");
  const attemptKey = "part-baseline-attempt:pilot.ods:part-1";
  const attemptBefore = localStorage.getItem(attemptKey);
  expect(JSON.parse(attemptBefore || "{}")).toMatchObject({operation:"establish",partId:"part-1",key:"request-key"});
  const panel = document.querySelector(".part-context-panel")!;
  const baselineComponent = panel.querySelector(".part-baseline-panel");
  const desktopToggle = document.querySelector<HTMLButtonElement>(".part-context-desktop-toggle")!;
  fireEvent.click(desktopToggle);
  expect(panel.classList.contains("closed")).toBe(true);
  expect(panel.getAttribute("aria-hidden")).toBe("true");
  expect(panel.querySelector(".part-baseline-panel")).toBe(baselineComponent);
  expect(localStorage.getItem(attemptKey)).toBe(attemptBefore);
  fireEvent.click(desktopToggle);
  expect(panel.classList.contains("open")).toBe(true);
  expect(panel.querySelector(".part-baseline-panel")).toBe(baselineComponent);
  expect(screen.getByRole("button", {name:"Retry baseline publication"})).toBeTruthy();
  expect(mocks.partBaselineReview).toHaveBeenCalledTimes(1);
  expect(mocks.establishPartBaseline).toHaveBeenCalledTimes(1);
});

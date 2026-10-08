import {cleanup, fireEvent, render, screen, waitFor, within} from "@testing-library/react";
import {afterEach, beforeEach, expect, it, vi} from "vitest";
import {PartMappingView} from "../src/PartMappingView";

const mocks = vi.hoisted(() => ({partMappings: vi.fn(), searchParts: vi.fn(), savePartMappings: vi.fn()}));
vi.mock("../src/api", () => ({api: mocks}));

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
beforeEach(() => {
  Object.values(mocks).forEach(mock => mock.mockReset());
  vi.stubGlobal("crypto", {randomUUID: () => "request-key"});
  sessionStorage.clear();
  mocks.partMappings.mockResolvedValue(detail());
  mocks.searchParts.mockResolvedValue({items:[chassis,bracket],total:2,offset:0,limit:30,hasMore:false});
  mocks.savePartMappings.mockResolvedValue({savedRows:1,version:1,idempotent:false});
});
afterEach(() => {cleanup(); vi.unstubAllGlobals();});

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
  expect(screen.getByRole("columnheader", {name:"Material to Cut"})).toBeTruthy();
  expect(screen.getByRole("columnheader", {name:"Plate Part to Cut"})).toBeTruthy();
  expect(screen.getByText("Plate 1")).toBeTruthy();
  expect(screen.getByText("MS Plate")).toBeTruthy();
  expect(screen.getByText("row 42 · headers row 8")).toBeTruthy();
});

it("saves only one group, retries the identical request, and reloads saved state", async () => {
  const savedDetail = detail([group("mcl-shaft","5. Material Cut List Price","Shaft",10,{part:chassis,reviewed:true,version:1})], {version:1,unresolvedGroups:0});
  mocks.savePartMappings.mockRejectedValueOnce(new Error("response lost")).mockResolvedValue({savedRows:1,version:1});
  mocks.partMappings.mockResolvedValueOnce(detail()).mockResolvedValueOnce(savedDetail);
  render(<PartMappingView path="pilot.ods"/>);
  await choosePart();
  fireEvent.change(screen.getByLabelText("Reason for these assignments"), {target:{value:"Reviewed shaft"}});
  fireEvent.click(screen.getByRole("button", {name:"Save this mapping"}));
  await screen.findByRole("alert");
  expect((screen.getByRole("combobox", {name:"Part assignment for Shaft"}) as HTMLInputElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole("button", {name:"Retry saving this mapping"}));
  expect(await screen.findByText("Saved")).toBeTruthy();
  expect(mocks.savePartMappings.mock.calls[1]).toEqual(mocks.savePartMappings.mock.calls[0]);
  expect(mocks.savePartMappings.mock.calls[0][0]).toMatchObject({path:"pilot.ods",expectedHash:"hash1",expectedVersion:0,
    expectedAssociationKey:"assoc1",expectedAssociationVersion:2,decisions:{"mcl-shaft":"part-1"},reason:"Reviewed shaft"});
});

it("keeps hidden drafts and names every pending group in batch confirmation", async () => {
  const second = group("tool-bracket","Tool Shop Items","Bracket",18);
  mocks.partMappings.mockResolvedValue(detail([group(),second]));
  render(<PartMappingView path="pilot.ods"/>);
  await choosePart("Shaft",0);
  await choosePart("Bracket",1);
  fireEvent.change(screen.getByLabelText("Reason for these assignments"), {target:{value:"Reviewed both source rows"}});
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

it("persists temporary drafts and exact source context when browsing Parts, then restores the chosen Part", async () => {
  const onOpenParts = vi.fn();
  const view = render(<PartMappingView path="pilot.ods" onOpenParts={onOpenParts}/>);
  await choosePart();
  fireEvent.change(screen.getByLabelText("Search descriptions or source fields"), {target:{value:"MS Plate"}});
  fireEvent.change(screen.getByLabelText("Source sheet"), {target:{value:"mcl"}});
  fireEvent.change(screen.getByLabelText("Reason for these assignments"), {target:{value:"Review in progress"}});
  const shaftCard = screen.getByText("Shaft").closest(".part-source-group")!;
  fireEvent.click(within(shaftCard).getByRole("button", {name:"Browse / create Part", exact:true}));
  expect(onOpenParts).toHaveBeenCalledWith("mcl-shaft","part-1","select",expect.objectContaining({sourceHash:"hash1",evidenceFingerprint:"evidence-mcl-shaft"}));
  expect(JSON.parse(sessionStorage.getItem("part-mapping:pilot.ods") || "{}")).toMatchObject({decisions:{"mcl-shaft":"part-1"},reason:"Review in progress",query:"MS Plate",sheetFilter:"mcl"});
  view.unmount();
  render(<PartMappingView path="pilot.ods" returnSelection={{path:"pilot.ods",groupKey:"mcl-shaft",partId:"part-2",mode:"select",sourceHash:"hash1",associationKey:"assoc1",associationVersion:2,evidenceFingerprint:"evidence-mcl-shaft"}} onReturnSelectionConsumed={vi.fn()}/>);
  await waitFor(() => expect((screen.getByRole("combobox", {name:"Part assignment for Shaft"}) as HTMLInputElement).value).toContain("SM-P-000002"));
  expect(screen.getByLabelText("Search descriptions or source fields")).toHaveProperty("value","MS Plate");
  expect(screen.getByLabelText("Reason for these assignments")).toHaveProperty("value","Review in progress");
  expect(mocks.savePartMappings).not.toHaveBeenCalled();
});

it("rejects a Part returned from Parts when that source group changed", async () => {
  mocks.searchParts.mockResolvedValue({items:[chassis],total:1});
  render(<PartMappingView path="pilot.ods" returnSelection={{path:"pilot.ods",groupKey:"mcl-shaft",partId:"part-1",mode:"select",sourceHash:"hash1",associationKey:"assoc1",associationVersion:2,evidenceFingerprint:"old-evidence"}}/>);
  expect(await screen.findByText("The source group changed while Parts was open. Review the refreshed group before assigning a Part.")).toBeTruthy();
  expect((screen.getByRole("combobox", {name:"Part assignment for Shaft"}) as HTMLInputElement).value).toBe("No Part selected");
});

it("holds stale selections until the refreshed source is explicitly reviewed", async () => {
  mocks.partMappings.mockResolvedValueOnce(detail()).mockResolvedValueOnce(detail([group("mcl-shaft","5. Material Cut List Price","Shaft",10,{evidenceFingerprint:"changed-evidence"})],{sourceHash:"hash2"}));
  mocks.savePartMappings.mockRejectedValueOnce(new Error("PART_REVIEW_STALE: The workbook changed; reload the review"));
  render(<PartMappingView path="pilot.ods"/>);
  await choosePart();
  fireEvent.change(screen.getByLabelText("Reason for these assignments"), {target:{value:"Review changed source"}});
  fireEvent.click(screen.getByRole("button", {name:"Save this mapping"}));
  await screen.findByRole("alert");
  fireEvent.click(screen.getByRole("button", {name:"Review current source"}));
  expect(await screen.findByRole("region", {name:"Drafts held for source review"})).toBeTruthy();
  expect(screen.getByText(/previous choice: SM-P-000001/)).toBeTruthy();
  expect((screen.getByRole("combobox", {name:"Part assignment for Shaft"}) as HTMLInputElement).value).toBe("No Part selected");
  fireEvent.click(screen.getByRole("button", {name:"Reapply after reviewing current rows"}));
  expect((screen.getByRole("combobox", {name:"Part assignment for Shaft"}) as HTMLInputElement).value).toContain("SM-P-000001");
  expect(screen.getByLabelText("Reason for these assignments")).toHaveProperty("value","Review changed source");
  expect(mocks.savePartMappings).toHaveBeenCalledTimes(1);
});

it("holds drafts for explicit review when a normal reload finds changed source evidence", async () => {
  mocks.partMappings.mockResolvedValueOnce(detail()).mockResolvedValueOnce(detail(
    [group("mcl-shaft","5. Material Cut List Price","Shaft",10,{evidenceFingerprint:"changed-evidence"})], {sourceHash:"hash2"}));
  render(<PartMappingView path="pilot.ods"/>);
  await choosePart();
  fireEvent.change(screen.getByLabelText("Reason for these assignments"), {target:{value:"Review after editing workbook"}});
  fireEvent.click(screen.getByRole("button", {name:"Reload review"}));
  expect(await screen.findByRole("region", {name:"Drafts held for source review"})).toBeTruthy();
  expect(screen.getByLabelText("Reason for these assignments")).toHaveProperty("value","Review after editing workbook");
  expect(screen.getByText(/Pending choices were held for explicit review/)).toBeTruthy();
  expect((screen.getByRole("combobox", {name:"Part assignment for Shaft"}) as HTMLInputElement).value).toBe("No Part selected");
  expect(mocks.savePartMappings).not.toHaveBeenCalled();
});

it("supports keyboard navigation in the searchable Part combobox", async () => {
  render(<PartMappingView path="pilot.ods"/>);
  const combo = await screen.findByRole("combobox", {name:"Part assignment for Shaft"});
  fireEvent.focus(combo);
  await screen.findByRole("option", {name:/SM-P-000002/});
  fireEvent.keyDown(combo, {key:"ArrowDown"});
  fireEvent.keyDown(combo, {key:"Enter"});
  await waitFor(() => expect((combo as HTMLInputElement).value).toContain("SM-P-000002"));
});

it("keeps saved assignments distinct from an explicit audited clear", async () => {
  const saved = group("mcl-shaft","5. Material Cut List Price","Shaft",10,{part:chassis,reviewed:true,version:1});
  mocks.partMappings.mockResolvedValue(detail([saved],{version:1,unresolvedGroups:0}));
  render(<PartMappingView path="pilot.ods"/>);
  await screen.findByText("Saved");
  fireEvent.click(screen.getByRole("button", {name:"Clear saved Part…"}));
  expect(screen.getByText("Unsaved changes")).toBeTruthy();
  expect((screen.getByRole("combobox", {name:"Part assignment for Shaft"}) as HTMLInputElement).value).toBe("No Part selected");
  fireEvent.change(screen.getByLabelText("Reason for these assignments"), {target:{value:"Assignment was made in error"}});
  fireEvent.click(screen.getByRole("button", {name:"Save this mapping"}));
  await waitFor(() => expect(mocks.savePartMappings).toHaveBeenCalledTimes(1));
  expect(mocks.savePartMappings.mock.calls[0][0].decisions).toEqual({"mcl-shaft":""});
});

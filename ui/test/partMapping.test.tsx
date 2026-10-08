import {act, cleanup, fireEvent, render, screen, waitFor, within} from "@testing-library/react";
import {afterEach, beforeEach, expect, it, vi} from "vitest";
import {PartMappingView} from "../src/PartMappingView";

const mocks = vi.hoisted(() => ({partMappings: vi.fn(), searchParts: vi.fn(), savePartMappings: vi.fn(),
  partBaselineReview: vi.fn(), establishPartBaseline: vi.fn(), comparePartBaseline: vi.fn(), decidePartManufacturingComparison: vi.fn()}));
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
});
afterEach(() => {cleanup(); vi.unstubAllGlobals();});

async function expandGroup(label = "Shaft") {
  const toggles = await screen.findAllByRole("button", {name:/Source Part/});
  const toggle = toggles.find(button => button.textContent?.includes(label));
  if (toggle?.getAttribute("aria-expanded") === "false") fireEvent.click(toggle);
}
async function choosePart(label = "Shaft", index = 0) {
  await expandGroup(label);
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
  const groupToggle = screen.getByRole("button", {name:/CNC Cut List Frame Source Part/});
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
  fireEvent.click(screen.getByRole("button", {name:"Save this mapping"}));
  await screen.findByRole("alert");
  expect((screen.getByRole("combobox", {name:"Part assignment for Shaft"}) as HTMLInputElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole("button", {name:"Retry saving this mapping"}));
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

it("persists temporary drafts and exact source context when browsing Parts, then restores the chosen Part", async () => {
  const onOpenParts = vi.fn();
  const view = render(<PartMappingView path="pilot.ods" onOpenParts={onOpenParts}/>);
  await choosePart();
  fireEvent.change(screen.getByLabelText("Search descriptions or source fields"), {target:{value:"MS Plate"}});
  fireEvent.change(screen.getByLabelText("Source sheet"), {target:{value:"mcl"}});
  fireEvent.change(screen.getByLabelText("Search descriptions or source fields"), {target:{value:"MS Plate"}});
  const shaftCard = screen.getByText("Shaft").closest(".part-source-group")!;
  fireEvent.click(within(shaftCard).getByRole("button", {name:"Browse / create Part", exact:true}));
  expect(onOpenParts).toHaveBeenCalledWith("mcl-shaft","part-1","select",expect.objectContaining({sourceHash:"hash1",evidenceFingerprint:"evidence-mcl-shaft"}));
  expect(JSON.parse(sessionStorage.getItem("part-mapping:pilot.ods") || "{}")).toMatchObject({decisions:{"mcl-shaft":"part-1"},query:"MS Plate",sheetFilter:"mcl"});
  view.unmount();
  render(<PartMappingView path="pilot.ods" returnSelection={{path:"pilot.ods",groupKey:"mcl-shaft",partId:"part-2",mode:"select",sourceHash:"hash1",associationKey:"assoc1",associationVersion:2,evidenceFingerprint:"evidence-mcl-shaft"}} onReturnSelectionConsumed={vi.fn()}/>);
  await waitFor(() => expect((screen.getByRole("combobox", {name:"Part assignment for Shaft"}) as HTMLInputElement).value).toContain("SM-P-000002"));
  expect(screen.getByLabelText("Search descriptions or source fields")).toHaveProperty("value","MS Plate");
  expect(mocks.savePartMappings).not.toHaveBeenCalled();
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
  fireEvent.click(screen.getByRole("button", {name:"Save this mapping"}));
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
  fireEvent.keyDown(combo, {key:"ArrowDown"});
  fireEvent.keyDown(combo, {key:"Enter"});
  await waitFor(() => expect((combo as HTMLInputElement).value).toContain("SM-P-000002"));
});

it("keeps collapsed-group and source-detail expansion per workbook across reloads", async () => {
  const first = render(<PartMappingView path="pilot.ods"/>);
  await expandGroup("Shaft");
  fireEvent.click(screen.getByRole("button", {name:"Expand source details"}));
  await waitFor(() => expect(JSON.parse(sessionStorage.getItem("part-mapping:pilot.ods") || "{}")).toMatchObject({
    expandedGroups:{"mcl-shaft":true},expandedSourceDetails:{"mcl-shaft":true}}));
  first.unmount();
  render(<PartMappingView path="pilot.ods"/>);
  const groupToggle = await screen.findByRole("button", {name:/5\. Material Cut List Price Shaft/});
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
  await screen.findByText("5. Material Cut List Price");
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
  await screen.findByText("5. Material Cut List Price");
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
  fireEvent.click(screen.getByRole("button", {name:"Save this mapping"}));
  await waitFor(() => expect(mocks.savePartMappings).toHaveBeenCalledTimes(1));
  expect(mocks.savePartMappings.mock.calls[0][0].decisions).toEqual({"mcl-shaft":""});
});

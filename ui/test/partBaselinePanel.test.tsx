import {cleanup, fireEvent, render, screen, waitFor, within} from "@testing-library/react";
import {afterEach, beforeEach, expect, it, vi} from "vitest";
import {PartBaselinePanel} from "../src/PartBaselinePanel";

const apiMocks = vi.hoisted(() => ({
  partBaselineReview: vi.fn(), establishPartBaseline: vi.fn(), comparePartBaseline: vi.fn(),
  partManufacturingComparison: vi.fn(), decidePartManufacturingComparison: vi.fn(), searchParts: vi.fn(),
}));
vi.mock("../src/api", () => ({api: apiMocks}));

const part = {id:"part-1", partNumber:"SM-P-000001", name:"S1KHF — Chassis", description:"Chassis", variant:"Standard",
  engineeringRevision:"A", selectable:true, duplicateName:false, outOfScopeCodes:[]};
const replacement = {id:"part-2", partNumber:"SM-P-000002", name:"S1KHF — Alternate Chassis", description:"Alternate Chassis", variant:"Standard",
  engineeringRevision:"A", selectable:true, duplicateName:false, outOfScopeCodes:[]};
const group = {key:"mcl:chassis", sheet:"5. Material Cut List Price", evidenceFingerprint:"evidence-chassis", rows:[], description:"Chassis"};

function review(baselineStatus = "not_established", requiresAppendConfirmation = false) {
  return {baselineStatus, source:{fileId:"pilot.ods", workbookPath:"C:/isolated/pilot.ods", sourceHash:"workbook-hash",
    associationKey:"association-1", associationVersion:2, mappingVersion:4, mappingPolicyVersion:"part-source-labels-v2"},
    families:{
      mcl:{family:"mcl",sheet:"5. Material Cut List Price",sourceEvidenceStatus:"ok",mappedGroupCount:1,requirementCount:1,applicabilityStatus:"unconfirmed",completenessConfirmed:false},
      toolshop:{family:"toolshop",sheet:"Tool Shop Items",sourceEvidenceStatus:"ok",mappedGroupCount:0,requirementCount:0,applicabilityStatus:"unconfirmed",completenessConfirmed:false},
      cnc:{family:"cnc",sheet:"CNC Cut List",sourceEvidenceStatus:"ok",mappedGroupCount:0,requirementCount:0,applicabilityStatus:"unconfirmed",completenessConfirmed:false},
    },existingContent:{processRequirements:0,components:0,purchaseSpecifications:0,requiresAppendConfirmation}};
}

const physical = {family:"mcl",material:"aluminum",quantity:"2",quantity_uom:"nos",dimension:"3 x 25",dimension_uom:"mm",
  length:null,width:null,thickness:null,length_uom:null,width_uom:null,thickness_uom:null,weight_kg:"0.11",weight_grams:null,
  weight_uom:"kg",item_code:null,item_name:null,plate_part:null,toolshop_detail:null,item_group:"hf"};
function comparison(differenceType = "proposed_modification") {
  const incoming = {DifferenceKey:"difference-1",DifferenceType:differenceType,Status:"pending_review",Family:"mcl",
    SourceSheet:"5. Material Cut List Price",SourceRow:10,MappingGroupKey:group.key,MappingEvidenceFingerprint:group.evidenceFingerprint,
    IncomingObservation:701,IncomingValues:JSON.stringify({physical,rawFields:{material_to_cut:"Aluminum",qty:"2"}}),
    BaselineValues:JSON.stringify({physical:{...physical,material:"ms plate"},rawFields:{material_to_cut:"MS Plate"}}),
    FieldDifferences:JSON.stringify([{field:"material",baseline:"ms plate",incoming:"aluminum"}]),
    CandidateBaselineLines:differenceType === "ambiguous_correspondence" ? JSON.stringify(["requirement-1"]) : JSON.stringify([])};
  const differences = [incoming];
  if (differenceType === "ambiguous_correspondence") differences.push({DifferenceKey:"baseline-ambiguity",DifferenceType:"ambiguous_correspondence",
    Status:"pending_review",Family:"mcl",SourceSheet:"5. Material Cut List Price",SourceRow:10,
    BaselineRevisionLine:801,BaselineRequirementKey:"requirement-1",BaselineValues:JSON.stringify({physical:{...physical,material:"ms plate"}}),
    IncomingValues:"",CandidateBaselineLines:"[]"});
  return {comparison:{ComparisonKey:"comparison-1",Status:"complete",WorkbookName:"pilot.ods",SourceHash:"workbook-hash",BaselineSourceHash:"baseline-hash"},
    families:[],differences,decisions:[],proposals:[]};
}

let requestNumber = 0;
beforeEach(() => {
  Object.values(apiMocks).forEach(mock => mock.mockReset());
  localStorage.clear(); sessionStorage.clear(); requestNumber = 0;
  vi.stubGlobal("crypto", {randomUUID:() => `request-${++requestNumber}`});
  apiMocks.partBaselineReview.mockResolvedValue(review());
  apiMocks.establishPartBaseline.mockResolvedValue({baselineStatus:"established"});
  apiMocks.comparePartBaseline.mockResolvedValue(comparison());
  apiMocks.partManufacturingComparison.mockResolvedValue(comparison());
  apiMocks.decidePartManufacturingComparison.mockResolvedValue(comparison());
  apiMocks.searchParts.mockResolvedValue({items:[replacement]});
});
afterEach(() => {cleanup(); vi.unstubAllGlobals();});

function renderPanel(onUseDifferentPart = vi.fn()) {
  return render(<PartBaselinePanel path="pilot.ods" parts={[part,replacement]} groups={[group]} onUseDifferentPart={onUseDifferentPart}/>);
}

async function openAndReview() {
  fireEvent.click(screen.getByRole("button", {name:/Manufacturing baseline & workbook comparison/}));
  fireEvent.click(await screen.findByRole("button", {name:/Review this workbook/}));
  await screen.findByText("5. Material Cut List Price");
}

function completeFamilyConfirmations() {
  const mcl = screen.getByText("5. Material Cut List Price").closest(".part-baseline-family")!;
  fireEvent.change(within(mcl).getByLabelText("Applicability"), {target:{value:"applicable"}});
  fireEvent.click(within(mcl).getByLabelText("All applicable rows for this Part are mapped and complete"));
  for (const name of ["Tool Shop Items", "CNC Cut List"]) {
    const family = screen.getByText(name).closest(".part-baseline-family")!;
    fireEvent.change(within(family).getByLabelText("Applicability"), {target:{value:"not_applicable"}});
    fireEvent.click(within(family).getByLabelText("I confirm this source family does not apply"));
  }
}

it("blocks initial baseline submission and explains every missing confirmation", async () => {
  renderPanel();
  await openAndReview();
  expect(screen.getByText(/Before establishing the baseline/)).toBeTruthy();
  expect(screen.getAllByText(/choose Applicable or Confirmed not applicable/i)).toHaveLength(3);
  expect(screen.getByRole("button", {name:"Establish Part baseline"}).hasAttribute("disabled")).toBe(true);
  expect(apiMocks.establishPartBaseline).not.toHaveBeenCalled();
});

it("blocks a second baseline when Grist reports partial publication but this browser has no retry identity", async () => {
  apiMocks.partBaselineReview.mockResolvedValue(review("recovery_required"));
  renderPanel();
  await openAndReview();
  completeFamilyConfirmations();
  expect(screen.getByRole("alert").textContent).toMatch(/does not have the original request identity/i);
  expect(screen.getByRole("button", {name:"Establish Part baseline"}).hasAttribute("disabled")).toBe(true);
  expect(apiMocks.establishPartBaseline).not.toHaveBeenCalled();
});

it("unlocks editable confirmation after a definite server rejection, then submits the corrected payload", async () => {
  apiMocks.partBaselineReview.mockResolvedValueOnce(review()).mockResolvedValue(review("not_established", true));
  apiMocks.establishPartBaseline.mockRejectedValueOnce(Object.assign(new Error("Existing content needs append confirmation"), {retryDisposition:"safe_to_edit"}))
    .mockResolvedValueOnce({baselineStatus:"established"});
  renderPanel();
  await openAndReview();
  completeFamilyConfirmations();
  fireEvent.click(screen.getByRole("button", {name:"Establish Part baseline"}));
  await screen.findByRole("alert");
  const mcl = screen.getByText("5. Material Cut List Price").closest(".part-baseline-family")!;
  expect((within(mcl).getByLabelText("Applicability") as HTMLSelectElement).disabled).toBe(false);
  expect(localStorage.getItem("part-baseline-attempt:pilot.ods:part-1")).toBeNull();
  fireEvent.click(screen.getByRole("button", {name:/Review this workbook/}));
  const append = await screen.findByLabelText("Append workbook-derived lines without replacing existing content.");
  fireEvent.click(append);
  completeFamilyConfirmations();
  fireEvent.click(screen.getByRole("button", {name:"Establish Part baseline"}));
  await screen.findByText(/Initial manufacturing baseline is published in Grist/);
  expect(apiMocks.establishPartBaseline).toHaveBeenCalledTimes(2);
  expect(apiMocks.establishPartBaseline.mock.calls[0][1].appendExisting).toBe(false);
  expect(apiMocks.establishPartBaseline.mock.calls[1][1].appendExisting).toBe(true);
  expect(apiMocks.establishPartBaseline.mock.calls[1][2]).not.toBe(apiMocks.establishPartBaseline.mock.calls[0][2]);
});

it("retains an uncertain baseline publication identity and blocks competing actions through reload", async () => {
  apiMocks.partBaselineReview.mockResolvedValueOnce(review()).mockResolvedValue(review("recovery_required"));
  apiMocks.establishPartBaseline.mockRejectedValueOnce(new Error("response lost after Grist commit"))
    .mockResolvedValueOnce({baselineStatus:"established"});
  const first = renderPanel();
  await openAndReview();
  completeFamilyConfirmations();
  fireEvent.click(screen.getByRole("button", {name:"Establish Part baseline"}));
  await screen.findByRole("alert");
  const saved = JSON.parse(localStorage.getItem("part-baseline-attempt:pilot.ods:part-1") || "null");
  expect(saved).toMatchObject({operation:"establish",partId:"part-1",key:"request-1"});
  expect(screen.getByRole("button", {name:"Retry baseline publication"}).hasAttribute("disabled")).toBe(false);
  expect(screen.queryByRole("button", {name:"Compare incoming workbook"})).toBeNull();
  first.unmount();

  renderPanel();
  fireEvent.click(screen.getByRole("button", {name:/Manufacturing baseline & workbook comparison/}));
  await screen.findByRole("button", {name:"Retry baseline publication"});
  expect(screen.getByRole("button", {name:"Review this workbook"}).hasAttribute("disabled")).toBe(true);
  fireEvent.click(screen.getByRole("button", {name:"Retry baseline publication"}));
  await screen.findByText(/Initial manufacturing baseline is published in Grist/);
  expect(apiMocks.establishPartBaseline).toHaveBeenCalledTimes(2);
  expect(apiMocks.establishPartBaseline.mock.calls[1]).toEqual(apiMocks.establishPartBaseline.mock.calls[0]);
  expect(localStorage.getItem("part-baseline-attempt:pilot.ods:part-1")).toBeNull();
});

it("keeps an uncertain comparison's exact request through reload and retries it", async () => {
  apiMocks.partBaselineReview.mockResolvedValue(review("established"));
  apiMocks.comparePartBaseline.mockRejectedValueOnce(new Error("response lost after request"))
    .mockResolvedValueOnce(comparison());
  const first = renderPanel();
  await openAndReview();
  fireEvent.click(screen.getByRole("button", {name:"Compare incoming workbook"}));
  await screen.findByRole("alert");
  const stored = JSON.parse(localStorage.getItem("part-baseline-attempt:pilot.ods:part-1") || "null");
  expect(stored).toMatchObject({operation:"compare",partId:"part-1",key:"request-1"});
  expect(screen.getByRole("button", {name:"Retry comparison"}).hasAttribute("disabled")).toBe(false);
  first.unmount();

  renderPanel();
  fireEvent.click(screen.getByRole("button", {name:/Manufacturing baseline & workbook comparison/}));
  await screen.findByRole("button", {name:"Retry comparison"});
  fireEvent.click(screen.getByRole("button", {name:"Retry comparison"}));
  await screen.findByRole("heading", {name:/Workbook comparison/});
  expect(apiMocks.comparePartBaseline).toHaveBeenCalledTimes(2);
  expect(apiMocks.comparePartBaseline.mock.calls[1]).toEqual(apiMocks.comparePartBaseline.mock.calls[0]);
  expect(localStorage.getItem("part-baseline-attempt:pilot.ods:part-1")).toBeNull();
});

it.each(["keep_existing_baseline", "match_correspondence", "propose_part_change", "use_different_part"] as const)(
  "retains and retries the exact %s decision after a lost response", async action => {
    const initialComparison = comparison(action === "match_correspondence" ? "ambiguous_correspondence" : "proposed_modification");
    const recoveredComparison = {...initialComparison, idempotent:true,
      ...(action === "use_different_part" ? {replacementMapping:{groupKey:group.key}} : {})};
    apiMocks.partBaselineReview.mockResolvedValue(review("established"));
    apiMocks.comparePartBaseline.mockResolvedValue(initialComparison);
    apiMocks.decidePartManufacturingComparison.mockRejectedValueOnce(new Error("decision response was lost"))
      .mockResolvedValueOnce(recoveredComparison);
    const onUseDifferentPart = vi.fn();
    const first = renderPanel(onUseDifferentPart);
    await openAndReview();
    fireEvent.click(screen.getByRole("button", {name:"Compare incoming workbook"}));
    await screen.findByRole("heading", {name:/Workbook comparison/});
    fireEvent.change(screen.getByLabelText("Decision reason"), {target:{value:"Reviewed physical change"}});
    if (action === "match_correspondence") {
      fireEvent.change(screen.getByLabelText("Match this incoming row to a baseline requirement"), {target:{value:"requirement-1"}});
      fireEvent.click(screen.getByRole("button", {name:"Match to baseline requirement"}));
    } else if (action === "keep_existing_baseline") {
      fireEvent.click(screen.getByRole("button", {name:"Keep existing baseline"}));
    } else if (action === "propose_part_change") {
      fireEvent.click(screen.getByRole("button", {name:"Propose Part change"}));
    } else {
      fireEvent.change(screen.getByLabelText("Replacement canonical Part"), {target:{value:"Alternate"}});
      const replacementOption = await within(await screen.findByRole("listbox")).findByRole("option", {name:/SM-P-000002/});
      fireEvent.click(replacementOption);
      await waitFor(() => expect(screen.getByRole("button", {name:"Use a different Part"}).hasAttribute("disabled")).toBe(false));
      fireEvent.click(screen.getByRole("button", {name:"Use a different Part"}));
    }
    await screen.findByRole("button", {name:"Retry saved decision"});
    const attempt = JSON.parse(localStorage.getItem("part-baseline-decision-attempt:pilot.ods:part-1") || "null");
    expect(attempt).toMatchObject({comparisonKey:"comparison-1",partId:"part-1",action,status:"failed",key:"request-2"});
    expect(attempt.payload).toMatchObject({action,reason:"Reviewed physical change",path:"pilot.ods"});
    first.unmount();
    renderPanel(onUseDifferentPart);
    fireEvent.click(screen.getByRole("button", {name:/Manufacturing baseline & workbook comparison/}));
    await screen.findByRole("button", {name:"Retry saved decision"});
    fireEvent.click(screen.getByRole("button", {name:"Retry saved decision"}));
    await screen.findByText(/saved decision was recovered/);
    expect(apiMocks.decidePartManufacturingComparison).toHaveBeenCalledTimes(2);
    expect(apiMocks.decidePartManufacturingComparison.mock.calls[1]).toEqual(apiMocks.decidePartManufacturingComparison.mock.calls[0]);
    expect(localStorage.getItem("part-baseline-decision-attempt:pilot.ods:part-1")).toBeNull();
    if (action === "use_different_part") expect(onUseDifferentPart).toHaveBeenCalledWith(group.key, replacement);
  },
);

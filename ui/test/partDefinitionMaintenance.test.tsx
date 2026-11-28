import {afterEach, beforeEach, expect, it, vi} from "vitest";
import {cleanup, fireEvent, render, screen} from "@testing-library/react";
import {PartDefinitionMaintenance} from "../src/PartDefinitionMaintenance";
import {ApiError} from "../src/api";

const apiMocks = vi.hoisted(() => ({
  partDefinition: vi.fn(), partDefinitionOptions: vi.fn(), savePartDefinitionDraft: vi.fn(),
  bindPartDefinitionMaterial: vi.fn(),
  submitPartDefinitionChangeRequest: vi.fn(), reopenPartDefinitionChangeRequest: vi.fn(), approveAndPublishPartDefinition: vi.fn(),
}));
vi.mock("../src/api", () => ({api: apiMocks, ApiError: class ApiError extends Error {
  status: number; code: string; retryDisposition: string;
  constructor(message: string, status = 500, code = "", retryDisposition = "retry_same_request") {
    super(message); this.status = status; this.code = code; this.retryDisposition = retryDisposition;
  }
}}));

const accepted = {
  partId: "part-1", partNumber: "SM-P-000001", partName: "Bracket", partRecordId: 10, revisionId: 20,
  revisionLabel: "A", revisionStatus: "draft", baselineStatus: "established", finalizeOnPublication: false,
  applicability: {mcl:{applicable:true,status:"applicable"},toolshop:{applicable:false,status:"not_applicable"},cnc:{applicable:false,status:"not_applicable"}},
  requirements: [{requirementKey:"req-1",identityStatus:"stable",family:"mcl",quantity:1,uom:"Nos",processType:"material_cut",
    detail:{ItemName:"Bracket blank",Quantity:1,Length:100,Width:50,Thickness:3,DimensionUOM:"mm",EngineeringAttributes:{grain:"long"}},
    provenance:{type:"ods_baseline",evidence:{sourceRow:10}}}],
  components:[], purchases:[], revisionHistory:[{id:20,revisionLabel:"A",status:"draft",current:true}],
  source:{workbookName:"Original.ods",workbookPath:"Original.ods",sourceHash:"abc",readOnly:true},
  actions:{editPart:true,revisionHistory:true,compareWithOds:true,publicationSchemaAvailable:true},
  canApprove:false,pendingChangeRequests:[],
};

beforeEach(() => {
  Object.values(apiMocks).forEach(mock => mock.mockReset());
  localStorage.clear();
  let counter = 0;
  vi.stubGlobal("crypto", {randomUUID:() => `part-definition-${++counter}`});
  apiMocks.partDefinition.mockResolvedValue(accepted);
  apiMocks.partDefinitionOptions.mockResolvedValue({parts:[],materials:[{id:5,name:"Steel"}],purchaseItems:[],families:["mcl","toolshop","cnc"]});
  apiMocks.bindPartDefinitionMaterial.mockResolvedValue({status:"bound",materialId:5,masterMaterialId:55,identityCaptured:false});
  apiMocks.savePartDefinitionDraft.mockResolvedValue({draftKey:"draft-1",saveVersion:1,contentFingerprint:"fp",content:{}});
  apiMocks.submitPartDefinitionChangeRequest.mockResolvedValue({changeRequestKey:"cr-1",status:"pending_approval",impact:{assemblies:[],modelCodeConfigurations:[]}});
  apiMocks.approveAndPublishPartDefinition.mockResolvedValue({status:"published",publishedRevisionLabel:"B",dependentPartCount:0,configurationCount:0});
});
afterEach(() => {cleanup(); vi.unstubAllGlobals(); localStorage.clear();});

it("recovers an uncertain submission after reload with the exact saved key and payload", async () => {
  const draft = {draftKey:"draft-1",saveVersion:1,baseRevisionId:20,reason:"Review change",content:{
    baseRevisionId:20,applicability:accepted.applicability,requirements:accepted.requirements,components:[],purchases:[],finalizeOnPublication:false}};
  apiMocks.partDefinition.mockResolvedValue({...accepted,draft});
  apiMocks.submitPartDefinitionChangeRequest.mockRejectedValueOnce(new Error("response lost"));
  const props = {partId:"part-1",onSaved:vi.fn(),onCompare:vi.fn()};
  const first = render(<PartDefinitionMaintenance {...props}/>);
  fireEvent.click(await screen.findByRole("button",{name:"Edit Part"}));
  fireEvent.click(screen.getByRole("button",{name:"Request approval"}));
  await screen.findByText("response lost");
  const saved = JSON.parse(localStorage.getItem("safari.part-definition:part-1:change-request") || "null");
  first.unmount();
  apiMocks.partDefinition.mockResolvedValue(accepted);
  render(<PartDefinitionMaintenance {...props}/>);
  expect((await screen.findByRole("button",{name:"Edit Part"}) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole("button",{name:"Confirm original submission"}));
  await screen.findByText(/Change Request submitted for review/);
  expect(apiMocks.submitPartDefinitionChangeRequest.mock.calls[1]).toEqual(["part-1",JSON.parse(saved.payload),saved.key]);
  expect(localStorage.getItem("safari.part-definition:part-1:change-request")).toBeNull();
});

it("allows correction after a confirmed pre-write rejection", async () => {
  const draft = {draftKey:"draft-1",saveVersion:1,baseRevisionId:20,reason:"Review change",content:{
    baseRevisionId:20,applicability:accepted.applicability,requirements:accepted.requirements,components:[],purchases:[],finalizeOnPublication:false}};
  apiMocks.partDefinition.mockResolvedValue({...accepted,draft});
  apiMocks.submitPartDefinitionChangeRequest.mockRejectedValueOnce(new ApiError("Draft already submitted",409,"PART_DEFINITION_DRAFT_IMMUTABLE","safe_to_edit"));
  render(<PartDefinitionMaintenance partId="part-1" onSaved={vi.fn()} onCompare={vi.fn()}/>);
  fireEvent.click(await screen.findByRole("button",{name:"Edit Part"}));
  fireEvent.click(screen.getByRole("button",{name:"Request approval"}));
  await screen.findByText("Draft already submitted");
  expect(localStorage.getItem("safari.part-definition:part-1:change-request")).toBeNull();
  fireEvent.click(screen.getByRole("button",{name:"Request approval"}));
  await screen.findByText(/Change Request submitted for review/);
  expect(apiMocks.submitPartDefinitionChangeRequest.mock.calls[0][2]).not.toBe(apiMocks.submitPartDefinitionChangeRequest.mock.calls[1][2]);
});

it("keeps an uncertain submission locked after a request conflict", async () => {
  const storageKey = "safari.part-definition:part-1:change-request";
  const saved = {key:"original-key",payload:JSON.stringify({draftKey:"draft-1",reason:"original"}),status:"pending"};
  localStorage.setItem(storageKey,JSON.stringify(saved));
  apiMocks.partDefinition.mockResolvedValue({...accepted,canApprove:true,pendingChangeRequests:[{key:"cr-1",status:"pending_approval",impact:{fingerprint:"fp"}}]});
  apiMocks.submitPartDefinitionChangeRequest.mockRejectedValue(new ApiError("Key conflict",409,"PART_REQUEST_CONFLICT","retry_same_request"));
  render(<PartDefinitionMaintenance partId="part-1" onSaved={vi.fn()} onCompare={vi.fn()}/>);
  const retry = await screen.findByRole("button",{name:"Confirm original submission"});
  expect((screen.getByRole("button",{name:"Approve and publish as Irshad"}) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(retry);
  await screen.findByText("Key conflict");
  expect(JSON.parse(localStorage.getItem(storageKey) || "null")).toEqual(saved);
  expect((screen.getByRole("button",{name:"Edit Part"}) as HTMLButtonElement).disabled).toBe(true);
});

it("saves a direct edit as a Grist draft and reloads the persisted reason before CR submission", async () => {
  const withDraft = {...accepted,draft:{draftKey:"draft-1",saveVersion:1,baseRevisionId:20,reason:"Initial test edit",content:{
    baseRevisionId:20,applicability:accepted.applicability,requirements:accepted.requirements,components:[],purchases:[],finalizeOnPublication:false,
  }}};
  apiMocks.partDefinition.mockResolvedValueOnce(withDraft).mockResolvedValue({...withDraft,draft:{...withDraft.draft,reason:"Increase blank allowance"}});
  const savedContent = {baseRevisionId:20,expectedSaveVersion:1,applicability:accepted.applicability,requirements:accepted.requirements,components:[],purchases:[],finalizeOnPublication:false};
  apiMocks.savePartDefinitionDraft.mockResolvedValue({draftKey:"draft-1",saveVersion:2,contentFingerprint:"fp2",content:savedContent});
  const onSaved = vi.fn();
  render(<PartDefinitionMaintenance partId="part-1" onSaved={onSaved} onCompare={vi.fn()}/>);

  fireEvent.click(await screen.findByRole("button",{name:"Edit Part"}));
  expect((screen.getByLabelText("Engineering change reason") as HTMLInputElement).value).toBe("Initial test edit");
  fireEvent.change(screen.getByLabelText("Quantity — MCL row 1"),{target:{value:"2"}});
  fireEvent.change(screen.getByLabelText("Engineering change reason"),{target:{value:"Increase blank allowance"}});
  fireEvent.click(screen.getByRole("button",{name:"Save draft"}));
  await screen.findByText(/Draft saved in Safari Grist/);
  expect(apiMocks.savePartDefinitionDraft).toHaveBeenCalledTimes(1);
  expect(apiMocks.savePartDefinitionDraft.mock.calls[0][1].requirements[0]).toMatchObject({requirementKey:"req-1",quantity:"2"});
  expect(apiMocks.savePartDefinitionDraft.mock.calls[0][1].expectedSaveVersion).toBe(1);
  fireEvent.click(screen.getByRole("button",{name:"Request approval"}));
  await screen.findByText(/Change Request submitted for review/);
  expect(apiMocks.submitPartDefinitionChangeRequest).toHaveBeenCalledWith("part-1",{draftKey:"draft-1",reason:"Increase blank allowance"},expect.any(String));
  expect(onSaved).not.toHaveBeenCalled();
});

it("retains typed form values after a terminal draft validation rejection", async () => {
  apiMocks.savePartDefinitionDraft.mockRejectedValueOnce(new ApiError(
    "The saved material basis needs correction.", 422, "PART_MATERIAL_BASIS_INVALID", "safe_to_edit"));
  render(<PartDefinitionMaintenance partId="part-1" onSaved={vi.fn()} onCompare={vi.fn()}/>);
  fireEvent.click(await screen.findByRole("button", {name:"Edit Part"}));
  fireEvent.change(screen.getByLabelText("Remarks — MCL row 1"), {target:{value:"Keep this authored remark"}});
  fireEvent.change(screen.getByLabelText("Engineering change reason"), {target:{value:"Preserve failed-save edits"}});
  fireEvent.click(screen.getByRole("button", {name:"Save draft"}));
  await screen.findByText("The saved material basis needs correction.");
  expect((screen.getByLabelText("Remarks — MCL row 1") as HTMLInputElement).value).toBe("Keep this authored remark");
  expect((screen.getByLabelText("Engineering change reason") as HTMLInputElement).value).toBe("Preserve failed-save edits");
  expect(localStorage.getItem("safari.part-definition:part-1:draft-save")).toBeNull();
});

it("locks an uncertain draft save across reload and retries the original key and payload", async () => {
  apiMocks.savePartDefinitionDraft.mockRejectedValueOnce(new Error("connection lost after save"));
  const props = {partId:"part-1",onSaved:vi.fn(),onCompare:vi.fn()};
  const first = render(<PartDefinitionMaintenance {...props}/>);
  fireEvent.click(await screen.findByRole("button",{name:"Edit Part"}));
  fireEvent.change(screen.getByLabelText("Engineering change reason"),{target:{value:"Add measured requirement"}});
  fireEvent.click(screen.getByRole("button",{name:"Save draft"}));
  await screen.findByText(/connection lost after save/);
  const stored = JSON.parse(localStorage.getItem("safari.part-definition:part-1:draft-save") || "null");
  expect(stored?.status).toBe("pending");
  const originalPayload = JSON.parse(stored.payload);
  expect(originalPayload.expectedSaveVersion).toBe(0);
  expect(screen.getByRole("button",{name:"Retry original draft save"})).toBeTruthy();
  first.unmount();

  apiMocks.savePartDefinitionDraft.mockResolvedValue({draftKey:"draft-1",saveVersion:1,contentFingerprint:"fp",content:originalPayload});
  render(<PartDefinitionMaintenance {...props}/>);
  fireEvent.click(await screen.findByRole("button",{name:"Retry original draft save"}));
  await screen.findByText(/original draft save was confirmed/);
  expect(apiMocks.savePartDefinitionDraft).toHaveBeenCalledTimes(2);
  expect(apiMocks.savePartDefinitionDraft.mock.calls[0][2]).toBe(stored.key);
  expect(apiMocks.savePartDefinitionDraft.mock.calls[1][2]).toBe(stored.key);
  expect(apiMocks.savePartDefinitionDraft.mock.calls[1][1]).toEqual(originalPayload);
  expect(localStorage.getItem("safari.part-definition:part-1:draft-save")).toBeNull();
});

it("derives MCL mass consumption from piece count, cut length and the selected material factor", async () => {
  apiMocks.partDefinitionOptions.mockResolvedValue({parts:[],materials:[{id:5,name:"Steel",kgPerMeter:1.58,
    weightEvidence:{kgPerMeter:"1.58",factorUnit:"kg/m",source:"Costing-New",sourceDate:"2026-09-24"}}],purchaseItems:[],families:["mcl","toolshop","cnc"]});
  render(<PartDefinitionMaintenance partId="part-1" onSaved={vi.fn()} onCompare={vi.fn()}/>);
  fireEvent.click(await screen.findByRole("button",{name:"Edit Part"}));
  fireEvent.click(screen.getByRole("button",{name:"Add MCL row"}));
  fireEvent.focus(screen.getByLabelText("Material To Use — MCL row 2"));
  fireEvent.click(screen.getByRole("option",{name:/Steel.*Local ID 5/}));
  fireEvent.change(screen.getByLabelText("Quantity — MCL row 2"),{target:{value:"4"}});
  fireEvent.change(screen.getByLabelText("Dimension to Cut — MCL row 2"),{target:{value:"200"}});
  fireEvent.change(screen.getByLabelText("Engineering change reason"),{target:{value:"Add measured MCL stock"}});
  expect(screen.queryByLabelText("Rate-basis quantity — MCL row 2")).toBeNull();
  expect(screen.getByText("1.264 kg per parent Part")).toBeTruthy();
  fireEvent.click(screen.getByRole("button",{name:"Save draft"}));
  await screen.findByText(/Draft saved in Safari Grist/);
  const authored = apiMocks.savePartDefinitionDraft.mock.calls[0][1].requirements.at(-1);
  expect(authored).toMatchObject({quantity:"4",uom:"Nos"});
  expect(authored.detail).toMatchObject({Material:5,QuantityUOM:"kg",MaterialDisplayName:"Steel",DimensionMM:200,DimensionText:"200 mm"});
  expect(authored.detail.Quantity).toBeCloseTo(0.316,12);
});

it("retries an uncertain approved publication with the same durable request key", async () => {
  const pending = {...accepted,canApprove:true,pendingChangeRequests:[{key:"cr-1",status:"pending_approval",reason:"Reviewed",impact:{fingerprint:"impact-1",assemblies:[],modelCodeConfigurations:[]}}]};
  apiMocks.partDefinition.mockResolvedValue(pending);
  apiMocks.approveAndPublishPartDefinition.mockRejectedValueOnce(new Error("connection lost after commit"));
  const onSaved = vi.fn();
  render(<PartDefinitionMaintenance partId="part-1" onSaved={onSaved} onCompare={vi.fn()}/>);

  const approve = await screen.findByRole("button",{name:/Approve and publish/});
  fireEvent.click(approve);
  await screen.findByText(/connection lost after commit/);
  const stored = JSON.parse(localStorage.getItem("safari.part-definition:part-1:approve-publish") || "null");
  expect(stored?.key).toBeTruthy();
  fireEvent.click(screen.getByRole("button",{name:"Retry original approval"}));
  await screen.findByText(/Accepted revision B is published/);
  expect(apiMocks.approveAndPublishPartDefinition).toHaveBeenCalledTimes(2);
  expect(apiMocks.approveAndPublishPartDefinition.mock.calls[0][1]).toBe(stored.key);
  expect(apiMocks.approveAndPublishPartDefinition.mock.calls[1][1]).toBe(stored.key);
  expect(onSaved).toHaveBeenCalledTimes(1);
  expect(localStorage.getItem("safari.part-definition:part-1:approve-publish")).toBeNull();
});

it.each([409, 422])("keeps the original approval after recoverable %s and reload", async status => {
  const pending = {...accepted,canApprove:true,pendingChangeRequests:[{key:"cr-1",status:"pending_approval",reason:"Reviewed",impact:{fingerprint:"impact-1",assemblies:[],modelCodeConfigurations:[]}}]};
  apiMocks.partDefinition.mockResolvedValue(pending);
  apiMocks.approveAndPublishPartDefinition.mockRejectedValueOnce(new ApiError("staged publication",status,"PART_CHILD_REVISION_DRAFT","retry_same_request"));
  const props = {partId:"part-1",onSaved:vi.fn(),onCompare:vi.fn()};
  const first = render(<PartDefinitionMaintenance {...props}/>);
  fireEvent.click(await screen.findByRole("button",{name:/Approve and publish/}));
  await screen.findByText("staged publication");
  const stored = JSON.parse(localStorage.getItem("safari.part-definition:part-1:approve-publish") || "null");
  expect(JSON.parse(stored.payload)).toEqual({changeRequestKey:"cr-1"});
  expect((screen.getByRole("button",{name:"Edit Part"}) as HTMLButtonElement).disabled).toBe(true);
  first.unmount();
  render(<PartDefinitionMaintenance {...props}/>);
  fireEvent.click(await screen.findByRole("button",{name:"Retry original approval"}));
  await screen.findByText(/Accepted revision B is published/);
  expect(apiMocks.approveAndPublishPartDefinition.mock.calls.map(call => call.slice(0,2))).toEqual([["cr-1",stored.key],["cr-1",stored.key]]);
  expect(localStorage.getItem("safari.part-definition:part-1:approve-publish")).toBeNull();
});

it("unlocks a proven safe prewrite approval rejection", async () => {
  const pending = {...accepted,canApprove:true,pendingChangeRequests:[{key:"cr-1",status:"pending_approval",impact:{fingerprint:"impact-1",assemblies:[],modelCodeConfigurations:[]}}]};
  apiMocks.partDefinition.mockResolvedValue(pending);
  apiMocks.approveAndPublishPartDefinition.mockRejectedValueOnce(new ApiError("Approver required",422,"PART_CHANGE_REQUEST_APPROVER_REQUIRED","safe_to_edit"));
  render(<PartDefinitionMaintenance partId="part-1" onSaved={vi.fn()} onCompare={vi.fn()}/>);
  fireEvent.click(await screen.findByRole("button",{name:/Approve and publish/}));
  await screen.findByText("Approver required");
  expect(localStorage.getItem("safari.part-definition:part-1:approve-publish")).toBeNull();
  expect(screen.queryByRole("button",{name:"Retry original approval"})).toBeNull();
});

it("uses the verified saved approval when browser storage is empty", async () => {
  apiMocks.partDefinition.mockResolvedValue({...accepted,canApprove:true,pendingChangeRequests:[{key:"cr-1",status:"approved",approvalRecoveryKey:"server-original",impact:{fingerprint:"impact-1",assemblies:[],modelCodeConfigurations:[]}}]});
  render(<PartDefinitionMaintenance partId="part-1" onSaved={vi.fn()} onCompare={vi.fn()}/>);
  fireEvent.click(await screen.findByRole("button",{name:"Recover saved approval"}));
  await screen.findByText(/Accepted revision B is published/);
  expect(apiMocks.approveAndPublishPartDefinition).toHaveBeenCalledWith("cr-1","server-original");
});

it("offers explicit classification for duplicate legacy occurrences and shows blank versus missing source status", async () => {
  const legacy = {...accepted,
    applicability:{...accepted.applicability,toolshop:{applicable:true,status:"applicable"}},
    requirements:[
      {requirementKey:"legacy-duplicate-requirement:part-1:20:70",legacyRequirementKey:"legacy-key",identityStatus:"legacy_duplicate_unclassified",family:"toolshop",quantity:1,uom:"Nos",lineRevisionId:50,fieldStatus:{ToolShopRemarks:"missing"},detail:{ToolShopRemarks:null},provenance:{type:"legacy_unclassified"}},
      {requirementKey:"legacy-duplicate-requirement:part-1:20:71",legacyRequirementKey:"legacy-key",identityStatus:"legacy_duplicate_unclassified",family:"toolshop",quantity:1,uom:"Nos",lineRevisionId:50,fieldStatus:{ToolShopRemarks:"blank"},detail:{ToolShopRemarks:""},provenance:{type:"legacy_unclassified"}},
    ],
  };
  apiMocks.partDefinition.mockResolvedValue(legacy);
  const onSaved = vi.fn();
  render(<PartDefinitionMaintenance partId="part-1" onSaved={onSaved} onCompare={vi.fn()}/>);

  fireEvent.click(await screen.findByRole("button",{name:"Edit Part"}));
  expect(screen.getAllByText("missing in source")).toHaveLength(1);
  expect(screen.getAllByText("blank in source")).toHaveLength(1);
  fireEvent.click(screen.getAllByRole("button",{name:"Assign reviewed occurrence identity"})[0]);
  fireEvent.change(screen.getByLabelText("Engineering change reason"),{target:{value:"Classify duplicate line"}});
  fireEvent.click(screen.getByRole("button",{name:"Save draft"}));

  await screen.findByText(/Draft saved in Safari Grist/);
  const submitted = apiMocks.savePartDefinitionDraft.mock.calls[0][1].requirements;
  expect(submitted[0]).toMatchObject({identityStatus:"safari_authored",requirementKey:"safari-authored-requirement:part-definition-1",legacyRequirementKey:"legacy-key"});
  expect(submitted[1]).toMatchObject({identityStatus:"legacy_duplicate_unclassified",requirementKey:"legacy-duplicate-requirement:part-1:20:71"});
});

it("shows the stable assembly path and configuration before and proposed pins for approval", async () => {
  apiMocks.partDefinition.mockResolvedValue({...accepted,canApprove:true,pendingChangeRequests:[{key:"cr-1",status:"pending_approval",reason:"Reviewed engineering change",impact:{
    target:{partId:"part-1",partRecordId:10,currentRevisionLabel:"A",proposedRevisionLabel:"B"},
    assemblies:[{partId:"assembly-1",partNumber:"SM-A-000001",partRecordId:11,currentRevisionLabel:"A",proposedRevisionLabel:"B",pathDetails:[{partPath:["assembly-1","part-1"],edges:[{componentIdentity:"assembly-1:bracket",parentPartId:"assembly-1",parentRevisionLabel:"A",proposedParentRevisionLabel:"B",childPartId:"part-1",pinnedChildRevisionLabel:"A",proposedChildRevisionLabel:"B",quantity:2,uom:"Nos",sourcingRoute:"make"}]}]}],
    modelCodeConfigurations:[{modelCode:"M-1",modelCodeRecordId:90,configurationRecordId:100,currentConfigurationRevision:4,proposedConfigurationRevision:5,selectionIdentity:"selection:p-1",partId:"part-1",partRevisionId:20,proposedPartRevisionLabel:"B",quantity:3,uom:"each",sourcingRoute:"buy",label:"main",optionGroup:"size"}],
    spares:{status:"unsupported_by_b2_p1",items:[],message:"B2 spare-definition composition propagation is reserved for the later B2 composition package."},
    unknown:[],fingerprint:"impact-1",
  }}]});
  render(<PartDefinitionMaintenance partId="part-1" onSaved={vi.fn()} onCompare={vi.fn()}/>);

  expect(await screen.findByText(/assembly-1:bracket/)).toBeTruthy();
  expect(screen.getByText(/pinned Rev A → proposed Rev B/)).toBeTruthy();
  expect(screen.getByText(/Configuration record 100 Rev 4 → proposed Rev 5/)).toBeTruthy();
  expect(screen.getByText(/B2 spare-definition composition propagation/)).toBeTruthy();
});

it("shows the immutable submitted definition diff in a fresh approver session", async () => {
  const before = {...accepted, components:[{componentIdentity:"component-1",childPartId:"child-1",quantity:1,uom:"each"}], purchases:[]};
  const after = {...accepted, requirements:[{...accepted.requirements[0],quantity:2}], components:[], purchases:[]};
  apiMocks.partDefinition.mockResolvedValue({...accepted,canApprove:true,pendingChangeRequests:[{
    key:"cr-fresh-review",status:"pending_approval",reason:"Reviewed change",before,after,
    impact:{fingerprint:"impact-1",assemblies:[],modelCodeConfigurations:[]},
  }]});
  render(<PartDefinitionMaintenance partId="part-1" onSaved={vi.fn()} onCompare={vi.fn()}/>);

  expect((await screen.findAllByText("Quantity per Part")).length).toBeGreaterThan(0);
  expect(screen.getByText("Store · child-1")).toBeTruthy();
  expect(screen.getByText("component-1")).toBeTruthy();
  expect(screen.getByText("removed")).toBeTruthy();
  expect(screen.getAllByText("1 Nos").length).toBeGreaterThan(0);
  expect(screen.getAllByText("2 Nos").length).toBeGreaterThan(0);
});

it("reopens preserved stale changes through a new saved draft", async () => {
  const before = {...accepted,requirements:[{...accepted.requirements[0],quantity:1}]};
  const after = {...accepted,requirements:[{...accepted.requirements[0],quantity:2}]};
  const stale = {...accepted,staleChangeRequests:[{key:"cr-stale",status:"stale",reason:"Reviewed change",
    staleCode:"PART_DEPENDENCY_CLOSURE_STALE",staleReason:"The configuration changed.",before,after,canReopen:true}]};
  const reopenedDraft = {draftKey:"draft-reopened",saveVersion:1,baseRevisionId:20,reason:"Review closure",
    content:{baseRevisionId:20,applicability:accepted.applicability,requirements:after.requirements,components:[],purchases:[],finalizeOnPublication:false}};
  apiMocks.partDefinition.mockResolvedValueOnce(stale).mockResolvedValue({...accepted,draft:reopenedDraft});
  apiMocks.reopenPartDefinitionChangeRequest.mockResolvedValue({draftKey:"draft-reopened",saveVersion:1,status:"open"});
  render(<PartDefinitionMaintenance partId="part-1" onSaved={vi.fn()} onCompare={vi.fn()}/>);

  fireEvent.click(await screen.findByText("Review the preserved submitted definition"));
  expect(screen.getAllByText("Quantity per Part").length).toBeGreaterThan(0);
  fireEvent.change(screen.getByLabelText("Reason for reopening this saved change"),{target:{value:"Review closure"}});
  fireEvent.click(screen.getByRole("button",{name:"Reopen saved changes as a new draft"}));

  await screen.findByText(/Saved changes reopened as draft v1/);
  expect(apiMocks.reopenPartDefinitionChangeRequest).toHaveBeenCalledWith("part-1","cr-stale",{reason:"Review closure"},expect.any(String));
  expect((screen.getByLabelText("Quantity — MCL row 1") as HTMLInputElement).value).toBe("2");
  expect(screen.getByLabelText("Engineering change reason")).toBeTruthy();
});

it("adds exactly one focused row to the explicitly chosen MCL, Tool Shop, CNC and Store tables", async () => {
  const {container} = render(<PartDefinitionMaintenance partId="part-1" onSaved={vi.fn()} onCompare={vi.fn()}/>);
  fireEvent.click(await screen.findByRole("button",{name:"Edit Part"}));
  fireEvent.click(screen.getByRole("button",{name:"Add MCL row"}));
  expect(screen.getByLabelText("Material To Use — MCL row 2")).toBeTruthy();
  fireEvent.click(screen.getByRole("button",{name:"Add MCL row"}));
  expect(screen.getByLabelText("Material To Use — MCL row 3")).toBeTruthy();
  fireEvent.click(screen.getByRole("button",{name:"Add Tool Shop row"}));
  const toolItem = screen.getByLabelText("Material To Use — Tool Shop row 1");
  expect(document.activeElement).toBe(toolItem);
  expect(screen.getByText(/Tool Shop marked applicable in this draft/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button",{name:"Add CNC row"}));
  fireEvent.click(screen.getByRole("button",{name:"Add Store row"}));

  const keys = [...container.querySelectorAll<HTMLTableRowElement>("tr[data-requirement-key]")].map(row => row.dataset.requirementKey);
  expect(keys).toHaveLength(5); // one accepted row plus four deliberate manufacturing additions
  expect(new Set(keys).size).toBe(5);
  expect(container.querySelectorAll('[aria-label="MCL requirements"] tbody tr')).toHaveLength(3);
  expect(container.querySelectorAll('[aria-label="Tool Shop requirements"] tbody tr')).toHaveLength(1);
  expect(container.querySelectorAll('[aria-label="CNC requirements"] tbody tr')).toHaveLength(1);
  expect(container.querySelectorAll('[aria-label="Store requirements"] tbody tr')).toHaveLength(1);
});

it("captures a source-only canonical identity from CNC and selects the new local Material without deriving CNC weight", async () => {
  const canonical = "Alang Cut Piece-Casting Template No. 83 - Round Plate";
  const sourceOnly = {sourceKey:"costing-new-test:MasterMaterial:56",masterMaterialId:56,canonicalName:canonical,code:"PL-83",
    bindingStatus:"unbound",materialId:null,factorStatus:"available",kgPerMeter:"5"};
  const capturedMaterial = {id:99,name:canonical,kgPerMeter:5,weightEvidence:{status:"available",kgPerMeter:"5",factorUnit:"kg/m"}};
  const beforeCapture = {parts:[],materials:[{id:5,name:"Steel"}],materialCatalog:[sourceOnly],
    materialWeightSource:{status:"available",catalogCount:402},purchaseItems:[],families:["mcl","toolshop","cnc"]};
  const afterCapture = {parts:[],materials:[{id:5,name:"Steel"},capturedMaterial],materialCatalog:[{...sourceOnly,bindingStatus:"bound",materialId:99}],
    materialWeightSource:{status:"available",catalogCount:402},purchaseItems:[],families:["mcl","toolshop","cnc"]};
  apiMocks.partDefinitionOptions.mockResolvedValueOnce(beforeCapture).mockResolvedValue(afterCapture);
  apiMocks.bindPartDefinitionMaterial.mockResolvedValue({status:"bound",materialId:99,masterMaterialId:56,identityCaptured:true});
  render(<PartDefinitionMaintenance partId="part-1" onSaved={vi.fn()} onCompare={vi.fn()}/>);
  fireEvent.click(await screen.findByRole("button",{name:"Edit Part"}));
  fireEvent.click(screen.getByRole("button",{name:"Add CNC row"}));
  fireEvent.change(screen.getByLabelText("Per Part Weight (kg) — CNC row 1"),{target:{value:"0.25"}});
  fireEvent.click(screen.getAllByText("Search and review a Costing-New material binding")[1]);
  fireEvent.change(screen.getByLabelText("Costing-New material — CNC row 1"),{target:{value:"56"}});
  fireEvent.change(screen.getByLabelText("Engineering change reason"),{target:{value:"Capture reviewed source identity"}});
  fireEvent.click(screen.getByRole("button",{name:"Review and capture as canonical Material"}));
  await screen.findByText(new RegExp(`${canonical} is now a canonical Safari Material`));
  expect(apiMocks.bindPartDefinitionMaterial).toHaveBeenCalledWith("part-1",{
    sourceMasterMaterialId:56,reason:"Capture reviewed source identity",captureCanonicalIdentity:true,
  },expect.stringMatching(/^material-binding:/));
  expect((screen.getByLabelText("Material To Use — CNC row 1") as HTMLInputElement).value).toBe(canonical);
  expect((screen.getByLabelText("Per Part Weight (kg) — CNC row 1") as HTMLInputElement).value).toBe("0.25");
  fireEvent.click(screen.getByRole("button",{name:"Save draft"}));
  await screen.findByText(/Draft saved in Safari Grist/);
  expect(apiMocks.savePartDefinitionDraft.mock.calls[0][1].requirements.at(-1).detail).toMatchObject({
    Material:99,MaterialDisplayName:canonical,PartWeightKg:"0.25",
  });
});

it("cancels then confirms row removal in the draft table", async () => {
  const {container} = render(<PartDefinitionMaintenance partId="part-1" onSaved={vi.fn()} onCompare={vi.fn()}/>);
  fireEvent.click(await screen.findByRole("button",{name:"Edit Part"}));
  const firstRemove = () => screen.getAllByRole("button",{name:"Remove row"})[0];
  fireEvent.click(firstRemove());
  expect(await screen.findByRole("alertdialog")).toBeTruthy();
  expect(container.querySelectorAll('[aria-label="MCL requirements"] tbody tr')).toHaveLength(1);
  fireEvent.click(screen.getByRole("button",{name:"Cancel removal"}));
  expect(container.querySelectorAll('[aria-label="MCL requirements"] tbody tr')).toHaveLength(1);
  fireEvent.click(firstRemove());
  fireEvent.click(screen.getByRole("button",{name:"Remove from draft"}));
  expect(container.querySelectorAll('[aria-label="MCL requirements"] tbody tr')).toHaveLength(0);
  expect(accepted.requirements).toHaveLength(1); // accepted membership is untouched until publication
});

it("rejects malformed CNC Per Part Weight even when its costing unit is Nos", async () => {
  apiMocks.partDefinition.mockResolvedValue({...accepted,
    applicability:{...accepted.applicability,cnc:{applicable:true,status:"applicable"}},
    requirements:[...accepted.requirements,{requirementKey:"cnc-row",identityStatus:"stable",family:"cnc",quantity:2,uom:"Nos",processType:"cnc",
      detail:{PartWeightKg:0.25,Quantity:1,QuantityUOM:"Nos",PlatePart:"Mounting plate"}}],
  });
  render(<PartDefinitionMaintenance partId="part-1" onSaved={vi.fn()} onCompare={vi.fn()}/>);
  fireEvent.click(await screen.findByRole("button",{name:"Edit Part"}));
  fireEvent.change(screen.getByLabelText("Per Part Weight (kg) — CNC row 1"),{target:{value:"BAD123"}});
  fireEvent.change(screen.getByLabelText("Engineering change reason"),{target:{value:"Reject malformed CNC weight"}});
  fireEvent.click(screen.getByRole("button",{name:"Save draft"}));

  expect(await screen.findByText("PartWeightKg must be a finite number greater than zero.")).toBeTruthy();
  expect(apiMocks.savePartDefinitionDraft).not.toHaveBeenCalled();
});

it("shows saved MCL cut length and CNC per-part weight without conflating total weight", async () => {
  apiMocks.partDefinition.mockResolvedValue({...accepted,applicability:{...accepted.applicability,cnc:{applicable:true,status:"applicable"}},requirements:[
    {...accepted.requirements[0],detail:{...accepted.requirements[0].detail,DimensionMM:250,DimensionInches:250/25.4,DimensionText:"250 mm",DimensionUOM:"mm"}},
    {requirementKey:"cnc-row",identityStatus:"stable",family:"cnc",quantity:3,uom:"Nos",processType:"cnc",detail:{ItemName:"Cut plate",PartWeightKg:0.5,WeightKg:1,WeightGrams:1000,Quantity:0.5,QuantityUOM:"kg",PlatePart:"Mounting plate"}},
  ]});
  render(<PartDefinitionMaintenance partId="part-1" onSaved={vi.fn()} onCompare={vi.fn()}/>);
  expect(await screen.findByText("250 mm")).toBeTruthy();
  expect(screen.getAllByText("0.5 kg").length).toBeGreaterThan(0);
  expect(screen.getByText("1.5 kg")).toBeTruthy();
  fireEvent.click(screen.getAllByText("Saved calculation and identity")[1]);
  expect(screen.getByText(/Saved total weight fields: WeightKg 1 kg/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button",{name:"Edit Part"}));
  expect((screen.getByLabelText("Dimension to Cut — MCL row 1") as HTMLInputElement).value).toBe("250");
  fireEvent.change(screen.getByLabelText("Per Part Weight (kg) — CNC row 1"),{target:{value:"0.6"}});
  expect(screen.getByText("1.8 kg")).toBeTruthy();
  expect((screen.getByLabelText("Per Part Weight (kg) — CNC row 1") as HTMLInputElement).value).toBe("0.6");
});

it("opens a normalized known MCL baseline with its material selected and no duplicate-basis prompt", async () => {
  apiMocks.partDefinitionOptions.mockResolvedValue({parts:[],materials:[{id:5,name:"MS-Angles 65 x 65 x 6",kgPerMeter:5.8,
    weightEvidence:{status:"available",kgPerMeter:"5.8",factorUnit:"kg/m",source:"Costing-New",sourceDate:"2026-11-24"}}],purchaseItems:[],families:["mcl","toolshop","cnc"]});
  apiMocks.partDefinition.mockResolvedValue({...accepted,requirements:[{requirementKey:"known-mcl",identityStatus:"stable",family:"mcl",
    quantity:2,uom:"Nos",processType:"material_cut",materialConsumptionStatus:"resolved",
    detail:{Material:5,MaterialDisplayName:"Angles 65 x 65 x 6",Quantity:0.435,QuantityUOM:"kg",DimensionMM:75,
      DimensionText:"75",DimensionUOM:"mm",WeightKg:0.87,EngineeringAttributes:{materialConsumptionBasis:"mcl_kg_per_piece",
        materialWeightEvidence:{status:"available",kgPerMeter:"5.8",factorUnit:"kg/m",source:"Costing-New",sourceDate:"2026-11-24"}}},
    provenance:{type:"ods_baseline",evidence:{sourceRow:7}}}]});
  render(<PartDefinitionMaintenance partId="part-1" onSaved={vi.fn()} onCompare={vi.fn()}/>);
  expect(await screen.findByText("0.435 kg / piece")).toBeTruthy();
  expect(screen.getByText("0.87 kg per parent Part")).toBeTruthy();
  expect(screen.queryByRole("group",{name:/Legacy consumption review/})).toBeNull();
  fireEvent.click(screen.getByRole("button",{name:"Edit Part"}));
  expect((screen.getByLabelText("Material To Use — MCL row 1") as HTMLInputElement).value).toBe("MS-Angles 65 x 65 x 6");
  expect(screen.queryByLabelText("Resolve material consumption — MCL row 1")).toBeNull();
});

it("normalizes a legacy physical MCL row once after its canonical local material is chosen", async () => {
  apiMocks.partDefinitionOptions.mockResolvedValue({parts:[],materials:[{id:5,name:"MS-Angles 65 x 65 x 6",kgPerMeter:5.8,
    weightEvidence:{status:"available",kgPerMeter:"5.8",factorUnit:"kg/m",sourceDate:"2026-11-24"}}],purchaseItems:[],families:["mcl","toolshop","cnc"]});
  apiMocks.partDefinition.mockResolvedValue({...accepted,requirements:[{requirementKey:"legacy-mcl",identityStatus:"stable",family:"mcl",
    quantity:2,uom:"Nos",processType:"material_cut",materialConsumptionStatus:"legacy_duplicate_ambiguous",
    detail:{Material:null,MaterialDisplayName:"Angles 65 x 65 x 6",Quantity:2,QuantityUOM:"Nos",DimensionMM:75,DimensionText:"75",DimensionUOM:"mm"},
    provenance:{type:"ods_baseline"}}]});
  render(<PartDefinitionMaintenance partId="part-1" onSaved={vi.fn()} onCompare={vi.fn()}/>);
  fireEvent.click(await screen.findByRole("button",{name:"Edit Part"}));
  expect(screen.queryByRole("group",{name:/Legacy consumption review/})).toBeNull();
  expect(screen.getByText(/Legacy physical consumption is preserved as saved/)).toBeTruthy();
  const material = screen.getByLabelText("Material To Use — MCL row 1") as HTMLInputElement;
  fireEvent.focus(material);
  fireEvent.click(screen.getByRole("option",{name:/MS-Angles 65 x 65 x 6.*Local ID 5/}));
  expect(screen.getByText("0.435 kg / piece")).toBeTruthy();
  fireEvent.change(screen.getByLabelText("Engineering change reason"),{target:{value:"Resolve physical MCL material from local identity"}});
  fireEvent.click(screen.getByRole("button",{name:"Save draft"}));
  await screen.findByText(/Draft saved in Safari Grist/);
  const authored = apiMocks.savePartDefinitionDraft.mock.calls[0][1].requirements[0];
  expect(authored).toMatchObject({quantity:2,uom:"Nos",materialConsumptionResolution:"mcl_kg_per_piece"});
  expect(authored.detail).toMatchObject({Material:5,Quantity:0.435,QuantityUOM:"kg",DimensionMM:75});
});

it("uses current local mass factors while retaining historical evidence and alias-selected identity", async () => {
  apiMocks.partDefinitionOptions.mockResolvedValue({parts:[],materials:[{id:5,name:"MS-Angles 65 x 65 x 6",sourceAliases:["Angles 65x65x6"],kgPerMeter:6,
    weightEvidence:{status:"available",kgPerMeter:"6",factorUnit:"kg/m",source:"Safari local master",sourceDate:"2026-11-27"}}],purchaseItems:[],families:["mcl","toolshop","cnc"],
    materialWeightSource:{status:"available",catalogCount:1}});
  apiMocks.partDefinition.mockResolvedValue({...accepted,requirements:[{requirementKey:"known-mcl",identityStatus:"stable",family:"mcl",
    quantity:2,uom:"Nos",processType:"material_cut",materialConsumptionStatus:"resolved",
    detail:{Material:5,MaterialDisplayName:"Angles 65x65x6",Quantity:0.435,QuantityUOM:"kg",DimensionMM:75,
      DimensionText:"75",DimensionUOM:"mm",EngineeringAttributes:{materialWeightEvidence:{status:"available",kgPerMeter:"5.8",factorUnit:"kg/m",sourceDate:"2026-11-24"}}},
    materialWeightEvidence:{status:"available",kgPerMeter:"5.8",factorUnit:"kg/m",sourceDate:"2026-11-24"},provenance:{type:"ods_baseline"}}]});
  render(<PartDefinitionMaintenance partId="part-1" onSaved={vi.fn()} onCompare={vi.fn()}/>);
  expect(await screen.findByText("0.45 kg / piece")).toBeTruthy();
  expect(screen.getByText("0.9 kg per parent Part")).toBeTruthy();
  fireEvent.click(screen.getAllByText("Saved calculation and identity")[0]);
  fireEvent.click(screen.getByText("Historical factor evidence"));
  expect(screen.getAllByText(/5.8/).length).toBeGreaterThan(0);

  fireEvent.click(screen.getByRole("button",{name:"Edit Part"}));
  const material = screen.getByLabelText("Material To Use — MCL row 1") as HTMLInputElement;
  expect(material.value).toBe("MS-Angles 65 x 65 x 6");
  fireEvent.focus(material);
  fireEvent.change(material,{target:{value:"Angles 65x65x6"}});
  expect(screen.getByRole("option",{name:/MS-Angles 65 x 65 x 6.*Local ID 5.*Angles 65x65x6/})).toBeTruthy();
  fireEvent.keyDown(material,{key:"ArrowDown"});
  fireEvent.keyDown(material,{key:"Enter"});
  expect(material.value).toBe("MS-Angles 65 x 65 x 6");
  expect(screen.getByText("0.45 kg / piece")).toBeTruthy();
});

it("keeps a phased-out saved material visible with its current factor but excludes it from new choices", async () => {
  apiMocks.partDefinitionOptions.mockResolvedValue({parts:[],materials:[{id:5,name:"Saved steel",selectable:false,kgPerMeter:7,
    weightEvidence:{status:"available",kgPerMeter:"7",factorUnit:"kg/m",source:"Safari local master",sourceDate:"2026-11-27"}}],
    purchaseItems:[],families:["mcl","toolshop","cnc"]});
  apiMocks.partDefinition.mockResolvedValue({...accepted,requirements:[{requirementKey:"phased-out-mcl",identityStatus:"stable",family:"mcl",
    quantity:2,uom:"Nos",processType:"material_cut",materialConsumptionStatus:"resolved",
    detail:{Material:5,MaterialDisplayName:"Saved steel",Quantity:0.435,QuantityUOM:"kg",DimensionMM:75,DimensionText:"75",DimensionUOM:"mm",
      EngineeringAttributes:{materialWeightEvidence:{status:"available",kgPerMeter:"5.8",factorUnit:"kg/m",sourceDate:"2026-11-20"}}},
    provenance:{type:"ods_baseline"}}]});
  render(<PartDefinitionMaintenance partId="part-1" onSaved={vi.fn()} onCompare={vi.fn()}/>);
  expect(await screen.findByText("0.525 kg / piece")).toBeTruthy();
  expect(screen.getByText("1.05 kg per parent Part")).toBeTruthy();
  fireEvent.click(screen.getByRole("button",{name:"Edit Part"}));
  const material=screen.getByLabelText("Material To Use — MCL row 1") as HTMLInputElement;
  expect(material.value).toBe("Saved steel");
  fireEvent.focus(material);
  fireEvent.change(material,{target:{value:"Saved steel"}});
  expect(screen.queryByRole("option",{name:/Saved steel/})).toBeNull();
  expect(screen.getByText(/No local material matches/)).toBeTruthy();
});

it("distinguishes a missing saved CNC total from a zero weight", async () => {
  apiMocks.partDefinition.mockResolvedValue({...accepted,applicability:{...accepted.applicability,cnc:{applicable:true,status:"applicable"}},requirements:[
    {requirementKey:"cnc-missing-total",identityStatus:"safari_authored",family:"cnc",quantity:2,uom:"Nos",processType:"cnc",fieldStatus:{WeightKg:"missing",WeightGrams:"missing"},detail:{ItemName:"New plate",PartWeightKg:0.25,WeightKg:0,WeightGrams:0,Quantity:0.25,QuantityUOM:"kg",PlatePart:"New plate"}},
  ]});
  render(<PartDefinitionMaintenance partId="part-1" onSaved={vi.fn()} onCompare={vi.fn()}/>);
  expect(await screen.findByText("0.5 kg")).toBeTruthy();
  fireEvent.click(screen.getByText("Saved calculation and identity"));
  expect(screen.getByText("Saved total weight: not recorded")).toBeTruthy();
  expect(screen.queryByText(/Saved total weight fields:.*0 kg/)).toBeNull();
});

it("renders persisted field history with family, item, before/after values and units", async () => {
  apiMocks.partDefinition.mockResolvedValue({...accepted,revisionHistory:[{id:20,revisionLabel:"A",status:"accepted",current:false,actor:"Irshad",reason:"Reviewed",occurredAt:"2026-11-22",changedRows:[]},
    {id:21,revisionLabel:"B",status:"accepted",current:true,actor:"Irshad",reason:"Cut update",occurredAt:"2026-11-23",beforeRevisionLabel:"A",changedRows:[
      {kind:"modified",family:"MCL",item:"Bracket blank",occurrence:"req-1",field:"Dimension to Cut",before:"250 mm",after:"300 mm"},
      {kind:"removed",family:"CNC",item:"Mounting plate",occurrence:"cnc-1",field:"Per Part Weight",before:"0.25 kg",after:"Not present"},
    ]}]});
  render(<PartDefinitionMaintenance partId="part-1" onSaved={vi.fn()} onCompare={vi.fn()}/>);
  fireEvent.click(await screen.findByText("Engineering revision history"));
  fireEvent.click(screen.getByText("Changed rows · 2"));
  expect(screen.getAllByText("Dimension to Cut").length).toBeGreaterThan(0);
  expect(screen.getByText("250 mm")).toBeTruthy();
  expect(screen.getByText("300 mm")).toBeTruthy();
  expect(screen.getByText("Irshad · 2026-11-23")).toBeTruthy();
});

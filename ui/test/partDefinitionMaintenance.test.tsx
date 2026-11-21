import {afterEach, beforeEach, expect, it, vi} from "vitest";
import {cleanup, fireEvent, render, screen} from "@testing-library/react";
import {PartDefinitionMaintenance} from "../src/PartDefinitionMaintenance";
import {ApiError} from "../src/api";

const apiMocks = vi.hoisted(() => ({
  partDefinition: vi.fn(), partDefinitionOptions: vi.fn(), savePartDefinitionDraft: vi.fn(),
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
  fireEvent.change(screen.getByLabelText("Quantity"),{target:{value:"2"}});
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

it("collects an explicit MCL rate quantity and unit for a newly authored requirement", async () => {
  render(<PartDefinitionMaintenance partId="part-1" onSaved={vi.fn()} onCompare={vi.fn()}/>);
  fireEvent.click(await screen.findByRole("button",{name:"Edit Part"}));
  fireEvent.click(screen.getByRole("button",{name:"Add requirement"}));
  const quantities = screen.getAllByLabelText("Rate-basis quantity") as HTMLInputElement[];
  const units = screen.getAllByLabelText("Rate-basis unit") as HTMLInputElement[];
  fireEvent.change(quantities[quantities.length - 1],{target:{value:"2"}});
  fireEvent.change(units[units.length - 1],{target:{value:"kg"}});
  fireEvent.change(screen.getAllByLabelText("Material identity")[1],{target:{value:"5"}});
  fireEvent.change(screen.getByLabelText("Engineering change reason"),{target:{value:"Add measured MCL"}});
  fireEvent.click(screen.getByRole("button",{name:"Save draft"}));
  await screen.findByText(/Draft saved in Safari Grist/);
  const authored = apiMocks.savePartDefinitionDraft.mock.calls[0][1].requirements.at(-1);
  expect(authored.detail).toMatchObject({Material:"5",Quantity:"2",QuantityUOM:"kg"});
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
  fireEvent.click(screen.getAllByRole("button",{name:"Assign reviewed requirement identity"})[0]);
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

  expect(await screen.findByText("requirements[req-1].quantity")).toBeTruthy();
  expect(screen.getByText("components[component-1]")).toBeTruthy();
  expect(screen.getByText("removed")).toBeTruthy();
  expect(screen.getByText("1")).toBeTruthy();
  expect(screen.getByText("2")).toBeTruthy();
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
  expect(screen.getByText("requirements[req-1].quantity")).toBeTruthy();
  fireEvent.change(screen.getByLabelText("Reason for reopening this saved change"),{target:{value:"Review closure"}});
  fireEvent.click(screen.getByRole("button",{name:"Reopen saved changes as a new draft"}));

  await screen.findByText(/Saved changes reopened as draft v1/);
  expect(apiMocks.reopenPartDefinitionChangeRequest).toHaveBeenCalledWith("part-1","cr-stale",{reason:"Review closure"},expect.any(String));
  expect((screen.getByLabelText("Quantity") as HTMLInputElement).value).toBe("2");
  expect(screen.getByLabelText("Engineering change reason")).toBeTruthy();
});

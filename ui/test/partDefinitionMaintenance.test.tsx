import {afterEach, beforeEach, expect, it, vi} from "vitest";
import {cleanup, fireEvent, render, screen} from "@testing-library/react";
import {PartDefinitionMaintenance} from "../src/PartDefinitionMaintenance";

const apiMocks = vi.hoisted(() => ({
  partDefinition: vi.fn(), partDefinitionOptions: vi.fn(), savePartDefinitionDraft: vi.fn(),
  submitPartDefinitionChangeRequest: vi.fn(), approveAndPublishPartDefinition: vi.fn(),
}));
vi.mock("../src/api", () => ({api: apiMocks}));

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

it("saves a direct edit as a Grist draft and reloads the persisted reason before CR submission", async () => {
  const withDraft = {...accepted,draft:{draftKey:"draft-1",saveVersion:1,baseRevisionId:20,reason:"Initial test edit",content:{
    baseRevisionId:20,applicability:accepted.applicability,requirements:accepted.requirements,components:[],purchases:[],finalizeOnPublication:false,
  }}};
  apiMocks.partDefinition.mockResolvedValueOnce(withDraft).mockResolvedValue({...withDraft,draft:{...withDraft.draft,reason:"Increase blank allowance"}});
  const savedContent = {baseRevisionId:20,applicability:accepted.applicability,requirements:accepted.requirements,components:[],purchases:[],finalizeOnPublication:false};
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
  fireEvent.click(screen.getByRole("button",{name:"Request approval"}));
  await screen.findByText(/Change Request submitted for review/);
  expect(apiMocks.submitPartDefinitionChangeRequest).toHaveBeenCalledWith("part-1",{draftKey:"draft-1",reason:"Increase blank allowance"},expect.any(String));
  expect(onSaved).not.toHaveBeenCalled();
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
  fireEvent.click(screen.getByRole("button",{name:/Approve and publish/}));
  await screen.findByText(/Accepted revision B is published/);
  expect(apiMocks.approveAndPublishPartDefinition).toHaveBeenCalledTimes(2);
  expect(apiMocks.approveAndPublishPartDefinition.mock.calls[0][1]).toBe(stored.key);
  expect(apiMocks.approveAndPublishPartDefinition.mock.calls[1][1]).toBe(stored.key);
  expect(onSaved).toHaveBeenCalledTimes(1);
  expect(localStorage.getItem("safari.part-definition:part-1:approve-publish")).toBeNull();
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

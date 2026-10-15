import {cleanup, fireEvent, render, screen, waitFor, within} from "@testing-library/react";
import {afterEach, beforeEach, expect, it, vi} from "vitest";
import {ApiError} from "../src/api";
import {PartsView} from "../src/PartsView";

const mocks = vi.hoisted(() => ({
  parts: vi.fn(), partScopeTargets: vi.fn(), partDetails: vi.fn(),
  partNamePreview: vi.fn(), createManagedPart: vi.fn(), createAssembly: vi.fn(), maintainPartShortcode: vi.fn(), savePartIntendedSharing: vi.fn(),
  addPartComponent: vi.fn(), recordPartPurchase: vi.fn(),
}));
vi.mock("../src/api", async () => ({...(await vi.importActual<typeof import("../src/api")>("../src/api")), api: mocks}));

const part = {id:"part-uuid", partNumber:"SM-P-000001", name:"S1K — Chassis — Standard", description:"Chassis", variant:"Standard",
  scope:"product_model", scopeTargetId:"model-1", scopeTarget:"Safari 1000 HF", shortcode:"S1K", engineeringRevision:"A",
  status:"active", metadataVersion:1, aliases:[], legacy:false, actor:"operator", reason:"Initial design"};
const details = {part:{...part, compositionStatus:"draft", metadataHistory:[], lifecycleHistory:[]}, mappingHistory:[],
  processLines:{status:"available",items:[]}, components:{status:"available",items:[]}, drawings:{status:"empty",items:[]},
  intendedSharing:{status:"available",version:0,items:[{id:"code-2",code:"S1KHFSTD",modelId:"model-1",model:"Safari 1000 HF",productId:"product-1",product:"Safari 1000",createdBy:"operator",createdReason:"Shared chassis"}],history:[]},
  usedIn:{status:"available",coverage:"direct_selections_only",items:[{modelCodeId:"code-1",modelCode:"S1KHFELP",model:"Safari 1000 HF",configurationId:8,configurationVersion:2,configurationRevisionId:9,selectionIdentity:"front-actuator",occurrenceLabel:"Front actuator",quantity:2,uom:"each",sourcingRoute:"make",direct:true}],message:"Direct Parts selected in current published configurations. Component/indirect usage is not included."},
  purchases:{specifications:[{specification:{id:41,SpecificationCode:"MOTOR-1",Manufacturer:"Maker",ManufacturerPartNumber:"M-1",CostingUOM:"each",CostingCurrency:"INR"},
    rate:{status:"unavailable",rate:null,reason:"No eligible actual purchase exists.",ratePolicy:"net merchandise per costing UOM"},vendors:[],purchases:[],unitConversions:[],currencyConversions:[]}],vendorCatalog:[]}};
const scopeTargets = (modelShortcode: string | null) => ({scopes:[
  {id:"global",label:"Global",target:{id:"global",label:"Safari Manufacturing",shortcode:"SM"}},
  {id:"product",label:"Product",targets:[{id:"product-1",label:"Safari 1000",shortcode:"S1K"}]},
  {id:"product_model",label:"Product Model",targets:[{id:"model-1",parentId:"product-1",label:"Safari 1000 HF",shortcode:modelShortcode}]},
  {id:"model_code",label:"Model Code",targets:[{id:"code-1",parentId:"model-1",label:"S1KHFELP"},{id:"code-2",parentId:"model-1",label:"S1KHFSTD"}]},
]});

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((accept, fail) => { resolve = accept; reject = fail; });
  return {promise, resolve, reject};
}

async function fillAndSaveCreation(kind: "part" | "assembly", description: string, reason: string) {
  const label = kind === "assembly" ? "Assembly" : "Part";
  fireEvent.click(screen.getAllByRole("button", {name:`New ${label}`})[0]);
  const form = await screen.findByRole("region", {name:`Create a canonical ${label}`});
  const descriptionInput = within(form).getByLabelText(`What is the ${label} called?`) as HTMLInputElement;
  await waitFor(() => expect(descriptionInput.disabled).toBe(false));
  fireEvent.change(descriptionInput, {target:{value:description}});
  fireEvent.change(within(form).getByLabelText(`Why is this ${label} needed?`), {target:{value:reason}});
  const save = within(form).getByRole("button", {name:`Save ${label}`}) as HTMLButtonElement;
  await waitFor(() => expect(save.disabled).toBe(false));
  fireEvent.click(save);
}

beforeEach(() => {
  sessionStorage.clear(); window.history.replaceState(null,"", "#parts");
  Object.values(mocks).forEach(mock => mock.mockReset());
  vi.stubGlobal("crypto", {randomUUID:()=>"parts-test-request"});
  mocks.partScopeTargets.mockResolvedValue({scopes:[
    {id:"global",label:"Global",target:{id:"global",label:"Safari Manufacturing",shortcode:"SM"}},
    {id:"product",label:"Product",targets:[{id:"product-1",label:"Safari 1000",shortcode:"S1K"},{id:"product-2",label:"Safari 500",shortcode:"S500"}]},
    {id:"product_model",label:"Product Model",targets:[{id:"model-1",parentId:"product-1",label:"Safari 1000 HF",shortcode:"S1KHF"},{id:"model-2",parentId:"product-1",label:"Safari 1000 HD",shortcode:"S1KHD"}]},
    {id:"model_code",label:"Model Code",targets:[{id:"code-1",parentId:"model-1",label:"S1KHFELP"},{id:"code-2",parentId:"model-1",label:"S1KHFSTD"},{id:"code-3",parentId:"model-2",label:"S1KHDSTD"}]},
  ]});
  mocks.partNamePreview.mockImplementation(async (scope, targetId, description, variant) => ({name:`${targetId === "model-1" ? "S1KHF" : "S1K"} — ${description}${variant ? ` — ${variant}` : ""}`,available:true,collision:[]}));
  mocks.createManagedPart.mockResolvedValue({part:{...part,id:"new-part",partNumber:"SM-P-000002",name:"S1KHF — Chassis — Standard"}});
  mocks.createAssembly.mockResolvedValue({part:{...part,id:"new-assembly",partNumber:"SM-A-000001",identityKind:"assembly",name:"S1KHF — Main Assembly"}});
  mocks.maintainPartShortcode.mockResolvedValue({shortcode:"S1KHF",version:1});
  mocks.savePartIntendedSharing.mockResolvedValue({partId:part.id,version:1,activeCodeIds:["code-2"]});
  mocks.parts.mockResolvedValue({items:[part],legacyItems:[],storage:"Grist Safari Manufacturing"});
  mocks.partDetails.mockResolvedValue(details);
});
afterEach(() => {cleanup(); vi.unstubAllGlobals();});

it("collapses and expands scope targets, then opens the selected Part details", async () => {
  render(<PartsView/>);
  const modelScope = await screen.findByRole("treeitem", {name:/Product Model/});
  expect(screen.queryByRole("treeitem", {name:/Unmatched \/ retired target/})).toBeNull();
  await screen.findByRole("treeitem", {name:/Safari 1000 1/});
  const productParent = screen.getAllByRole("treeitem", {name:/Safari 1000/}).find(item => item.getAttribute("aria-expanded") === "false");
  expect(productParent).toBeTruthy();
  fireEvent.click(productParent!);
  const target = screen.getByRole("treeitem", {name:/Safari 1000 HF/});
  expect(screen.queryByRole("treeitem", {name:/SM-P-000001/})).toBeNull();
  fireEvent.click(target);
  const partRow = await screen.findByRole("treeitem", {name:/SM-P-000001/});
  fireEvent.click(partRow);
  await screen.findByRole("heading", {name:"S1K — Chassis — Standard"});
  fireEvent.click(screen.getByRole("button", {name:/Open full Part details/}));
  fireEvent.click(await screen.findByRole("button", {name:"Purchase details"}));
  await screen.findByRole("heading", {name:/Purchased Part details/});
  expect(screen.getByText("No eligible actual purchase exists.")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", {name:"Used in configurations"}));
  expect(screen.getByRole("heading", {name:/Used in configurations/})).toBeTruthy();
  expect(screen.getByText("Configured directly; no intended-sharing link is recorded.")).toBeTruthy();
  expect(mocks.partDetails).toHaveBeenCalledWith("part-uuid");
  expect(modelScope.getAttribute("aria-expanded")).toBe("true");
  expect(sessionStorage.getItem("parts:expanded")).toContain("target:product_model:model-1");
});

it("shows saved MCL, Tool Shop and CNC mappings separately from baseline and pinned process lines", async () => {
  mocks.partDetails.mockResolvedValue({...details,
    manufacturingBaseline:{status:"not_established",message:"Manufacturing requirements are not yet established."},
    processLines:{status:"available",items:[]},
    mappedLines:{status:"partial",readOnly:true,baselineIndependent:true,
      message:"Current source or mapping evidence is unavailable for one saved source file; retained assignments are historical and unverified.",
      items:[
        {key:"mcl",family:"MCL",description:"Plate blank",descriptionStatus:"available",workbookName:"pilot.ods",workbookPath:"costing/pilot.ods",sheet:"5. Material Cut List Price",row:7,sourceStatus:"readable",assignmentStatus:"current",details:[{field:"qty",label:"Quantity",status:"available",value:"2"},{field:"dimension_to_cut_mm",label:"Dimension",status:"blank",value:null}],provenance:{version:3,actor:"owner",reason:"Inline Part mapping",occurredAt:"2026-10-15T07:00:00Z"}},
        {key:"tool",family:"Tool Shop",description:"Weld fixture",workbookName:"pilot.ods",workbookPath:"costing/pilot.ods",sheet:"Tool Shop Items",row:12,sourceStatus:"readable",assignmentStatus:"current",details:[{field:"toolshop_remarks",label:"Remarks",status:"blank",value:null}]},
        {key:"cnc",family:"CNC",description:"Frame",workbookName:"pilot.ods",workbookPath:"costing/pilot.ods",sheet:"CNC Cut List",row:4,sourceStatus:"readable",assignmentStatus:"current",details:[{field:"thickness",label:"Thickness",status:"missing",value:null}]},
      ],
      historical:[{key:"unavailable",family:"MCL",description:"Saved flange",workbookName:"missing.ods",workbookPath:"old/missing.ods",sheet:"5. Material Cut List Price",row:9,sourceStatus:"unavailable",assignmentStatus:"unverifiable",details:[],provenance:{version:1,actor:"owner",reason:"Initial assignment"},semanticComparison:{status:"source_unavailable",message:"File no longer exists."}}]},
  });
  render(<PartsView/>);
  await screen.findByRole("treeitem", {name:/Product Model/});
  const productParent = screen.getAllByRole("treeitem", {name:/Safari 1000/}).find(item => item.getAttribute("aria-expanded") === "false");
  fireEvent.click(productParent!);
  fireEvent.click(screen.getByRole("treeitem", {name:/Safari 1000 HF/}));
  fireEvent.click(await screen.findByRole("treeitem", {name:/SM-P-000001/}));
  fireEvent.click(screen.getByRole("button", {name:"Mapped workbook lines"}));

  expect(await screen.findByText("MCL · Plate blank")).toBeTruthy();
  expect(screen.getByText("Tool Shop · Weld fixture")).toBeTruthy();
  expect(screen.getByText("CNC · Frame")).toBeTruthy();
  expect(screen.getByText(/Dimension: \(blank in source\)/)).toBeTruthy();
  expect(screen.getByText(/Thickness: Column not in source/)).toBeTruthy();
  expect(screen.getByText("Saved v3 · owner · Inline Part mapping · 2026-10-15T07:00:00Z")).toBeTruthy();
  expect(screen.getByText("Historical or changed source evidence")).toBeTruthy();
  expect(screen.getByText("Unverifiable history")).toBeTruthy();
  expect(screen.getByText("File no longer exists.")).toBeTruthy();
  expect(screen.getByText(/remain visible when no manufacturing baseline exists/)).toBeTruthy();
  expect(mocks.addPartComponent).not.toHaveBeenCalled();
  expect(mocks.recordPartPurchase).not.toHaveBeenCalled();

  fireEvent.click(screen.getAllByRole("button", {name:"Manufacturing baseline"}).at(-1)!);
  expect(screen.getByText("Manufacturing requirements are not yet established.")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", {name:"Process lines"}));
  expect(screen.getByText("No process requirements are pinned to this Part. MCL, Toolshop and CNC are all optional.")).toBeTruthy();
});

it("filters one Model's code list and keeps intended codes separate from its naming anchor", async () => {
  render(<PartsView/>);
  fireEvent.click(screen.getAllByRole("button", {name:/New Part/})[0]);
  const productFilter = await screen.findByLabelText("Intended sharing Product filter") as HTMLSelectElement;
  await waitFor(() => expect(productFilter.disabled).toBe(false));
  fireEvent.change(productFilter, {target:{value:"product-1"}});
  const modelFilter = screen.getByLabelText(/Intended sharing Product Model filter/) as HTMLSelectElement;
  await waitFor(() => expect(modelFilter.disabled).toBe(false));
  fireEvent.change(modelFilter, {target:{value:"model-1"}});
  expect(screen.getByRole("checkbox", {name:/S1KHFELP/})).toBeTruthy();
  expect(screen.getByRole("checkbox", {name:/S1KHFSTD/})).toBeTruthy();
  expect(screen.queryByRole("checkbox", {name:/S1KHDSTD/})).toBeNull();
  fireEvent.click(screen.getByRole("checkbox", {name:/S1KHFELP/}));
  fireEvent.click(screen.getByRole("checkbox", {name:/S1KHFSTD/}));
  fireEvent.change(screen.getByLabelText("What is the Part called?"), {target:{value:"Chassis"}});
  fireEvent.change(screen.getByLabelText("What distinguishes this design?"), {target:{value:"Standard"}});
  fireEvent.change(screen.getByLabelText("Why is this Part needed?"), {target:{value:"Common design across both codes"}});
  await waitFor(() => expect(screen.getByText("S1KHF — Chassis — Standard")).toBeTruthy());
  fireEvent.click(screen.getByRole("button", {name:"Save Part"}));
  await waitFor(() => expect(mocks.createManagedPart).toHaveBeenCalled());
  const [payload] = mocks.createManagedPart.mock.calls[0];
  expect(payload).toMatchObject({scope:"product_model",targetId:"model-1",selectedProductId:"product-1",selectedProductModelId:"model-1",intendedModelCodeIds:["code-1","code-2"]});
  expect(payload.expectedName).toBe("S1KHF — Chassis — Standard");
  expect(mocks.savePartIntendedSharing).not.toHaveBeenCalled();
});

it("allows Product-level descendant codes without a Model selection and keeps Global naming independent", async () => {
  render(<PartsView/>);
  fireEvent.click(screen.getAllByRole("button", {name:/New Part/})[0]);
  fireEvent.change(await screen.findByLabelText("Name derives from"), {target:{value:"product"}});
  fireEvent.change(screen.getByLabelText("Name derives from"), {target:{value:"global"}});
  const productFilter = screen.getByLabelText("Intended sharing Product filter") as HTMLSelectElement;
  await waitFor(() => expect(productFilter.disabled).toBe(false));
  fireEvent.change(productFilter, {target:{value:"product-1"}});
  expect((screen.getByLabelText("Name derives from") as HTMLSelectElement).value).toBe("global");
  expect((screen.getByLabelText(/Intended sharing Product Model filter/) as HTMLSelectElement).value).toBe("");
  expect(screen.getByRole("checkbox", {name:/S1KHDSTD/})).toBeTruthy();
  fireEvent.click(screen.getByRole("checkbox", {name:/S1KHDSTD/}));
  expect(screen.getByText("1 intended Model Code")).toBeTruthy();
});

it("restores a failed initial Part save from the same browser session request", async () => {
  mocks.createManagedPart.mockReset()
    .mockRejectedValueOnce(new Error("PART_CREATED_SHARING_PENDING: intended sharing can be resumed"))
    .mockResolvedValueOnce({part:{...part,id:"resumed-part",partNumber:"SM-P-000002",name:"S1KHF — Chassis — Standard"}});
  const saveForm = async () => {
    fireEvent.click(screen.getAllByRole("button", {name:/New Part/})[0]);
    const productFilter = await screen.findByLabelText("Intended sharing Product filter") as HTMLSelectElement;
    await waitFor(() => expect(productFilter.disabled).toBe(false));
    fireEvent.change(productFilter, {target:{value:"product-1"}});
    const modelFilter = screen.getByLabelText(/Intended sharing Product Model filter/) as HTMLSelectElement;
    await waitFor(() => expect(modelFilter.disabled).toBe(false));
    fireEvent.change(modelFilter, {target:{value:"model-1"}});
    fireEvent.click(screen.getByRole("checkbox", {name:/S1KHFELP/}));
    fireEvent.change(screen.getByLabelText("What is the Part called?"), {target:{value:"Chassis"}});
    fireEvent.change(screen.getByLabelText("What distinguishes this design?"), {target:{value:"Standard"}});
    fireEvent.change(screen.getByLabelText("Why is this Part needed?"), {target:{value:"Resumable initial save"}});
    await waitFor(() => expect(screen.getByText("S1KHF — Chassis — Standard")).toBeTruthy());
    fireEvent.click(screen.getByRole("button", {name:"Save Part"}));
    await screen.findByRole("alert");
  };
  render(<PartsView/>);
  await saveForm();
  const firstCall = mocks.createManagedPart.mock.calls[0];
  expect(JSON.parse(sessionStorage.getItem("parts:create-attempt") || "{}").key).toBe(firstCall[1]);
  cleanup();
  render(<PartsView/>);
  await screen.findByDisplayValue("Chassis");
  fireEvent.click(await screen.findByRole("button", {name:/Retry same creation request/}));
  await waitFor(() => expect(mocks.createManagedPart).toHaveBeenCalledTimes(2));
  expect(mocks.createManagedPart.mock.calls[1][1]).toBe(firstCall[1]);
  expect(mocks.createManagedPart.mock.calls[1][0]).toEqual(firstCall[0]);
});

it("keeps a partially published create locked and retries its original name after shortcode maintenance", async () => {
  let currentShortcode = "S1K";
  const serverParts = new Map<string, {part: typeof part}>();
  mocks.partScopeTargets.mockImplementation(async () => {
    const current:any = scopeTargets(currentShortcode);
    current.scopes.find((item:any) => item.id === "product").targets[0].shortcode = currentShortcode;
    return current;
  });
  mocks.partNamePreview.mockImplementation(async (scope, targetId, description, variant) => ({
    name:`${scope === "product" && currentShortcode === "SNEW" ? "SNEW" : targetId === "model-1" ? "S1KHF" : "S1K"} — ${description}${variant ? ` — ${variant}` : ""}`,
    available:true,collision:[],
  }));
  mocks.createManagedPart.mockReset()
    .mockImplementationOnce((payload: any, key: string) => {
      serverParts.set(key, {part:{...part,id:"server-part-1",partNumber:"SM-P-000001",name:payload.expectedName}});
      return Promise.reject(new ApiError("PART_NAME_PREVIEW_STALE after the Part row committed", 409,
        "PART_NAME_PREVIEW_STALE", "retry_same_request"));
    })
    .mockImplementation((payload: any, key: string) => Promise.resolve(serverParts.get(key)));

  const firstView = render(<PartsView/>);
  fireEvent.click(screen.getAllByRole("button", {name:/New Part/})[0]);
  fireEvent.change(await screen.findByLabelText("Name derives from"), {target:{value:"product"}});
  const target = await screen.findByLabelText("Scope target") as HTMLSelectElement;
  await waitFor(() => expect(target.disabled).toBe(false));
  fireEvent.change(target, {target:{value:"product-1"}});
  const description = screen.getByLabelText("What is the Part called?") as HTMLInputElement;
  fireEvent.change(description, {target:{value:"Chassis"}});
  fireEvent.change(screen.getByLabelText("Why is this Part needed?"), {target:{value:"Recover after partial Grist publication"}});
  await waitFor(() => expect(screen.getByText("S1K — Chassis")).toBeTruthy());
  fireEvent.click(screen.getByRole("button", {name:"Save Part"}));
  await screen.findByRole("alert");
  const original = mocks.createManagedPart.mock.calls[0];
  expect(screen.getByRole("button", {name:"Retry same creation request"})).toHaveProperty("disabled", false);
  expect(description.disabled).toBe(true);
  expect(screen.getByRole("button", {name:"Recovery is required"})).toHaveProperty("disabled", true);
  expect(serverParts.size).toBe(1);

  currentShortcode = "SNEW";
  firstView.unmount();
  render(<PartsView/>);
  expect(await screen.findByRole("option", {name:"Safari 1000 · SNEW"})).toBeTruthy();
  const restoredDescription = await screen.findByDisplayValue("Chassis") as HTMLInputElement;
  expect(restoredDescription.disabled).toBe(true);
  expect(screen.getByRole("button", {name:"Retry same creation request"})).toBeTruthy();
  expect(screen.queryByRole("button", {name:"Save Part"})).toBeNull();
  fireEvent.click(screen.getByRole("button", {name:"Retry same creation request"}));
  await waitFor(() => expect(screen.queryByLabelText("What is the Part called?")).toBeNull());
  expect(mocks.createManagedPart).toHaveBeenCalledTimes(2);
  expect(mocks.createManagedPart.mock.calls[1]).toEqual(original);
  expect(serverParts.size).toBe(1);
  expect([...serverParts.values()][0].part).toMatchObject({partNumber:"SM-P-000001",name:"S1K — Chassis"});
});

it("unlocks a standalone creation after a structured pre-write rejection so it can be corrected", async () => {
  vi.stubGlobal("crypto", {randomUUID: vi.fn().mockReturnValueOnce("rejected-request").mockReturnValueOnce("corrected-request")});
  mocks.createManagedPart.mockReset()
    .mockRejectedValueOnce(new ApiError("PART_NAME_PREVIEW_STALE: refresh the name", 409, "PART_NAME_PREVIEW_STALE", "safe_to_edit"))
    .mockResolvedValueOnce({part:{...part,id:"corrected-standalone",partNumber:"SM-P-000003",name:"S1K — Chassis corrected"}});
  render(<PartsView/>);
  fireEvent.click(screen.getAllByRole("button", {name:/New Part/})[0]);
  const description = await screen.findByLabelText("What is the Part called?") as HTMLInputElement;
  await waitFor(() => expect(description.disabled).toBe(false));
  fireEvent.change(description, {target:{value:"Chassis"}});
  fireEvent.change(screen.getByLabelText("Why is this Part needed?"), {target:{value:"Correct a rejected Part request"}});
  await waitFor(() => expect(screen.getByText("S1K — Chassis")).toBeTruthy());
  fireEvent.click(screen.getByRole("button", {name:"Save Part"}));
  await screen.findByRole("alert");
  expect(description.disabled).toBe(false);
  expect(screen.getByRole("button", {name:"Back to Parts"})).toHaveProperty("disabled", false);
  const firstCall = mocks.createManagedPart.mock.calls[0];
  fireEvent.change(description, {target:{value:"Chassis corrected"}});
  await waitFor(() => expect(screen.getByText("S1K — Chassis corrected")).toBeTruthy());
  fireEvent.click(screen.getByRole("button", {name:"Save Part"}));
  await waitFor(() => expect(mocks.createManagedPart).toHaveBeenCalledTimes(2));
  expect(mocks.createManagedPart.mock.calls[1][0]).toMatchObject({...firstCall[0],description:"Chassis corrected",expectedName:"S1K — Chassis corrected"});
  expect(mocks.createManagedPart.mock.calls[1][1]).not.toBe(firstCall[1]);
});

it("allows standalone creation to be cancelled after a structured pre-write rejection", async () => {
  mocks.createManagedPart.mockRejectedValueOnce(new ApiError("Correct the Part fields", 422, "PART_INPUT_INVALID", "safe_to_edit"));
  render(<PartsView/>);
  fireEvent.click(screen.getAllByRole("button", {name:/New Part/})[0]);
  const description = await screen.findByLabelText("What is the Part called?") as HTMLInputElement;
  await waitFor(() => expect(description.disabled).toBe(false));
  fireEvent.change(description, {target:{value:"Chassis"}});
  fireEvent.change(screen.getByLabelText("Why is this Part needed?"), {target:{value:"Cancel after a rejected request"}});
  await waitFor(() => expect(screen.getByText("S1K — Chassis")).toBeTruthy());
  fireEvent.click(screen.getByRole("button", {name:"Save Part"}));
  await screen.findByRole("alert");
  fireEvent.click(screen.getByRole("button", {name:"Back to Parts"}));
  expect(screen.queryByLabelText("What is the Part called?")).toBeNull();
  expect(sessionStorage.getItem("parts:create-attempt")).toBeNull();
});

it("refreshes the mounted name preview after a shortcode change and ignores an older preview response", async () => {
  const obsoletePreview = new Promise<{name:string;available:boolean;collision:never[]}>(resolve => { (window as any).__resolveObsoletePartPreview = resolve; });
  mocks.partScopeTargets.mockReset()
    .mockResolvedValueOnce(scopeTargets("S1KHF"))
    .mockResolvedValueOnce(scopeTargets("S1KHF"))
    .mockResolvedValueOnce(scopeTargets("S1000HF"));
  mocks.partNamePreview.mockReset()
    .mockReturnValueOnce(obsoletePreview)
    .mockResolvedValueOnce({name:"S1000HF — Chassis — Reinforced",available:true,collision:[]});
  mocks.maintainPartShortcode.mockReset().mockResolvedValue({shortcode:"S1000HF",version:2});
  render(<PartsView/>);
  await screen.findByRole("treeitem", {name:/Product Model/});
  fireEvent.click(screen.getAllByRole("button", {name:/New Part/})[0]);
  const nameScope = await screen.findByLabelText("Name derives from") as HTMLSelectElement;
  await waitFor(() => expect(nameScope.disabled).toBe(false));
  fireEvent.change(nameScope, {target:{value:"product_model"}});
  const nameTarget = await screen.findByLabelText("Scope target") as HTMLSelectElement;
  await waitFor(() => expect(nameTarget.disabled).toBe(false));
  fireEvent.change(nameTarget, {target:{value:"model-1"}});
  fireEvent.change(screen.getByLabelText("What is the Part called?"), {target:{value:"Chassis"}});
  fireEvent.change(screen.getByLabelText("What distinguishes this design?"), {target:{value:"Reinforced"}});
  fireEvent.change(screen.getByLabelText("Why is this Part needed?"), {target:{value:"Use the current reviewed prefix"}});
  await waitFor(() => expect(mocks.partNamePreview).toHaveBeenCalledTimes(1));
  fireEvent.click(screen.getByRole("button", {name:"Review shortcode"}));
  fireEvent.change(screen.getByLabelText("Shortcode"), {target:{value:"S1000HF"}});
  fireEvent.change(screen.getByLabelText("Why is this shortcode being set?"), {target:{value:"Prefix changed in the master"}});
  fireEvent.click(screen.getByRole("button", {name:"Save shortcode"}));
  await waitFor(() => expect(mocks.partNamePreview).toHaveBeenCalledTimes(2));
  await screen.findByText("S1000HF — Chassis — Reinforced");
  (window as any).__resolveObsoletePartPreview({name:"S1KHF — Chassis — Reinforced",available:true,collision:[]});
  await waitFor(() => expect(screen.getByText("S1000HF — Chassis — Reinforced")).toBeTruthy());
  expect(screen.queryByText("S1KHF — Chassis — Reinforced")).toBeNull();
  expect((screen.getByLabelText("What is the Part called?") as HTMLInputElement).value).toBe("Chassis");
  expect((screen.getByLabelText("What distinguishes this design?") as HTMLInputElement).value).toBe("Reinforced");
  delete (window as any).__resolveObsoletePartPreview;
});

it("recovers an uncertain shortcode save with the exact request after remount", async () => {
  mocks.partScopeTargets.mockReset().mockResolvedValue(scopeTargets(null));
  mocks.partNamePreview.mockResolvedValue({name:"S1KHF — Chassis",available:true,collision:[]});
  mocks.maintainPartShortcode.mockReset()
    .mockRejectedValueOnce(new Error("response lost after the shortcode write"))
    .mockResolvedValueOnce({shortcode:"S1KHF",version:1});
  render(<PartsView/>);
  fireEvent.click(screen.getAllByRole("button", {name:/New Part/})[0]);
  const nameScope = await screen.findByLabelText("Name derives from") as HTMLSelectElement;
  await waitFor(() => expect(nameScope.disabled).toBe(false));
  fireEvent.change(nameScope, {target:{value:"product_model"}});
  const nameTarget = await screen.findByLabelText("Scope target") as HTMLSelectElement;
  await waitFor(() => expect(nameTarget.disabled).toBe(false));
  fireEvent.change(nameTarget, {target:{value:"model-1"}});
  fireEvent.change(screen.getByLabelText("What is the Part called?"), {target:{value:"Chassis"}});
  fireEvent.change(screen.getByLabelText("Why is this Part needed?"), {target:{value:"Create after recovering the prefix"}});
  fireEvent.change(await screen.findByLabelText("Shortcode"), {target:{value:"S1KHF"}});
  fireEvent.change(screen.getByLabelText("Why is this shortcode being set?"), {target:{value:"Recover a lost confirmation"}});
  fireEvent.click(screen.getByRole("button", {name:"Save shortcode"}));
  await screen.findByRole("alert");
  const original = mocks.maintainPartShortcode.mock.calls[0];
  expect(JSON.parse(sessionStorage.getItem("part-shortcode:attempt") || "{}")).toMatchObject({key:original[1],payload:original[0]});
  expect(screen.getByLabelText("Shortcode")).toHaveProperty("disabled", true);
  cleanup();
  render(<PartsView/>);
  fireEvent.click(await screen.findAllByRole("button", {name:/New Part/}).then(buttons => buttons[0]));
  const retryShortcode = await screen.findByRole("button", {name:"Retry shortcode save"}) as HTMLButtonElement;
  await waitFor(() => expect(retryShortcode.disabled).toBe(false));
  fireEvent.click(retryShortcode);
  await waitFor(() => expect(mocks.maintainPartShortcode).toHaveBeenCalledTimes(2));
  expect(mocks.maintainPartShortcode.mock.calls[1]).toEqual(original);
  await waitFor(() => expect(screen.getByText("S1KHF — Chassis")).toBeTruthy());
  expect(screen.queryByText(/Resolve the missing shortcode/)).toBeNull();
});

it("saves audited intended-sharing changes without changing actual configuration usage", async () => {
  render(<PartsView/>);
  await screen.findByRole("treeitem", {name:/Product Model/});
  const productParent = screen.getAllByRole("treeitem", {name:/Safari 1000/}).find(item => item.getAttribute("aria-expanded") === "false");
  fireEvent.click(productParent!);
  fireEvent.click(screen.getByRole("treeitem", {name:/Safari 1000 HF/}));
  fireEvent.click(await screen.findByRole("treeitem", {name:/SM-P-000001/}));
  fireEvent.click(await screen.findByRole("button", {name:/Open full Part details/}));
  fireEvent.click(await screen.findByRole("button", {name:"Intended sharing"}));
  await screen.findByRole("heading", {name:/Intended sharing/});
  fireEvent.click(screen.getByRole("button", {name:/Remove S1KHFSTD/}));
  fireEvent.change(screen.getByLabelText("Why is intended sharing changing?"), {target:{value:"This code no longer shares the housing"}});
  fireEvent.click(screen.getByRole("button", {name:"Save intended sharing"}));
  await waitFor(() => expect(mocks.savePartIntendedSharing).toHaveBeenCalledWith("part-uuid", expect.objectContaining({expectedVersion:0,intendedModelCodeIds:[],reason:"This code no longer shares the housing"}), "parts-test-request"));
  expect(screen.getByText(/Direct Parts selected in current published configurations/)).toBeTruthy();
});

it("shows component navigation and actual purchase capture affordances", async () => {
  const child = {...part, id:"child-uuid", partNumber:"SM-P-000002", name:"S1K — Bearing"};
  mocks.parts.mockResolvedValue({items:[part,child],legacyItems:[],storage:"Grist Safari Manufacturing"});
  mocks.partDetails.mockResolvedValue({...details,components:{status:"available",items:[{componentKey:"c1",childPartId:"child-uuid",childPartNumber:child.partNumber,childName:child.name,quantity:2,uom:"each",childRevisionLabel:"A"}]}});
  render(<PartsView/>);
  await screen.findByRole("treeitem", {name:/Product Model/});
  const productParent = screen.getAllByRole("treeitem", {name:/Safari 1000/}).find(item => item.getAttribute("aria-expanded") === "false");
  fireEvent.click(productParent!);
  fireEvent.click(screen.getByRole("treeitem", {name:/Safari 1000 HF/}));
  fireEvent.click(await screen.findByRole("treeitem", {name:/SM-P-000001/}));
  fireEvent.click(await screen.findByRole("button", {name:/Open full Part details/}));
  fireEvent.click(await screen.findByRole("button", {name:"Purchase details"}));
  await screen.findByRole("heading", {name:/Purchased Part details/});
  expect(screen.getByText(/Component Part · pinned Rev A · 2 each/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", {name:/Record actual purchase/}));
  expect(screen.getByLabelText("Transaction reference")).toBeTruthy();
  expect(screen.getByText(/MaterialRateLog and Default Material rates are not used/)).toBeTruthy();
});

it("uses stable app-specific names and autofill hints in the standalone shared creation form", async () => {
  render(<PartsView/>);
  const search = screen.getByLabelText("Search Parts by number, name or alias") as HTMLInputElement;
  expect(search.name).toBe("parts-register-search");
  expect(search.autocomplete).toBe("off");
  expect(search.getAttribute("spellcheck")).toBe("false");
  expect(search.getAttribute("autocapitalize")).toBe("none");
  fireEvent.click(screen.getAllByRole("button", {name:/New Part/})[0]);
  const form = await screen.findByRole("region", {name:"Create a canonical Part"});
  await waitFor(() => expect((within(form).getByLabelText("What is the Part called?") as HTMLInputElement).disabled).toBe(false));
  const editable = Array.from(form.querySelectorAll<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>("input:not([type=checkbox]),select,textarea"));
  expect(editable.length).toBeGreaterThan(5);
  for (const control of editable) {
    expect(control.name).not.toBe("");
    expect(control.getAttribute("autocomplete")).toBe("off");
    expect(control.name).not.toMatch(/address|card|payment|password|email|phone/i);
  }
  expect((within(form).getByLabelText("Search intended Model Codes") as HTMLInputElement).getAttribute("autocapitalize")).toBe("none");
  expect((within(form).getByLabelText("Search intended Model Codes") as HTMLInputElement).getAttribute("spellcheck")).toBe("false");
});

it("creates an explicit SM-A Assembly without exposing intended Model Code assignment", async () => {
  render(<PartsView/>);
  fireEvent.click(screen.getAllByRole("button", {name:"New Assembly"})[0]);
  const form = await screen.findByRole("region", {name:"Create a canonical Assembly"});
  const description = within(form).getByLabelText("What is the Assembly called?") as HTMLInputElement;
  await waitFor(() => expect(description.disabled).toBe(false));
  fireEvent.change(description, {target:{value:"Main Assembly"}});
  fireEvent.change(within(form).getByLabelText("Why is this Assembly needed?"), {target:{value:"Create the first assembly identity"}});
  await waitFor(() => expect(screen.getByText("S1K — Main Assembly")).toBeTruthy());
  expect(within(form).queryByLabelText("Intended sharing Product filter")).toBeNull();
  expect(within(form).queryByLabelText("Search intended Model Codes")).toBeNull();
  fireEvent.click(within(form).getByRole("button", {name:"Save Assembly"}));
  await waitFor(() => expect(mocks.createAssembly).toHaveBeenCalledTimes(1));
  expect(mocks.createAssembly.mock.calls[0][0]).toMatchObject({
    scope:"global", targetId:"global", description:"Main Assembly", identityKind:"assembly",
    reason:"Create the first assembly identity",
  });
  expect(mocks.createManagedPart).not.toHaveBeenCalled();
});

it("keeps an unresolved Part request in Part recovery while a new Assembly uses its own request", async () => {
  const firstPartResponse = deferred<any>();
  const reservedRequests = new Map<string, any>();
  mocks.createManagedPart.mockReset()
    .mockImplementationOnce(() => firstPartResponse.promise)
    .mockImplementation((payload: any, key: string) => {
      expect(reservedRequests.get(key)).toEqual(payload);
      return Promise.resolve({part:{...part,id:"part-recovered-after-switch",partNumber:"SM-P-000002",name:payload.expectedName}});
    });
  vi.stubGlobal("crypto", {randomUUID:vi.fn().mockReturnValueOnce("part-create-key").mockReturnValueOnce("assembly-create-key")});
  render(<PartsView/>);

  await fillAndSaveCreation("part", "Switchable Part", "Keep the Part request recoverable");
  await waitFor(() => expect(mocks.createManagedPart).toHaveBeenCalledTimes(1));
  const originalPartRequest = mocks.createManagedPart.mock.calls[0];
  expect(reservedRequests.has(originalPartRequest[1])).toBe(false);
  expect(JSON.parse(sessionStorage.getItem("parts:create-attempt") || "{}")).toEqual({
    key:originalPartRequest[1], payload:originalPartRequest[0], origin:null,
  });

  await fillAndSaveCreation("assembly", "Independent Assembly", "Use a distinct Assembly request");
  await waitFor(() => expect(mocks.createAssembly).toHaveBeenCalledTimes(1));
  expect(mocks.createAssembly.mock.calls[0][0]).toMatchObject({identityKind:"assembly",description:"Independent Assembly"});
  expect(mocks.createAssembly.mock.calls[0][1]).toBe("assembly-create-key");
  expect(mocks.createAssembly.mock.calls[0][0]).not.toEqual(originalPartRequest[0]);

  reservedRequests.set(originalPartRequest[1], originalPartRequest[0]);
  firstPartResponse.reject(new Error("The response was lost after the Part request was reserved."));
  await firstPartResponse.promise.catch(() => undefined);
  fireEvent.click(screen.getAllByRole("button", {name:"New Part"})[0]);
  const restoredDescription = await screen.findByLabelText("What is the Part called?") as HTMLInputElement;
  expect(restoredDescription.value).toBe("Switchable Part");
  const retry = screen.getByRole("button", {name:"Retry same creation request"});
  expect((retry as HTMLButtonElement).disabled).toBe(false);
  fireEvent.click(retry);
  await waitFor(() => expect(mocks.createManagedPart).toHaveBeenCalledTimes(2));
  expect(mocks.createManagedPart.mock.calls[1]).toEqual(originalPartRequest);
  expect(mocks.createAssembly.mock.calls[0][1]).not.toBe(originalPartRequest[1]);
});

it("keeps an unresolved Assembly request recoverable after switching to and reloading a Part form", async () => {
  const firstAssemblyResponse = deferred<any>();
  const reservedRequests = new Map<string, any>();
  mocks.createAssembly.mockReset()
    .mockImplementationOnce(() => firstAssemblyResponse.promise)
    .mockImplementation((payload: any, key: string) => {
      expect(reservedRequests.get(key)).toEqual(payload);
      return Promise.resolve({part:{...part,id:"assembly-recovered-after-switch",identityKind:"assembly",partNumber:"SM-A-000002",name:payload.expectedName}});
    });
  vi.stubGlobal("crypto", {randomUUID:vi.fn().mockReturnValueOnce("assembly-create-key").mockReturnValueOnce("part-create-key")});
  render(<PartsView/>);

  await fillAndSaveCreation("assembly", "Switchable Assembly", "Keep the Assembly request recoverable");
  await waitFor(() => expect(mocks.createAssembly).toHaveBeenCalledTimes(1));
  const originalAssemblyRequest = mocks.createAssembly.mock.calls[0];
  expect(reservedRequests.has(originalAssemblyRequest[1])).toBe(false);
  expect(JSON.parse(sessionStorage.getItem("assemblies:create-attempt") || "{}")).toEqual({
    key:originalAssemblyRequest[1], payload:originalAssemblyRequest[0], origin:null,
  });

  await fillAndSaveCreation("part", "Independent Part", "Use a distinct Part request");
  await waitFor(() => expect(mocks.createManagedPart).toHaveBeenCalledTimes(1));
  expect(mocks.createManagedPart.mock.calls[0][0]).not.toHaveProperty("identityKind", "assembly");
  expect(mocks.createManagedPart.mock.calls[0][0]).toMatchObject({description:"Independent Part"});
  expect(mocks.createManagedPart.mock.calls[0][1]).toBe("part-create-key");
  expect(mocks.createManagedPart.mock.calls[0][0]).not.toEqual(originalAssemblyRequest[0]);

  reservedRequests.set(originalAssemblyRequest[1], originalAssemblyRequest[0]);
  firstAssemblyResponse.reject(new Error("The response was lost after the Assembly request was reserved."));
  await firstAssemblyResponse.promise.catch(() => undefined);
  cleanup();
  render(<PartsView/>);
  const restoredDescription = await screen.findByLabelText("What is the Assembly called?") as HTMLInputElement;
  expect(restoredDescription.value).toBe("Switchable Assembly");
  const retry = screen.getByRole("button", {name:"Retry same creation request"});
  expect((retry as HTMLButtonElement).disabled).toBe(false);
  fireEvent.click(retry);
  await waitFor(() => expect(mocks.createAssembly).toHaveBeenCalledTimes(2));
  expect(mocks.createAssembly.mock.calls[1]).toEqual(originalAssemblyRequest);
  expect(mocks.createManagedPart.mock.calls[0][1]).not.toBe(originalAssemblyRequest[1]);
});

it("opens Manufacturing baseline only after delayed Part details render", async () => {
  const pendingDetails = deferred<any>();
  mocks.partDetails.mockReturnValueOnce(pendingDetails.promise);
  render(<PartsView/>);
  await screen.findByRole("treeitem", {name:/Product Model/});
  const productParent = screen.getAllByRole("treeitem", {name:/Safari 1000/}).find(item => item.getAttribute("aria-expanded") === "false");
  fireEvent.click(productParent!);
  fireEvent.click(screen.getByRole("treeitem", {name:/Safari 1000 HF/}));
  fireEvent.click(await screen.findByRole("treeitem", {name:/SM-P-000001/}));
  fireEvent.click(await screen.findByRole("button", {name:/Manufacturing baseline/}));
  expect(document.getElementById("manufacturing-baseline")).toBeNull();

  pendingDetails.resolve(details);
  await waitFor(() => expect((document.getElementById("manufacturing-baseline") as HTMLDetailsElement)?.open).toBe(true));
});

it("opens composition only after delayed Assembly details render", async () => {
  const assembly = {...part, id:"assembly-delayed", identityKind:"assembly" as const, partNumber:"SM-A-000001", name:"S1K — Delayed Assembly", scope:"global", scopeTargetId:"global", scopeTarget:"Safari Manufacturing"};
  const pendingDetails = deferred<any>();
  mocks.parts.mockResolvedValue({items:[assembly,part],legacyItems:[],storage:"Grist Safari Manufacturing"});
  mocks.partDetails.mockReturnValueOnce(pendingDetails.promise);
  render(<PartsView/>);
  fireEvent.click(await screen.findByRole("treeitem", {name:/SM-A-000001/}));
  fireEvent.click(await screen.findByRole("button", {name:/Open composition/}));
  expect(document.getElementById("components")).toBeNull();

  pendingDetails.resolve({...details, part:{...assembly,compositionStatus:"draft"}, components:{status:"available",items:[]}});
  await waitFor(() => expect((document.getElementById("components") as HTMLDetailsElement)?.open).toBe(true));
});

it("cancels a delayed section shortcut when navigation moves to another Part", async () => {
  const otherPart = {...part, id:"other-part", partNumber:"SM-P-000002", name:"S1K — Other Part"};
  const firstDetails = deferred<any>();
  const secondDetails = deferred<any>();
  mocks.parts.mockResolvedValue({items:[part,otherPart],legacyItems:[],storage:"Grist Safari Manufacturing"});
  mocks.partDetails.mockImplementation((id: string) => id === part.id ? firstDetails.promise : secondDetails.promise);
  render(<PartsView/>);
  await screen.findByRole("treeitem", {name:/Product Model/});
  const productParent = screen.getAllByRole("treeitem", {name:/Safari 1000/}).find(item => item.getAttribute("aria-expanded") === "false");
  fireEvent.click(productParent!);
  fireEvent.click(screen.getByRole("treeitem", {name:/Safari 1000 HF/}));
  fireEvent.click(await screen.findByRole("treeitem", {name:/SM-P-000001/}));
  fireEvent.click(await screen.findByRole("button", {name:/Manufacturing baseline/}));
  fireEvent.click(screen.getByRole("treeitem", {name:/SM-P-000002/}));

  firstDetails.resolve(details);
  secondDetails.resolve({...details, part:{...otherPart,compositionStatus:"draft"}});
  fireEvent.click(await screen.findByRole("button", {name:/Open full Part details/}));
  await waitFor(() => expect(document.getElementById("manufacturing-baseline")).not.toBeNull());
  expect((document.getElementById("manufacturing-baseline") as HTMLDetailsElement).open).toBe(false);
});

it("shows accepted baseline provenance from the Part and preserves blank versus missing source fields", async () => {
  const baselineDetails = {
    ...details,
    manufacturingBaseline:{status:"established",sourceHash:"workbook-hash",revisionLabel:"A",
      baseline:{WorkbookName:"S1KHF.ods",WorkbookPath:"pilot/S1KHF.ods",SourceHash:"workbook-hash"},
      families:[{FamilyKey:"MCL",Family:"MCL",ApplicabilityStatus:"applicable",SourceEvidenceStatus:"complete",RequirementCount:1,CompletenessConfirmed:true}],
      requirements:[
        {key:"mcl",family:"mcl",sheet:"5. Material Cut List Price",row:2,lineKey:"part-baseline-line:opaque-hash",physical:{item_name:null,quantity:"2",quantity_uom:"nos",material:"ms plate",dimension:"3 x 25",weight_kg:null},rawFields:{product_part_name:"Browser QA Plate",material_to_cut:"MS Plate",dimension_to_cut_mm:"3 x 25",qty:"2",in_use:"Yes",remarks:"Synthetic fixture"},cellEvidence:{sourceHeaders:{product_part_name:"Machine Piece Description"}}},
        {key:"toolshop",family:"toolshop",sheet:"Tool Shop Items",row:18,lineKey:"toolshop:line",physical:{item_name:"Weld fixture",quantity:1,quantity_uom:"Nos"},lineDetail:{toolShopItemsRemarks:{sourceStatus:"blank",value:null}}},
        {key:"cnc",family:"cnc",sheet:"CNC Cut List",row:27,lineKey:"cnc:line",physical:{plate_part:"Frame",quantity:1,quantity_uom:"Nos",length:25,width:3,thickness:2},lineDetail:{platePartToCut:{sourceStatus:"missing",value:null}}},
      ]},
  };
  mocks.partDetails.mockResolvedValue(baselineDetails);
  render(<PartsView/>);
  await screen.findByRole("treeitem", {name:/Product Model/});
  const productParent = screen.getAllByRole("treeitem", {name:/Safari 1000/}).find(item => item.getAttribute("aria-expanded") === "false");
  fireEvent.click(productParent!);
  fireEvent.click(screen.getByRole("treeitem", {name:/Safari 1000 HF/}));
  fireEvent.click(await screen.findByRole("treeitem", {name:/SM-P-000001/}));
  fireEvent.click(await screen.findByRole("button", {name:/Manufacturing baseline/}));
  await screen.findByText("S1KHF.ods · pilot/S1KHF.ods");
  expect(screen.getByText("MCL · Machine Piece Description: Browser QA Plate")).toBeTruthy();
  expect(screen.getByText("Tool Shop · Weld fixture")).toBeTruthy();
  expect(screen.getByText("CNC · Frame")).toBeTruthy();
  expect(screen.queryByText(/part-baseline-line:/)).toBeNull();
  expect(screen.getByText(/5\. Material Cut List Price · row 2 · quantity 2 nos · material ms plate/)).toBeTruthy();
  expect(screen.getByText("Tool Shop Items Remarks: (blank in source)")).toBeTruthy();
  expect(screen.getByText("Plate Part to Cut: Column not in source")).toBeTruthy();
  expect(mocks.addPartComponent).not.toHaveBeenCalled();
  expect(mocks.recordPartPurchase).not.toHaveBeenCalled();
});

it("drills through three assembly levels to a leaf Part and returns along the saved parent path", async () => {
  const root = {...part, id:"assembly-root", identityKind:"assembly" as const, partNumber:"SM-A-000001", name:"S1K — Root Assembly", scope:"global", scopeTargetId:"global", scopeTarget:"Safari Manufacturing"};
  const middle = {...root, id:"assembly-middle", partNumber:"SM-A-000002", name:"S1K — Middle Assembly"};
  const inner = {...root, id:"assembly-inner", partNumber:"SM-A-000003", name:"S1K — Inner Assembly"};
  const leaf = {...part, id:"leaf-part", partNumber:"SM-P-000001", name:"S1K — Leaf Part"};
  const items = [root, middle, inner, leaf];
  const components:Record<string, any[]> = {
    [root.id]:[{componentKey:"root-middle",childPartId:middle.id,childPartNumber:middle.partNumber,childName:middle.name,childIdentityKind:"assembly",relationshipStatus:"available",childRevisionLabel:"A",quantity:2,uom:"each"}],
    [middle.id]:[{componentKey:"middle-inner",childPartId:inner.id,childPartNumber:inner.partNumber,childName:inner.name,childIdentityKind:"assembly",relationshipStatus:"available",childRevisionLabel:"A",quantity:3,uom:"set"}],
    [inner.id]:[{componentKey:"inner-leaf",childPartId:leaf.id,childPartNumber:leaf.partNumber,childName:leaf.name,childIdentityKind:"part",relationshipStatus:"available",childRevisionLabel:"A",quantity:4,uom:"each"}],
  };
  mocks.parts.mockResolvedValue({items,legacyItems:[],storage:"Grist Safari Manufacturing"});
  mocks.partDetails.mockImplementation(async (id:string) => ({...details,
    part:{...items.find(item => item.id === id),compositionStatus:"draft",metadataHistory:[],lifecycleHistory:[]},
    components:{status:"available",items:components[id] || []},
    manufacturingBaseline:{status:"unavailable",message:"Assembly requirements are represented by pinned child composition."},
    processLines:{status:"available",items:[]},purchases:{specifications:[],vendorCatalog:[]},
  }));
  render(<PartsView/>);
  fireEvent.click(await screen.findByRole("treeitem", {name:/SM-A-000001/}));
  fireEvent.click(await screen.findByRole("button", {name:/Open composition/}));
  const middleLink = await screen.findByRole("button", {name:/SM-A-000002 · S1K — Middle Assembly/});
  expect(screen.getByText(/Subassembly · pinned Rev A · 2 each/)).toBeTruthy();
  fireEvent.click(middleLink);
  expect(await screen.findByRole("button", {name:"Back to S1K — Root Assembly"})).toBeTruthy();
  fireEvent.click(await screen.findByRole("button", {name:/SM-A-000003 · S1K — Inner Assembly/}));
  expect(await screen.findByRole("button", {name:"Back to S1K — Middle Assembly"})).toBeTruthy();
  fireEvent.click(await screen.findByRole("button", {name:/SM-P-000001 · S1K — Leaf Part/}));
  expect(await screen.findByRole("button", {name:"Back to S1K — Inner Assembly"})).toBeTruthy();
  fireEvent.click(screen.getByRole("button", {name:"Back to S1K — Inner Assembly"}));
  expect(await screen.findByRole("button", {name:/SM-P-000001 · S1K — Leaf Part/})).toBeTruthy();
  fireEvent.click(screen.getByRole("button", {name:"Back to S1K — Middle Assembly"}));
  expect(await screen.findByRole("button", {name:/SM-A-000003 · S1K — Inner Assembly/})).toBeTruthy();
  fireEvent.click(screen.getByRole("button", {name:"Back to S1K — Root Assembly"}));
  expect(await screen.findByRole("button", {name:/SM-A-000002 · S1K — Middle Assembly/})).toBeTruthy();
});

it("keeps Part detail sections collapsed until opened, and section shortcuts reopen them", async () => {
  render(<PartsView/>);
  await screen.findByRole("treeitem", {name:/Product Model/});
  const productParent = screen.getAllByRole("treeitem", {name:/Safari 1000/}).find(item => item.getAttribute("aria-expanded") === "false");
  fireEvent.click(productParent!);
  fireEvent.click(screen.getByRole("treeitem", {name:/Safari 1000 HF/}));
  fireEvent.click(await screen.findByRole("treeitem", {name:/SM-P-000001/}));
  fireEvent.click(await screen.findByRole("button", {name:/Open full Part details/}));

  const overview = document.getElementById("overview") as HTMLDetailsElement;
  const baseline = document.getElementById("manufacturing-baseline") as HTMLDetailsElement;
  const sharing = document.getElementById("intended-sharing") as HTMLDetailsElement;
  expect(overview.open).toBe(true);
  expect(baseline.open).toBe(false);
  expect(sharing.open).toBe(false);

  fireEvent.click(screen.getByText("Overview", {selector:"summary h3"}));
  expect(overview.open).toBe(false);
  fireEvent.click(screen.getAllByRole("button", {name:"Manufacturing baseline"})[0]);
  await waitFor(() => expect(baseline.open).toBe(true));
  fireEvent.click(screen.getByRole("button", {name:"Intended sharing"}));
  expect(sharing.open).toBe(true);
  expect(screen.getByText(/Advisory links only/)).toBeTruthy();
});

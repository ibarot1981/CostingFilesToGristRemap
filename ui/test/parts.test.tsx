import {cleanup, fireEvent, render, screen, waitFor} from "@testing-library/react";
import {afterEach, beforeEach, expect, it, vi} from "vitest";
import {PartsView} from "../src/PartsView";

const mocks = vi.hoisted(() => ({
  parts: vi.fn(), partScopeTargets: vi.fn(), partDetails: vi.fn(),
  partNamePreview: vi.fn(), createManagedPart: vi.fn(), savePartIntendedSharing: vi.fn(),
  addPartComponent: vi.fn(), recordPartPurchase: vi.fn(),
}));
vi.mock("../src/api", () => ({api: mocks}));

const part = {id:"part-uuid", partNumber:"SM-P-000001", name:"S1K — Chassis — Standard", description:"Chassis", variant:"Standard",
  scope:"product_model", scopeTargetId:"model-1", scopeTarget:"Safari 1000 HF", shortcode:"S1K", engineeringRevision:"A",
  status:"active", metadataVersion:1, aliases:[], legacy:false, actor:"operator", reason:"Initial design"};
const details = {part:{...part, compositionStatus:"draft", metadataHistory:[], lifecycleHistory:[]}, mappingHistory:[],
  processLines:{status:"available",items:[]}, components:{status:"available",items:[]}, drawings:{status:"empty",items:[]},
  intendedSharing:{status:"available",version:0,items:[{id:"code-2",code:"S1KHFSTD",modelId:"model-1",model:"Safari 1000 HF",productId:"product-1",product:"Safari 1000",createdBy:"operator",createdReason:"Shared chassis"}],history:[]},
  usedIn:{status:"available",coverage:"direct_selections_only",items:[{modelCodeId:"code-1",modelCode:"S1KHFELP",model:"Safari 1000 HF",configurationId:8,configurationVersion:2,configurationRevisionId:9,selectionIdentity:"front-actuator",occurrenceLabel:"Front actuator",quantity:2,uom:"each",sourcingRoute:"make",direct:true}],message:"Direct Parts selected in current published configurations. Component/indirect usage is not included."},
  purchases:{specifications:[{specification:{id:41,SpecificationCode:"MOTOR-1",Manufacturer:"Maker",ManufacturerPartNumber:"M-1",CostingUOM:"each",CostingCurrency:"INR"},
    rate:{status:"unavailable",rate:null,reason:"No eligible actual purchase exists.",ratePolicy:"net merchandise per costing UOM"},vendors:[],purchases:[],unitConversions:[],currencyConversions:[]}],vendorCatalog:[]}};

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
  mocks.savePartIntendedSharing.mockResolvedValue({partId:part.id,version:1,activeCodeIds:["code-2"]});
  mocks.parts.mockResolvedValue({items:[part],legacyItems:[],storage:"Grist Safari Manufacturing"});
  mocks.partDetails.mockResolvedValue(details);
});
afterEach(() => {cleanup(); vi.unstubAllGlobals();});

it("collapses and expands scope targets, then opens the selected Part details", async () => {
  render(<PartsView/>);
  const modelScope = await screen.findByRole("treeitem", {name:/Product Model/});
  expect(screen.queryByRole("treeitem", {name:/Unmatched \/ retired target/})).toBeNull();
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
  await screen.findByRole("heading", {name:/Purchased Part details/});
  expect(screen.getByText("No eligible actual purchase exists.")).toBeTruthy();
  expect(screen.getByRole("heading", {name:/Used in configurations/})).toBeTruthy();
  expect(screen.getByText("Configured directly; no intended-sharing link is recorded.")).toBeTruthy();
  expect(mocks.partDetails).toHaveBeenCalledWith("part-uuid");
  expect(modelScope.getAttribute("aria-expanded")).toBe("true");
  expect(sessionStorage.getItem("parts:expanded")).toContain("target:product_model:model-1");
});

it("filters one Model's code list and keeps intended codes separate from its naming anchor", async () => {
  render(<PartsView/>);
  fireEvent.click(screen.getAllByRole("button", {name:/New Part/})[0]);
  fireEvent.change(await screen.findByLabelText("Intended sharing Product filter"), {target:{value:"product-1"}});
  fireEvent.change(screen.getByLabelText("Intended sharing Product Model filter"), {target:{value:"model-1"}});
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
  fireEvent.change(screen.getByLabelText("Intended sharing Product filter"), {target:{value:"product-1"}});
  expect((screen.getByLabelText("Name derives from") as HTMLSelectElement).value).toBe("global");
  expect((screen.getByLabelText("Intended sharing Product Model filter") as HTMLSelectElement).value).toBe("");
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
    fireEvent.change(await screen.findByLabelText("Intended sharing Product filter"), {target:{value:"product-1"}});
    fireEvent.change(screen.getByLabelText("Intended sharing Product Model filter"), {target:{value:"model-1"}});
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
  fireEvent.click(await screen.findByRole("button", {name:"Retry same request"}));
  await waitFor(() => expect(mocks.createManagedPart).toHaveBeenCalledTimes(2));
  expect(mocks.createManagedPart.mock.calls[1][1]).toBe(firstCall[1]);
  expect(mocks.createManagedPart.mock.calls[1][0]).toEqual(firstCall[0]);
});

it("saves audited intended-sharing changes without changing actual configuration usage", async () => {
  render(<PartsView/>);
  await screen.findByRole("treeitem", {name:/Product Model/});
  const productParent = screen.getAllByRole("treeitem", {name:/Safari 1000/}).find(item => item.getAttribute("aria-expanded") === "false");
  fireEvent.click(productParent!);
  fireEvent.click(screen.getByRole("treeitem", {name:/Safari 1000 HF/}));
  fireEvent.click(await screen.findByRole("treeitem", {name:/SM-P-000001/}));
  fireEvent.click(await screen.findByRole("button", {name:/Open full Part details/}));
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
  await screen.findByRole("heading", {name:/Purchased Part details/});
  expect(screen.getByText(/2 each · Rev A/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", {name:/Record actual purchase/}));
  expect(screen.getByLabelText("Transaction reference")).toBeTruthy();
  expect(screen.getByText(/MaterialRateLog and Default Material rates are not used/)).toBeTruthy();
});

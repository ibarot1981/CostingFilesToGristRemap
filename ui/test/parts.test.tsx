import {cleanup, fireEvent, render, screen, waitFor} from "@testing-library/react";
import {afterEach, beforeEach, expect, it, vi} from "vitest";
import {PartsView} from "../src/PartsView";

const mocks = vi.hoisted(() => ({
  parts: vi.fn(), partScopeTargets: vi.fn(), partDetails: vi.fn(),
  addPartComponent: vi.fn(), recordPartPurchase: vi.fn(),
}));
vi.mock("../src/api", () => ({api: mocks}));

const part = {id:"part-uuid", partNumber:"SM-P-000001", name:"S1K — Chassis — Standard", description:"Chassis", variant:"Standard",
  scope:"product_model", scopeTargetId:"model-1", scopeTarget:"Safari 1000 HF", shortcode:"S1K", engineeringRevision:"A",
  status:"active", metadataVersion:1, aliases:[], legacy:false, actor:"operator", reason:"Initial design"};
const details = {part:{...part, compositionStatus:"draft", metadataHistory:[], lifecycleHistory:[]}, mappingHistory:[],
  processLines:{status:"available",items:[]}, components:{status:"available",items:[]}, drawings:{status:"empty",items:[]},
  usedIn:{status:"unavailable",items:[],message:"Configuration tables are not implemented."},
  purchases:{specifications:[{specification:{id:41,SpecificationCode:"MOTOR-1",Manufacturer:"Maker",ManufacturerPartNumber:"M-1",CostingUOM:"each",CostingCurrency:"INR"},
    rate:{status:"unavailable",rate:null,reason:"No eligible actual purchase exists.",ratePolicy:"net merchandise per costing UOM"},vendors:[],purchases:[],unitConversions:[],currencyConversions:[]}],vendorCatalog:[]}};

beforeEach(() => {
  sessionStorage.clear(); window.history.replaceState(null,"", "#parts");
  Object.values(mocks).forEach(mock => mock.mockReset());
  vi.stubGlobal("crypto", {randomUUID:()=>"parts-test-request"});
  mocks.partScopeTargets.mockResolvedValue({scopes:[
    {id:"global",label:"Global",target:{id:"global",label:"Safari Manufacturing"}},
    {id:"product",label:"Product",targets:[{id:"product-1",label:"Safari 1000"}]},
    {id:"product_model",label:"Product Model",targets:[{id:"model-1",parentId:"product-1",label:"Safari 1000 HF"}]},
    {id:"model_code",label:"Model Code",targets:[{id:"code-1",parentId:"model-1",label:"S1KHFELP"}]},
  ]});
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
  expect(mocks.partDetails).toHaveBeenCalledWith("part-uuid");
  expect(modelScope.getAttribute("aria-expanded")).toBe("true");
  expect(sessionStorage.getItem("parts:expanded")).toContain("target:product_model:model-1");
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

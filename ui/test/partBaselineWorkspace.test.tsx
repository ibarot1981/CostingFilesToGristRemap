import {cleanup, fireEvent, render, screen, waitFor} from "@testing-library/react";
import {afterEach, beforeEach, expect, it, vi} from "vitest";
import {PartBaselineWorkspace} from "../src/PartBaselineWorkspace";

const mocks = vi.hoisted(() => ({partMappings:vi.fn(),partBaselineReview:vi.fn(),establishPartBaseline:vi.fn(),comparePartBaseline:vi.fn(),partManufacturingComparison:vi.fn(),decidePartManufacturingComparison:vi.fn(),searchParts:vi.fn()}));
vi.mock("../src/api",() => ({api:mocks}));

const chassis = {id:"part-1",partNumber:"SM-P-000001",name:"Chassis",description:"Main chassis",engineeringRevision:"A",selectable:true,duplicateName:false,status:"active",manufacturingBaselineStatus:"not_established"};
const bracket = {id:"part-2",partNumber:"SM-P-000002",name:"Bracket",description:"Mounting bracket",engineeringRevision:"A",selectable:true,duplicateName:false,status:"active",manufacturingBaselineStatus:"established"};
const group = {key:"cnc-frame",sheet:"CNC Cut List",evidenceFingerprint:"evidence-42",description:"Frame",rows:[{lineKey:"cnc-frame:line:42",semanticEvidence:{fingerprint:"line-42"}}],reviewed:true,part:chassis};
const mapping = {parts:[chassis,bracket],groups:[group],sourceHash:"source-hash",associationKey:"association-key",associationVersion:2,version:4};
const review = (status:string) => ({baselineStatus:status,source:{fileId:"file-1",workbookPath:"pilot.ods",sourceHash:"source-hash",associationKey:"association-key",associationVersion:2,mappingVersion:4,mappingPolicyVersion:"v2"},families:{},existingContent:{processRequirements:0,components:0,purchaseSpecifications:0,requiresAppendConfirmation:false}});

beforeEach(() => {
  Object.values(mocks).forEach(mock => mock.mockReset());
  localStorage.clear();
  mocks.partMappings.mockResolvedValue(mapping);
  mocks.partBaselineReview.mockImplementation((partId:string) => Promise.resolve(review(partId === "part-2" ? "established" : "not_established")));
});
afterEach(() => {cleanup();});

it("searches mapped Parts, loads each selected baseline status and advances through the workbook Parts", async () => {
  render(<PartBaselineWorkspace path="pilot.ods" initialPartId="part-1" returnLabel="Return to Part Mapping" onBack={() => {}} onUseDifferentPart={() => {}}/>);
  await screen.findByRole("heading",{name:"Chassis"});
  await waitFor(() => expect(mocks.partBaselineReview).toHaveBeenCalledWith("part-1","pilot.ods"));
  const search = screen.getByRole("textbox",{name:"Search mapped Parts"});
  fireEvent.change(search,{target:{value:"Mounting bracket"}});
  expect(screen.getByRole("button",{name:/SM-P-000002 · Bracket/})).toBeTruthy();
  expect(screen.queryByRole("button",{name:/SM-P-000001 · Chassis/})).toBeNull();
  fireEvent.click(screen.getByRole("button",{name:/SM-P-000002 · Bracket/}));
  await screen.findByRole("heading",{name:"Bracket"});
  await waitFor(() => expect(mocks.partBaselineReview).toHaveBeenCalledWith("part-2","pilot.ods"));
  expect(screen.getAllByText("established").length).toBeGreaterThan(0);
  fireEvent.change(search,{target:{value:""}});
  fireEvent.click(screen.getByRole("button",{name:"Next Part"}));
  await screen.findByRole("heading",{name:"Chassis"});
  await waitFor(() => expect(mocks.partBaselineReview.mock.calls.filter(call => call[0] === "part-1")).toHaveLength(2));
  expect(screen.getByText(/Your Part Mapping drafts remain available/)).toBeTruthy();
});

import {cleanup, fireEvent, render, screen, waitFor} from "@testing-library/react";
import {afterEach, beforeEach, expect, it, vi} from "vitest";
import {PartBaselineWorkspace} from "../src/PartBaselineWorkspace";

const mocks = vi.hoisted(() => ({partMappings:vi.fn(),partBaselineWorkbookPreview:vi.fn(),confirmWorkbookPartBaselines:vi.fn(),partBaselineReview:vi.fn(),establishPartBaseline:vi.fn(),comparePartBaseline:vi.fn(),partManufacturingComparison:vi.fn(),decidePartManufacturingComparison:vi.fn(),searchParts:vi.fn()}));
vi.mock("../src/api",() => ({api:mocks}));

const chassis = {id:"part-1",partNumber:"SM-P-000001",name:"Chassis",description:"Main chassis",engineeringRevision:"A",selectable:true,duplicateName:false,status:"active",manufacturingBaselineStatus:"not_established"};
const bracket = {id:"part-2",partNumber:"SM-P-000002",name:"Bracket",description:"Mounting bracket",engineeringRevision:"A",selectable:true,duplicateName:false,status:"active",manufacturingBaselineStatus:"established"};
const group = {key:"cnc-frame",sheet:"CNC Cut List",evidenceFingerprint:"evidence-42",description:"Frame",rows:[{lineKey:"cnc-frame:line:42",semanticEvidence:{fingerprint:"line-42"}}],reviewed:true,part:chassis};
const mapping = {parts:[chassis,bracket],groups:[group],sourceHash:"source-hash",associationKey:"association-key",associationVersion:2,version:4};
const review = (status:string) => ({baselineStatus:status,source:{fileId:"file-1",workbookPath:"pilot.ods",sourceHash:"source-hash",associationKey:"association-key",associationVersion:2,mappingVersion:4,mappingPolicyVersion:"v2"},families:{},existingContent:{processRequirements:0,components:0,purchaseSpecifications:0,requiresAppendConfirmation:false}});
const preview = () => ({source:{fileId:"file-1",workbookPath:"pilot.ods",sourceHash:"source-hash",associationKey:"association-key",associationVersion:2,mappingVersion:4,mappingPolicyVersion:"v2",sourceSheets:{}},contextKey:"context-4",previewKey:"preview-4",parts:[
  {id:"part-1",partNumber:"SM-P-000001",name:"Chassis",snapshotKey:"snap-1",status:"ready",blockers:[],mappedLineCount:2,requirementCount:2,requirementsByFamily:{mcl:{mappedLines:2,pendingLines:1,requirements:2,applicabilityStatus:"applicable",completenessConfirmed:false}},existingContent:{processRequirements:0,components:0,purchaseSpecifications:0,requiresAppendConfirmation:false}},
  {id:"part-2",partNumber:"SM-P-000002",name:"Bracket",snapshotKey:"snap-2",status:"ready",blockers:[],mappedLineCount:1,requirementCount:1,requirementsByFamily:{cnc:{mappedLines:1,pendingLines:0,requirements:1,applicabilityStatus:"applicable",completenessConfirmed:true}},existingContent:{processRequirements:1,components:2,purchaseSpecifications:0,requiresAppendConfirmation:true}},
  {id:"part-3",partNumber:"SM-P-000003",name:"Guard",snapshotKey:"snap-3",status:"blocked",blockers:["CNC row 56 was removed and needs review."],mappedLineCount:1,requirementCount:1,requirementsByFamily:{},existingContent:{processRequirements:0,components:0,purchaseSpecifications:0,requiresAppendConfirmation:false}},
],pendingRows:[{groupKey:"mcl-mixed",sheet:"5. Material Cut List Price",family:"mcl",row:17,description:"Pending plate",lineKey:"mcl-line-17",lineFingerprint:"line-fp-17",evidenceFingerprint:"group-fp",status:"needs_review",reason:"Choose a Part for this row.",affectedPartIds:[]}],counts:{mappedParts:3,readyParts:2,alreadyEstablishedParts:0,blockedParts:1,pendingRows:1},canConfirm:true,workbookComplete:false});

beforeEach(() => {
  Object.values(mocks).forEach(mock => mock.mockReset());
  localStorage.clear();
  mocks.partMappings.mockResolvedValue(mapping);
  mocks.partBaselineWorkbookPreview.mockResolvedValue(preview());
  mocks.confirmWorkbookPartBaselines.mockResolvedValue({status:"partial",totalSuccess:false,results:[{partId:"part-1",partNumber:"SM-P-000001",status:"established",requirementCount:2},{partId:"part-2",partNumber:"SM-P-000002",status:"established",requirementCount:1}],pendingRows:preview().pendingRows,workbookComplete:false});
  mocks.partBaselineReview.mockImplementation((partId:string) => Promise.resolve(review(partId === "part-2" ? "established" : "not_established")));
});
afterEach(() => {cleanup();});

it("searches mapped Parts, loads each selected baseline status and advances through the workbook Parts", async () => {
  render(<PartBaselineWorkspace path="pilot.ods" initialPartId="part-1" returnLabel="Return to Part Mapping" onBack={() => {}} onUseDifferentPart={() => {}} onReviewPendingRow={() => {}}/>);
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
  expect(screen.getByText(/Unsaved mapping drafts remain available/)).toBeTruthy();
});

it("shows a workbook preview, requires existing-content consent, and confirms every ready Part together", async () => {
  const onReviewPendingRow = vi.fn();
  render(<PartBaselineWorkspace path="pilot.ods" initialPartId="part-1" returnLabel="Return to Part Mapping" onBack={() => {}} onUseDifferentPart={() => {}} onReviewPendingRow={onReviewPendingRow}/>);
  await screen.findByRole("heading",{name:"Confirm manufacturing baselines"});
  expect(screen.getByText("2",{selector:".baseline-bulk-counts strong"})).toBeTruthy();
  expect(screen.getByText(/Pending ODS rows · 1/)).toBeTruthy();
  expect(screen.getByText(/CNC row 56 was removed and needs review\./)).toBeTruthy();
  const confirm = screen.getByRole("button",{name:"Confirm 2 ready baselines"}) as HTMLButtonElement;
  expect(confirm.disabled).toBe(true);
  fireEvent.click(screen.getByLabelText(/Append these workbook rows while preserving existing Rev A content/));
  expect(confirm.disabled).toBe(false);
  fireEvent.click(confirm);
  await screen.findByText(/2 Part baseline\(s\) established/);
  expect(mocks.confirmWorkbookPartBaselines).toHaveBeenCalledTimes(1);
  const [payload,key] = mocks.confirmWorkbookPartBaselines.mock.calls[0];
  expect(key).toMatch(/^[0-9a-f-]{36}$/i);
  expect(payload).toMatchObject({path:"pilot.ods",expectedContextKey:"context-4",previewKey:"preview-4",parts:[
    {partId:"part-1",snapshotKey:"snap-1",appendExisting:false},
    {partId:"part-2",snapshotKey:"snap-2",appendExisting:true},
  ]});
  expect(screen.getByText(/workbook is not fully reviewed/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button",{name:"Review in Part Mapping"}));
  expect(onReviewPendingRow).toHaveBeenCalledWith(expect.objectContaining({row:17,lineKey:"mcl-line-17"}),expect.objectContaining({sourceHash:"source-hash",mappingVersion:4}));
});

it("keeps the same bulk idempotency key after a lost response and prevents duplicate submit", async () => {
  let resolveRequest: ((value: unknown) => void) | undefined;
  mocks.confirmWorkbookPartBaselines.mockImplementationOnce(() => new Promise(resolve => {resolveRequest = resolve;}));
  render(<PartBaselineWorkspace path="pilot.ods" initialPartId="part-1" returnLabel="Return to Part Mapping" onBack={() => {}} onUseDifferentPart={() => {}} onReviewPendingRow={() => {}}/>);
  await screen.findByRole("heading",{name:"Confirm manufacturing baselines"});
  fireEvent.click(screen.getByLabelText(/Append these workbook rows while preserving existing Rev A content/));
  const confirm = screen.getByRole("button",{name:"Confirm 2 ready baselines"});
  fireEvent.click(confirm);
  fireEvent.click(confirm);
  expect(mocks.confirmWorkbookPartBaselines).toHaveBeenCalledTimes(1);
  const firstCall = mocks.confirmWorkbookPartBaselines.mock.calls[0];
  resolveRequest?.({status:"partial",totalSuccess:false,results:[{partId:"part-1",status:"established"},{partId:"part-2",status:"failed",retryable:true}],pendingRows:preview().pendingRows,workbookComplete:false});
  await screen.findByRole("button",{name:"Retry same confirmation"});
  fireEvent.click(screen.getByRole("button",{name:"Retry same confirmation"}));
  await waitFor(() => expect(mocks.confirmWorkbookPartBaselines).toHaveBeenCalledTimes(2));
  expect(mocks.confirmWorkbookPartBaselines.mock.calls[1]).toEqual(firstCall);
});

it("drops a stale bulk attempt and refreshes evidence before allowing another confirmation", async () => {
  const stale = Object.assign(new Error("The workbook or saved mapping changed since this preview."), {retryDisposition:"refresh_required"});
  mocks.confirmWorkbookPartBaselines.mockRejectedValueOnce(stale);
  render(<PartBaselineWorkspace path="pilot.ods" initialPartId="part-1" returnLabel="Return to Part Mapping" onBack={() => {}} onUseDifferentPart={() => {}} onReviewPendingRow={() => {}}/>);
  await screen.findByRole("heading",{name:"Confirm manufacturing baselines"});
  fireEvent.click(screen.getByLabelText(/Append these workbook rows while preserving existing Rev A content/));
  fireEvent.click(screen.getByRole("button",{name:"Confirm 2 ready baselines"}));
  const alert = await screen.findByRole("alert");
  expect(alert.textContent).toMatch(/preview is being refreshed/i);
  await waitFor(() => expect(mocks.partBaselineWorkbookPreview).toHaveBeenCalledTimes(2));
  expect(screen.queryByRole("button",{name:"Retry same confirmation"})).toBeNull();
  expect(screen.getByRole("button",{name:"Confirm 2 ready baselines"})).toBeTruthy();
  expect(localStorage.getItem("workbook-baseline-confirm-attempt:pilot.ods")).toBeNull();
});

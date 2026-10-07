import {cleanup, fireEvent, render, screen} from "@testing-library/react";
import {afterEach, beforeEach, expect, it, vi} from "vitest";
import {PartMappingView} from "../src/PartMappingView";
const mocks = vi.hoisted(() => ({partMappings: vi.fn(), createPart: vi.fn(), savePartMappings: vi.fn()}));
vi.mock("../src/api", () => ({api: mocks}));
const part = {id:"1", name:"Drive Shaft", selectable:true, duplicateName:false};
const detail = {sourceHash:"hash1", associationKey:"assoc1", associationVersion:2, version:0, schemaAvailable:true,
  parts:[part], groups:[{key:"shaft", description:"Shaft", blankDescription:false, rows:[{sheet:"Tool Shop Items",row:10}], part:null, reviewed:false}], unresolvedGroups:1, history:[]};
beforeEach(() => { Object.values(mocks).forEach(mock => mock.mockReset()); vi.stubGlobal("crypto", {randomUUID: () => "request-key"}); mocks.partMappings.mockResolvedValue(detail); });
afterEach(() => {cleanup(); vi.unstubAllGlobals();});

it("pins assignments and retries the identical request after response loss", async () => {
  mocks.savePartMappings.mockRejectedValueOnce(new Error("response lost")).mockResolvedValue({savedRows:1,version:1});
  render(<PartMappingView path="pilot.ods"/>);
  await screen.findByText("Shaft");
  fireEvent.change(screen.getByLabelText("Part for Shaft"), {target:{value:"1"}});
  fireEvent.change(screen.getByLabelText("Reason for these assignments"), {target:{value:"Reviewed shaft"}});
  fireEvent.click(screen.getByRole("button",{name:"Save 1 selected groups"}));
  await screen.findByRole("alert");
  expect((screen.getByLabelText("Part for Shaft") as HTMLSelectElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole("button",{name:"Retry saving assignments"}));
  await screen.findByText("Saved 1 source-row assignments in review version 1.");
  expect(mocks.savePartMappings.mock.calls[1]).toEqual(mocks.savePartMappings.mock.calls[0]);
  expect(mocks.savePartMappings.mock.calls[0][0]).toMatchObject({expectedHash:"hash1",expectedVersion:0,expectedAssociationKey:"assoc1",expectedAssociationVersion:2,decisions:{shaft:"1"}});
});

it("delegates Part creation to the Parts register and excludes ambiguous choices", async () => {
  mocks.partMappings.mockResolvedValue({...detail, parts:[part,{...part,id:"2",name:"Duplicate",duplicateName:true}]});
  const onOpenParts = vi.fn();
  render(<PartMappingView path="pilot.ods" onOpenParts={onOpenParts}/>);
  await screen.findByText("Shaft");
  expect(screen.queryByRole("option",{name:"Duplicate"})).toBeNull();
  expect(screen.queryByLabelText("New Part name")).toBeNull();
  expect(screen.queryByRole("button",{name:"Create Part"})).toBeNull();
  fireEvent.change(screen.getByLabelText("Part for Shaft"),{target:{value:"1"}});
  fireEvent.change(screen.getByLabelText("Reason for these assignments"),{target:{value:"Reviewed"}});
  fireEvent.click(screen.getByRole("button",{name:"Open in Parts"}));
  expect(onOpenParts).toHaveBeenCalledWith("shaft", "1");
  expect(mocks.savePartMappings).not.toHaveBeenCalled();
  expect(JSON.parse(sessionStorage.getItem("part-mapping:pilot.ods") || "{}")).toMatchObject({decisions:{shaft:"1"},reason:"Reviewed"});
});

it("ignores a late response when the selected workbook changes", async () => {
  let resolveOld: (value: unknown) => void = () => {};
  mocks.partMappings.mockImplementationOnce(() => new Promise(resolve => {resolveOld=resolve;})).mockResolvedValue({...detail,groups:[],unresolvedGroups:0});
  const view=render(<PartMappingView path="old.ods"/>);
  view.rerender(<PartMappingView path="new.ods"/>);
  await screen.findByText("0 of 0 description groups need review");
  resolveOld(detail);
  await vi.waitFor(() => expect(screen.queryByText("Shaft")).toBeNull());
});

it("clears saved mapping context if source evidence changes while in the Parts register", async () => {
  sessionStorage.setItem("part-mapping:pilot.ods", JSON.stringify({path:"pilot.ods", sourceHash:"hash1", version:0,
    associationKey:"assoc1", associationVersion:2, decisions:{shaft:"1"}, reason:"Reviewed", query:"shaft", mappingAttempt:null}));
  mocks.partMappings.mockResolvedValue({...detail,sourceHash:"hash2"});
  render(<PartMappingView path="pilot.ods"/>);
  await screen.findByText("Shaft");
  expect(JSON.parse(sessionStorage.getItem("part-mapping:pilot.ods") || "{}")).toMatchObject({sourceHash:"hash2",decisions:{},reason:""});
  expect((screen.getByLabelText("Part for Shaft") as HTMLSelectElement).value).toBe("");
  expect(mocks.savePartMappings).not.toHaveBeenCalled();
});

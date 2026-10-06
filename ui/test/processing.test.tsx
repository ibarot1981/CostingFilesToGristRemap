import {cleanup,fireEvent,render,screen} from "@testing-library/react";
import {afterEach,beforeEach,expect,it,vi} from "vitest";
import {ProcessingPanel} from "../src/ProcessingPanel";

const mocks=vi.hoisted(()=>({processingState:vi.fn(),changeProcessingState:vi.fn()}));
vi.mock("../src/api",()=>({api:mocks}));
afterEach(()=>{cleanup();vi.unstubAllGlobals();});
beforeEach(()=>{
  Object.values(mocks).forEach(m=>m.mockReset());
  vi.stubGlobal("crypto",{randomUUID:()=>"processing-request"});
  mocks.processingState.mockResolvedValue({state:"associated",effectiveState:"associated",version:0,sourceHash:"revision-a",schemaAvailable:true,associationKey:"assoc-a",associationVersion:2,history:[]});
});

it("requires a reason, pins reviewed evidence and retains the request key after response loss",async()=>{
  mocks.changeProcessingState.mockRejectedValueOnce(new Error("response lost")).mockResolvedValue({idempotent:true});
  render(<ProcessingPanel path="nested/pilot.ods"/>);
  await screen.findByText("State: Associated · version 0");
  fireEvent.change(screen.getByLabelText("Next processing state"),{target:{value:"associated"}});
  const button=screen.getByRole("button",{name:"Record processing state"}) as HTMLButtonElement;
  expect(button.disabled).toBe(true);
  fireEvent.change(screen.getByLabelText("Processing reason"),{target:{value:"Reviewed source association"}});
  fireEvent.click(button);
  await screen.findByText("Error: response lost");
  fireEvent.click(button);
  await vi.waitFor(()=>expect(mocks.changeProcessingState).toHaveBeenCalledTimes(2));
  const [payload,key]=mocks.changeProcessingState.mock.calls[0];
  expect(payload).toMatchObject({expectedHash:"revision-a",expectedVersion:0,expectedAssociationKey:"assoc-a",expectedAssociationVersion:2});
  expect(mocks.changeProcessingState.mock.calls[1]).toEqual([payload,key]);
  expect(screen.queryByRole("button",{name:"Mark processed"})).toBeNull();
});

it("shows persisted history and pending source changes while keeping unavailable completion actions hidden",async()=>{
  mocks.processingState.mockResolvedValue({state:"processed",effectiveState:"changes_pending",version:7,sourceHash:"revision-b",sourceChanged:true,schemaAvailable:true,history:[{key:"old",from_state:"ready_to_store",state:"processed",actor:"Irshad",reason:"Approved structure",occurred_at:"2026-10-05",source_hash:"revision-a"}]});
  render(<ProcessingPanel path="pilot.ods"/>);
  await screen.findByText("State: Changes pending · version 7");
  expect(screen.getByText("The source or association changed since the last recorded review.")).toBeTruthy();
  expect(screen.getByText("Processing history (1)")).toBeTruthy();
  expect(screen.getByText("Approved structure")).toBeTruthy();
  expect(screen.queryByRole("option",{name:"Processed"})).toBeNull();
});

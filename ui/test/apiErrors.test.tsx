import {afterEach, expect, it, vi} from "vitest";
import {api, ApiError} from "../src/api";

afterEach(() => vi.unstubAllGlobals());

it.each([
  ["BASELINE_CONFIRMATION_REQUIRED", "safe_to_edit"],
  ["PART_COMPARISON_STALE", "refresh_required"],
  ["PART_NAME_PREVIEW_STALE", "safe_to_edit"],
])("preserves the server retry disposition for %s", async (code, retryDisposition) => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
    ok:false,
    status:422,
    json:async () => ({detail:{code, message:"Review current evidence", retryDisposition}}),
  }));

  await expect(api.partBaselineReview("part-1", "pilot.ods")).rejects.toMatchObject<ApiError>({
    name:"ApiError", status:422, code, retryDisposition,
  });
});

it.each(["unknown-disposition", null, 17])("treats an unrecognized retry disposition as an uncertain request (%s)", async retryDisposition => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
    ok:false,
    status:422,
    json:async () => ({detail:{code:"UNCLASSIFIED_422", message:"Request failed", retryDisposition}}),
  }));

  await expect(api.createManagedPart({description:"Shaft"}, "stable-key")).rejects.toMatchObject<ApiError>({
    name:"ApiError", status:422, code:"UNCLASSIFIED_422", retryDisposition:"retry_same_request",
  });
});

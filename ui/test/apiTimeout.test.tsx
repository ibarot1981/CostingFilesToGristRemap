import {afterEach, expect, it, vi} from "vitest";
import {api} from "../src/api";

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

it("turns a stalled baseline write into an uncertain exact-retry outcome", async () => {
  vi.useFakeTimers();
  const fetchMock = vi.fn((_path: string, init?: RequestInit) => new Promise<Response>((_resolve, reject) => {
    init?.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")), {once: true});
  }));
  vi.stubGlobal("fetch", fetchMock);

  const pending = api.comparePartBaseline("part-1", {expectedMappingVersion: 7}, "comparison-request-1");
  const assertion = expect(pending).rejects.toThrow("outcome may be uncertain. Retry the saved request.");
  await vi.advanceTimersByTimeAsync(30_000);

  await assertion;
  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(fetchMock.mock.calls[0][1]?.headers).toMatchObject({"Idempotency-Key": "comparison-request-1"});
  expect(fetchMock.mock.calls[0][1]?.signal?.aborted).toBe(true);
});

it("explains an incomplete baseline response as an uncertain retry", async () => {
  const fetchMock = vi.fn().mockResolvedValue(new Response("", {status: 200}));
  vi.stubGlobal("fetch", fetchMock);

  await expect(api.comparePartBaseline("part-1", {expectedMappingVersion: 7}, "comparison-request-2"))
    .rejects.toThrow("server response was incomplete; the outcome may be uncertain. Retry the saved request.");
});

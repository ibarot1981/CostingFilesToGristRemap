import { afterEach, expect, it, vi } from "vitest";
import { api } from "../src/api";

afterEach(() => vi.unstubAllGlobals());

it("filters returned assemblies only from the Live Cost configuration catalog", async () => {
  const part = { id: "part-1", partNumber: "SM-P-000001", identityKind: "part" };
  const assembly = { id: "assembly-1", partNumber: "SM-A-000001", identityKind: "assembly" };
  const legacy = { id: "legacy:old-part", name: "Legacy Part" };
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({ items: [part, assembly], legacyItems: [legacy], storage: "Grist Safari Manufacturing" }),
  });
  vi.stubGlobal("fetch", fetchMock);

  const result = await api.partsForConfiguration("shaft");

  expect(fetchMock).toHaveBeenCalledWith("/api/parts?search=shaft&include_retired=false", undefined);
  expect(result.items).toEqual([part]);
  expect(result.legacyItems).toEqual([legacy]);
});

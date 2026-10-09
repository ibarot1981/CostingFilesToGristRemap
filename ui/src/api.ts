import type { AssociationState, AssociationValidation, CatalogSummary, CostingReview, DirectoryProductMapping, ExplorerItem, MappedFile, ModelCode, Preview, Product, ProductModel, ReconciliationIssue } from "./types";

async function get<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(path, signal ? { signal } : undefined);
  const payload = await response.json();
  if (!response.ok) throw apiError(payload, response.status);
  return payload;
}

async function send<T>(path: string, body: unknown, headers: Record<string, string> = {}, method = "POST", timeoutMs?: number): Promise<T> {
  const controller = timeoutMs ? new AbortController() : null;
  let timedOut = false;
  const timer = timeoutMs ? setTimeout(() => {
    timedOut = true;
    controller?.abort();
  }, timeoutMs) : null;
  try {
    const response = await fetch(path, { method, headers: { "Content-Type": "application/json", ...headers }, body: JSON.stringify(body), ...(controller ? { signal: controller.signal } : {}) });
    const payload = await response.json();
    if (!response.ok) throw apiError(payload, response.status);
    return payload;
  } catch (cause) {
    if (timedOut) throw new Error(`Request timed out after ${Math.round((timeoutMs || 0) / 1000)} seconds; the outcome may be uncertain. Retry the saved request.`);
    if (timeoutMs && cause instanceof SyntaxError) throw new Error("The server response was incomplete; the outcome may be uncertain. Retry the saved request.");
    throw cause;
  } finally {
    if (timer !== null) clearTimeout(timer);
  }
}

const PART_BASELINE_WRITE_TIMEOUT_MS = 30_000;

export class ApiError extends Error {
  constructor(message: string, readonly status: number, readonly code = "", readonly retryDisposition = "retry_same_request", readonly detail: unknown = null) {
    super(message);
    this.name = "ApiError";
  }
}

function apiError(payload: any, status: number): ApiError {
  const detail = payload?.detail;
  const code = typeof detail?.code === "string" ? `${detail.code}: ` : "";
  const message = typeof detail?.message === "string" ? detail.message : typeof detail === "string" ? detail : `Request failed (${status})`;
  return new ApiError(`${code}${message}`, status, typeof detail?.code === "string" ? detail.code : "",
    typeof detail?.retryDisposition === "string" ? detail.retryDisposition : "retry_same_request", detail);
}

export const api = {
  parts: (search = "", scope = "", targetId = "") => get<any>(`/api/parts?${new URLSearchParams({ search, scope, target_id: targetId })}`),
  searchParts: (query = "", offset = 0, limit = 30, path = "", signal?: AbortSignal) => get<any>(`/api/parts/search?${new URLSearchParams({ query, offset: String(offset), limit: String(limit), path })}`, signal),
  partScopeTargets: () => get<any>("/api/parts/scope-targets"),
  partNamePreview: (scope: string, targetId: string, description: string, variant: string, excludeId = "") => get<any>(`/api/parts/preview?${new URLSearchParams({ scope, target_id: targetId, description, variant, exclude_id: excludeId })}`),
  createManagedPart: (payload: unknown, key: string) => send<any>("/api/parts", payload, { "Idempotency-Key": key }),
  maintainPartShortcode: (payload: unknown, key: string) => send<any>("/api/parts/shortcodes", payload, { "Idempotency-Key": key }),
  partDetails: (id: string) => get<any>(`/api/parts/${encodeURIComponent(id)}`),
  savePartIntendedSharing: (id: string, payload: unknown, key: string) => send<any>(`/api/parts/${encodeURIComponent(id)}/intended-sharing`, payload, { "Idempotency-Key": key }, "PUT"),
  partMetadataPreview: (id: string, scope: string, targetId: string, description: string, variant: string) => get<any>(`/api/parts/${encodeURIComponent(id)}/metadata-preview?${new URLSearchParams({ scope, target_id: targetId, description, variant })}`),
  updatePartMetadata: (id: string, payload: unknown, key: string) => send<any>(`/api/parts/${encodeURIComponent(id)}/metadata`, payload, { "Idempotency-Key": key }),
  retirePart: (id: string, payload: unknown, key: string) => send<any>(`/api/parts/${encodeURIComponent(id)}/retire`, payload, { "Idempotency-Key": key }),
  addPartComponent: (id: string, payload: unknown, key: string) => send<any>(`/api/parts/${encodeURIComponent(id)}/components`, payload, { "Idempotency-Key": key }),
  finalizePartRevision: (id: string, payload: unknown, key: string) => send<any>(`/api/parts/${encodeURIComponent(id)}/finalize-revision`, payload, { "Idempotency-Key": key }),
  linkPartProcessLine: (id: string, payload: unknown, key: string) => send<any>(`/api/parts/${encodeURIComponent(id)}/process-lines`, payload, { "Idempotency-Key": key }),
  partLineCandidates: (search = "") => get<any>(`/api/part-line-candidates?${new URLSearchParams({ search })}`),
  addPartDrawing: (id: string, payload: unknown, key: string) => send<any>(`/api/parts/${encodeURIComponent(id)}/drawings`, payload, { "Idempotency-Key": key }),
  createPartVendor: (id: string, payload: unknown, key: string) => send<any>(`/api/parts/${encodeURIComponent(id)}/vendors`, payload, { "Idempotency-Key": key }),
  createPartPurchaseSpecification: (id: string, payload: unknown, key: string) => send<any>(`/api/parts/${encodeURIComponent(id)}/purchase-specifications`, payload, { "Idempotency-Key": key }),
  createPartVendorMapping: (id: string, payload: unknown, key: string) => send<any>(`/api/parts/${encodeURIComponent(id)}/vendor-mappings`, payload, { "Idempotency-Key": key }),
  recordPartPurchase: (id: string, payload: unknown, key: string) => send<any>(`/api/parts/${encodeURIComponent(id)}/purchases`, payload, { "Idempotency-Key": key }),
  addPartUnitConversion: (id: string, specId: number, payload: unknown, key: string) => send<any>(`/api/parts/${encodeURIComponent(id)}/purchase-specifications/${specId}/unit-conversions`, payload, { "Idempotency-Key": key }),
  addPartCurrencyConversion: (id: string, specId: number, payload: unknown, key: string) => send<any>(`/api/parts/${encodeURIComponent(id)}/purchase-specifications/${specId}/currency-conversions`, payload, { "Idempotency-Key": key }),
  partPurchaseRate: (id: string, asOf = "") => get<any>(`/api/parts/${encodeURIComponent(id)}/purchase-rate${asOf ? `?as_of=${encodeURIComponent(asOf)}` : ""}`),
  partsForConfiguration: (search = "") => get<any>(`/api/parts?${new URLSearchParams({ search, include_retired: "false" })}`),
  costingConfiguration: (codeId: string) => get<any>(`/api/model-codes/${encodeURIComponent(codeId)}/costing-configuration`),
  saveCostingConfiguration: (codeId: string, payload: unknown, key: string) => send<any>(`/api/model-codes/${encodeURIComponent(codeId)}/costing-configuration`, payload, { "Idempotency-Key": key }, "PUT"),
  liveCost: (codeId: string) => get<any>(`/api/model-codes/${encodeURIComponent(codeId)}/live-cost`),
  costSnapshots: (codeId: string, offset = 0, limit = 25) => get<any>(`/api/model-codes/${encodeURIComponent(codeId)}/cost-snapshots?offset=${offset}&limit=${limit}`),
  saveCostSnapshot: (codeId: string, payload: unknown, key: string) => send<any>(`/api/model-codes/${encodeURIComponent(codeId)}/cost-snapshots`, payload, { "Idempotency-Key": key }),
  costSnapshot: (key: string, offset = 0, limit = 100) => get<any>(`/api/cost-snapshots/${encodeURIComponent(key)}?offset=${offset}&limit=${limit}`),
  compareCosts: (codeId: string, payload: unknown) => send<any>(`/api/model-codes/${encodeURIComponent(codeId)}/cost-comparisons`, payload),
  costPolicy: (codeId: string) => get<any>(`/api/model-codes/${encodeURIComponent(codeId)}/cost-policy`),
  saveCostPolicy: (scope: string, payload: unknown, key: string) => send<any>(`/api/cost-snapshot-policies/${encodeURIComponent(scope)}`, payload, { "Idempotency-Key": key }, "PUT"),
  recordProcessRate: (lineMasterId: number, payload: unknown, key: string) => send<any>(`/api/line-masters/${lineMasterId}/process-rates`, payload, { "Idempotency-Key": key }),
  savePartPurchaseRateEvidence: (id: string, payload: unknown) => send<any>(`/api/parts/${encodeURIComponent(id)}/purchase-rate-evidence`, payload),
  partMappings: (path: string) => get<any>(`/api/parts/mappings?path=${encodeURIComponent(path)}`),
  savePartMappings: (payload: unknown, key: string) => send<any>("/api/parts/mappings", payload, {"Idempotency-Key": key}),
  partBaselineReview: (id: string, path: string) => get<any>(`/api/parts/${encodeURIComponent(id)}/manufacturing-baseline/review?path=${encodeURIComponent(path)}`),
  establishPartBaseline: (id: string, payload: unknown, key: string) => send<any>(`/api/parts/${encodeURIComponent(id)}/manufacturing-baseline/establish`, payload, {"Idempotency-Key": key}, "POST", PART_BASELINE_WRITE_TIMEOUT_MS),
  comparePartBaseline: (id: string, payload: unknown, key: string) => send<any>(`/api/parts/${encodeURIComponent(id)}/manufacturing-baseline/compare`, payload, {"Idempotency-Key": key}, "POST", PART_BASELINE_WRITE_TIMEOUT_MS),
  partManufacturingComparison: (key: string) => get<any>(`/api/parts/manufacturing-comparisons/${encodeURIComponent(key)}`),
  decidePartManufacturingComparison: (key: string, payload: unknown, requestKey: string) => send<any>(`/api/parts/manufacturing-comparisons/${encodeURIComponent(key)}/decisions`, payload, {"Idempotency-Key": requestKey}, "POST", PART_BASELINE_WRITE_TIMEOUT_MS),
  processingState: (path: string) => get<any>(`/api/processing/state?path=${encodeURIComponent(path)}`),
  changeProcessingState: (payload: unknown, key: string) => send<any>("/api/processing/state", payload, {"Idempotency-Key":key}),
  fileAssociation: (path: string) => get<import("./types").SavedAssociation>(`/api/explorer/association?path=${encodeURIComponent(path)}`),
  normalized: (path: string, filters: Record<string, string> = {}) => get<any>(`/api/catalog/normalized?${new URLSearchParams({ path, ...filters })}`),
  codeRecords: (codeId: string, filters: Record<string, string> = {}) => get<any>(`/api/model-codes/${encodeURIComponent(codeId)}/records?${new URLSearchParams(filters)}`),
  summary: () => get<CatalogSummary>("/api/catalog/summary"),
  products: () => get<Product[]>("/api/products"),
  models: (productId?: string) => get<ProductModel[]>(`/api/products/models${productId ? `?product_id=${encodeURIComponent(productId)}` : ""}`),
  codes: (modelId: string) => get<{ active: ModelCode[]; available: ModelCode[]; legacy: ModelCode[]; conflicts: ModelCode[] }>(`/api/models/${encodeURIComponent(modelId)}/codes`),
  tree: (path = "") => get<{ root: string; path: string; items: ExplorerItem[] }>(`/api/explorer/tree?path=${encodeURIComponent(path)}`),
  files: (query = "") => get<{ total: number; items: ExplorerItem[]; source?: "filesystem" | "cached-report" }>(`/api/catalog/files?limit=1000&extension=.ods&query=${encodeURIComponent(query)}`),
  inspect: (path: string) => get<ExplorerItem>(`/api/explorer/inspect?path=${encodeURIComponent(path)}`),
  preview: (path: string, sheet = "") => get<Preview>(`/api/catalog/preview?path=${encodeURIComponent(path)}&sheet=${encodeURIComponent(sheet)}&row_count=200&column_count=30`),
  refreshPreview: (path: string, sheet = "") => send<Preview>(`/api/catalog/preview/refresh?path=${encodeURIComponent(path)}&sheet=${encodeURIComponent(sheet)}&row_count=200&column_count=30`, {}),
  costingReview: (path: string) => send<CostingReview>("/api/catalog/costing-review", { path }),
  acceptCostingReview: (payload: unknown, idempotencyKey: string) => send<Record<string, unknown>>("/api/catalog/costing-review/accept", payload, { "Idempotency-Key": idempotencyKey }),
  validateAssociation: (payload: unknown) => send<AssociationValidation>("/api/associations/validate", payload),
  saveAssociation: (payload: unknown, idempotencyKey: string) => send<Record<string, unknown>>("/api/associations", payload, { "Idempotency-Key": idempotencyKey }),
  mappedFiles: () => get<{ total: number; items: MappedFile[] }>("/api/mapped-files"),
  associations: () => get<AssociationState>("/api/associations"),
  reconciliationIssues: (filters: Record<string, string> = {}) => {
    const query = new URLSearchParams(Object.entries(filters).filter(([, value]) => Boolean(value)));
    return get<{ total: number; items: ReconciliationIssue[] }>(`/api/reconciliation/issues?${query.toString()}`);
  },
  reconciliationIssue: (id: string) => get<{ issue: ReconciliationIssue; observations: Record<string, unknown>[]; associationHistory: Record<string, unknown>[]; auditTrail: Record<string, unknown>[] }>(`/api/reconciliation/issues/${encodeURIComponent(id)}`),
  issueAction: (id: string, action: string, payload: unknown, key: string) => send<{ issue: ReconciliationIssue }>(`/api/reconciliation/issues/${encodeURIComponent(id)}/actions/${encodeURIComponent(action)}`, payload, { "Idempotency-Key": key }),
  reconciliationScan: (dryRun = true, limit = 50) => send<Record<string, unknown>>("/api/reconciliation/scan", { dryRun, limit }),
  sourceRevisionPreview: (id: string) => send<Record<string, unknown>>(`/api/reconciliation/issues/${encodeURIComponent(id)}/source-revision/preview`, {}),
  sourceRevisionApply: (id: string, payload: unknown, key: string) => send<Record<string, unknown>>(`/api/reconciliation/issues/${encodeURIComponent(id)}/source-revision/apply`, payload, { "Idempotency-Key": key }),
  identityCleanupPreview: (modelIds: string[] = []) => send<{ items: Record<string, unknown>[]; canApplyCount: number }>("/api/reconciliation/identity-cleanup/preview", { modelIds }),
  identityCleanupApply: (payload: unknown, key: string) => send<Record<string, unknown>>("/api/reconciliation/identity-cleanup/apply", payload, { "Idempotency-Key": key }),
  directoryMappings: (status = "") => get<{ total: number; items: DirectoryProductMapping[] }>(`/api/directory-mappings${status ? `?status=${encodeURIComponent(status)}` : ""}`),
  proposeDirectoryMapping: (payload: unknown, key: string) => send<{ mapping: DirectoryProductMapping }>("/api/directory-mappings", payload, { "Idempotency-Key": key }),
  directoryMappingAction: (id: string, action: string, payload: unknown, key: string) => send<{ mapping: DirectoryProductMapping }>(`/api/directory-mappings/${encodeURIComponent(id)}/${action}`, payload, { "Idempotency-Key": key }),
};

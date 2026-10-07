import type { AssociationState, AssociationValidation, CatalogSummary, CostingReview, DirectoryProductMapping, ExplorerItem, MappedFile, ModelCode, Preview, Product, ProductModel, ReconciliationIssue } from "./types";

async function get<T>(path: string): Promise<T> {
  const response = await fetch(path);
  const payload = await response.json();
  if (!response.ok) throw apiError(payload, response.status);
  return payload;
}

async function send<T>(path: string, body: unknown, headers: Record<string, string> = {}): Promise<T> {
  const response = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json", ...headers }, body: JSON.stringify(body) });
  const payload = await response.json();
  if (!response.ok) throw apiError(payload, response.status);
  return payload;
}

function apiError(payload: any, status: number): Error {
  const detail = payload?.detail;
  const code = typeof detail?.code === "string" ? `${detail.code}: ` : "";
  const message = typeof detail?.message === "string" ? detail.message : typeof detail === "string" ? detail : `Request failed (${status})`;
  return new Error(`${code}${message}`);
}

export const api = {
  parts: (search = "", scope = "", targetId = "") => get<any>(`/api/parts?${new URLSearchParams({ search, scope, target_id: targetId })}`),
  partScopeTargets: () => get<any>("/api/parts/scope-targets"),
  partNamePreview: (scope: string, targetId: string, description: string, variant: string, excludeId = "") => get<any>(`/api/parts/preview?${new URLSearchParams({ scope, target_id: targetId, description, variant, exclude_id: excludeId })}`),
  createManagedPart: (payload: unknown, key: string) => send<any>("/api/parts", payload, { "Idempotency-Key": key }),
  maintainPartShortcode: (payload: unknown, key: string) => send<any>("/api/parts/shortcodes", payload, { "Idempotency-Key": key }),
  partDetails: (id: string) => get<any>(`/api/parts/${encodeURIComponent(id)}`),
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
  savePartPurchaseRateEvidence: (id: string, payload: unknown) => send<any>(`/api/parts/${encodeURIComponent(id)}/purchase-rate-evidence`, payload),
  partMappings: (path: string) => get<any>(`/api/parts/mappings?path=${encodeURIComponent(path)}`),
  savePartMappings: (payload: unknown, key: string) => send<any>("/api/parts/mappings", payload, {"Idempotency-Key": key}),
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

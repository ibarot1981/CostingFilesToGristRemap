import type { AssociationState, AssociationValidation, CatalogSummary, ExplorerItem, MappedFile, ModelCode, Preview, Product, ProductModel } from "./types";

async function get<T>(path: string): Promise<T> {
  const response = await fetch(path);
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.detail?.message || payload.detail || `Request failed (${response.status})`);
  return payload;
}

async function send<T>(path: string, body: unknown, headers: Record<string, string> = {}): Promise<T> {
  const response = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json", ...headers }, body: JSON.stringify(body) });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.detail?.message || payload.detail || `Request failed (${response.status})`);
  return payload;
}

export const api = {
  summary: () => get<CatalogSummary>("/api/catalog/summary"),
  products: () => get<Product[]>("/api/products"),
  models: (productId?: string) => get<ProductModel[]>(`/api/products/models${productId ? `?product_id=${encodeURIComponent(productId)}` : ""}`),
  codes: (modelId: string) => get<{ active: ModelCode[]; available: ModelCode[]; legacy: ModelCode[]; conflicts: ModelCode[] }>(`/api/models/${encodeURIComponent(modelId)}/codes`),
  tree: (path = "") => get<{ root: string; path: string; items: ExplorerItem[] }>(`/api/explorer/tree?path=${encodeURIComponent(path)}`),
  files: (query = "") => get<{ total: number; items: ExplorerItem[]; source?: "filesystem" | "cached-report" }>(`/api/catalog/files?limit=1000&extension=.ods&query=${encodeURIComponent(query)}`),
  inspect: (path: string) => get<ExplorerItem>(`/api/explorer/inspect?path=${encodeURIComponent(path)}`),
  preview: (path: string, sheet = "") => get<Preview>(`/api/catalog/preview?path=${encodeURIComponent(path)}&sheet=${encodeURIComponent(sheet)}&row_count=50&column_count=30`),
  validateAssociation: (payload: unknown) => send<AssociationValidation>("/api/associations/validate", payload),
  saveAssociation: (payload: unknown, idempotencyKey: string) => send<Record<string, unknown>>("/api/associations", payload, { "Idempotency-Key": idempotencyKey }),
  mappedFiles: () => get<{ total: number; items: MappedFile[] }>("/api/mapped-files"),
  associations: () => get<AssociationState>("/api/associations"),
};

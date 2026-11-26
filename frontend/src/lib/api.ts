import type {
  CardScanResult,
  Client,
  ClientInput,
  Dashboard,
  DocumentDetail,
  DocumentSummary,
  ExtractedRow,
  Extraction,
  ItemData,
  OcrToken,
  Options,
  PriceBookItem,
  StagedItem,
  SystemInfo,
  UserSettings,
} from "./types";

export const API_BASE = (process.env.NEXT_PUBLIC_API_URL || "").replace(/\/$/, "");

export class ApiError extends Error {
  status: number;
  details: unknown;
  constructor(status: number, message: string, details?: unknown) {
    super(message);
    this.status = status;
    this.details = details;
  }
}

function messageFrom(detail: unknown, fallback: string): string {
  if (!detail) return fallback;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((d) => (typeof d === "object" && d && "msg" in d ? String((d as { msg: string }).msg) : String(d)))
      .join("; ");
  }
  if (typeof detail === "object") {
    const o = detail as { message?: string; errors?: string[]; reasons?: string[] };
    const extra = o.errors?.join("; ") || o.reasons?.join("; ");
    return [o.message, extra].filter(Boolean).join(": ") || fallback;
  }
  return fallback;
}

export async function api<T>(path: string, init?: RequestInit & { json?: unknown }): Promise<T> {
  const headers = new Headers(init?.headers);
  let body = init?.body;
  if (init?.json !== undefined) {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(init.json);
  }
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, { ...init, headers, body, cache: "no-store" });
  } catch {
    throw new ApiError(0, "Cannot reach the ScanWise API. Is the backend running on port 8000?");
  }
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  let data: unknown = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text;
  }
  if (!res.ok) {
    const detail = (data as { detail?: unknown } | null)?.detail ?? data;
    throw new ApiError(res.status, messageFrom(detail, `Request failed (${res.status})`), detail);
  }
  return data as T;
}

export const url = (path: string) => `${API_BASE}${path}`;

export interface ExtractionResponse {
  document: DocumentSummary;
  extraction: Extraction;
  new_id?: string;
}

export interface UploadResult {
  documents: DocumentSummary[];
  duplicates: Record<string, { id: string; filename: string }>;
}

/** Upload with progress (fetch has no upload-progress events). */
export function uploadDocuments(
  files: (File | Blob)[],
  opts: { mode: "single" | "batch" | "auto"; source: "upload" | "camera"; pagesMeta?: unknown[]; names?: string[]; llmMode?: string },
  onProgress?: (fraction: number) => void,
): Promise<UploadResult> {
  return new Promise((resolve, reject) => {
    const fd = new FormData();
    files.forEach((f, i) => {
      const name = opts.names?.[i] || (f instanceof File ? f.name : `page-${i + 1}.jpg`);
      fd.append("files", f, name);
    });
    fd.append("mode", opts.mode);
    fd.append("source", opts.source);
    if (opts.pagesMeta) fd.append("pages_meta", JSON.stringify(opts.pagesMeta));
    if (opts.llmMode) fd.append("llm_mode", opts.llmMode);
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${API_BASE}/api/documents`);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress?.(e.loaded / e.total);
    };
    xhr.onerror = () => reject(new ApiError(0, "Upload failed — check your connection and that the backend is running."));
    xhr.onload = () => {
      let data: unknown = null;
      try {
        data = JSON.parse(xhr.responseText);
      } catch {
        /* ignore */
      }
      if (xhr.status >= 200 && xhr.status < 300) resolve(data as UploadResult);
      else {
        const detail = (data as { detail?: unknown } | null)?.detail ?? data;
        reject(new ApiError(xhr.status, messageFrom(detail, `Upload failed (${xhr.status})`), detail));
      }
    };
    xhr.send(fd);
  });
}

export const Api = {
  system: () => api<SystemInfo>("/api/system"),
  options: () => api<Options>("/api/options"),
  settings: () => api<UserSettings>("/api/settings"),
  saveSettings: (s: UserSettings) => api<UserSettings>("/api/settings", { method: "PUT", json: s }),
  dashboard: () => api<Dashboard>("/api/dashboard"),

  documents: (params: { status?: string; q?: string; limit?: number; offset?: number } = {}) => {
    const sp = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => v !== undefined && v !== "" && sp.set(k, String(v)));
    return api<{ total: number; items: DocumentSummary[] }>(`/api/documents?${sp}`);
  },
  document: (id: string) => api<DocumentDetail>(`/api/documents/${id}`),
  status: (id: string) => api<{ id: string; status: string; progress: number; progress_message: string | null; error: string | null }>(`/api/documents/${id}/status`),
  tokens: (id: string, page: number) => api<{ tokens: OcrToken[]; width: number; height: number }>(`/api/documents/${id}/pages/${page}/tokens`),
  patch: (id: string, updates: { path: string; value: string | null }[], verify: string[] = []) =>
    api<ExtractionResponse>(`/api/documents/${id}/extraction`, { method: "PATCH", json: { updates, verify } }),
  addLine: (id: string, afterId?: string) => api<ExtractionResponse>(`/api/documents/${id}/line-items`, { method: "POST", json: { after_id: afterId ?? null } }),
  deleteLine: (id: string, itemId: string) => api<ExtractionResponse>(`/api/documents/${id}/line-items/${itemId}`, { method: "DELETE" }),
  addTax: (id: string) => api<ExtractionResponse>(`/api/documents/${id}/taxes`, { method: "POST" }),
  deleteTax: (id: string, taxId: string) => api<ExtractionResponse>(`/api/documents/${id}/taxes/${taxId}`, { method: "DELETE" }),
  recalculate: (id: string) => api<ExtractionResponse>(`/api/documents/${id}/recalculate`, { method: "POST" }),
  approve: (id: string, acknowledge: boolean) => api<ExtractionResponse>(`/api/documents/${id}/approve`, { method: "POST", json: { acknowledge } }),
  reprocess: (id: string, llmMode?: string) => api<DocumentSummary>(`/api/documents/${id}/reprocess`, { method: "POST", json: { llm_mode: llmMode ?? null } }),
  deleteDocument: (id: string) => api<void>(`/api/documents/${id}`, { method: "DELETE" }),
  audit: (id: string) => api<{ action: string; field_path: string | null; old_value: unknown; new_value: unknown; detail: string | null; created_at: string }[]>(`/api/documents/${id}/audit`),
  exportUrl: (id: string, format: string) => url(`/api/documents/${id}/export?format=${format}`),

  lineItems: (params: Record<string, string | undefined>) => {
    const sp = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => v && sp.set(k, v));
    return api<{ total: number; items: ExtractedRow[] }>(`/api/line-items?${sp}`);
  },
  lineItemsExportUrl: (format: string, params: Record<string, string | undefined>) => {
    const sp = new URLSearchParams({ format });
    Object.entries(params).forEach(([k, v]) => v && sp.set(k, v));
    return url(`/api/line-items/export?${sp}`);
  },

  priceBook: (q?: string, category?: string) => {
    const sp = new URLSearchParams();
    if (q) sp.set("q", q);
    if (category && category !== "All") sp.set("category", category);
    return api<{ items: PriceBookItem[]; categories: string[] }>(`/api/pricebook?${sp}`);
  },
  priceBookItem: (id: string) => api<PriceBookItem>(`/api/pricebook/${id}`),
  createItem: (data: Partial<ItemData>) => api<PriceBookItem>("/api/pricebook", { method: "POST", json: data }),
  updateItem: (id: string, data: Partial<ItemData>) => api<PriceBookItem>(`/api/pricebook/${id}`, { method: "PATCH", json: data }),
  deleteItem: (id: string) => api<void>(`/api/pricebook/${id}`, { method: "DELETE" }),
  bulkDelete: (ids: string[]) => api<{ deleted: number }>("/api/pricebook/bulk-delete", { method: "POST", json: { ids } }),
  bulkCategory: (ids: string[], category: string) => api<{ updated: number }>("/api/pricebook/bulk-category", { method: "POST", json: { ids, category } }),
  checkDuplicates: (name: string, unit?: string | null, excludeId?: string) =>
    api<{ item_id: string; name: string; rate: string | null; currency: string | null; unit: string | null; score: number; reason: string }[]>(
      "/api/pricebook/check-duplicates",
      { method: "POST", json: { name, unit: unit ?? null, exclude_id: excludeId ?? null } },
    ),
  stage: (documentId: string, itemIds?: string[]) => api<StagedItem[]>("/api/pricebook/stage", { method: "POST", json: { document_id: documentId, item_ids: itemIds ?? null } }),
  importFile: (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return api<StagedItem[]>("/api/pricebook/import", { method: "POST", body: fd });
  },
  commit: (decisions: { data: ItemData; action: string; target_id: string | null }[]) =>
    api<{ created: number; updated: number; skipped: number }>("/api/pricebook/commit", { method: "POST", json: { decisions } }),
  priceBookExportUrl: (format: string, category?: string) =>
    url(`/api/pricebook/export?format=${format}${category && category !== "All" ? `&category=${encodeURIComponent(category)}` : ""}`),

  // --- clients + business-card scanner ------------------------------------
  /** files[0] = front (required), files[1] = back (optional). JPEG/PNG only. */
  extractCard: (files: (File | Blob)[], llmMode?: string, signal?: AbortSignal) => {
    const fd = new FormData();
    files.forEach((f, i) => fd.append("files", f, f instanceof File ? f.name : `card-${i + 1}.jpg`));
    if (llmMode) fd.append("llm_mode", llmMode);
    return api<CardScanResult>("/api/business-cards/extract", { method: "POST", body: fd, signal });
  },
  listClients: (q?: string, limit = 200, offset = 0) => {
    const sp = new URLSearchParams({ limit: String(limit), offset: String(offset) });
    if (q) sp.set("q", q);
    return api<{ total: number; items: Client[] }>(`/api/clients?${sp}`);
  },
  client: (id: string) => api<Client>(`/api/clients/${id}`),
  createClient: (data: ClientInput) => api<Client>("/api/clients", { method: "POST", json: data }),
  updateClient: (id: string, data: Partial<ClientInput>) => api<Client>(`/api/clients/${id}`, { method: "PATCH", json: data }),
  deleteClient: (id: string) => api<void>(`/api/clients/${id}`, { method: "DELETE" }),
};

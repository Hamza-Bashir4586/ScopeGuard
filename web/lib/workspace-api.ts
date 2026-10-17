import type {
  Finding, FindingPatch, InvoiceDraft, InvoiceDraftPatch, InvoiceGeneration,
  ProjectCard, ProjectDetail, RecordCategory, RecordPage, Scan, ScanSummary, SourceRecord,
} from "./workspace-types";

export const WORKSPACE_API_URL = (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000").replace(/\/+$/, "");
export const LOCAL_BACKEND_DEMO = process.env.NEXT_PUBLIC_LOCAL_DEMO === "1";
const segment = encodeURIComponent;

export class WorkspaceApiError extends Error {
  constructor(message: string, public status: number, public code?: string, public details?: Record<string, unknown>) {
    super(message); this.name = "WorkspaceApiError";
  }
}

export function workspaceError(error: unknown): string {
  if (error instanceof WorkspaceApiError) {
    const blockers = error.details?.blockers;
    return Array.isArray(blockers) && blockers.length ? `${error.message} ${blockers.filter((b): b is string => typeof b === "string").join(". ")}` : error.message;
  }
  return error instanceof Error ? error.message : "The request could not be completed. Please try again.";
}

async function response(path: string, init: RequestInit = {}): Promise<Response> {
  let res: Response;
  try { res = await fetch(`${WORKSPACE_API_URL}${path}`, { ...init, cache: "no-store" }); }
  catch (error) {
    if (error instanceof Error && error.name === "AbortError") throw error;
    throw new WorkspaceApiError("Could not reach the ScopeGuard backend. Check that the API is running and the frontend API URL is configured.", 0, "connection_failed");
  }
  if (!res.ok) {
    let body: { error?: string; code?: string; details?: Record<string, unknown> } = {};
    try { body = await res.json(); } catch { /* Avoid exposing upstream HTML or raw response bodies. */ }
    throw new WorkspaceApiError(body.error || `The request failed (${res.status}).`, res.status, body.code, body.details);
  }
  return res;
}
async function json<T>(path: string, init?: RequestInit): Promise<T> { return (await response(path, init)).json() as Promise<T>; }
const body = (method: "POST" | "PATCH", value: unknown): RequestInit => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(value) });
const projectPath = (id: string) => `/projects/${segment(id)}`;
function query(values: Record<string, string | number | null | undefined>): string {
  const params = new URLSearchParams();
  Object.entries(values).forEach(([key, value]) => { if (value !== undefined && value !== null && value !== "") params.set(key, String(value)); });
  return params.size ? `?${params}` : "";
}

// No user tokens, cookies, SDK credentials, or client-side financial calculations.
export const workspaceApi = {
  projects: (signal?: AbortSignal) => json<{ projects: ProjectCard[] }>("/projects", { signal }),
  project: (id: string, signal?: AbortSignal) => json<ProjectDetail>(projectPath(id), { signal }),
  resetDemo: (id: string, value: { request_id: string; expected_revision: number }) => json<{ reset_id: string; message: string }>(`${projectPath(id)}/demo-reset`, body("POST", value)),
  records: (id: string, categoryId?: string, signal?: AbortSignal) => json<{ records: SourceRecord[]; categories: RecordCategory[]; total: number }>(`${projectPath(id)}/records${query({ category_id: categoryId })}`, { signal }),
  upload: (id: string, file: File) => { const form = new FormData(); form.append("file", file); return json<SourceRecord>(`${projectPath(id)}/records`, { method: "POST", body: form }); },
  recordPage: (id: string, fileId: string, versionId: string, page: number, scanId?: string, findingId?: string, signal?: AbortSignal) => json<RecordPage>(`${projectPath(id)}/records/${segment(fileId)}/pages/${page}${query({ version_id: versionId, scan_id: scanId, finding_id: findingId })}`, { signal }),
  recordContent: async (id: string, fileId: string, versionId: string, signal?: AbortSignal) => (await response(`${projectPath(id)}/records/${segment(fileId)}/content${query({ version_id: versionId })}`, { signal })).blob(),
  startScan: (value: { project_id: string; cutoff_date?: string; request_id: string }) => json<{ scan_id: string; project_id: string; status: "queued" }>("/scans", body("POST", value)),
  scans: (id: string, signal?: AbortSignal) => json<{ scans: ScanSummary[] }>(`/scans${query({ project_id: id, limit: 20 })}`, { signal }),
  scan: (id: string, signal?: AbortSignal) => json<Scan>(`/scans/${segment(id)}`, { signal }),
  review: (findingId: string, value: FindingPatch) => json<Finding>(`/findings/${segment(findingId)}`, body("PATCH", value)),
  draft: (id: string, signal?: AbortSignal) => json<InvoiceDraft>(`${projectPath(id)}/invoice-draft`, { signal }),
  updateDraft: (id: string, value: InvoiceDraftPatch) => json<InvoiceDraft>(`${projectPath(id)}/invoice-draft`, body("PATCH", value)),
  generate: (id: string, value: { request_id: string; expected_revision: number }) => json<InvoiceGeneration>(`${projectPath(id)}/invoice-generations`, body("POST", value)),
  generation: (id: string, generationId: string, signal?: AbortSignal) => json<InvoiceGeneration>(`${projectPath(id)}/invoice-generations/${segment(generationId)}`, { signal }),
  generationContent: async (id: string, generationId: string) => (await response(`${projectPath(id)}/invoice-generations/${segment(generationId)}/content`)).blob(),
  saveToBox: (id: string, generationId: string) => json<InvoiceGeneration>(`${projectPath(id)}/invoice-generations/${segment(generationId)}/save-to-box`, body("POST", {})),
};

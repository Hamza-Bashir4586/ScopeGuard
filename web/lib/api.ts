// Typed client for the ScopeGuard API (docs/api-contract.md).
// With NEXT_PUBLIC_USE_MOCK=1 all calls go to the in-browser mock instead.

import type {
  CreatePacketRequest,
  CreateScanResponse,
  Finding,
  FindingPatch,
  ListScansResponse,
  Packet,
  Scan,
} from "./types";
import { ApiError } from "./errors";
import { mockApi } from "./mock";

export { ApiError };

export const API_URL = (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000").replace(
  /\/+$/,
  "",
);

export const USE_MOCK = process.env.NEXT_PUBLIC_USE_MOCK === "1";


async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, {
      method,
      headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError(`Could not reach the ScopeGuard API at ${API_URL}.`, 0);
  }

  const text = await res.text();
  let data: unknown = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = null;
    }
  }

  if (!res.ok) {
    const msg =
      data && typeof data === "object" && "error" in data && typeof data.error === "string"
        ? data.error
        : `Request failed (${res.status} ${res.statusText || "error"})`;
    throw new ApiError(msg, res.status);
  }
  return data as T;
}

export interface ScopeGuardApi {
  createScan(): Promise<CreateScanResponse>;
  listScans(limit?: number): Promise<ListScansResponse>;
  getScan(scanId: string): Promise<Scan>;
  patchFinding(findingId: string, patch: FindingPatch): Promise<Finding>;
  createPacket(req: CreatePacketRequest): Promise<Packet>;
}

const httpApi: ScopeGuardApi = {
  createScan: () => request<CreateScanResponse>("POST", "/scans", {}),
  listScans: (limit = 20) => request<ListScansResponse>("GET", `/scans?limit=${limit}`),
  getScan: (scanId) => request<Scan>("GET", `/scans/${encodeURIComponent(scanId)}`),
  patchFinding: (findingId, patch) =>
    request<Finding>("PATCH", `/findings/${encodeURIComponent(findingId)}`, patch),
  createPacket: (req) => request<Packet>("POST", "/packets", req),
};

export const api: ScopeGuardApi = USE_MOCK ? mockApi : httpApi;

export function errorMessage(err: unknown): string {
  if (err instanceof Error) return err.message;
  return String(err);
}

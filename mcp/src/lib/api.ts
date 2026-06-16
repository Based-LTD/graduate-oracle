// HTTP client for graduate-oracle. Adds Authorization header from config.
// Throws structured errors so tool handlers can format them for the LLM.

import { getApiKey, getApiBase } from "./config.js";

const USER_AGENT = `goracle-mcp/0.1.0 node/${process.version}`;

export class ApiError extends Error {
  status: number;
  body: unknown;
  constructor(message: string, status: number, body: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

interface RequestOpts {
  method?: "GET" | "POST";
  body?: unknown;
  headers?: Record<string, string>;
  withAuth?: boolean;
  timeoutMs?: number;
}

function buildUrl(path: string): string {
  if (path.startsWith("http://") || path.startsWith("https://")) return path;
  const base = getApiBase();
  return `${base.replace(/\/+$/, "")}/${path.replace(/^\/+/, "")}`;
}

async function request(path: string, opts: RequestOpts = {}): Promise<unknown> {
  const {
    method = "GET",
    body,
    headers = {},
    withAuth = true,
    timeoutMs = 20_000,
  } = opts;

  const url = buildUrl(path);
  const h: Record<string, string> = { "User-Agent": USER_AGENT, ...headers };
  if (withAuth) {
    const key = getApiKey();
    if (key) h["Authorization"] = `Bearer ${key}`;
  }
  if (body && !h["Content-Type"]) h["Content-Type"] = "application/json";

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  try {
    const res = await fetch(url, {
      method,
      headers: h,
      body: body ? JSON.stringify(body) : undefined,
      signal: controller.signal,
    });
    const text = await res.text();
    let json: unknown = null;
    try { json = text ? JSON.parse(text) : null; } catch { /* leave null */ }

    if (!res.ok) {
      const j = (json ?? {}) as Record<string, unknown>;
      const detail = (j.detail as Record<string, unknown> | undefined)?.error
                  ?? j.error
                  ?? j.message
                  ?? text
                  ?? res.statusText;
      throw new ApiError(`HTTP ${res.status}: ${String(detail)}`, res.status, json);
    }
    return json;
  } finally {
    clearTimeout(timer);
  }
}

export const api = {
  get:  (path: string, opts: RequestOpts = {}) =>
    request(path, { ...opts, method: "GET" }),
  post: (path: string, body: unknown, opts: RequestOpts = {}) =>
    request(path, { ...opts, method: "POST", body }),
};

// Friendly auth message for tools — shown to the LLM when no key is present.
export function authMissingMessage(): string {
  return (
    "graduate-oracle API key not configured. " +
    "Set GORACLE_API_KEY env var, or run `npx goracle signup builder` " +
    "to mint a key (saves to ~/.config/goracle/config.json which this " +
    "MCP server reads automatically). The `accuracy_receipts` tool works " +
    "without a key — try that first."
  );
}

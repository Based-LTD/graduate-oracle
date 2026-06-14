// HTTP wrapper. Adds Authorization header from config when a key exists;
// surfaces non-2xx as Error objects with the server's error message attached.

import { config } from "./config.js";

const USER_AGENT = `goracle-cli/${process.env.npm_package_version || "0.1.0"} node/${process.version}`;

function buildUrl(path) {
  const base = config.get("apiBase") || "https://graduateoracle.fun";
  // Allow callers to pass either "/api/v1/probe/X" or a full URL.
  if (path.startsWith("http://") || path.startsWith("https://")) return path;
  return base.replace(/\/+$/, "") + "/" + path.replace(/^\/+/, "");
}

async function request(path, { method = "GET", body, headers = {}, withAuth = true } = {}) {
  const url = buildUrl(path);
  const h = { "User-Agent": USER_AGENT, ...headers };
  if (withAuth) {
    const key = config.get("apiKey");
    if (key) h["Authorization"] = `Bearer ${key}`;
  }
  if (body && !h["Content-Type"]) h["Content-Type"] = "application/json";

  const res = await fetch(url, {
    method,
    headers: h,
    body: body ? JSON.stringify(body) : undefined,
  });

  const text = await res.text();
  let json = null;
  try { json = text ? JSON.parse(text) : null; } catch { /* leave as text */ }

  if (!res.ok) {
    const detail = json?.detail?.error || json?.error || json?.message || text || res.statusText;
    const err = new Error(`HTTP ${res.status}: ${detail}`);
    err.status = res.status;
    err.body = json ?? text;
    throw err;
  }
  return json ?? text;
}

export const api = {
  get:  (path, opts = {}) => request(path, { ...opts, method: "GET" }),
  post: (path, body, opts = {}) => request(path, { ...opts, method: "POST", body }),
  url:  buildUrl,
};

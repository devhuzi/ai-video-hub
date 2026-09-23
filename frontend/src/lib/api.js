// Single API client. Every request carries the bearer token; any 401 clears the
// token and notifies the app so it can show the password gate again.
import { readString, writeString } from "./storage";

const BACKEND_URL = (process.env.REACT_APP_BACKEND_URL || "").replace(/\/$/, "");
const API = `${BACKEND_URL}/api`;
const TOKEN_KEY = "aivph.token";

let token = readString(TOKEN_KEY);
const unauthorizedListeners = new Set();

export function getToken() {
  return token;
}

export function setToken(value) {
  token = value || null;
  writeString(TOKEN_KEY, token);
}

export function clearToken() {
  setToken(null);
}

export function onUnauthorized(listener) {
  unauthorizedListeners.add(listener);
  return () => unauthorizedListeners.delete(listener);
}

export class ApiError extends Error {
  constructor(status, detail) {
    super(detail || `Request failed (${status})`);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

// Human-readable message for any thrown error.
export function errorMessage(err, fallback = "Something went wrong") {
  if (!err) return fallback;
  if (err instanceof ApiError) return err.detail || fallback;
  if (err.name === "TypeError") return "Can't reach the server. Check that the backend is running.";
  return err.message || fallback;
}

function detailFrom(body) {
  if (!body) return null;
  if (typeof body === "string") return body;
  const d = body.detail;
  if (typeof d === "string") return d;
  // FastAPI validation errors: [{loc, msg}]
  if (Array.isArray(d)) return d.map((e) => e.msg || JSON.stringify(e)).join("; ");
  return null;
}

// Media served by our own API (/api/...) can't send an Authorization header
// from <img>/<video>, so the backend accepts ?token= on those routes. Use this
// only for element src / downloads — never for links meant to be shared.
export function mediaSrc(url) {
  if (!url || !url.startsWith("/api/")) return url;
  const auth = token ? `${url.includes("?") ? "&" : "?"}token=${encodeURIComponent(token)}` : "";
  return `${BACKEND_URL}${url}${auth}`;
}

// Only absolute https URLs (e.g. a NextCloud share) are safe to copy or share.
export function isShareableUrl(url) {
  return typeof url === "string" && /^https:\/\//i.test(url);
}

async function request(method, path, { body, query, signal, auth = true } = {}) {
  let url = `${API}${path}`;
  if (query) {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(query)) {
      if (v !== undefined && v !== null && v !== "") qs.set(k, String(v));
    }
    const s = qs.toString();
    if (s) url += `?${s}`;
  }
  const headers = {};
  if (auth && token) headers.Authorization = `Bearer ${token}`;
  let payload;
  if (body instanceof FormData) {
    payload = body;
  } else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }

  const res = await fetch(url, { method, headers, body: payload, signal });

  if (res.status === 401 && auth) {
    clearToken();
    unauthorizedListeners.forEach((fn) => fn());
  }
  if (!res.ok) {
    let parsed = null;
    try {
      parsed = await res.json();
    } catch {
      // non-JSON error body
    }
    throw new ApiError(res.status, detailFrom(parsed) || `${res.status} ${res.statusText}`.trim());
  }
  if (res.status === 204) return null;
  const text = await res.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    // Typically an HTML page from a dev server or proxy instead of the API.
    throw new ApiError(res.status, "The server returned something other than API data. Check that the backend is running and reachable at /api.");
  }
}

// Some list endpoints historically wrapped arrays ({pipelines: [...]}) —
// accept either shape.
function unwrapList(data, key) {
  if (Array.isArray(data)) return data;
  if (data && Array.isArray(data[key])) return data[key];
  return [];
}

export const api = {
  login: (password) => request("POST", "/auth/login", { body: { password }, auth: false }),

  listPipelines: (signal) =>
    request("GET", "/pipelines", { signal }).then((d) => unwrapList(d, "pipelines")),
  getPipeline: (id, signal) => request("GET", `/pipelines/${encodeURIComponent(id)}`, { signal }),
  createPipeline: (body) => request("POST", "/pipelines", { body }),
  createScriptPipeline: (body) => request("POST", "/script-pipelines", { body }),

  pausePipeline: (id) => request("POST", `/pipelines/${encodeURIComponent(id)}/pause`),
  // `body`: optional new model selections (see RetryRequest on the backend).
  retryPipeline: (id, body) => request("POST", `/pipelines/${encodeURIComponent(id)}/retry`, { body }),
  cancelPipeline: (id) => request("POST", `/pipelines/${encodeURIComponent(id)}/cancel`),
  deletePipeline: (id) => request("DELETE", `/pipelines/${encodeURIComponent(id)}`),
  // `models`: optional { image_model } or { video_model } for just this item.
  regenerate: (id, kind, index, models = {}) =>
    request("POST", `/pipelines/${encodeURIComponent(id)}/regenerate`, { body: { kind, index, ...models } }),

  getSetup: (signal) => request("GET", "/setup", { signal }),
  getTtsVoices: (signal) =>
    request("GET", "/tts/voices", { signal }).then((d) => ({ items: unwrapList(d, "voices"), defaultId: null, provider: d?.provider || null })),
  // Model catalogues also report the server's default model id.
  getImageModels: (signal) =>
    request("GET", "/images/models", { signal }).then((d) => ({
      items: unwrapList(d, "models"), defaultId: d?.default || null, packDefaultId: d?.pack_default || null,
    })),
  getBalances: (signal) => request("GET", "/balances", { signal }),
  getVideoModels: (signal) =>
    request("GET", "/video/models", { signal }).then((d) => ({ items: unwrapList(d, "models"), defaultId: d?.default || null })),
  getLlmModels: (signal) =>
    request("GET", "/llm/models", { signal }).then((d) => ({ items: unwrapList(d, "models"), defaultId: d?.default || null })),
  getEstimate: (query, signal) => request("GET", "/estimate", { query, signal }),

  uploadLogo: (file) => {
    const form = new FormData();
    form.append("file", file);
    return request("POST", "/uploads/logo", { body: form });
  },
};

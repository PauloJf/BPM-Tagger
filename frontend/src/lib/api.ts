// Thin fetch wrapper: same-origin cookie auth + X-CSRF-Token on mutations.
//
// The CSRF token is obtained from /api/me (or /api/login) and cached in module
// scope; the AuthProvider seeds it on boot and refreshes it on login.

let csrfToken = "";

export function setCsrfToken(token: string) {
  csrfToken = token || "";
}

export function getCsrfToken() {
  return csrfToken;
}

// Invoked whenever a request comes back 401 (session expired). The AuthProvider
// registers a handler that flips auth state so the app shows the login screen
// instead of stranding the user on a page full of generic errors.
let onUnauthorized: (() => void) | null = null;

export function setUnauthorizedHandler(fn: (() => void) | null) {
  onUnauthorized = fn;
}

export class ApiError extends Error {
  status: number;
  body: unknown;
  constructor(status: number, message: string, body?: unknown) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

async function parse(resp: Response) {
  const text = await resp.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = {};
  const opts: RequestInit = { method, credentials: "same-origin", headers };
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  if (method !== "GET" && method !== "HEAD") {
    headers["X-CSRF-Token"] = csrfToken;
  }
  const resp = await fetch(path, opts);
  const data = await parse(resp);
  if (!resp.ok) {
    // Session expired mid-session: let the app drop back to the login screen.
    // The login endpoint returns 401 on a wrong password too — don't treat
    // that as a session expiry.
    if (resp.status === 401 && path !== "/api/login") onUnauthorized?.();
    const msg =
      (data && typeof data === "object" && "error" in data && String((data as { error: unknown }).error)) ||
      `HTTP ${resp.status}`;
    throw new ApiError(resp.status, msg, data);
  }
  return data as T;
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body),
  put: <T>(path: string, body?: unknown) => request<T>("PUT", path, body),
  patch: <T>(path: string, body?: unknown) => request<T>("PATCH", path, body),
  del: <T>(path: string, body?: unknown) => request<T>("DELETE", path, body),
};

/** Send raw binary data (e.g. an image upload) with the CSRF header. */
export async function apiUpload<T>(path: string, body: Blob, method = "PUT"): Promise<T> {
  const resp = await fetch(path, {
    method,
    credentials: "same-origin",
    headers: { "X-CSRF-Token": csrfToken, "Content-Type": "application/octet-stream" },
    body,
  });
  const data = await parse(resp);
  if (!resp.ok) {
    if (resp.status === 401) onUnauthorized?.();
    const msg =
      (data && typeof data === "object" && "error" in data && String((data as { error: unknown }).error)) ||
      `HTTP ${resp.status}`;
    throw new ApiError(resp.status, msg, data);
  }
  return data as T;
}

/** Build an /audio streaming URL for a music-dir file path. */
export function audioUrl(filePath: string) {
  return `/audio?path=${encodeURIComponent(filePath)}`;
}

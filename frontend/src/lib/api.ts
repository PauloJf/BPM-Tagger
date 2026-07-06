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
  del: <T>(path: string, body?: unknown) => request<T>("DELETE", path, body),
};

/** Build an /audio streaming URL for a music-dir file path. */
export function audioUrl(filePath: string) {
  return `/audio?path=${encodeURIComponent(filePath)}`;
}

const CSRF_COOKIE = "dbbm_csrf";
const CSRF_HEADER = "X-CSRF-Token";
const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

export class ApiError extends Error {
  readonly status: number;
  readonly detail: unknown;

  constructor(status: number, message: string, detail: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  body?: unknown;
  /** Set false for calls where a 401 is an expected answer (login, session probe). */
  redirectOn401?: boolean;
}

let onUnauthorized: (() => void) | null = null;

export function setUnauthorizedHandler(handler: (() => void) | null): void {
  onUnauthorized = handler;
}

function readCookie(name: string): string | undefined {
  const prefix = `${name}=`;
  return document.cookie
    .split("; ")
    .find((c) => c.startsWith(prefix))
    ?.slice(prefix.length);
}

function describeError(detail: unknown, fallback: string): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const messages = detail
      .map((d) => (d && typeof d === "object" && "msg" in d ? String(d.msg) : null))
      .filter((m): m is string => m !== null);
    if (messages.length > 0) return messages.join("; ");
  }
  return fallback;
}

async function send(path: string, method: string, body: unknown): Promise<Response> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (!SAFE_METHODS.has(method)) {
    const token = readCookie(CSRF_COOKIE);
    if (token) headers[CSRF_HEADER] = token;
  }
  return fetch(path, {
    method,
    headers,
    credentials: "same-origin",
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

async function readDetail(response: Response): Promise<unknown> {
  try {
    const data: unknown = await response.json();
    return data && typeof data === "object" && "detail" in data ? data.detail : undefined;
  } catch {
    return undefined;
  }
}

export async function api<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, redirectOn401 = true } = options;

  let response = await send(path, method, body);
  if (response.status === 403 && !SAFE_METHODS.has(method)) {
    const detail = await readDetail(response.clone());
    if (detail === "csrf_failed") {
      // Missing or stale token: this GET (re)issues the cookie, then retry once.
      await send("/api/auth/status", "GET", undefined);
      response = await send(path, method, body);
    }
  }

  if (response.ok) {
    if (response.status === 204) return undefined as T;
    return (await response.json()) as T;
  }

  const detail = await readDetail(response);
  if (response.status === 401 && redirectOn401) onUnauthorized?.();
  throw new ApiError(
    response.status,
    describeError(detail, `Request failed (${response.status})`),
    detail,
  );
}

// OAuth redirect relay for DBBM's "Connect Google Drive" (docs/DESIGN.md section 9).
//
// Google only accepts public https redirect URIs, so this page is the registered one. It sends
// the browser on to the DBBM install that started the flow: the install's origin travels in
// `state`. The query (code, state, scope, error) is passed through unchanged. The code is useless
// without the PKCE verifier and client secret, which never leave the install.
//
// No storage, no network requests, no third-party code. It only ever redirects to
// <origin>/api/oauth/google/callback on a bare http(s) origin.

export const CALLBACK_PATH = "/api/oauth/google/callback";

function decodeState(state) {
  const base64 = state.replace(/-/g, "+").replace(/_/g, "/");
  const padded = base64 + "=".repeat((4 - (base64.length % 4)) % 4);
  const binary = atob(padded);
  const bytes = Uint8Array.from(binary, (c) => c.charCodeAt(0));
  return JSON.parse(new TextDecoder().decode(bytes));
}

/** The bare http(s) origin in `state`, or null if it is missing or anything more than that. */
export function returnOrigin(state) {
  if (typeof state !== "string" || !state || !/^[A-Za-z0-9_-]+$/.test(state)) return null;
  let data;
  try {
    data = decodeState(state);
  } catch {
    return null;
  }
  if (!data || data.v !== 1 || typeof data.o !== "string" || typeof data.n !== "string") return null;
  let url;
  try {
    url = new URL(data.o);
  } catch {
    return null;
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") return null;
  if (url.username || url.password || !url.hostname) return null;
  // Exactly an origin: no path, query or fragment, and no trailing slash or other variation.
  if (url.origin !== data.o) return null;
  return url.origin;
}

/** Where to send the browser for a given query string, or null to stop here. */
export function relayTarget(search) {
  const params = new URLSearchParams(search);
  const origin = returnOrigin(params.get("state"));
  if (!origin) return null;
  const query = search.startsWith("?") ? search : `?${search}`;
  return origin + CALLBACK_PATH + query;
}

function run() {
  const target = relayTarget(window.location.search);
  const status = document.getElementById("status");
  if (!target) {
    status.textContent =
      "This link is not a valid Google Drive connect request. Go back to DBBM and click Connect again.";
    return;
  }
  status.textContent = "Returning to DBBM…";
  window.location.replace(target);
}

if (typeof window !== "undefined" && typeof document !== "undefined") {
  run();
}

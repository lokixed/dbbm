import { useCallback, useEffect, useRef, useState } from "react";
import {
  fetchGdriveConnectState,
  startGdriveConnect,
  type GdriveConnectRequest,
} from "@/api/destinations";
import type { DriveScope } from "@/api/types";

/** Google sends the browser back here after the consent screen (register it in the OAuth client). */
export const CALLBACK_PATH = "/api/oauth/google/callback";

/** The redirect URI for the address DBBM is open at in this browser. */
export function connectRedirectUri(): string {
  return `${window.location.origin}${CALLBACK_PATH}`;
}

/** Google accepts only https redirect URIs, or plain http on the machine itself (localhost). */
export function connectAvailable(): boolean {
  const { protocol, hostname } = window.location;
  if (protocol === "https:") return true;
  return (
    hostname === "localhost" ||
    hostname.endsWith(".localhost") ||
    hostname === "127.0.0.1" ||
    hostname === "[::1]"
  );
}

/** The done page (opened in Google's tab) tells the form tab the result on this channel. */
export const CONNECT_CHANNEL = "dbbm-gdrive-connect";
const POLL_MS = 2000;
const GIVE_UP_MS = 10 * 60 * 1000;

export interface ConnectMessage {
  flow: string | null;
  error: string | null;
}

/** Keep in step with ERROR_MESSAGES in backend/app/services/gdrive_oauth.py. */
export const CONNECT_ERRORS: Record<string, string> = {
  access_denied: "Access was denied on the Google consent screen.",
  expired: "The connect request expired or was already used. Click Connect again.",
  not_signed_in: "Sign in to DBBM in this browser, then click Connect again.",
  exchange_failed: "Google refused the authorization code. Check the client ID and secret.",
  unreachable: "Could not reach Google to finish connecting. Try again.",
  no_refresh_token:
    "Google returned no refresh token. Remove DBBM's access in your Google account settings, then connect again.",
  scope_not_granted: "Google Drive access was not granted. Tick the Drive permission on the consent screen.",
  google_error: "Google reported an error. Try again.",
};

export function connectErrorMessage(code: string | null): string {
  return (code && CONNECT_ERRORS[code]) || CONNECT_ERRORS.google_error;
}

export type ConnectState =
  | { phase: "idle" }
  | { phase: "starting" }
  | { phase: "waiting"; flowId: string }
  | { phase: "connected"; flowId: string; clientId: string; scope: DriveScope }
  | { phase: "failed"; message: string };

/**
 * Runs "Connect Google Drive": opens Google sign-in in a new tab (so the unsaved form stays) and
 * waits for the done page's message, polling the flow status as a fallback.
 */
export function useGdriveConnect() {
  const [state, setState] = useState<ConnectState>({ phase: "idle" });
  const pending = useRef<{ flowId: string; clientId: string; scope: DriveScope; since: number } | null>(null);

  const finish = useCallback((flowId: string, error: string | null) => {
    const current = pending.current;
    if (!current || current.flowId !== flowId) return;
    pending.current = null;
    setState(
      error
        ? { phase: "failed", message: connectErrorMessage(error) }
        : { phase: "connected", flowId, clientId: current.clientId, scope: current.scope },
    );
  }, []);

  useEffect(() => {
    if (typeof BroadcastChannel === "undefined") return;
    const channel = new BroadcastChannel(CONNECT_CHANNEL);
    channel.onmessage = (event: MessageEvent<ConnectMessage>) => {
      const { flow, error } = event.data ?? {};
      if (flow) finish(flow, error ?? null);
      else if (error && pending.current) finish(pending.current.flowId, error);
    };
    return () => channel.close();
  }, [finish]);

  const waitingFlow = state.phase === "waiting" ? state.flowId : null;
  useEffect(() => {
    if (!waitingFlow) return;
    const timer = window.setInterval(() => {
      const current = pending.current;
      if (!current || current.flowId !== waitingFlow) return;
      if (Date.now() - current.since > GIVE_UP_MS) {
        finish(waitingFlow, "expired");
        return;
      }
      fetchGdriveConnectState(waitingFlow).then(
        (result) => {
          if (result.status !== "pending") finish(waitingFlow, result.error);
        },
        () => finish(waitingFlow, "expired"),
      );
    }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [waitingFlow, finish]);

  /** Call from a click handler: the tab is opened before the request so it is not blocked. */
  const connect = useCallback(async (request: GdriveConnectRequest) => {
    const tab = window.open("", "_blank");
    if (!tab) {
      setState({ phase: "failed", message: "The browser blocked the new tab. Allow pop-ups for DBBM, then try again." });
      return;
    }
    setState({ phase: "starting" });
    try {
      const started = await startGdriveConnect(request);
      pending.current = {
        flowId: started.flow_id,
        clientId: request.client_id,
        scope: request.scope,
        since: Date.now(),
      };
      setState({ phase: "waiting", flowId: started.flow_id });
      tab.opener = null;
      tab.location.href = started.authorize_url;
    } catch (error) {
      tab.close();
      setState({ phase: "failed", message: error instanceof Error ? error.message : "Could not start" });
    }
  }, []);

  const reset = useCallback(() => {
    pending.current = null;
    setState({ phase: "idle" });
  }, []);

  return { state, connect, reset };
}

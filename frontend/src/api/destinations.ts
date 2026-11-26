import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";
import type {
  DestinationInput,
  DestinationRecord,
  DriveScope,
  GdriveConnectStart,
  GdriveConnectState,
  RetentionPreview,
  RetentionRunResult,
  TestResult,
} from "./types";

export const destinationKeys = { all: ["destinations"] as const };

export function useDestinations() {
  return useQuery({
    queryKey: destinationKeys.all,
    queryFn: () => api<DestinationRecord[]>("/api/destinations"),
  });
}

export function useSaveDestination() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, input }: { id: string | null; input: DestinationInput }) =>
      id === null
        ? api<DestinationRecord>("/api/destinations", { method: "POST", body: input })
        : api<DestinationRecord>(`/api/destinations/${id}`, { method: "PUT", body: input }),
    onSuccess: () =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: destinationKeys.all }),
        queryClient.invalidateQueries({ queryKey: ["dashboard"] }),
      ]),
  });
}

export function useDeleteDestination() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api<void>(`/api/destinations/${id}`, { method: "DELETE" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: destinationKeys.all }),
  });
}

/** Tests the settings in a form without saving them. `id` is the saved destination being edited,
 * if any: its stored secrets fill in any secret the form leaves unchanged. */
export function useTestDestinationDraft() {
  return useMutation({
    mutationFn: ({ id, input }: { id: string | null; input: DestinationInput }) =>
      api<TestResult>(`/api/destinations/test${id ? `?destination_id=${encodeURIComponent(id)}` : ""}`, {
        method: "POST",
        body: input,
      }),
  });
}

export function useTestDestination() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      api<TestResult>(`/api/destinations/${id}/test`, { method: "POST" }),
    onSettled: () =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: destinationKeys.all }),
        queryClient.invalidateQueries({ queryKey: ["dashboard"] }),
      ]),
  });
}

export function useRetentionPreview() {
  return useMutation({
    mutationFn: ({ id, keepLast, keepDays }: { id: string; keepLast: number | null; keepDays: number | null }) => {
      const params = new URLSearchParams();
      if (keepLast !== null) params.set("keep_last", String(keepLast));
      if (keepDays !== null) params.set("keep_days", String(keepDays));
      return api<RetentionPreview>(`/api/destinations/${id}/retention/preview?${params.toString()}`);
    },
  });
}

export function useRunRetention() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      api<RetentionRunResult>(`/api/destinations/${id}/retention/run`, { method: "POST" }),
    onSuccess: () =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: ["runs"] }),
        queryClient.invalidateQueries({ queryKey: ["dashboard"] }),
      ]),
  });
}

/** The redirect URI to register in the Google OAuth client (the relay page). */
export function useGdriveConnectConfig(enabled: boolean) {
  return useQuery({
    queryKey: ["gdrive-oauth-config"],
    queryFn: () => api<{ redirect_url: string }>("/api/destinations/gdrive/oauth/config"),
    enabled,
    staleTime: Infinity,
  });
}

export interface GdriveConnectRequest {
  client_id: string;
  /** Omitted to use the stored secret of `destination_id`. */
  client_secret?: string;
  scope: DriveScope;
  destination_id?: string;
}

export function startGdriveConnect(body: GdriveConnectRequest): Promise<GdriveConnectStart> {
  return api<GdriveConnectStart>("/api/destinations/gdrive/oauth/start", { method: "POST", body });
}

export function fetchGdriveConnectState(flowId: string): Promise<GdriveConnectState> {
  return api<GdriveConnectState>(`/api/destinations/gdrive/oauth/${encodeURIComponent(flowId)}`);
}

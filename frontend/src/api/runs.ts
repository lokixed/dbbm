import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api } from "./client";
import {
  ACTIVE_STATUSES,
  type Dashboard,
  type RunDetail,
  type RunPage,
  type RunStatus,
  type RunSummary,
} from "./types";

export const runKeys = {
  all: ["runs"] as const,
  list: (filters: RunFilters) => ["runs", "list", filters] as const,
  detail: (id: string) => ["runs", "detail", id] as const,
};

export interface RunFilters {
  jobId: string;
  status: RunStatus | "";
}

const REFRESH_MS = 3000;

function isActive(status: RunStatus): boolean {
  return ACTIVE_STATUSES.includes(status);
}

export function useRuns(filters: RunFilters) {
  const params = new URLSearchParams({ limit: "100" });
  if (filters.jobId) params.set("job_id", filters.jobId);
  if (filters.status) params.set("status", filters.status);
  return useQuery({
    queryKey: runKeys.list(filters),
    queryFn: () => api<RunPage>(`/api/runs?${params.toString()}`),
    // Poll only while a run is queued or running.
    refetchInterval: (query) =>
      query.state.data?.items.some((r) => isActive(r.status)) ? REFRESH_MS : false,
  });
}

export function useRun(id: string) {
  return useQuery({
    queryKey: runKeys.detail(id),
    queryFn: () => api<RunDetail>(`/api/runs/${id}`),
    refetchInterval: (query) =>
      query.state.data && !isActive(query.state.data.status) ? false : REFRESH_MS,
  });
}

export function useCancelRun() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api<RunDetail>(`/api/runs/${id}/cancel`, { method: "POST" }),
    onSuccess: () =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: runKeys.all }),
        queryClient.invalidateQueries({ queryKey: ["jobs"] }),
        queryClient.invalidateQueries({ queryKey: ["dashboard"] }),
      ]),
  });
}

export function useRetryRun() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api<RunSummary>(`/api/runs/${id}/retry`, { method: "POST" }),
    onSuccess: () =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: runKeys.all }),
        queryClient.invalidateQueries({ queryKey: ["jobs"] }),
        queryClient.invalidateQueries({ queryKey: ["dashboard"] }),
      ]),
  });
}

export function useDashboard() {
  return useQuery({
    queryKey: ["dashboard"],
    queryFn: () => api<Dashboard>("/api/dashboard"),
    refetchInterval: (query) =>
      query.state.data?.jobs.some((e) => e.latest_status && isActive(e.latest_status))
        ? REFRESH_MS
        : false,
  });
}

/** The whole log as plain text; used when the live stream is unavailable or has finished. */
export async function fetchRunLog(id: string): Promise<string> {
  const response = await fetch(`/api/runs/${encodeURIComponent(id)}/logs?format=text`, {
    credentials: "same-origin",
    headers: { Accept: "text/plain" },
  });
  if (!response.ok) throw new ApiError(response.status, `Could not load the log (${response.status})`, null);
  return response.text();
}

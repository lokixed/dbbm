import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";
import { runKeys } from "./runs";
import type { JobInput, JobRecord, RunSummary } from "./types";

export const jobKeys = { all: ["jobs"] as const };
const dashboardKey = ["dashboard"] as const;

export function useJobs() {
  return useQuery({
    queryKey: jobKeys.all,
    queryFn: () => api<JobRecord[]>("/api/jobs"),
    // Keeps "last run" fresh while something is in progress.
    refetchInterval: (query) =>
      query.state.data?.some((j) => j.last_run?.status === "queued" || j.last_run?.status === "running")
        ? 3000
        : false,
  });
}

function useInvalidateJobs() {
  const queryClient = useQueryClient();
  return () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: jobKeys.all }),
      queryClient.invalidateQueries({ queryKey: dashboardKey }),
      queryClient.invalidateQueries({ queryKey: runKeys.all }),
    ]);
}

export function useSaveJob() {
  const invalidate = useInvalidateJobs();
  return useMutation({
    mutationFn: ({ id, input }: { id: string | null; input: JobInput }) =>
      id === null
        ? api<JobRecord>("/api/jobs", { method: "POST", body: input })
        : api<JobRecord>(`/api/jobs/${id}`, { method: "PUT", body: input }),
    onSuccess: invalidate,
  });
}

export function useDeleteJob() {
  const invalidate = useInvalidateJobs();
  return useMutation({
    mutationFn: (id: string) => api<void>(`/api/jobs/${id}`, { method: "DELETE" }),
    onSuccess: invalidate,
  });
}

export function useSetJobEnabled() {
  const invalidate = useInvalidateJobs();
  return useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      api<JobRecord>(`/api/jobs/${id}/${enabled ? "enable" : "disable"}`, { method: "POST" }),
    onSuccess: invalidate,
  });
}

export function useRunJob() {
  const invalidate = useInvalidateJobs();
  return useMutation({
    mutationFn: (id: string) => api<RunSummary>(`/api/jobs/${id}/run`, { method: "POST" }),
    onSuccess: invalidate,
  });
}

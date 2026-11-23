import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";
import type { SourceInput, SourceRecord, TestResult } from "./types";

export const sourceKeys = { all: ["sources"] as const };

export function useSources() {
  return useQuery({
    queryKey: sourceKeys.all,
    queryFn: () => api<SourceRecord[]>("/api/sources"),
  });
}

export function useSaveSource() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, input }: { id: string | null; input: SourceInput }) =>
      id === null
        ? api<SourceRecord>("/api/sources", { method: "POST", body: input })
        : api<SourceRecord>(`/api/sources/${id}`, { method: "PUT", body: input }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: sourceKeys.all }),
  });
}

export function useDeleteSource() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api<void>(`/api/sources/${id}`, { method: "DELETE" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: sourceKeys.all }),
  });
}

export function useTestSource() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api<TestResult>(`/api/sources/${id}/test`, { method: "POST" }),
    // The result is stored server-side as last_test, so refresh the list.
    onSettled: () => queryClient.invalidateQueries({ queryKey: sourceKeys.all }),
  });
}

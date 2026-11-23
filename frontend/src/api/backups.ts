import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";

export interface Backup {
  id: string;
  run_id: string;
  job_id: string | null;
  job_name: string;
  source_id: string | null;
  source_name: string | null;
  source_database: string;
  destination_id: string;
  destination_name: string | null;
  destination_type: string | null;
  path: string;
  size_bytes: number;
  sha256: string;
  md5: string | null;
  compressed: boolean;
  created_at: string;
  /** Null until "Verify now" has re-read the copy; it was verified when it was uploaded. */
  verified_at: string | null;
  missing: boolean;
}

export interface BackupPage {
  items: Backup[];
  total: number;
}

export interface VerifyResult {
  status: "ok" | "missing" | "corrupt";
  detail: string;
  verified_at: string | null;
}

export interface RescanResult {
  destination_id: string;
  checked: number;
  missing: number;
}

export interface BackupFilters {
  jobId: string;
  sourceId: string;
  destinationId: string;
  database: string;
  status: "" | "ok" | "missing";
  from: string;
  to: string;
}

export const PAGE_SIZE = 50;

export const backupKeys = { all: ["backups"] as const };

function toParams(filters: BackupFilters, page: number): URLSearchParams {
  const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(page * PAGE_SIZE) });
  if (filters.jobId) params.set("job_id", filters.jobId);
  if (filters.sourceId) params.set("source_id", filters.sourceId);
  if (filters.destinationId) params.set("destination_id", filters.destinationId);
  if (filters.database) params.set("database", filters.database);
  if (filters.status) params.set("status", filters.status);
  if (filters.from) params.set("from", new Date(`${filters.from}T00:00:00`).toISOString());
  if (filters.to) params.set("to", new Date(`${filters.to}T23:59:59`).toISOString());
  return params;
}

export function useBackups(filters: BackupFilters, page: number) {
  return useQuery({
    queryKey: [...backupKeys.all, filters, page],
    queryFn: () => api<BackupPage>(`/api/backups?${toParams(filters, page).toString()}`),
    placeholderData: (previous) => previous,
  });
}

export function downloadUrl(id: string): string {
  return `/api/backups/${encodeURIComponent(id)}/download`;
}

export function useVerifyBackup() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api<VerifyResult>(`/api/backups/${id}/verify`, { method: "POST" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: backupKeys.all }),
  });
}

export function useDeleteBackup() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api<void>(`/api/backups/${id}`, { method: "DELETE" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: backupKeys.all }),
  });
}

export function useRescanDestination() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (destinationId: string) =>
      api<RescanResult>(`/api/backups/rescan?destination_id=${encodeURIComponent(destinationId)}`, {
        method: "POST",
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: backupKeys.all }),
  });
}

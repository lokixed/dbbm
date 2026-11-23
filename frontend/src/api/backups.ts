import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";
import type { RunSummary } from "./types";

export interface Backup {
  id: string;
  run_id: string;
  job_id: string | null;
  job_name: string;
  source_id: string | null;
  source_name: string | null;
  source_type: string | null;
  source_database: string;
  destination_id: string;
  destination_name: string | null;
  destination_type: string | null;
  path: string;
  size_bytes: number;
  sha256: string;
  md5: string | null;
  compressed: boolean;
  /** The stored file is age ciphertext; `recipients` are the fingerprints of the keys that open it. */
  encrypted: boolean;
  recipients: string[];
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

export interface RestoreInput {
  target_source_id: string;
  target_database: string;
  drop: boolean;
  confirm_database: string;
  confirm_overwrite_original: boolean;
  /** The age private key of an encrypted backup. Sent once, kept in memory, never stored. */
  private_key?: string;
}

/** All stored copies (one per destination) of the same database from the same run. */
export function useBackupCopies(runId: string, database: string, enabled: boolean) {
  return useQuery({
    queryKey: [...backupKeys.all, "copies", runId, database],
    enabled,
    queryFn: async () => {
      const page = await api<BackupPage>(`/api/backups?run_id=${encodeURIComponent(runId)}&limit=200`);
      return page.items.filter((b) => b.source_database === database);
    },
  });
}

export function useRestoreBackup() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, input }: { id: string; input: RestoreInput }) =>
      api<RunSummary>(`/api/backups/${id}/restore`, { method: "POST", body: input }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["runs"] }),
  });
}

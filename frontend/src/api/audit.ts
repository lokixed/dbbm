import { useQuery } from "@tanstack/react-query";
import { api } from "./client";
import { fetchAllPages, MAX_API_PAGE } from "./fetchAll";

export interface AuditEntry {
  id: string;
  timestamp: string;
  username: string | null;
  action: string;
  object_type: string | null;
  object_id: string | null;
  details: Record<string, unknown>;
  ip: string | null;
}

export interface AuditPage {
  items: AuditEntry[];
  total: number;
  /** Every action name that appears in the log, for the filter. */
  actions: string[];
}

export interface AuditFilters {
  action: string;
  username: string;
  search: string;
  from: string;
  to: string;
}

function toParams(filters: AuditFilters, offset: number): URLSearchParams {
  const params = new URLSearchParams({ limit: String(MAX_API_PAGE), offset: String(offset) });
  if (filters.action) params.set("action", filters.action);
  if (filters.username.trim()) params.set("username", filters.username.trim());
  if (filters.search.trim()) params.set("search", filters.search.trim());
  if (filters.from) params.set("from", new Date(`${filters.from}T00:00:00`).toISOString());
  if (filters.to) params.set("to", new Date(`${filters.to}T23:59:59`).toISOString());
  return params;
}

export function useAuditLog(filters: AuditFilters) {
  return useQuery({
    queryKey: ["audit", filters],
    queryFn: () => fetchAllPages((offset) => api<AuditPage>(`/api/audit?${toParams(filters, offset).toString()}`)),
    placeholderData: (previous) => previous,
  });
}

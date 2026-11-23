import { useQuery } from "@tanstack/react-query";
import { api } from "./client";

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

export const AUDIT_PAGE_SIZE = 50;

function toParams(filters: AuditFilters, page: number): URLSearchParams {
  const params = new URLSearchParams({
    limit: String(AUDIT_PAGE_SIZE),
    offset: String(page * AUDIT_PAGE_SIZE),
  });
  if (filters.action) params.set("action", filters.action);
  if (filters.username.trim()) params.set("username", filters.username.trim());
  if (filters.search.trim()) params.set("search", filters.search.trim());
  if (filters.from) params.set("from", new Date(`${filters.from}T00:00:00`).toISOString());
  if (filters.to) params.set("to", new Date(`${filters.to}T23:59:59`).toISOString());
  return params;
}

export function useAuditLog(filters: AuditFilters, page: number) {
  return useQuery({
    queryKey: ["audit", filters, page],
    queryFn: () => api<AuditPage>(`/api/audit?${toParams(filters, page).toString()}`),
    placeholderData: (previous) => previous,
  });
}

import { useState } from "react";
import Alert from "xedonium/Alert";
import Button from "xedonium/Button";
import Card from "xedonium/Card";
import HelperText from "xedonium/HelperText";
import Input from "xedonium/Input";
import Loader from "xedonium/Loader";
import PageHeader from "xedonium/PageHeader";
import Select from "xedonium/Select";
import DataGrid, { type DataGridColumn } from "xedonium/DataGrid";
import { AUDIT_PAGE_SIZE, useAuditLog, type AuditEntry, type AuditFilters } from "@/api/audit";
import { formatTime } from "@/lib/format";

const EMPTY: AuditFilters = { action: "", username: "", search: "", from: "", to: "" };

function summarize(details: Record<string, unknown>): string {
  const text = JSON.stringify(details);
  return text === "{}" ? "" : text.length > 160 ? `${text.slice(0, 160)}…` : text;
}

const COLUMNS: DataGridColumn<AuditEntry>[] = [
  {
    key: "time",
    header: "Time",
    sortable: true,
    accessor: (entry) => entry.timestamp,
    render: (entry) => <span className="whitespace-nowrap">{formatTime(entry.timestamp)}</span>,
  },
  { key: "user", header: "User", sortable: true, accessor: (entry) => entry.username ?? "", render: (entry) => entry.username ?? "-" },
  { key: "action", header: "Action", sortable: true, accessor: (entry) => entry.action, render: (entry) => <code className="text-xs">{entry.action}</code> },
  {
    key: "object",
    header: "Object",
    render: (entry) => (entry.object_type ? `${entry.object_type} ${entry.object_id ?? ""}` : "-"),
  },
  { key: "ip", header: "IP", sortable: true, accessor: (entry) => entry.ip ?? "", render: (entry) => entry.ip ?? "-" },
  {
    key: "details",
    header: "Details",
    render: (entry) => <span className="break-all text-xs text-app-muted">{summarize(entry.details)}</span>,
  },
];

/** Read-only audit trail: who did what, when, and from where. */
export default function AuditPage() {
  const [filters, setFilters] = useState<AuditFilters>(EMPTY);
  const [page, setPage] = useState(0);
  const log = useAuditLog(filters, page);

  const setFilter = (patch: Partial<AuditFilters>) => {
    setFilters({ ...filters, ...patch });
    setPage(0);
  };

  const total = log.data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / AUDIT_PAGE_SIZE));

  return (
    <div className="space-y-6">
      <PageHeader
        title="Audit log"
        subtitle="Sign-ins, changes, downloads and restores. Entries are kept for the period set in Settings."
      />

      <div className="filter-bar flex flex-wrap items-end gap-3">
        <div className="w-full sm:w-56">
          <Select
            aria-label="Filter by action"
            value={filters.action}
            onChange={(action) => setFilter({ action })}
            options={[
              { value: "", label: "All actions" },
              ...(log.data?.actions ?? []).map((a) => ({ value: a, label: a })),
            ]}
          />
        </div>
        <div className="w-full sm:w-40">
          <Input
            aria-label="Filter by user"
            placeholder="User"
            value={filters.username}
            onChange={(username) => setFilter({ username })}
          />
        </div>
        <div className="w-full sm:w-48">
          <Input
            aria-label="Search action or object"
            placeholder="Search"
            value={filters.search}
            onChange={(search) => setFilter({ search })}
          />
        </div>
        <div className="w-full sm:w-40">
          <Input aria-label="From date" type="date" value={filters.from} onChange={(from) => setFilter({ from })} />
        </div>
        <div className="w-full sm:w-40">
          <Input aria-label="To date" type="date" value={filters.to} onChange={(to) => setFilter({ to })} />
        </div>
        <Button variant="secondary" onClick={() => setFilters(EMPTY)}>
          Clear filters
        </Button>
      </div>

      {log.isPending && <Loader variant="dots" label="Loading audit log..." />}
      {log.isError && (
        <Alert tone="danger">
          <div className="flex items-center justify-between gap-4">
            <span>Could not load the audit log: {log.error.message}</span>
            <Button variant="secondary" onClick={() => void log.refetch()}>
              Retry
            </Button>
          </div>
        </Alert>
      )}
      {log.isSuccess && total === 0 && (
        <Card>
          <HelperText>No entries match.</HelperText>
        </Card>
      )}
      {log.isSuccess && total > 0 && (
        <Card padded={false}>
          <DataGrid
            caption="Audit log"
            columns={COLUMNS}
            rows={log.data.items}
            pageSize={AUDIT_PAGE_SIZE}
            hideFooterWhenSinglePage
          />
        </Card>
      )}

      {total > AUDIT_PAGE_SIZE && (
        <div className="flex items-center justify-between text-sm text-app-muted">
          <span>
            Page {page + 1} of {pages} ({total} entries)
          </span>
          <div className="flex gap-2">
            <Button variant="secondary" disabled={page === 0} onClick={() => setPage(page - 1)}>
              Previous
            </Button>
            <Button variant="secondary" disabled={page + 1 >= pages} onClick={() => setPage(page + 1)}>
              Next
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

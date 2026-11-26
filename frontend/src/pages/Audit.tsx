import { useEffect, useMemo, useState } from "react";
import Alert from "xedonium/Alert";
import Badge from "xedonium/Badge";
import Button from "xedonium/Button";
import Card from "xedonium/Card";
import Drawer from "xedonium/Drawer";
import HelperText from "xedonium/HelperText";
import Input from "xedonium/Input";
import FilterIcon from "xedonium/icons/Filter";
import SearchIcon from "xedonium/icons/Search";
import PageHeader from "xedonium/PageHeader";
import Select from "xedonium/Select";
import DataGrid, { type DataGridColumn } from "xedonium/DataGrid";
import RelativeTime from "xedonium/RelativeTime";
import { useAuditLog, type AuditEntry, type AuditFilters } from "@/api/audit";
import { formatTime } from "@/lib/format";
import { AuditObjectLabel } from "@/components/AuditObjectLabel";
import { FilterCell } from "@/components/FilterCell";
import { LogoLoader } from "@/components/LogoLoader";
import { AuditBulkActions } from "@/components/audit/AuditBulkActions";

/** The object's type as the entry recorded it (sources, destinations and channels store one). */
function recordedType(entry: AuditEntry): string | undefined {
  return typeof entry.details.type === "string" ? entry.details.type : undefined;
}

const EMPTY: AuditFilters = { action: "", username: "", search: "", from: "", to: "" };

/** The details as one line: `key value, key value`; the cell truncates it with an ellipsis. */
function summarize(details: Record<string, unknown>): string {
  return Object.entries(details)
    .map(([key, value]) => `${key} ${typeof value === "object" && value !== null ? JSON.stringify(value) : String(value)}`)
    .join(", ");
}

function buildColumns(onOpen: (entry: AuditEntry) => void): DataGridColumn<AuditEntry>[] {
  return [
  {
    key: "time",
    header: "Time",
    sortable: true,
    accessor: (entry) => entry.timestamp,
    render: (entry) => (
      <button
        type="button"
        data-audit-id={entry.id}
        onClick={() => onOpen(entry)}
        className="whitespace-nowrap text-left hover:underline"
      >
        <RelativeTime date={entry.timestamp} tooltip />
      </button>
    ),
  },
  { key: "user", header: "User", sortable: true, accessor: (entry) => entry.username ?? "", render: (entry) => entry.username ?? "-" },
  { key: "action", header: "Action", sortable: true, accessor: (entry) => entry.action, render: (entry) => <code className="text-xs">{entry.action}</code> },
  {
    key: "object",
    header: "Object",
    sortable: true,
    accessor: (entry) => entry.object_type ?? "",
    render: (entry) =>
      entry.object_type ? (
        <span title={entry.object_id ?? undefined}>
          <AuditObjectLabel objectType={entry.object_type} objectId={entry.object_id} recordedType={recordedType(entry)}>
            {entry.object_type}
            {entry.object_id && <span className="ml-1 font-mono text-xs text-app-muted">{entry.object_id.slice(0, 8)}</span>}
          </AuditObjectLabel>
        </span>
      ) : (
        <span className="text-app-muted">-</span>
      ),
  },
    {
      key: "details",
      header: "Details",
      render: (entry) => {
        const text = summarize(entry.details);
        return text ? (
          <div className="max-w-[24rem] truncate text-xs text-app-muted" title={text}>
            {text}
          </div>
        ) : (
          <span className="text-app-muted">-</span>
        );
      },
    },
  ];
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1">
      <dt className="text-xs font-semibold uppercase tracking-widest text-app-muted">{label}</dt>
      <dd className="break-all text-sm">{children}</dd>
    </div>
  );
}

function EntryDetails({ entry }: { entry: AuditEntry }) {
  const hasDetails = Object.keys(entry.details).length > 0;
  return (
    <dl className="space-y-4">
      <Field label="Time">
        <RelativeTime date={entry.timestamp} />
        <div className="text-xs text-app-muted">{formatTime(entry.timestamp)}</div>
      </Field>
      <Field label="User">{entry.username ?? "-"}</Field>
      <Field label="Action">
        <code className="text-xs">{entry.action}</code>
      </Field>
      <Field label="Object">
        {entry.object_type ? (
          <AuditObjectLabel objectType={entry.object_type} objectId={entry.object_id} recordedType={recordedType(entry)}>
            {entry.object_type}
          </AuditObjectLabel>
        ) : (
          "-"
        )}
      </Field>
      {entry.object_id && (
        <Field label="Object ID">
          <code className="text-xs">{entry.object_id}</code>
        </Field>
      )}
      <Field label="IP address">{entry.ip ?? "-"}</Field>
      <Field label="Entry ID">
        <code className="text-xs">{entry.id}</code>
      </Field>
      <Field label="Details">
        {hasDetails ? (
          <pre className="max-h-[50vh] overflow-auto whitespace-pre-wrap border border-app-border bg-app-bg p-3 text-xs">
            {JSON.stringify(entry.details, null, 2)}
          </pre>
        ) : (
          <span className="text-app-muted">None</span>
        )}
      </Field>
    </dl>
  );
}

/** Read-only audit trail: who did what, when, and from where. */
export default function AuditPage() {
  const [filters, setFilters] = useState<AuditFilters>(EMPTY);
  const log = useAuditLog(filters);

  const [draft, setDraft] = useState<AuditFilters>(EMPTY);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<AuditEntry | null>(null);
  const [checkedIds, setCheckedIds] = useState<string[]>([]);
  const columns = useMemo(() => buildColumns(setSelected), []);

  // The grid has no row-click hook, so a click anywhere in a row opens the entry whose time button
  // it holds (the button itself also opens it, for keyboard users).
  const onGridClick = (event: React.MouseEvent<HTMLDivElement>) => {
    const target = event.target as HTMLElement;
    if (target.closest("button, a, input, select")) return;
    const id = target.closest("tbody tr")?.querySelector<HTMLElement>("[data-audit-id]")?.dataset.auditId;
    const entry = log.data?.items.find((item) => item.id === id);
    if (entry) setSelected(entry);
  };

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setFilters((current) => (current.search === query ? current : { ...current, search: query }));
    }, 300);
    return () => window.clearTimeout(timer);
  }, [query]);

  const countActive = (f: AuditFilters) =>
    (Object.keys(EMPTY) as (keyof AuditFilters)[]).filter((k) => k !== "search" && f[k] !== EMPTY[k]).length;
  const activeCount = countActive(filters);
  const filtersActive = activeCount > 0 || filters.search !== "";

  const openFilters = () => {
    setDraft(filters);
    setFiltersOpen(true);
  };
  const applyFilters = () => {
    setFilters(draft);
    setFiltersOpen(false);
  };
  const clearFilters = () => {
    setFilters(EMPTY);
    setDraft(EMPTY);
    setQuery("");
  };

  const total = log.data?.total ?? 0;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Audit log"
        subtitle="Sign-ins, changes, downloads and restores. Entries are kept for the period set in Settings."
      />

      <div className="flex flex-wrap items-stretch gap-2">
        <div className="w-full sm:w-72">
          <Input
            aria-label="Search action or object"
            placeholder="Search action or object"
            value={query}
            onChange={setQuery}
            clearable
            startAdornment={<SearchIcon className="h-4 w-4" />}
          />
        </div>
        <div className="flex flex-wrap items-stretch gap-2 sm:ml-auto">
          {filtersActive && (
            <Button variant="flat" className="!h-auto" onClick={clearFilters}>
              Clear filters
            </Button>
          )}
          <Button variant="secondary" className="!h-auto" onClick={openFilters}>
            <FilterIcon className="mr-2 h-4 w-4" />
            Filters
            {activeCount > 0 && <Badge badgeContent={String(activeCount)} color="default" className="ml-2" />}
          </Button>
        </div>
      </div>

      <Drawer
        open={selected !== null}
        onClose={() => setSelected(null)}
        title="Audit entry"
        side="right"
        width="max-w-lg"
        animated
      >
        {selected && <EntryDetails entry={selected} />}
      </Drawer>

      <Drawer
        open={filtersOpen}
        onClose={() => setFiltersOpen(false)}
        title="Filter audit log"
        side="right"
        width="max-w-sm"
        animated
        footer={
          <div className="flex justify-between gap-2">
            <Button
              variant="flat"
              onClick={() => setDraft({ ...EMPTY, search: draft.search })}
              disabled={countActive(draft) === 0}
            >
              Reset
            </Button>
            <Button onClick={applyFilters}>Apply</Button>
          </div>
        }
      >
        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault();
            applyFilters();
          }}
        >
          <FilterCell label="Action">
            <Select
              aria-label="Filter by action"
              value={draft.action}
              onChange={(action) => setDraft({ ...draft, action })}
              options={[
                { value: "", label: "All actions" },
                ...(log.data?.actions ?? []).map((a) => ({ value: a, label: a })),
              ]}
            />
          </FilterCell>
          <FilterCell label="User">
            <Input
              aria-label="Filter by user"
              placeholder="Username"
              value={draft.username}
              onChange={(username) => setDraft({ ...draft, username })}
              clearable
            />
          </FilterCell>
          <div className="grid grid-cols-2 gap-3">
            <FilterCell label="From">
              <Input aria-label="From date" type="date" value={draft.from} onChange={(from) => setDraft({ ...draft, from })} />
            </FilterCell>
            <FilterCell label="To">
              <Input aria-label="To date" type="date" value={draft.to} onChange={(to) => setDraft({ ...draft, to })} />
            </FilterCell>
          </div>
        </form>
      </Drawer>

      {log.isPending && <LogoLoader />}
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
      <AuditBulkActions
        selected={(log.data?.items ?? []).filter((entry) => checkedIds.includes(entry.id))}
        onClear={() => setCheckedIds([])}
      />

      {log.isSuccess && total > 0 && (
        <div onClick={onGridClick} className="[&_tbody_tr]:cursor-pointer">
          <DataGrid
            caption="Audit log"
            selectable
            selected={checkedIds}
            onSelectionChange={(keys) => setCheckedIds(keys.map(String))}
            columns={columns}
            rows={log.data.items}
            pageSize={10}
            pageSizeOptions={[10, 20, 50, 100]}
            hideFooterWhenSinglePage
          />
        </div>
      )}
    </div>
  );
}

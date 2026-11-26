import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import Alert from "xedonium/Alert";
import Button from "xedonium/Button";
import Badge from "xedonium/Badge";
import Drawer from "xedonium/Drawer";
import Input from "xedonium/Input";
import FilterIcon from "xedonium/icons/Filter";
import PageHeader from "xedonium/PageHeader";
import Select from "xedonium/Select";
import DataGrid, { type DataGridColumn } from "xedonium/DataGrid";
import Tooltip from "xedonium/Tooltip";
import RelativeTime from "xedonium/RelativeTime";
import { useDestinations } from "@/api/destinations";
import { useJobs } from "@/api/jobs";
import { useRuns, type RunFilters } from "@/api/runs";
import { useSources } from "@/api/sources";
import type { RunSummary, RunStatus } from "@/api/types";
import { FilterCell } from "@/components/FilterCell";
import { RunStatusBadge } from "@/components/RunStatusBadge";
import { RunBulkActions } from "@/components/runs/RunBulkActions";
import { LogoLoader } from "@/components/LogoLoader";
import { SourceTypeLabel } from "@/components/SourceTypeIcon";
import { useJobSourceType } from "@/lib/typeLookups";
import { formatBytes, formatDuration } from "@/lib/format";
import { SearchBox } from "@/components/SearchBox";

const STATUSES: RunStatus[] = [
  "queued",
  "running",
  "success",
  "partial",
  "failed",
  "cancelled",
  "interrupted",
];

const makeColumns = (sourceTypeOf: (jobId: string | null) => string | undefined): DataGridColumn<RunSummary>[] => [
  {
    key: "job", sortable: true, accessor: (run) => run.job_name,
    header: "Job",
    render: (run) => (
      <Link to={`/runs/${run.id}`} className="font-medium hover:underline">
        <SourceTypeLabel type={run.source_type ?? sourceTypeOf(run.job_id)}>{run.job_name}</SourceTypeLabel>
      </Link>
    ),
  },
  {
    key: "trigger", sortable: true, accessor: (run) => run.trigger,
    header: "Trigger",
    render: (run) =>
      run.parent_run_id ? (
        <Tooltip text="Open the retried run">
          <Link to={`/runs/${run.parent_run_id}`} className="capitalize hover:underline">
            {run.trigger}
          </Link>
        </Tooltip>
      ) : (
        <span className="capitalize">{run.trigger}</span>
      ),
  },
  {
    key: "status", sortable: true, accessor: (run) => run.status,
    header: "Status",
    render: (run) =>
      run.status === "partial" ? (
        <Tooltip text="Some destinations failed; open the run to retry them.">
          <RunStatusBadge status={run.status} />
        </Tooltip>
      ) : (
        <RunStatusBadge status={run.status} />
      ),
  },
  { key: "started", sortable: true, accessor: (run) => run.started_at ?? run.created_at, header: "Started", render: (run) => <RelativeTime date={run.started_at ?? run.created_at} /> },
  { key: "duration", sortable: true, accessor: (run) => (run.started_at && run.finished_at ? Date.parse(run.finished_at) - Date.parse(run.started_at) : -1), header: "Duration", render: (run) => formatDuration(run.started_at, run.finished_at) },
  { key: "artifacts", sortable: true, accessor: (run) => run.artifact_count, header: "Artifacts", align: "right", render: (run) => run.artifact_count },
  {
    key: "size", sortable: true, accessor: (run) => run.total_size,
    header: "Total size",
    align: "right",
    render: (run) => (run.artifact_count ? formatBytes(run.total_size) : "-"),
  },
];

const EMPTY: RunFilters = { jobId: "", sourceId: "", destinationId: "", status: "", from: "", to: "" };
const FILTER_KEYS = ["jobId", "sourceId", "destinationId", "status", "from", "to"] as const;

/** The filters live in the URL (?job=&source=&destination=&status=&from=&to=), so the edit pages
 * can link to a filtered list and back/forward keeps them. */
const URL_PARAM: Record<(typeof FILTER_KEYS)[number], string> = {
  jobId: "job",
  sourceId: "source",
  destinationId: "destination",
  status: "status",
  from: "from",
  to: "to",
};

function filtersFrom(params: URLSearchParams): RunFilters {
  const status = params.get("status");
  return {
    jobId: params.get("job") ?? "",
    sourceId: params.get("source") ?? "",
    destinationId: params.get("destination") ?? "",
    status: STATUSES.includes(status as RunStatus) ? (status as RunStatus) : "",
    from: params.get("from") ?? "",
    to: params.get("to") ?? "",
  };
}

function toParams(filters: RunFilters): Record<string, string> {
  return Object.fromEntries(
    FILTER_KEYS.filter((key) => filters[key] !== "").map((key) => [URL_PARAM[key], filters[key]]),
  );
}

export default function RunsPage() {
  const [params, setParams] = useSearchParams();
  const filters = useMemo(() => filtersFrom(params), [params]);
  const [draft, setDraft] = useState<RunFilters>(EMPTY);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [query, setQuery] = useState("");
  const runs = useRuns(filters);
  const jobs = useJobs();
  const sources = useSources();
  const destinations = useDestinations();
  const columns = makeColumns(useJobSourceType());

  const applyFilters = (next: RunFilters) => {
    setParams(toParams(next), { replace: true });
    setFiltersOpen(false);
  };
  const activeCount = FILTER_KEYS.filter((key) => filters[key] !== "").length;
  const draftActive = FILTER_KEYS.some((key) => draft[key] !== "");
  const openFilters = () => {
    setDraft(filters);
    setFiltersOpen(true);
  };

  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const needle = query.trim().toLowerCase();
  const rows = useMemo(
    () =>
      (runs.data?.items ?? []).filter(
        (run) =>
          needle === "" || [run.job_name, run.trigger, run.status].some((text) => text.toLowerCase().includes(needle)),
      ),
    [runs.data, needle],
  );

  return (
    <div className="space-y-6">
      <PageHeader
        title="Runs"
        subtitle="History of every backup run. The list refreshes while a run is in progress."
      />

      <div className="flex flex-wrap items-stretch gap-2">
        <SearchBox label="Search runs" placeholder="Search job, trigger or status" value={query} onChange={setQuery} />
        <div className="flex flex-wrap items-stretch gap-2 sm:ml-auto">
          {(activeCount > 0 || query !== "") && (
            <Button
              variant="flat"
              className="!h-auto"
              onClick={() => {
                setQuery("");
                applyFilters(EMPTY);
              }}
            >
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
        open={filtersOpen}
        onClose={() => setFiltersOpen(false)}
        title="Filter runs"
        side="right"
        width="max-w-sm"
        animated
        footer={
          <div className="flex justify-between gap-2">
            <Button variant="flat" onClick={() => setDraft(EMPTY)} disabled={!draftActive}>
              Reset
            </Button>
            <Button onClick={() => applyFilters(draft)}>Apply</Button>
          </div>
        }
      >
        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault();
            applyFilters(draft);
          }}
        >
          <FilterCell label="Job">
            <Select
              aria-label="Filter by job"
              value={draft.jobId}
              onChange={(jobId) => setDraft({ ...draft, jobId })}
              options={[
                { value: "", label: "All jobs" },
                ...(jobs.data ?? []).map((job) => ({ value: job.id, label: job.name })),
              ]}
            />
          </FilterCell>
          <FilterCell label="Source">
            <Select
              aria-label="Filter by source"
              value={draft.sourceId}
              onChange={(sourceId) => setDraft({ ...draft, sourceId })}
              options={[
                { value: "", label: "All sources" },
                ...(sources.data ?? []).map((source) => ({ value: source.id, label: source.name })),
              ]}
            />
          </FilterCell>
          <FilterCell label="Destination">
            <Select
              aria-label="Filter by destination"
              value={draft.destinationId}
              onChange={(destinationId) => setDraft({ ...draft, destinationId })}
              options={[
                { value: "", label: "All destinations" },
                ...(destinations.data ?? []).map((dest) => ({ value: dest.id, label: dest.name })),
              ]}
            />
          </FilterCell>
          <FilterCell label="Status">
            <Select
              aria-label="Filter by status"
              value={draft.status}
              onChange={(status) => setDraft({ ...draft, status: status as RunStatus | "" })}
              options={[
                { value: "", label: "All statuses" },
                ...STATUSES.map((status) => ({ value: status, label: status })),
              ]}
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

      {runs.isPending && <LogoLoader />}
      {runs.isError && (
        <Alert tone="danger">
          <div className="flex items-center justify-between gap-4">
            <span>Could not load runs: {runs.error.message}</span>
            <Button variant="secondary" onClick={() => void runs.refetch()}>
              Retry
            </Button>
          </div>
        </Alert>
      )}
      <RunBulkActions
        selected={rows.filter((run) => selectedIds.includes(run.id))}
        onClear={() => setSelectedIds([])}
      />

      {runs.isSuccess && (
        <DataGrid
          selectable
          selected={selectedIds}
          onSelectionChange={(keys) => setSelectedIds(keys.map(String))}
          density="compact"
          pageSize={10}
          pageSizeOptions={[10, 20, 50, 100]}
          hideFooterWhenSinglePage
          columns={columns}
          rows={rows}
          empty='No runs match. Start one with "Run now" on the Jobs page.'
        />
      )}
    </div>
  );
}

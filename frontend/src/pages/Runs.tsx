import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import Alert from "xedonium/Alert";
import Button from "xedonium/Button";
import Card from "xedonium/Card";
import HelperText from "xedonium/HelperText";
import Loader from "xedonium/Loader";
import PageHeader from "xedonium/PageHeader";
import Select from "xedonium/Select";
import DataGrid, { type DataGridColumn } from "xedonium/DataGrid";
import Tooltip from "xedonium/Tooltip";
import RelativeTime from "xedonium/RelativeTime";
import { useJobs } from "@/api/jobs";
import { useRuns, type RunFilters } from "@/api/runs";
import type { RunSummary, RunStatus } from "@/api/types";
import { RunStatusBadge } from "@/components/RunStatusBadge";
import { formatBytes, formatDuration } from "@/lib/format";

const STATUSES: RunStatus[] = [
  "queued",
  "running",
  "success",
  "partial",
  "failed",
  "cancelled",
  "interrupted",
];

const COLUMNS: DataGridColumn<RunSummary>[] = [
  {
    key: "job", sortable: true, accessor: (run) => run.job_name,
    header: "Job",
    render: (run) => (
      <Link to={`/runs/${run.id}`} className="font-medium hover:underline">
        {run.job_name}
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

export default function RunsPage() {
  const [params, setParams] = useSearchParams();
  const statusParam = params.get("status");
  const [filters, setFilters] = useState<RunFilters>({
    jobId: "",
    status: STATUSES.includes(statusParam as RunStatus) ? (statusParam as RunStatus) : "",
  });
  const updateFilters = (next: RunFilters) => {
    setFilters(next);
    setParams(next.status ? { status: next.status } : {}, { replace: true });
  };
  const runs = useRuns(filters);
  const jobs = useJobs();

  return (
    <div className="space-y-6">
      <PageHeader
        title="Runs"
        subtitle="History of every backup run. The list refreshes while a run is in progress."
      />

      <div className="flex flex-wrap gap-3">
        <div className="w-full sm:w-56">
          <Select
          aria-label="Filter by job"
                    value={filters.jobId}
          onChange={(jobId) => updateFilters({ ...filters, jobId })}
          options={[
            { value: "", label: "All jobs" },
            ...(jobs.data ?? []).map((job) => ({ value: job.id, label: job.name })),
          ]}
        />
        </div>
        <div className="w-full sm:w-48">
          <Select
          aria-label="Filter by status"
                    value={filters.status}
          onChange={(status) => updateFilters({ ...filters, status: status as RunStatus | "" })}
          options={[
            { value: "", label: "All statuses" },
            ...STATUSES.map((status) => ({ value: status, label: status })),
          ]}
        />
        </div>
      </div>

      <Card padded={false}>
        {runs.isPending && <Loader variant="dots" label="Loading runs..." className="p-6" />}
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
        {runs.isSuccess && (
          <DataGrid
            density="compact"
            pageSize={25}
            hideFooterWhenSinglePage
            columns={COLUMNS}
            rows={runs.data.items}
            empty='No runs match. Start one with "Run now" on the Jobs page.'
          />
        )}
      </Card>
      {runs.isSuccess && runs.data.total > runs.data.items.length && (
        <HelperText>
          Showing the latest {runs.data.items.length} of {runs.data.total} runs.
        </HelperText>
      )}
    </div>
  );
}

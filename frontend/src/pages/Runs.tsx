import { Loader2 } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { useJobs } from "@/api/jobs";
import { useRuns, type RunFilters } from "@/api/runs";
import type { RunStatus } from "@/api/types";
import { RunStatusBadge } from "@/components/RunStatusBadge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Select } from "@/components/ui/select";
import { TBody, TD, TH, THead, Table, TR } from "@/components/ui/table";
import { formatBytes, formatDuration, formatTime } from "@/lib/format";

const STATUSES: RunStatus[] = [
  "queued",
  "running",
  "success",
  "partial",
  "failed",
  "cancelled",
  "interrupted",
];

export default function RunsPage() {
  const [filters, setFilters] = useState<RunFilters>({ jobId: "", status: "" });
  const runs = useRuns(filters);
  const jobs = useJobs();

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Runs</h1>
        <p className="text-sm text-muted-foreground">
          History of every backup run. The list refreshes while a run is in progress.
        </p>
      </div>

      <div className="flex flex-wrap gap-3">
        <Select
          aria-label="Filter by job"
          className="w-56"
          value={filters.jobId}
          onChange={(event) => setFilters({ ...filters, jobId: event.target.value })}
        >
          <option value="">All jobs</option>
          {jobs.data?.map((job) => (
            <option key={job.id} value={job.id}>
              {job.name}
            </option>
          ))}
        </Select>
        <Select
          aria-label="Filter by status"
          className="w-48"
          value={filters.status}
          onChange={(event) => setFilters({ ...filters, status: event.target.value as RunStatus | "" })}
        >
          <option value="">All statuses</option>
          {STATUSES.map((status) => (
            <option key={status} value={status}>
              {status}
            </option>
          ))}
        </Select>
      </div>

      <Card>
        {runs.isPending && (
          <p className="flex items-center gap-2 p-6 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" /> Loading runs...
          </p>
        )}
        {runs.isError && (
          <div role="alert" className="flex items-center justify-between gap-4 p-6 text-sm text-destructive">
            <span>Could not load runs: {runs.error.message}</span>
            <Button variant="secondary" size="sm" onClick={() => void runs.refetch()}>
              Retry
            </Button>
          </div>
        )}
        {runs.isSuccess && runs.data.items.length === 0 && (
          <p className="p-6 text-sm text-muted-foreground">
            No runs match. Start one with "Run now" on the Jobs page.
          </p>
        )}
        {runs.isSuccess && runs.data.items.length > 0 && (
          <Table>
            <THead>
              <tr>
                <TH>Job</TH>
                <TH>Trigger</TH>
                <TH>Status</TH>
                <TH>Started</TH>
                <TH>Duration</TH>
                <TH className="text-right">Artifacts</TH>
                <TH className="text-right">Total size</TH>
              </tr>
            </THead>
            <TBody>
              {runs.data.items.map((run) => (
                <TR key={run.id} className={run.status === "partial" ? "bg-amber-500/5" : undefined}>
                  <TD className="font-medium">
                    <Link to={`/runs/${run.id}`} className="hover:underline">
                      {run.job_name}
                    </Link>
                  </TD>
                  <TD className="capitalize">
                    {run.parent_run_id ? (
                      <Link to={`/runs/${run.parent_run_id}`} className="hover:underline" title="Open the retried run">
                        {run.trigger}
                      </Link>
                    ) : (
                      run.trigger
                    )}
                  </TD>
                  <TD>
                    <span title={run.status === "partial" ? "Some destinations failed; open the run to retry them." : undefined}>
                      <RunStatusBadge status={run.status} />
                    </span>
                  </TD>
                  <TD>{formatTime(run.started_at ?? run.created_at)}</TD>
                  <TD>{formatDuration(run.started_at, run.finished_at)}</TD>
                  <TD className="text-right">{run.artifact_count}</TD>
                  <TD className="text-right">{run.artifact_count ? formatBytes(run.total_size) : "-"}</TD>
                </TR>
              ))}
            </TBody>
          </Table>
        )}
      </Card>
      {runs.isSuccess && runs.data.total > runs.data.items.length && (
        <p className="text-xs text-muted-foreground">
          Showing the latest {runs.data.items.length} of {runs.data.total} runs.
        </p>
      )}
    </div>
  );
}

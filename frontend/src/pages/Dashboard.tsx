import { Link, useNavigate } from "react-router-dom";
import Alert from "xedonium/Alert";
import Badge from "xedonium/Badge";
import Button from "xedonium/Button";
import ButtonLink from "xedonium/ButtonLink";
import Card from "xedonium/Card";
import HelperText from "xedonium/HelperText";
import Loader from "xedonium/Loader";
import PageHeader from "xedonium/PageHeader";
import Progress from "xedonium/Progress";
import DataGrid, { type DataGridColumn } from "xedonium/DataGrid";
import Tooltip from "xedonium/Tooltip";
import PlayIcon from "xedonium/icons/Play";
import RelativeTime from "xedonium/RelativeTime";
import { useRunJob } from "@/api/jobs";
import { useDashboard } from "@/api/runs";
import type { Dashboard, DestinationHealth, DiskUsage } from "@/api/types";
import { AuthBadge, ReauthBanner } from "@/components/destinations/AuthStatus";
import { RunStatusBadge } from "@/components/RunStatusBadge";
import { useToast } from "@/components/toast";
import { errorClassLabel } from "@/lib/errorClass";
import { formatBytes, formatTime } from "@/lib/format";

const HEALTH_COLUMNS: DataGridColumn<DestinationHealth>[] = [
  { key: "name", sortable: true, accessor: (d) => d.name, header: "Destination", render: (d) => <span className="font-medium">{d.name}</span> },
  { key: "type", sortable: true, accessor: (d) => d.type, header: "Type", render: (d) => (d.type === "gdrive" ? "Google Drive" : "Local drive") },
  {
    key: "last", sortable: true, accessor: (d) => d.last_success_at ?? "",
    header: "Last stored backup",
    render: (d) => (d.last_success_at ? <RelativeTime date={d.last_success_at} /> : "Never"),
  },
  {
    key: "status", sortable: true, accessor: (d) => d.auth_status,
    header: "Status",
    render: (d) => (
      <div className="flex flex-wrap gap-2">
        <AuthBadge type={d.type} status={d.auth_status} />
        {d.last_error_class && (
          <Badge badgeContent={`Last upload: ${errorClassLabel(d.last_error_class)}`} color="warning" />
        )}
      </div>
    ),
  },
];

function DestinationHealthCard({ destinations }: { destinations: DestinationHealth[] }) {
  if (destinations.length === 0) return null;
  return (
    <Card title="Destination health" headingLevel={2} padded={false}>
      <DataGrid density="compact"columns={HEALTH_COLUMNS} rows={destinations} hideFooterWhenSinglePage />
    </Card>
  );
}

function DiskBar({ disk, threshold }: { disk: DiskUsage; threshold: number }) {
  const usedPercent = disk.total_bytes ? (100 * disk.used_bytes) / disk.total_bytes : 0;
  return (
    <div className="space-y-1">
      <div className="flex flex-wrap items-baseline justify-between gap-x-2 text-sm">
        <span className="font-medium">
          {disk.label}
          {disk.kind === "destination" && (
            <span className="ml-1 text-xs text-app-muted">(local destination)</span>
          )}
        </span>
        <Tooltip text={`Warning when less than ${threshold}% is free`}>
          <span className={disk.below_threshold ? "text-red-600 dark:text-red-400" : "text-app-muted"}>
            {formatBytes(disk.free_bytes)} free of {formatBytes(disk.total_bytes)} ({disk.free_percent}%)
          </span>
        </Tooltip>
      </div>
      <Progress aria-label={`${disk.label} used`} value={Math.min(100, Math.round(usedPercent))} />
    </div>
  );
}

const FAILURES_PREVIEW = 5;

function OverviewCards({ data }: { data: Dashboard }) {
  const failures = data.failures_24h.slice(0, FAILURES_PREVIEW);
  const hiddenFailures = data.failures_24h.length - FAILURES_PREVIEW;
  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <Card title="Next scheduled runs" headingLevel={2}>
        <div className="space-y-1 text-sm">
          {data.next_runs.length === 0 && <HelperText>No scheduled jobs.</HelperText>}
          {data.next_runs.map((n) => (
            <div key={n.job_id} className="flex justify-between gap-2">
              <span className="font-medium">{n.job_name}</span>
              <Tooltip text={`${n.schedule} (${n.timezone})`}>
                <span className="text-app-muted"><RelativeTime date={n.next_run_at} /></span>
              </Tooltip>
            </div>
          ))}
        </div>
      </Card>
      <Card title="Failures in the last 24 h" headingLevel={2}>
        {data.failures_24h.length === 0 ? (
          <HelperText>None.</HelperText>
        ) : (
          <ul className="divide-y divide-app-border text-sm">
            {failures.map((f) => (
              <li key={f.run_id} className="flex items-center justify-between gap-3 py-2 first:pt-0 last:pb-0">
                <Link to={`/runs/${f.run_id}`} className="min-w-0 hover:underline">
                  <span className="block break-words font-medium">{f.job_name}</span>
                  <span className="block text-xs text-app-muted">
                    {f.finished_at ? <RelativeTime date={f.finished_at} /> : ""}
                  </span>
                </Link>
                <RunStatusBadge status={f.status} />
              </li>
            ))}
          </ul>
        )}
        {hiddenFailures > 0 && (
          <ButtonLink
            variant="flat"
            className="mt-3"
            href="/runs?status=failed"
            linkComponent={Link}
            linkProp="to"
          >
            View all
          </ButtonLink>
        )}
      </Card>
      <Card title="Disk usage" headingLevel={2}>
        <div className="space-y-3">
          {data.disks.length === 0 && <HelperText>No local volumes found.</HelperText>}
          {data.disks.map((d) => (
            <DiskBar key={d.key} disk={d} threshold={data.low_disk_threshold_percent} />
          ))}
        </div>
      </Card>
    </div>
  );
}

export default function DashboardPage() {
  const { toast } = useToast();
  const navigate = useNavigate();
  const dashboard = useDashboard();
  const run = useRunJob();

  const runNow = (jobId: string, name: string) =>
    run.mutate(jobId, {
      onSuccess: (started) => navigate(`/runs/${started.id}`),
      onError: (error) =>
        toast({ title: `Could not start ${name}`, description: error.message, variant: "error" }),
    });

  return (
    <div className="space-y-6">
      <PageHeader title="Dashboard" subtitle="The latest state of every backup job." />

      {dashboard.isPending && <Loader variant="inline" label="Loading..." />}
      {dashboard.isError && (
        <Alert tone="danger">
          <div className="flex items-center justify-between gap-4">
            <span>Could not load the dashboard: {dashboard.error.message}</span>
            <Button variant="secondary" onClick={() => void dashboard.refetch()}>
              Retry
            </Button>
          </div>
        </Alert>
      )}
      {dashboard.isSuccess && dashboard.data.scheduler?.state === "not_owner" && (
        <Alert tone="danger">
          Scheduling is off in this instance: another DBBM process uses the same data directory and
          owns the scheduler. Run only one instance per data directory.
        </Alert>
      )}
      {dashboard.isSuccess && (
        <ReauthBanner
          destinations={dashboard.data.destinations.filter((d) => d.auth_status === "needs_reauth")}
          onReconnect={(id) => navigate(`/destinations?reconnect=${encodeURIComponent(id)}`)}
        />
      )}
      {dashboard.isSuccess && dashboard.data.jobs.length === 0 && (
        <HelperText>
          No jobs yet. Add a source and a destination, then create a job on the{" "}
          <Link className="underline" to="/jobs">
            Jobs page
          </Link>
          .
        </HelperText>
      )}
      {dashboard.isSuccess && dashboard.data.jobs.length > 0 && (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {dashboard.data.jobs.map((entry) => {
            const active = entry.latest_status === "queued" || entry.latest_status === "running";
            const starting = run.isPending && run.variables === entry.job_id;
            return (
              <Card
                key={entry.job_id}
                title={entry.job_name}
                headingLevel={2}
                actions={
                  entry.latest_run_id ? (
                    <Link to={`/runs/${entry.latest_run_id}`} aria-label={`Latest run of ${entry.job_name}`}>
                      <RunStatusBadge status={entry.latest_status} />
                    </Link>
                  ) : (
                    <RunStatusBadge status={null} />
                  )
                }
              >
                <div className="space-y-3 text-sm">
                  <div className="flex flex-wrap gap-1">
                    {entry.overdue && (
                      <Tooltip text={entry.overdue_since ? `Since ${formatTime(entry.overdue_since)}` : "Overdue"}>
                        <Badge badgeContent="Overdue" color="danger" />
                      </Tooltip>
                    )}
                    {entry.skipped_schedules_24h > 0 && (
                      <Tooltip text="A scheduled run was skipped because the previous run was still active">
                        <Badge badgeContent={`${entry.skipped_schedules_24h} skipped`} color="warning" />
                      </Tooltip>
                    )}
                    {!entry.enabled && <Badge badgeContent="Disabled" color="secondary" />}
                  </div>
                  <dl className="grid grid-cols-[7rem_1fr] gap-y-1">
                    <dt className="text-app-muted">Last success</dt>
                    <dd>{entry.last_success_at ? <RelativeTime date={entry.last_success_at} /> : "Never"}</dd>
                    <dt className="text-app-muted">Next run</dt>
                    <dd>{entry.next_run_at ? <RelativeTime date={entry.next_run_at} /> : entry.schedule ? "-" : "Manual"}</dd>
                    <dt className="text-app-muted">Last size</dt>
                    <dd>{entry.last_artifact_size === null ? "-" : formatBytes(entry.last_artifact_size)}</dd>
                  </dl>
                  <Button
                    variant="secondary"
                    disabled={starting || active}
                    onClick={() => runNow(entry.job_id, entry.job_name)}
                  >
                    <PlayIcon className="h-4 w-4" /> Run now
                  </Button>
                </div>
              </Card>
            );
          })}
        </div>
      )}
      {dashboard.isSuccess && <OverviewCards data={dashboard.data} />}
      {dashboard.isSuccess && <DestinationHealthCard destinations={dashboard.data.destinations} />}
    </div>
  );
}

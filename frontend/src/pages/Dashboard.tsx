import { Loader2, Play } from "lucide-react";
import { Link, useNavigate } from "react-router-dom";
import { useRunJob } from "@/api/jobs";
import { useDashboard } from "@/api/runs";
import type { Dashboard, DestinationHealth, DiskUsage } from "@/api/types";
import { AuthBadge, ReauthBanner } from "@/components/destinations/AuthStatus";
import { RunStatusBadge } from "@/components/RunStatusBadge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { TBody, TD, TH, THead, Table, TR } from "@/components/ui/table";
import { useToast } from "@/components/ui/toast";
import { errorClassLabel } from "@/lib/errorClass";
import { formatBytes, formatTime } from "@/lib/format";

function DestinationHealthCard({ destinations }: { destinations: DestinationHealth[] }) {
  if (destinations.length === 0) return null;
  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-base">Destination health</CardTitle>
      </CardHeader>
      <CardContent className="p-0">
        <Table>
          <THead>
            <tr>
              <TH>Destination</TH>
              <TH>Type</TH>
              <TH>Last stored backup</TH>
              <TH>Status</TH>
            </tr>
          </THead>
          <TBody>
            {destinations.map((d) => (
              <TR key={d.id}>
                <TD className="font-medium">{d.name}</TD>
                <TD>{d.type === "gdrive" ? "Google Drive" : "Local drive"}</TD>
                <TD>{d.last_success_at ? formatTime(d.last_success_at) : "Never"}</TD>
                <TD>
                  <div className="flex flex-wrap gap-2">
                    <AuthBadge type={d.type} status={d.auth_status} />
                    {d.last_error_class && (
                      <Badge variant="warning">Last upload: {errorClassLabel(d.last_error_class)}</Badge>
                    )}
                  </div>
                </TD>
              </TR>
            ))}
          </TBody>
        </Table>
      </CardContent>
    </Card>
  );
}

function DiskBar({ disk, threshold }: { disk: DiskUsage; threshold: number }) {
  const usedPercent = disk.total_bytes ? (100 * disk.used_bytes) / disk.total_bytes : 0;
  return (
    <div className="space-y-1">
      <div className="flex justify-between text-sm">
        <span className="font-medium">
          {disk.label}
          {disk.kind === "destination" && <span className="ml-1 text-xs text-muted-foreground">(local destination)</span>}
        </span>
        <span className={disk.below_threshold ? "text-destructive" : "text-muted-foreground"}>
          {formatBytes(disk.free_bytes)} free of {formatBytes(disk.total_bytes)} ({disk.free_percent}%)
        </span>
      </div>
      <div
        className="relative h-2 overflow-hidden rounded-full bg-secondary"
        role="meter"
        aria-label={`${disk.label} used`}
        aria-valuenow={Math.round(usedPercent)}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <div
          className={disk.below_threshold ? "h-full bg-destructive" : "h-full bg-primary"}
          style={{ width: `${Math.min(100, usedPercent)}%` }}
        />
        <div
          className="absolute inset-y-0 w-0.5 bg-amber-500"
          style={{ left: `${100 - threshold}%` }}
          title={`Warning when less than ${threshold}% is free`}
        />
      </div>
    </div>
  );
}

function OverviewCards({ data }: { data: Dashboard }) {
  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Next scheduled runs</CardTitle>
        </CardHeader>
        <CardContent className="space-y-1 text-sm">
          {data.next_runs.length === 0 && <p className="text-muted-foreground">No scheduled jobs.</p>}
          {data.next_runs.map((n) => (
            <div key={n.job_id} className="flex justify-between gap-2">
              <span className="font-medium">{n.job_name}</span>
              <span className="text-muted-foreground" title={`${n.schedule} (${n.timezone})`}>
                {formatTime(n.next_run_at)}
              </span>
            </div>
          ))}
        </CardContent>
      </Card>
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Failures in the last 24 h</CardTitle>
        </CardHeader>
        <CardContent className="space-y-1 text-sm">
          {data.failures_24h.length === 0 && <p className="text-muted-foreground">None.</p>}
          {data.failures_24h.map((f) => (
            <Link key={f.run_id} to={`/runs/${f.run_id}`} className="flex justify-between gap-2 hover:underline">
              <span className="font-medium">{f.job_name}</span>
              <span className="flex items-center gap-2">
                <RunStatusBadge status={f.status} />
                <span className="text-xs text-muted-foreground">{f.finished_at ? formatTime(f.finished_at) : ""}</span>
              </span>
            </Link>
          ))}
        </CardContent>
      </Card>
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Disk usage</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {data.disks.length === 0 && <p className="text-sm text-muted-foreground">No local volumes found.</p>}
          {data.disks.map((d) => (
            <DiskBar key={d.key} disk={d} threshold={data.low_disk_threshold_percent} />
          ))}
        </CardContent>
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
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Dashboard</h1>
        <p className="text-sm text-muted-foreground">The latest state of every backup job.</p>
      </div>

      {dashboard.isPending && (
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" /> Loading...
        </p>
      )}
      {dashboard.isError && (
        <div role="alert" className="flex items-center justify-between gap-4 text-sm text-destructive">
          <span>Could not load the dashboard: {dashboard.error.message}</span>
          <Button variant="secondary" size="sm" onClick={() => void dashboard.refetch()}>
            Retry
          </Button>
        </div>
      )}
      {dashboard.isSuccess && dashboard.data.scheduler?.state === "not_owner" && (
        <div role="alert" className="rounded-md border border-destructive/50 p-3 text-sm text-destructive">
          Scheduling is off in this instance: another DBBackup process uses the same data directory and
          owns the scheduler. Run only one instance per data directory.
        </div>
      )}
      {dashboard.isSuccess && (
        <ReauthBanner
          destinations={dashboard.data.destinations.filter((d) => d.auth_status === "needs_reauth")}
          onReconnect={(id) => navigate(`/destinations?reconnect=${encodeURIComponent(id)}`)}
        />
      )}
      {dashboard.isSuccess && dashboard.data.jobs.length === 0 && (
        <p className="text-sm text-muted-foreground">
          No jobs yet. Add a source and a destination, then create a job on the{" "}
          <Link className="underline" to="/jobs">
            Jobs page
          </Link>
          .
        </p>
      )}
      {dashboard.isSuccess && dashboard.data.jobs.length > 0 && (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {dashboard.data.jobs.map((entry) => {
            const active = entry.latest_status === "queued" || entry.latest_status === "running";
            const starting = run.isPending && run.variables === entry.job_id;
            return (
              <Card key={entry.job_id}>
                <CardHeader className="flex-row items-start justify-between space-y-0 pb-3">
                  <div className="space-y-1">
                    <CardTitle className="text-base">{entry.job_name}</CardTitle>
                    <div className="flex flex-wrap gap-1">
                      {entry.overdue && (
                        <Badge variant="danger" title={entry.overdue_since ? `Since ${formatTime(entry.overdue_since)}` : undefined}>
                          Overdue
                        </Badge>
                      )}
                      {entry.skipped_schedules_24h > 0 && (
                        <Badge variant="warning" title="A scheduled run was skipped because the previous run was still active">
                          {entry.skipped_schedules_24h} skipped
                        </Badge>
                      )}
                      {!entry.enabled && <Badge variant="neutral">Disabled</Badge>}
                    </div>
                  </div>
                  {entry.latest_run_id ? (
                    <Link to={`/runs/${entry.latest_run_id}`} aria-label={`Latest run of ${entry.job_name}`}>
                      <RunStatusBadge status={entry.latest_status} />
                    </Link>
                  ) : (
                    <RunStatusBadge status={null} />
                  )}
                </CardHeader>
                <CardContent className="space-y-3 text-sm">
                  <dl className="grid grid-cols-2 gap-y-1">
                    <dt className="text-muted-foreground">Last success</dt>
                    <dd>{entry.last_success_at ? formatTime(entry.last_success_at) : "Never"}</dd>
                    <dt className="text-muted-foreground">Next run</dt>
                    <dd>{entry.next_run_at ? formatTime(entry.next_run_at) : entry.schedule ? "-" : "Manual"}</dd>
                    <dt className="text-muted-foreground">Last size</dt>
                    <dd>
                      {entry.last_artifact_size === null ? "-" : formatBytes(entry.last_artifact_size)}
                    </dd>
                  </dl>
                  <Button
                    variant="secondary"
                    size="sm"
                    disabled={starting || active}
                    onClick={() => runNow(entry.job_id, entry.job_name)}
                  >
                    {starting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
                    Run now
                  </Button>
                </CardContent>
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

import { ArrowLeft, Loader2, RotateCcw, XCircle } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useCancelRun, useRetryRun, useRun } from "@/api/runs";
import { ACTIVE_STATUSES } from "@/api/types";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { DestinationResults } from "@/components/runs/DestinationResults";
import { LogViewer } from "@/components/runs/LogViewer";
import { RunTimeline } from "@/components/runs/RunTimeline";
import { EventStatusBadge } from "@/components/notifications/EventStatusBadge";
import { RunStatusBadge } from "@/components/RunStatusBadge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { TBody, TD, TH, THead, Table, TR } from "@/components/ui/table";
import { useToast } from "@/components/ui/toast";
import { formatBytes, formatDuration, formatTime } from "@/lib/format";
import { hasRetryableFailures } from "@/lib/retry";
import { ApiError } from "@/api/client";

/** Re-renders every second while `active`, so a running duration keeps counting. */
function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [active]);
  return now;
}

export default function RunDetailPage() {
  const { id = "" } = useParams();
  const { toast } = useToast();
  const run = useRun(id);
  const cancel = useCancelRun();
  const retry = useRetryRun();
  const navigate = useNavigate();
  const [confirming, setConfirming] = useState(false);
  const active = run.data ? ACTIVE_STATUSES.includes(run.data.status) : false;
  const now = useNow(active);

  const startRetry = () =>
    retry.mutate(id, {
      onSuccess: (child) => {
        toast({ title: "Retry started", variant: "success" });
        navigate(`/runs/${child.id}`);
      },
      onError: (error) =>
        toast({ title: "Could not retry", description: error.message, variant: "error" }),
    });

  const confirmCancel = () =>
    cancel.mutate(id, {
      onSuccess: () => {
        toast({ title: "Run cancelled", variant: "success" });
        setConfirming(false);
      },
      onError: (error) => {
        toast({ title: "Could not cancel the run", description: error.message, variant: "error" });
        setConfirming(false);
      },
    });

  if (run.isPending) {
    return (
      <p className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" /> Loading run...
      </p>
    );
  }
  if (run.isError) {
    const missing = run.error instanceof ApiError && run.error.status === 404;
    return (
      <div role="alert" className="space-y-3 text-sm">
        <p className="text-destructive">
          {missing ? "This run does not exist." : `Could not load the run: ${run.error.message}`}
        </p>
        <Link className="underline" to="/runs">
          Back to runs
        </Link>
      </div>
    );
  }

  const data = run.data;
  const canRetry = data.status === "partial" && hasRetryableFailures(data.step_results);
  return (
    <div className="space-y-6">
      <Link to="/runs" className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:underline">
        <ArrowLeft className="h-4 w-4" /> All runs
      </Link>

      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="space-y-2">
          <h1 className="text-2xl font-semibold tracking-tight">{data.job_name}</h1>
          <div className="flex flex-wrap items-center gap-3 text-sm text-muted-foreground">
            <RunStatusBadge status={data.status} />
            <span>Duration {formatDuration(data.started_at, data.finished_at, now)}</span>
            <span>Started {formatTime(data.started_at ?? data.created_at)}</span>
            <span className="capitalize">Trigger: {data.trigger}</span>
            {data.scheduled_for && <span>Scheduled for {formatTime(data.scheduled_for)}</span>}
          </div>
          {(data.parent_run_id || data.retry_run_ids.length > 0) && (
            <div className="flex flex-wrap items-center gap-3 text-sm">
              {data.parent_run_id && (
                <Link className="underline" to={`/runs/${data.parent_run_id}`}>
                  Retry of an earlier run
                </Link>
              )}
              {data.retry_run_ids.map((childId, index) => (
                <Link key={childId} className="underline" to={`/runs/${childId}`}>
                  Retry {index + 1}
                </Link>
              ))}
            </div>
          )}
        </div>
        <div className="flex gap-2">
          {canRetry && (
            <Button onClick={startRetry} disabled={retry.isPending}>
              {retry.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <RotateCcw className="h-4 w-4" />}
              Retry failed destinations
            </Button>
          )}
          {active && (
            <Button variant="secondary" onClick={() => setConfirming(true)} disabled={cancel.isPending}>
              <XCircle className="h-4 w-4" /> Cancel run
            </Button>
          )}
        </div>
      </div>

      {data.error && (
        <div role="alert" className="rounded-md border border-destructive/50 p-3 text-sm text-destructive">
          {data.error}
        </div>
      )}

      <div className="grid gap-6 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Progress</CardTitle>
          </CardHeader>
          <CardContent>
            <RunTimeline results={data.step_results} />
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Live log</CardTitle>
          </CardHeader>
          <CardContent>
            <LogViewer key={id} runId={id} onDone={() => void run.refetch()} />
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Destinations</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          {(data.step_results.units ?? []).some((u) => u.destinations.length > 0) ? (
            <DestinationResults results={data.step_results} />
          ) : (
            <p className="px-6 pb-6 text-sm text-muted-foreground">No destination has been reached yet.</p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Artifacts</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          {data.artifacts.length === 0 ? (
            <p className="px-6 pb-6 text-sm text-muted-foreground">
              {active ? "Artifacts appear here as they are stored and verified." : "This run stored no artifacts."}
            </p>
          ) : (
            <Table>
              <THead>
                <tr>
                  <TH>Database</TH>
                  <TH>Destination</TH>
                  <TH>File</TH>
                  <TH className="text-right">Size</TH>
                  <TH>SHA-256</TH>
                </tr>
              </THead>
              <TBody>
                {data.artifacts.map((artifact) => (
                  <TR key={artifact.id}>
                    <TD className="font-medium">{artifact.source_database}</TD>
                    <TD>{artifact.destination_name ?? "-"}</TD>
                    <TD className="break-all font-mono text-xs">{artifact.path}</TD>
                    <TD className="whitespace-nowrap text-right" title={`${artifact.size_bytes} bytes`}>
                      {formatBytes(artifact.size_bytes)}
                    </TD>
                    <TD className="break-all font-mono text-xs">{artifact.sha256}</TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Notifications</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          {data.notifications.length === 0 ? (
            <p className="px-6 pb-6 text-sm text-muted-foreground">
              {active ? "Notifications are sent when the run finishes." : "No notification was sent for this run."}
            </p>
          ) : (
            <Table>
              <THead>
                <tr>
                  <TH>Channel</TH>
                  <TH>Event</TH>
                  <TH>Status</TH>
                  <TH>Attempts</TH>
                  <TH>Error</TH>
                </tr>
              </THead>
              <TBody>
                {data.notifications.map((n) => (
                  <TR key={n.id}>
                    <TD className="font-medium">{n.channel_name ?? "-"}</TD>
                    <TD>{n.event_type.replace("_", " ")}</TD>
                    <TD>
                      <EventStatusBadge status={n.status} />
                    </TD>
                    <TD>{n.attempts}</TD>
                    <TD className="text-xs text-muted-foreground">{n.error ?? ""}</TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <ConfirmDialog
        open={confirming}
        title="Cancel this run?"
        description="The dump process is stopped and its temporary files are removed. Backups already stored by this run are kept."
        confirmLabel="Cancel run"
        pending={cancel.isPending}
        pendingLabel="Cancelling..."
        onConfirm={confirmCancel}
        onCancel={() => setConfirming(false)}
      />
    </div>
  );
}

import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import Alert from "xedonium/Alert";
import Button from "xedonium/Button";
import Card from "xedonium/Card";
import HelperText from "xedonium/HelperText";
import PageHeader from "xedonium/PageHeader";
import Table, { type TableColumn } from "xedonium/Table";
import Tooltip from "xedonium/Tooltip";
import RotateCcwIcon from "xedonium/icons/RotateCcw";
import XCircleIcon from "xedonium/icons/XCircle";
import RelativeTime from "xedonium/RelativeTime";
import { ApiError } from "@/api/client";
import { ChannelTypeLabel } from "@/components/ChannelTypeIcon";
import { DestinationLabel } from "@/components/DestinationIcon";
import { SourceTypeLabel } from "@/components/SourceTypeIcon";
import type { ChannelTypeKey } from "@/lib/channelType";
import { useChannelType, useDestinationType, useJobSourceType } from "@/lib/typeLookups";
import { useCancelRun, useRetryRun, useRun } from "@/api/runs";
import { ACTIVE_STATUSES, type ArtifactRecord, type RunNotification } from "@/api/types";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { EventStatusBadge } from "@/components/notifications/EventStatusBadge";
import { RunStatusBadge } from "@/components/RunStatusBadge";
import { DestinationResults } from "@/components/runs/DestinationResults";
import { BackLink } from "@/components/BackLink";
import { LogViewer } from "@/components/runs/LogViewer";
import { RestoreSummaryCard } from "@/components/runs/RestoreSummaryCard";
import { RunTimeline } from "@/components/runs/RunTimeline";
import { useToast } from "@/components/toast";
import { LogoLoader } from "@/components/LogoLoader";
import { formatBytes, formatDuration } from "@/lib/format";
import { CopyText } from "@/components/CopyText";
import { hasRetryableFailures } from "@/lib/retry";
import { EVENT_TYPE_LABEL } from "@/lib/notificationLabels";

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

interface TypeLookups {
  sourceType: string | undefined;
  destinationType: (id: string | null) => string | undefined;
  channelType: (id: string | null) => ChannelTypeKey | undefined;
}

const makeArtifactColumns = ({ sourceType, destinationType }: TypeLookups): TableColumn<ArtifactRecord>[] => [
  { key: "database", header: "Database", render: (a) => <SourceTypeLabel type={sourceType} className="font-medium">{a.source_database}</SourceTypeLabel> },
  {
    key: "destination",
    header: "Destination",
    render: (a) => <DestinationLabel type={destinationType(a.destination_id)}>{a.destination_name ?? "-"}</DestinationLabel>,
  },
  { key: "file", header: "File", render: (a) => <CopyText value={a.path} head={14} tail={30} label="Copy file path" /> },
  {
    key: "size",
    header: "Size",
    align: "right",
    render: (a) => (
      <Tooltip text={`${a.size_bytes} bytes`}>
        <span className="whitespace-nowrap">{formatBytes(a.size_bytes)}</span>
      </Tooltip>
    ),
  },
  { key: "sha", header: "SHA-256", render: (a) => <CopyText value={a.sha256} head={12} tail={6} label="Copy SHA-256" /> },
];

const makeNotificationColumns = ({ channelType }: TypeLookups): TableColumn<RunNotification>[] => [
  { key: "channel", header: "Channel", 
    render: (n) => {
      const type = channelType(n.channel_id);
      const name = n.channel_name ?? "-";
      return type ? <ChannelTypeLabel type={type}>{name}</ChannelTypeLabel> : <span className="font-medium">{name}</span>;
    },
  },
  { key: "event", header: "Event", render: (n) => EVENT_TYPE_LABEL[n.event_type] },
  { key: "status", header: "Status", render: (n) => <EventStatusBadge status={n.status} /> },
  { key: "attempts", header: "Attempts", render: (n) => n.attempts },
  { key: "error", header: "Error", render: (n) => <span className="text-xs text-app-muted">{n.error ?? ""}</span> },
];

export default function RunDetailPage() {
  const { id = "" } = useParams();
  const { toast } = useToast();
  const run = useRun(id);
  const lookups: TypeLookups = {
    sourceType: useJobSourceType()(run.data?.job_id ?? null),
    destinationType: useDestinationType(),
    channelType: useChannelType(),
  };
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

  if (run.isPending) return <LogoLoader />;
  if (run.isError) {
    const missing = run.error instanceof ApiError && run.error.status === 404;
    return (
      <div className="space-y-3">
        <Alert tone="danger">
          {missing ? "This run does not exist." : `Could not load the run: ${run.error.message}`}
        </Alert>
        <Link className="text-sm underline" to="/runs">
          Back to runs
        </Link>
      </div>
    );
  }

  const data = run.data;
  const canRetry = data.status === "partial" && hasRetryableFailures(data.step_results);
  const hasDestinations = (data.step_results.units ?? []).some((u) => u.destinations.length > 0);
  return (
    <div className="space-y-6">
      <BackLink to="/runs" label="All runs" />

      <PageHeader title={data.job_name}>
        <div className="flex gap-2">
          {canRetry && (
            <Button onClick={startRetry} disabled={retry.isPending}>
              <RotateCcwIcon className="h-4 w-4" /> Retry failed destinations
            </Button>
          )}
          {active && (
            <Button variant="secondary" onClick={() => setConfirming(true)} disabled={cancel.isPending}>
              <XCircleIcon className="h-4 w-4" /> Cancel run
            </Button>
          )}
        </div>
      </PageHeader>

      <div className="space-y-2">
        <div className="flex flex-wrap items-center gap-3 text-sm text-app-muted">
          <RunStatusBadge status={data.status} />
          <span>Duration {formatDuration(data.started_at, data.finished_at, now)}</span>
          <span>Started <RelativeTime date={data.started_at ?? data.created_at} /></span>
          <span className="capitalize">Trigger: {data.trigger}</span>
          {data.scheduled_for && <span>Scheduled for <RelativeTime date={data.scheduled_for} /></span>}
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

      {data.error && <Alert tone="danger">{data.error}</Alert>}

      {data.kind === "restore" && data.step_results.restore && (
        <RestoreSummaryCard restore={data.step_results.restore} sourceType={data.source_type} />
      )}

      {/* grid-cols-1 caps the single column at the page width; without it long log lines
          (paths) widen both cards past the screen on phones. */}
      <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
        <Card title="Progress" headingLevel={2}>
          <RunTimeline results={data.step_results} />
        </Card>
        <Card title="Live log" headingLevel={2} padded={false}>
          <LogViewer key={id} runId={id} onDone={() => void run.refetch()} />
        </Card>
      </div>

      {/* A restore reads one stored copy and writes nothing to a destination. */}
      {data.kind === "backup" && (
        <>
          <Card title="Destinations" headingLevel={2} padded={false}>
            {hasDestinations ? (
              <DestinationResults results={data.step_results} />
            ) : (
              <HelperText className="px-6 py-6">
                {active ? "No destination has been reached yet." : "No destination was reached."}
              </HelperText>
            )}
          </Card>

          <Card title="Artifacts" headingLevel={2} padded={false}>
            <Table
              columns={makeArtifactColumns(lookups)}
              rows={data.artifacts}
              empty={active ? "Artifacts appear here as they are stored and verified." : "This run stored no artifacts."}
            />
          </Card>
        </>
      )}

      <Card title="Notifications" headingLevel={2} padded={false}>
        <Table
          columns={makeNotificationColumns(lookups)}
          rows={data.notifications}
          empty={active ? "Notifications are sent when the run finishes." : "No notification was sent for this run."}
        />
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

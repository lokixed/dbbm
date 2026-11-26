import { Link } from "react-router-dom";
import Alert from "xedonium/Alert";
import DataGrid, { type DataGridColumn } from "xedonium/DataGrid";
import RelativeTime from "xedonium/RelativeTime";
import { useChannelEvents } from "@/api/notifications";
import type { NotificationEvent } from "@/api/types";
import { LogoLoader } from "@/components/LogoLoader";
import { EventStatusBadge } from "@/components/notifications/EventStatusBadge";
import { EVENT_TYPE_LABEL } from "@/lib/notificationLabels";

const EVENT_COLUMNS: DataGridColumn<NotificationEvent>[] = [
  {
    key: "time",
    header: "Time",
    sortable: true,
    accessor: (event) => event.created_at,
    render: (event) => <span className="whitespace-nowrap text-xs"><RelativeTime date={event.created_at} /></span>,
  },
  {
    key: "event",
    header: "Event",
    sortable: true,
    accessor: (event) => EVENT_TYPE_LABEL[event.event_type],
    render: (event) => EVENT_TYPE_LABEL[event.event_type],
  },
  {
    key: "run",
    header: "Run",
    render: (event) =>
      event.run_id ? (
        <Link className="whitespace-nowrap hover:underline" to={`/runs/${event.run_id}`}>
          View run
        </Link>
      ) : (
        <span className="text-app-muted">-</span>
      ),
  },
  { key: "status", header: "Status", render: (event) => <EventStatusBadge status={event.status} /> },
  { key: "attempts", header: "Attempts", render: (event) => event.attempts },
  {
    key: "error",
    header: "Error",
    render: (event) => <span className="text-xs text-app-muted">{event.error ?? ""}</span>,
  },
];

/** The delivery events of one channel, newest first. */
export function ChannelEvents({ channelId }: { channelId: string }) {
  const events = useChannelEvents(channelId);
  if (events.isPending) return <LogoLoader label="Loading events" />;
  if (events.isError) return <Alert tone="danger">Could not load events: {events.error.message}</Alert>;
  return (
    <DataGrid
      density="compact"
      pageSize={10}
      pageSizeOptions={[10, 20, 50, 100]}
      hideFooterWhenSinglePage
      columns={EVENT_COLUMNS}
      rows={events.data.items}
      empty="No messages sent through this channel yet."
    />
  );
}

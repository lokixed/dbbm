import { useState } from "react";
import { Link } from "react-router-dom";
import Alert from "xedonium/Alert";
import Badge from "xedonium/Badge";
import Button from "xedonium/Button";
import Card from "xedonium/Card";
import HelperText from "xedonium/HelperText";
import Loader from "xedonium/Loader";
import PageHeader from "xedonium/PageHeader";
import Switch from "xedonium/Switch";
import Table, { type TableColumn } from "xedonium/Table";
import Tooltip from "xedonium/Tooltip";
import HistoryIcon from "xedonium/icons/History";
import PencilIcon from "xedonium/icons/Pencil";
import PlusIcon from "xedonium/icons/Plus";
import SendIcon from "xedonium/icons/Send";
import Trash2Icon from "xedonium/icons/Trash2";
import {
  EVENTS_PAGE_SIZE,
  useChannelEvents,
  useChannels,
  useDeleteChannel,
  useSaveChannel,
  useTestChannel,
} from "@/api/notifications";
import type { ChannelRecord, NotificationEvent } from "@/api/types";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { ChannelDialog } from "@/components/notifications/ChannelDialog";
import { EventStatusBadge } from "@/components/notifications/EventStatusBadge";
import { useToast } from "@/components/toast";
import { channelToValues, toChannelInput } from "@/lib/channelForm";
import { formatTime } from "@/lib/format";

const TYPE_LABEL = { slack: "Slack", email: "Email", webhook: "Webhook" } as const;

const EVENT_COLUMNS: TableColumn<NotificationEvent>[] = [
  {
    key: "time",
    header: "Time",
    render: (event) => <span className="whitespace-nowrap text-xs">{formatTime(event.created_at)}</span>,
  },
  {
    key: "event",
    header: "Event",
    render: (event) => (
      <>
        {event.event_type.replace("_", " ")}
        {event.run_id && (
          <Link className="ml-2 text-xs underline" to={`/runs/${event.run_id}`}>
            run
          </Link>
        )}
      </>
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

function EventsCard({ channel, onClose }: { channel: ChannelRecord; onClose: () => void }) {
  const [page, setPage] = useState(0);
  const events = useChannelEvents(channel.id, page);
  const pages = events.data ? Math.max(1, Math.ceil(events.data.total / EVENTS_PAGE_SIZE)) : 1;
  return (
    <Card
      title={`Delivery events: ${channel.name}`}
      padded={false}
      actions={
        <Button variant="flat" onClick={onClose}>
          Close
        </Button>
      }
    >
      <div className="space-y-3 pb-4">
        {events.isPending && <Loader variant="inline" label="Loading events..." className="px-6" />}
        {events.isError && <Alert tone="danger">Could not load events: {events.error.message}</Alert>}
        {events.isSuccess && (
          <>
            <Table
              columns={EVENT_COLUMNS}
              rows={events.data.items}
              empty="No messages sent through this channel yet."
            />
            {events.data.items.length > 0 && (
              <div className="flex items-center justify-end gap-2 px-6 text-sm">
                <span className="text-app-muted">
                  Page {page + 1} of {pages} ({events.data.total} events)
                </span>
                <Button variant="secondary" disabled={page === 0} onClick={() => setPage((p) => p - 1)}>
                  Previous
                </Button>
                <Button variant="secondary" disabled={page + 1 >= pages} onClick={() => setPage((p) => p + 1)}>
                  Next
                </Button>
              </div>
            )}
          </>
        )}
      </div>
    </Card>
  );
}

export default function NotificationsPage() {
  const { toast } = useToast();
  const channels = useChannels();
  const save = useSaveChannel();
  const test = useTestChannel();
  const remove = useDeleteChannel();
  const [editor, setEditor] = useState<{ open: boolean; channel: ChannelRecord | null }>({
    open: false,
    channel: null,
  });
  const [deleting, setDeleting] = useState<ChannelRecord | null>(null);
  const [eventsFor, setEventsFor] = useState<ChannelRecord | null>(null);
  const [results, setResults] = useState<Record<string, { ok: boolean; message: string }>>({});

  const toggle = (channel: ChannelRecord) =>
    save.mutate(
      {
        id: channel.id,
        input: toChannelInput({ ...channelToValues(channel), enabled: !channel.enabled }, {}),
      },
      {
        onError: (error) =>
          toast({ title: "Could not change the channel", description: error.message, variant: "error" }),
      },
    );

  const sendTest = (channel: ChannelRecord) =>
    test.mutate(channel.id, {
      onSuccess: (result) => setResults((r) => ({ ...r, [channel.id]: result })),
      onError: (error) => setResults((r) => ({ ...r, [channel.id]: { ok: false, message: error.message } })),
    });

  const confirmDelete = () => {
    if (!deleting) return;
    remove.mutate(deleting.id, {
      onSuccess: () => {
        toast({ title: "Channel deleted", description: deleting.name, variant: "success" });
        if (eventsFor?.id === deleting.id) setEventsFor(null);
        setDeleting(null);
      },
      onError: (error) => {
        toast({ title: "Could not delete the channel", description: error.message, variant: "error" });
        setDeleting(null);
      },
    });
  };

  const deleteText = deleting
    ? deleting.jobs.length > 0
      ? `"${deleting.name}" will be removed from ${deleting.jobs.length} job(s): ${deleting.jobs.map((j) => j.name).join(", ")}. Its delivery history is deleted too.`
      : `"${deleting.name}" and its delivery history will be deleted.`
    : "";

  const columns: TableColumn<ChannelRecord>[] = [
    {
      key: "name",
      header: "Name",
      render: (channel) => (
        <div className="font-medium">
          {channel.name}
          {channel.warnings.map((w) => (
            <HelperText key={w} className="mt-1 font-normal text-amber-700 dark:text-amber-400">
              {w}
            </HelperText>
          ))}
          {channel.secrets_error && (
            <HelperText className="mt-1 font-normal !text-red-600 dark:!text-red-400">
              {channel.secrets_error}
            </HelperText>
          )}
        </div>
      ),
    },
    { key: "type", header: "Type", render: (channel) => TYPE_LABEL[channel.type] },
    {
      key: "enabled",
      header: "Enabled",
      render: (channel) => (
        <Switch
          checked={channel.enabled}
          disabled={save.isPending}
          onChange={() => toggle(channel)}
          aria-label={`${channel.enabled ? "Disable" : "Enable"} ${channel.name}`}
        />
      ),
    },
    {
      key: "test",
      header: "Last test",
      render: (channel) => {
        const inline = results[channel.id];
        if (inline) {
          return (
            <span className={inline.ok ? "text-green-700 dark:text-green-400" : "text-red-600 dark:text-red-400"}>
              {inline.ok ? "Delivered" : `Failed: ${inline.message}`}
            </span>
          );
        }
        if (!channel.last_test) return <span className="text-app-muted">Never tested</span>;
        const badge = (
          <Badge
            badgeContent={channel.last_test.ok ? "OK" : "Failed"}
            color={channel.last_test.ok ? "success" : "danger"}
          />
        );
        return (
          <span className="inline-flex items-center gap-2 text-xs">
            {channel.last_test.message ? <Tooltip text={channel.last_test.message}>{badge}</Tooltip> : badge}
            <span className="text-app-muted">{formatTime(channel.last_test.at)}</span>
          </span>
        );
      },
    },
    {
      key: "jobs",
      header: "Jobs",
      render: (channel) => <span className="text-xs">{channel.jobs.map((j) => j.name).join(", ") || "-"}</span>,
    },
    {
      key: "actions",
      header: "Actions",
      align: "right",
      render: (channel) => {
        const sending = test.isPending && test.variables === channel.id;
        return (
          <div className="flex justify-end gap-1">
            <Button variant="flat" disabled={sending} onClick={() => sendTest(channel)}>
              <SendIcon className="h-4 w-4" /> Send test
            </Button>
            <Button variant="flat" onClick={() => setEventsFor(channel)}>
              <HistoryIcon className="h-4 w-4" /> Events
            </Button>
            <Button
              variant="flat"
              onClick={() => setEditor({ open: true, channel })}
              aria-label={`Edit ${channel.name}`}
            >
              <PencilIcon className="h-4 w-4" /> Edit
            </Button>
            <Button variant="flat" onClick={() => setDeleting(channel)} aria-label={`Delete ${channel.name}`}>
              <Trash2Icon className="h-4 w-4" /> Delete
            </Button>
          </div>
        );
      },
    },
  ];

  return (
    <div className="space-y-6">
      <PageHeader
        title="Notifications"
        subtitle="Where failures, recoveries, overdue jobs and system warnings are sent. Pick channels per job on the Jobs page; system warnings go to every enabled channel."
      >
        <Button onClick={() => setEditor({ open: true, channel: null })}>
          <PlusIcon className="h-4 w-4" /> Add channel
        </Button>
      </PageHeader>

      <Card padded={false}>
        {channels.isPending && <Loader variant="inline" label="Loading channels..." className="p-6" />}
        {channels.isError && (
          <Alert tone="danger">
            <div className="flex items-center justify-between gap-4">
              <span>Could not load channels: {channels.error.message}</span>
              <Button variant="secondary" onClick={() => void channels.refetch()}>
                Retry
              </Button>
            </div>
          </Alert>
        )}
        {channels.isSuccess && (
          <Table
            columns={columns}
            rows={channels.data}
            empty="No channels yet. Add Slack, email or a webhook to be told when a backup fails."
          />
        )}
      </Card>

      {eventsFor && <EventsCard key={eventsFor.id} channel={eventsFor} onClose={() => setEventsFor(null)} />}

      <ChannelDialog
        open={editor.open}
        channel={editor.channel}
        onClose={() => setEditor({ open: false, channel: null })}
      />
      <ConfirmDialog
        open={deleting !== null}
        title="Delete notification channel?"
        description={deleteText}
        confirmLabel="Delete"
        pending={remove.isPending}
        onConfirm={confirmDelete}
        onCancel={() => setDeleting(null)}
      />
    </div>
  );
}

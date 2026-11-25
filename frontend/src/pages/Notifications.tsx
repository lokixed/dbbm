import { useState } from "react";
import { Link } from "react-router-dom";
import Alert from "xedonium/Alert";
import Button from "xedonium/Button";
import Card from "xedonium/Card";
import HelperText from "xedonium/HelperText";
import PageHeader from "xedonium/PageHeader";
import Switch from "xedonium/Switch";
import DataGrid, { type DataGridColumn } from "xedonium/DataGrid";
import HistoryIcon from "xedonium/icons/History";
import PencilIcon from "xedonium/icons/Pencil";
import PlusIcon from "xedonium/icons/Plus";
import SendIcon from "xedonium/icons/Send";
import Trash2Icon from "xedonium/icons/Trash2";
import RelativeTime from "xedonium/RelativeTime";
import {
  useChannelEvents,
  useChannels,
  useDeleteChannel,
  useSaveChannel,
  useTestChannel,
} from "@/api/notifications";
import type { ChannelRecord, NotificationEvent } from "@/api/types";
import { RowMenu } from "@/components/RowMenu";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { ChannelDialog } from "@/components/notifications/ChannelDialog";
import { ChannelTypeLabel } from "@/components/ChannelTypeIcon";
import { CHANNEL_TYPE_NAME } from "@/lib/channelType";
import { LastTested, StatusBadge } from "@/components/StatusBadge";
import { EventStatusBadge } from "@/components/notifications/EventStatusBadge";
import { useToast } from "@/components/toast";
import { LogoLoader } from "@/components/LogoLoader";
import { channelToValues, toChannelInput } from "@/lib/channelForm";


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
  const events = useChannelEvents(channel.id);
  return (
    <Card
      title={`Delivery events: ${channel.name}`}
      actions={
        <Button variant="flat" onClick={onClose}>
          Close
        </Button>
      }
    >
      {events.isPending && <LogoLoader label="Loading events" />}
      {events.isError && <Alert tone="danger">Could not load events: {events.error.message}</Alert>}
      {events.isSuccess && (
        <DataGrid
          density="compact"
          pageSize={10}
          pageSizeOptions={[10, 20, 50, 100]}
          hideFooterWhenSinglePage
          columns={EVENT_COLUMNS}
          rows={events.data.items}
          empty="No messages sent through this channel yet."
        />
      )}
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

  const columns: DataGridColumn<ChannelRecord>[] = [
    {
      key: "name", sortable: true, accessor: (c) => c.name,
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
    { key: "type", sortable: true, accessor: (c) => CHANNEL_TYPE_NAME[c.type], header: "Type", render: (channel) => <ChannelTypeLabel type={channel.type} /> },
    {
      key: "enabled", sortable: true, accessor: (c) => (c.enabled ? 1 : 0),
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
      key: "test", sortable: true, accessor: (c) => (c.last_test ? (c.last_test.ok ? "OK" : "Failed") : "Never tested"),
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
        return <StatusBadge lastTest={channel.last_test} />;
      },
    },
    { key: "tested", sortable: true, accessor: (c) => c.last_test?.at ?? "", header: "Tested", render: (channel) => <LastTested lastTest={channel.last_test} /> },
    {
      key: "jobs", sortable: true, accessor: (c) => c.jobs.map((j) => j.name).join(", "),
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
          <RowMenu
            label={`Actions for ${channel.name}`}
            items={[
              { key: "test", label: "Send test", icon: <SendIcon className="h-4 w-4" />, disabled: sending, onClick: () => sendTest(channel) },
              { key: "events", label: "Events", icon: <HistoryIcon className="h-4 w-4" />, onClick: () => setEventsFor(channel) },
              { key: "edit", label: "Edit", icon: <PencilIcon className="h-4 w-4" />, onClick: () => setEditor({ open: true, channel }) },
              { key: "delete", label: "Delete", tone: "danger", icon: <Trash2Icon className="h-4 w-4" />, onClick: () => setDeleting(channel) },
            ]}
          />
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

      {channels.isPending && <LogoLoader label="Loading channels" />}
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
        <DataGrid
          density="compact"
          pageSize={10}
          pageSizeOptions={[10, 20, 50, 100]}
          searchable
          hideFooterWhenSinglePage
          columns={columns}
          rows={channels.data}
          empty="No channels yet. Add Slack, email or a webhook to be told when a backup fails."
        />
      )}

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

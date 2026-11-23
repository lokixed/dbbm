import { History, Loader2, Pencil, Plus, Send, Trash2 } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import {
  EVENTS_PAGE_SIZE,
  useChannelEvents,
  useChannels,
  useDeleteChannel,
  useSaveChannel,
  useTestChannel,
} from "@/api/notifications";
import type { ChannelRecord } from "@/api/types";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { ChannelDialog } from "@/components/notifications/ChannelDialog";
import { EventStatusBadge } from "@/components/notifications/EventStatusBadge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Switch } from "@/components/ui/switch";
import { TBody, TD, TH, THead, Table, TR } from "@/components/ui/table";
import { useToast } from "@/components/ui/toast";
import { channelToValues, toChannelInput } from "@/lib/channelForm";
import { formatTime } from "@/lib/format";

const TYPE_LABEL = { slack: "Slack", email: "Email", webhook: "Webhook" } as const;

function EventsCard({ channel, onClose }: { channel: ChannelRecord; onClose: () => void }) {
  const [page, setPage] = useState(0);
  const events = useChannelEvents(channel.id, page);
  const pages = events.data ? Math.max(1, Math.ceil(events.data.total / EVENTS_PAGE_SIZE)) : 1;
  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between space-y-0 pb-3">
        <CardTitle className="text-base">Delivery events: {channel.name}</CardTitle>
        <Button variant="ghost" size="sm" onClick={onClose}>
          Close
        </Button>
      </CardHeader>
      <CardContent className="space-y-3 p-0 pb-4">
        {events.isPending && (
          <p className="flex items-center gap-2 px-6 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" /> Loading events...
          </p>
        )}
        {events.isError && (
          <p role="alert" className="px-6 text-sm text-destructive">
            Could not load events: {events.error.message}
          </p>
        )}
        {events.isSuccess && events.data.items.length === 0 && (
          <p className="px-6 text-sm text-muted-foreground">No messages sent through this channel yet.</p>
        )}
        {events.isSuccess && events.data.items.length > 0 && (
          <>
            <Table>
              <THead>
                <tr>
                  <TH>Time</TH>
                  <TH>Event</TH>
                  <TH>Status</TH>
                  <TH>Attempts</TH>
                  <TH>Error</TH>
                </tr>
              </THead>
              <TBody>
                {events.data.items.map((event) => (
                  <TR key={event.id}>
                    <TD className="whitespace-nowrap text-xs">{formatTime(event.created_at)}</TD>
                    <TD>
                      {event.event_type.replace("_", " ")}
                      {event.run_id && (
                        <Link className="ml-2 text-xs underline" to={`/runs/${event.run_id}`}>
                          run
                        </Link>
                      )}
                    </TD>
                    <TD>
                      <EventStatusBadge status={event.status} />
                    </TD>
                    <TD>{event.attempts}</TD>
                    <TD className="max-w-md text-xs text-muted-foreground">{event.error ?? ""}</TD>
                  </TR>
                ))}
              </TBody>
            </Table>
            <div className="flex items-center justify-end gap-2 px-6 text-sm">
              <span className="text-muted-foreground">
                Page {page + 1} of {pages} ({events.data.total} events)
              </span>
              <Button size="sm" variant="secondary" disabled={page === 0} onClick={() => setPage((p) => p - 1)}>
                Previous
              </Button>
              <Button
                size="sm"
                variant="secondary"
                disabled={page + 1 >= pages}
                onClick={() => setPage((p) => p + 1)}
              >
                Next
              </Button>
            </div>
          </>
        )}
      </CardContent>
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

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Notifications</h1>
          <p className="text-sm text-muted-foreground">
            Where failures, recoveries, overdue jobs and system warnings are sent. Pick channels per job
            on the Jobs page; system warnings go to every enabled channel.
          </p>
        </div>
        <Button onClick={() => setEditor({ open: true, channel: null })}>
          <Plus className="h-4 w-4" /> Add channel
        </Button>
      </div>

      <Card>
        {channels.isPending && (
          <p className="flex items-center gap-2 p-6 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" /> Loading channels...
          </p>
        )}
        {channels.isError && (
          <div role="alert" className="flex items-center justify-between gap-4 p-6 text-sm text-destructive">
            <span>Could not load channels: {channels.error.message}</span>
            <Button variant="secondary" size="sm" onClick={() => void channels.refetch()}>
              Retry
            </Button>
          </div>
        )}
        {channels.isSuccess && channels.data.length === 0 && (
          <p className="p-6 text-sm text-muted-foreground">
            No channels yet. Add Slack, email or a webhook to be told when a backup fails.
          </p>
        )}
        {channels.isSuccess && channels.data.length > 0 && (
          <Table>
            <THead>
              <tr>
                <TH>Name</TH>
                <TH>Type</TH>
                <TH>Enabled</TH>
                <TH>Last test</TH>
                <TH>Jobs</TH>
                <TH className="text-right">Actions</TH>
              </tr>
            </THead>
            <TBody>
              {channels.data.map((channel) => {
                const sending = test.isPending && test.variables === channel.id;
                const inline = results[channel.id];
                return (
                  <TR key={channel.id}>
                    <TD className="font-medium">
                      {channel.name}
                      {channel.warnings.map((w) => (
                        <p key={w} className="mt-1 text-xs font-normal text-amber-700 dark:text-amber-400">
                          {w}
                        </p>
                      ))}
                      {channel.secrets_error && (
                        <p className="mt-1 text-xs font-normal text-destructive">{channel.secrets_error}</p>
                      )}
                    </TD>
                    <TD>{TYPE_LABEL[channel.type]}</TD>
                    <TD>
                      <Switch
                        checked={channel.enabled}
                        disabled={save.isPending}
                        onChange={() => toggle(channel)}
                        label={`${channel.enabled ? "Disable" : "Enable"} ${channel.name}`}
                        hideLabel
                      />
                    </TD>
                    <TD className="max-w-xs text-xs">
                      {inline ? (
                        <span className={inline.ok ? "text-green-700 dark:text-green-400" : "text-destructive"}>
                          {inline.ok ? "Delivered" : `Failed: ${inline.message}`}
                        </span>
                      ) : channel.last_test ? (
                        <span title={channel.last_test.message ?? ""}>
                          <Badge variant={channel.last_test.ok ? "success" : "danger"}>
                            {channel.last_test.ok ? "OK" : "Failed"}
                          </Badge>{" "}
                          <span className="text-muted-foreground">{formatTime(channel.last_test.at)}</span>
                        </span>
                      ) : (
                        <span className="text-muted-foreground">Never tested</span>
                      )}
                    </TD>
                    <TD className="text-xs">{channel.jobs.map((j) => j.name).join(", ") || "-"}</TD>
                    <TD>
                      <div className="flex justify-end gap-1">
                        <Button variant="ghost" size="sm" disabled={sending} onClick={() => sendTest(channel)}>
                          {sending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
                          Send test
                        </Button>
                        <Button variant="ghost" size="sm" onClick={() => setEventsFor(channel)}>
                          <History className="h-4 w-4" /> Events
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => setEditor({ open: true, channel })}
                          aria-label={`Edit ${channel.name}`}
                        >
                          <Pencil className="h-4 w-4" /> Edit
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => setDeleting(channel)}
                          aria-label={`Delete ${channel.name}`}
                        >
                          <Trash2 className="h-4 w-4" /> Delete
                        </Button>
                      </div>
                    </TD>
                  </TR>
                );
              })}
            </TBody>
          </Table>
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

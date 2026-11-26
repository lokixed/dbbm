import { useQueryClient } from "@tanstack/react-query";
import PauseIcon from "xedonium/icons/Pause";
import PowerIcon from "xedonium/icons/Power";
import SendIcon from "xedonium/icons/Send";
import Trash2Icon from "xedonium/icons/Trash2";
import { api } from "@/api/client";
import { channelKeys } from "@/api/notifications";
import type { ChannelRecord } from "@/api/types";
import { BulkBar, type BulkAction } from "@/components/BulkBar";
import { channelToValues, toChannelInput } from "@/lib/channelForm";
import { plural } from "@/lib/format";

function toggle(enabled: boolean): BulkAction<ChannelRecord> {
  const verb = enabled ? "Enable" : "Disable";
  return {
    key: verb.toLowerCase(),
    label: verb,
    progressLabel: enabled ? "Enabling" : "Disabling",
    icon: enabled ? <PowerIcon className="h-4 w-4" /> : <PauseIcon className="h-4 w-4" />,
    eligible: (rows) => rows.filter((c) => c.enabled !== enabled),
    skippedHint: `Channels that are already ${enabled ? "enabled" : "disabled"} are skipped.`,
    // The same request as the row's switch: the saved settings with `enabled` changed, secrets kept.
    perform: async (channel) => {
      await api(`/api/notifications/${channel.id}`, {
        method: "PUT",
        body: toChannelInput({ ...channelToValues(channel), enabled }, {}),
      });
      return "done";
    },
    summarize: (counts, failed) => ({
      title: `${verb} finished`,
      line: `${plural(counts.done ?? 0, "channel")} ${enabled ? "enabled" : "disabled"}${failed ? `, ${failed} failed` : ""}.`,
      ok: true,
    }),
  };
}

const ACTIONS: BulkAction<ChannelRecord>[] = [
  {
    key: "test",
    label: "Send test",
    progressLabel: "Sending test",
    icon: <SendIcon className="h-4 w-4" />,
    eligible: (rows) => rows,
    perform: async (channel) =>
      (await api<{ ok: boolean }>(`/api/notifications/${channel.id}/test`, { method: "POST" })).ok
        ? "delivered"
        : "failed",
    summarize: (counts, failed) => ({
      title: "Test messages sent",
      line: [
        `${counts.delivered ?? 0} delivered`,
        counts.failed ? `${counts.failed} failed` : null,
        failed ? `${failed} could not be sent` : null,
      ]
        .filter(Boolean)
        .join(", ")
        .concat(". The Status column shows each result."),
      ok: !counts.failed,
    }),
  },
  toggle(true),
  toggle(false),
  {
    key: "delete",
    label: "Delete",
    progressLabel: "Deleting",
    icon: <Trash2Icon className="h-4 w-4" />,
    variant: "danger",
    eligible: (rows) => rows,
    confirm: (rows) => {
      const jobs = [...new Set(rows.flatMap((c) => c.jobs.map((j) => j.name)))];
      return {
        title: `Delete ${plural(rows.length, "channel")}?`,
        description:
          (jobs.length > 0
            ? `They are removed from ${plural(jobs.length, "job")}: ${jobs.join(", ")}. `
            : "") + "Their delivery history is deleted too. This cannot be undone.",
        confirmLabel: `Delete ${plural(rows.length, "channel")}`,
      };
    },
    perform: async (channel) => {
      await api<void>(`/api/notifications/${channel.id}`, { method: "DELETE" });
      return "deleted";
    },
    summarize: (counts, failed) => ({
      title: "Delete finished",
      line: `${plural(counts.deleted ?? 0, "channel")} deleted${failed ? `, ${failed} failed` : ""}.`,
      ok: true,
    }),
  },
];

/** Test, enable, disable or delete the ticked notification channels at once. */
export function ChannelBulkActions({ selected, onClear }: { selected: ChannelRecord[]; onClear: () => void }) {
  const queryClient = useQueryClient();
  return (
    <BulkBar
      selected={selected}
      actions={ACTIONS}
      describe={(c) => c.name}
      onClear={onClear}
      onFinished={() =>
        Promise.all([
          queryClient.invalidateQueries({ queryKey: channelKeys.all }),
          queryClient.invalidateQueries({ queryKey: ["jobs"] }),
        ])
      }
    />
  );
}

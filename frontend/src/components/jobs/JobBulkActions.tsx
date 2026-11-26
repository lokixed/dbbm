import { useQueryClient } from "@tanstack/react-query";
import PauseIcon from "xedonium/icons/Pause";
import PlayIcon from "xedonium/icons/Play";
import PowerIcon from "xedonium/icons/Power";
import Trash2Icon from "xedonium/icons/Trash2";
import { api } from "@/api/client";
import { jobKeys } from "@/api/jobs";
import { runKeys } from "@/api/runs";
import type { JobRecord } from "@/api/types";
import { BulkBar, type BulkAction } from "@/components/BulkBar";
import { plural } from "@/lib/format";

function toggle(enabled: boolean): BulkAction<JobRecord> {
  const verb = enabled ? "Enable" : "Disable";
  return {
    key: verb.toLowerCase(),
    label: verb,
    progressLabel: enabled ? "Enabling" : "Disabling",
    icon: enabled ? <PowerIcon className="h-4 w-4" /> : <PauseIcon className="h-4 w-4" />,
    eligible: (rows) => rows.filter((j) => j.enabled !== enabled),
    skippedHint: `Jobs that are already ${enabled ? "enabled" : "disabled"} are skipped.`,
    perform: async (job) => {
      await api(`/api/jobs/${job.id}/${enabled ? "enable" : "disable"}`, { method: "POST" });
      return "done";
    },
    summarize: (counts, failed) => ({
      title: `${verb} finished`,
      line: `${plural(counts.done ?? 0, "job")} ${enabled ? "enabled" : "disabled"}${failed ? `, ${failed} failed` : ""}.`,
      ok: true,
    }),
  };
}

const ACTIONS: BulkAction<JobRecord>[] = [
  {
    key: "run",
    label: "Run now",
    progressLabel: "Starting",
    icon: <PlayIcon className="h-4 w-4" />,
    eligible: (rows) => rows,
    perform: async (job) => {
      await api(`/api/jobs/${job.id}/run`, { method: "POST" });
      return "started";
    },
    summarize: (counts, failed) => ({
      title: "Runs started",
      line: `${plural(counts.started ?? 0, "run")} started${failed ? `, ${failed} could not start` : ""}. They wait in the queue if more than the concurrency limit are running.`,
      ok: true,
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
    confirm: (rows) => ({
      title: `Delete ${plural(rows.length, "job")}?`,
      description:
        "The jobs and their schedules are removed. Their run history and existing backup files are kept. Jobs with a run in progress are not deleted.",
      confirmLabel: `Delete ${plural(rows.length, "job")}`,
    }),
    perform: async (job) => {
      await api<void>(`/api/jobs/${job.id}`, { method: "DELETE" });
      return "deleted";
    },
    summarize: (counts, failed) => ({
      title: "Delete finished",
      line: `${plural(counts.deleted ?? 0, "job")} deleted${failed ? `, ${failed} failed` : ""}.`,
      ok: true,
    }),
  },
];

/** Run, enable, disable or delete the ticked jobs at once. */
export function JobBulkActions({ selected, onClear }: { selected: JobRecord[]; onClear: () => void }) {
  const queryClient = useQueryClient();
  return (
    <BulkBar
      selected={selected}
      actions={ACTIONS}
      describe={(j) => j.name}
      onClear={onClear}
      onFinished={() =>
        Promise.all([
          queryClient.invalidateQueries({ queryKey: jobKeys.all }),
          queryClient.invalidateQueries({ queryKey: runKeys.all }),
          queryClient.invalidateQueries({ queryKey: ["dashboard"] }),
        ])
      }
    />
  );
}

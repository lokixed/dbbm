import { useQueryClient } from "@tanstack/react-query";
import RotateCcwIcon from "xedonium/icons/RotateCcw";
import XCircleIcon from "xedonium/icons/XCircle";
import { api } from "@/api/client";
import { runKeys } from "@/api/runs";
import { ACTIVE_STATUSES, type RunSummary } from "@/api/types";
import { BulkBar, type BulkAction } from "@/components/BulkBar";
import { plural } from "@/lib/format";

const ACTIONS: BulkAction<RunSummary>[] = [
  {
    key: "cancel",
    label: "Cancel selected",
    progressLabel: "Cancelling",
    icon: <XCircleIcon className="h-4 w-4" />,
    variant: "danger",
    eligible: (rows) => rows.filter((r) => ACTIVE_STATUSES.includes(r.status)),
    skippedHint: "Only queued or running runs can be cancelled; the others are skipped.",
    confirm: (rows) => ({
      title: `Cancel ${plural(rows.length, "run")}?`,
      description:
        "Running steps are stopped and their partial files removed. Backups already stored by these runs are kept.",
      confirmLabel: `Cancel ${plural(rows.length, "run")}`,
    }),
    perform: async (run) => {
      await api(`/api/runs/${run.id}/cancel`, { method: "POST" });
      return "cancelled";
    },
    summarize: (counts, failed) => ({
      title: "Cancel finished",
      line: `${plural(counts.cancelled ?? 0, "run")} cancelled${failed ? `, ${failed} not cancelled` : ""}.`,
      ok: true,
    }),
  },
  {
    key: "retry",
    label: "Retry failed destinations",
    progressLabel: "Starting retry",
    icon: <RotateCcwIcon className="h-4 w-4" />,
    eligible: (rows) => rows.filter((r) => r.status === "partial" && r.kind === "backup"),
    skippedHint: "Only partial backup runs can be retried; the others are skipped.",
    perform: async (run) => {
      await api(`/api/runs/${run.id}/retry`, { method: "POST" });
      return "started";
    },
    summarize: (counts, failed) => ({
      title: "Retries started",
      line: `${counts.started ?? 0} ${(counts.started ?? 0) === 1 ? "retry" : "retries"} started${failed ? `, ${failed} could not start` : ""}.`,
      ok: true,
    }),
  },
];

/** Cancel or retry the ticked runs at once. */
export function RunBulkActions({ selected, onClear }: { selected: RunSummary[]; onClear: () => void }) {
  const queryClient = useQueryClient();
  return (
    <BulkBar
      selected={selected}
      actions={ACTIONS}
      describe={(r) => r.job_name}
      onClear={onClear}
      onFinished={() =>
        Promise.all([
          queryClient.invalidateQueries({ queryKey: runKeys.all }),
          queryClient.invalidateQueries({ queryKey: ["jobs"] }),
          queryClient.invalidateQueries({ queryKey: ["dashboard"] }),
        ])
      }
    />
  );
}

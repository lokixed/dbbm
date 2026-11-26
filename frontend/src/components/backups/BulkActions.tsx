import { useQueryClient } from "@tanstack/react-query";
import ShieldCheckIcon from "xedonium/icons/ShieldCheck";
import Trash2Icon from "xedonium/icons/Trash2";
import { api } from "@/api/client";
import { backupKeys, type Backup, type VerifyResult } from "@/api/backups";
import { BulkBar, type BulkAction } from "@/components/BulkBar";
import { plural } from "@/lib/format";

const ACTIONS: BulkAction<Backup>[] = [
  {
    key: "verify",
    label: "Verify selected",
    progressLabel: "Verifying",
    icon: <ShieldCheckIcon className="h-4 w-4" />,
    eligible: (rows) => rows.filter((b) => !b.missing),
    skippedHint: "Backups already marked missing are skipped.",
    perform: async (backup) =>
      (await api<VerifyResult>(`/api/backups/${backup.id}/verify`, { method: "POST" })).status,
    summarize: (counts, failed) => ({
      title: "Verification finished",
      line:
        [
          `${counts.ok ?? 0} OK`,
          counts.missing ? `${counts.missing} missing` : null,
          counts.corrupt ? `${counts.corrupt} corrupt` : null,
          failed ? `${failed} could not be checked` : null,
        ]
          .filter(Boolean)
          .join(", ") + ".",
      ok: !counts.missing && !counts.corrupt,
    }),
  },
  {
    key: "delete",
    label: "Delete selected",
    progressLabel: "Deleting",
    icon: <Trash2Icon className="h-4 w-4" />,
    variant: "danger",
    eligible: (rows) => rows,
    confirm: (rows) => {
      const destinations = new Set(rows.map((b) => b.destination_name ?? b.destination_id)).size;
      return {
        title: `Delete ${plural(rows.length, "backup")}?`,
        description: `This permanently deletes ${plural(rows.length, "file")} and their manifests from ${plural(
          destinations,
          "destination",
        )}. Other copies of the same runs are not touched. This cannot be undone.`,
        confirmLabel: `Delete ${plural(rows.length, "backup")}`,
      };
    },
    perform: async (backup) => {
      await api<void>(`/api/backups/${backup.id}`, { method: "DELETE" });
      return "deleted";
    },
    summarize: (counts, failed) => ({
      title: "Delete finished",
      line: `${plural(counts.deleted ?? 0, "backup")} deleted${failed ? `, ${failed} failed` : ""}.`,
      ok: true,
    }),
  },
];

/** Verify or delete the ticked backups at once. */
export function BulkActions({ selected, onClear }: { selected: Backup[]; onClear: () => void }) {
  const queryClient = useQueryClient();
  return (
    <BulkBar
      selected={selected}
      actions={ACTIONS}
      describe={(b) => b.path}
      onClear={onClear}
      onFinished={() => queryClient.invalidateQueries({ queryKey: backupKeys.all })}
    />
  );
}

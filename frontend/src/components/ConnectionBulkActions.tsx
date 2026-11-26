import { useQueryClient } from "@tanstack/react-query";
import PlayIcon from "xedonium/icons/Play";
import Trash2Icon from "xedonium/icons/Trash2";
import { api } from "@/api/client";
import type { TestResult } from "@/api/types";
import { BulkBar, type BulkAction } from "@/components/BulkBar";
import { plural } from "@/lib/format";

type Kind = "source" | "destination";

interface Row {
  id: string;
  name: string;
}

const API: Record<Kind, string> = { source: "/api/sources", destination: "/api/destinations" };

const DELETE_TEXT: Record<Kind, string> = {
  source:
    "They and their saved credentials are permanently removed. Sources used by a job are refused and stay.",
  destination:
    "They are removed from DBBM; files already written are not deleted. Destinations used by a job or holding recorded backups are refused and stay.",
};

function actions(kind: Kind): BulkAction<Row>[] {
  return [
    {
      key: "test",
      label: "Test connection",
      progressLabel: "Testing",
      icon: <PlayIcon className="h-4 w-4" />,
      eligible: (rows) => rows,
      perform: async (row) =>
        (await api<TestResult>(`${API[kind]}/${row.id}/test`, { method: "POST" })).ok ? "ok" : "failed",
      summarize: (counts, failed) => ({
        title: "Connection tests finished",
        line: [
          `${counts.ok ?? 0} OK`,
          counts.failed ? `${counts.failed} failed` : null,
          failed ? `${failed} could not be tested` : null,
        ]
          .filter(Boolean)
          .join(", ")
          .concat(". The Status column shows each result."),
        ok: !counts.failed,
      }),
    },
    {
      key: "delete",
      label: "Delete",
      progressLabel: "Deleting",
      icon: <Trash2Icon className="h-4 w-4" />,
      variant: "danger",
      eligible: (rows) => rows,
      confirm: (rows) => ({
        title: `Delete ${plural(rows.length, kind)}?`,
        description: DELETE_TEXT[kind],
        confirmLabel: `Delete ${plural(rows.length, kind)}`,
      }),
      perform: async (row) => {
        await api<void>(`${API[kind]}/${row.id}`, { method: "DELETE" });
        return "deleted";
      },
      summarize: (counts, failed) => ({
        title: "Delete finished",
        line: `${plural(counts.deleted ?? 0, kind)} deleted${failed ? `, ${failed} refused` : ""}.`,
        ok: true,
      }),
    },
  ];
}

const ACTIONS: Record<Kind, BulkAction<Row>[]> = { source: actions("source"), destination: actions("destination") };

/** Test or delete the ticked sources or destinations at once. */
export function ConnectionBulkActions({
  kind,
  selected,
  onClear,
}: {
  kind: Kind;
  selected: Row[];
  onClear: () => void;
}) {
  const queryClient = useQueryClient();
  return (
    <BulkBar
      selected={selected}
      actions={ACTIONS[kind]}
      describe={(row) => row.name}
      onClear={onClear}
      onFinished={() =>
        Promise.all([
          queryClient.invalidateQueries({ queryKey: [kind === "source" ? "sources" : "destinations"] }),
          queryClient.invalidateQueries({ queryKey: ["dashboard"] }),
        ])
      }
    />
  );
}

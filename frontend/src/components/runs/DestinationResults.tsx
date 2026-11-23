import { Link } from "react-router-dom";
import Badge, { type BadgeColor } from "xedonium/Badge";
import Table, { type TableColumn } from "xedonium/Table";
import type { DestinationOutcome, StepResults, UnitResult } from "@/api/types";
import { errorClassLabel } from "@/lib/errorClass";
import { formatBytes, formatMs } from "@/lib/format";

const STATUS_COLOR: Record<string, BadgeColor> = { ok: "success", failed: "danger", skipped: "secondary" };

interface Row {
  id: string;
  unit: UnitResult;
  outcome: DestinationOutcome;
}

function stepMs(outcome: DestinationOutcome, name: "put" | "verify"): string {
  const ms = outcome.steps?.find((s) => s.name === name)?.duration_ms;
  return ms === null || ms === undefined ? "-" : formatMs(ms);
}

const COLUMNS: TableColumn<Row>[] = [
  {
    key: "database",
    header: "Database",
    render: ({ unit }) => (
      <span className="font-medium">{unit.kind === "globals" ? "Roles and globals" : unit.database}</span>
    ),
  },
  {
    key: "destination",
    header: "Destination",
    render: ({ outcome }) => (
      <>
        {outcome.destination_name ?? "-"}
        {outcome.type && (
          <span className="ml-1 text-xs text-app-muted">
            ({outcome.type === "gdrive" ? "Google Drive" : "local"})
          </span>
        )}
      </>
    ),
  },
  {
    key: "status",
    header: "Status",
    render: ({ outcome }) => (
      <div className="flex flex-wrap items-center gap-1">
        <Badge badgeContent={outcome.status} color={STATUS_COLOR[outcome.status] ?? "secondary"} />
        {outcome.retried_by && (
          <Link className="text-xs underline" to={`/runs/${outcome.retried_by}`}>
            by retry
          </Link>
        )}
      </div>
    ),
  },
  {
    key: "bytes",
    header: "Bytes",
    align: "right",
    render: ({ outcome }) => (outcome.bytes === undefined ? "-" : formatBytes(outcome.bytes)),
  },
  { key: "upload", header: "Upload", align: "right", render: ({ outcome }) => stepMs(outcome, "put") },
  { key: "verify", header: "Verify", align: "right", render: ({ outcome }) => stepMs(outcome, "verify") },
  {
    key: "error",
    header: "Error",
    render: ({ outcome }) => (
      <span className="break-words text-xs">
        {outcome.error_class && (
          <Badge
            badgeContent={errorClassLabel(outcome.error_class)}
            color="warning"
            className="mr-1"
          />
        )}
        {outcome.error ?? (outcome.previous_error ? `Earlier: ${outcome.previous_error}` : "")}
      </span>
    ),
  },
];

export function DestinationResults({ results }: { results: StepResults }) {
  const rows: Row[] = (results.units ?? []).flatMap((unit) =>
    unit.destinations.map((outcome, index) => ({
      id: `${unit.database}-${unit.kind}-${outcome.destination_id ?? index}`,
      unit,
      outcome,
    })),
  );
  if (rows.length === 0) return null;
  return <Table columns={COLUMNS} rows={rows} />;
}

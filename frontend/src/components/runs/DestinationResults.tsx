import { Link } from "react-router-dom";
import type { DestinationOutcome, StepResults } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { TBody, TD, TH, THead, Table, TR } from "@/components/ui/table";
import { errorClassLabel } from "@/lib/errorClass";
import { formatBytes, formatMs } from "@/lib/format";

const STATUS_VARIANT = { ok: "success", failed: "danger", skipped: "neutral" } as const;

function stepMs(outcome: DestinationOutcome, name: "put" | "verify"): string {
  const ms = outcome.steps?.find((s) => s.name === name)?.duration_ms;
  return ms === null || ms === undefined ? "-" : formatMs(ms);
}

export function DestinationResults({ results }: { results: StepResults }) {
  const rows = (results.units ?? []).flatMap((unit) =>
    unit.destinations.map((outcome, index) => ({ unit, outcome, index })),
  );
  if (rows.length === 0) return null;
  return (
    <Table>
      <THead>
        <tr>
          <TH>Database</TH>
          <TH>Destination</TH>
          <TH>Status</TH>
          <TH className="text-right">Bytes</TH>
          <TH className="text-right">Upload</TH>
          <TH className="text-right">Verify</TH>
          <TH>Error</TH>
        </tr>
      </THead>
      <TBody>
        {rows.map(({ unit, outcome, index }) => (
          <TR key={`${unit.database}-${unit.kind}-${outcome.destination_id ?? index}`}>
            <TD className="font-medium">{unit.kind === "globals" ? "Roles and globals" : unit.database}</TD>
            <TD>
              {outcome.destination_name ?? "-"}
              {outcome.type && (
                <span className="ml-1 text-xs text-muted-foreground">
                  ({outcome.type === "gdrive" ? "Google Drive" : "local"})
                </span>
              )}
            </TD>
            <TD>
              <div className="flex flex-wrap items-center gap-1">
                <Badge variant={STATUS_VARIANT[outcome.status]}>{outcome.status}</Badge>
                {outcome.retried_by && (
                  <Link className="text-xs underline" to={`/runs/${outcome.retried_by}`}>
                    by retry
                  </Link>
                )}
              </div>
            </TD>
            <TD className="whitespace-nowrap text-right">
              {outcome.bytes === undefined ? "-" : formatBytes(outcome.bytes)}
            </TD>
            <TD className="whitespace-nowrap text-right">{stepMs(outcome, "put")}</TD>
            <TD className="whitespace-nowrap text-right">{stepMs(outcome, "verify")}</TD>
            <TD className="max-w-sm break-words text-xs">
              {outcome.error_class && (
                <Badge variant="warning" className="mr-1">
                  {errorClassLabel(outcome.error_class)}
                </Badge>
              )}
              {outcome.error ?? (outcome.previous_error ? `Earlier: ${outcome.previous_error}` : "")}
            </TD>
          </TR>
        ))}
      </TBody>
    </Table>
  );
}

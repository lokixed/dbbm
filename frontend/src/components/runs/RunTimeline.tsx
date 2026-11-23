import Alert from "xedonium/Alert";
import HelperText from "xedonium/HelperText";
import AlertTriangleIcon from "xedonium/icons/AlertTriangle";
import CheckCircleIcon from "xedonium/icons/CheckCircle";
import CircleDotIcon from "xedonium/icons/CircleDot";
import MinusIcon from "xedonium/icons/Minus";
import XCircleIcon from "xedonium/icons/XCircle";
import type {
  DestinationOutcome,
  StepEntry,
  StepResults,
  SubStepEntry,
  UnitResult,
} from "@/api/types";
import { errorClassLabel } from "@/lib/errorClass";
import { formatBytes, formatMs } from "@/lib/format";

function StatusIcon({ status }: { status: string }) {
  switch (status) {
    case "ok":
      return <CheckCircleIcon className="h-4 w-4 shrink-0 text-green-600" aria-label="ok" />;
    case "failed":
      return <XCircleIcon className="h-4 w-4 shrink-0 text-red-600 dark:text-red-400" aria-label="failed" />;
    case "warning":
      return <AlertTriangleIcon className="h-4 w-4 shrink-0 text-amber-600" aria-label="warning" />;
    case "running":
      return <CircleDotIcon className="h-4 w-4 shrink-0 animate-pulse text-blue-500" aria-label="running" />;
    case "skipped":
    case "cancelled":
      return <MinusIcon className="h-4 w-4 shrink-0 text-app-muted" aria-label={status} />;
    default:
      return <CircleDotIcon className="h-4 w-4 shrink-0 text-app-muted" aria-label="pending" />;
  }
}

const STEP_LABELS: Record<string, string> = {
  preflight: "Preflight checks",
  backup: "Dump, store and verify",
  cleanup: "Cleanup",
};

const SUB_STEP_LABELS: Record<string, string> = {
  dump: "Dump",
  export: "Start export",
  poll: "Wait for export",
  download: "Download export",
  pack: "Pack into tar.gz",
  "cleanup-staging": "Delete staging export",
};

function SubStep({ step }: { step: SubStepEntry }) {
  return (
    <li className="flex items-start gap-2 text-sm">
      <StatusIcon status={step.status} />
      <div className="min-w-0">
        <p>
          {SUB_STEP_LABELS[step.name] ?? step.name}
          {step.duration_ms !== null && (
            <span className="ml-2 text-app-muted">{formatMs(step.duration_ms)}</span>
          )}
        </p>
        {step.detail && (
          <p
            className={`break-words ${
              step.status === "failed"
                ? "text-red-600 dark:text-red-400"
                : step.status === "warning"
                  ? "text-amber-700 dark:text-amber-400"
                  : "text-app-muted"
            }`}
          >
            {step.detail}
          </p>
        )}
      </div>
    </li>
  );
}

function Step({ step }: { step: StepEntry }) {
  return (
    <li className="flex items-start gap-3">
      <StatusIcon status={step.status} />
      <div className="min-w-0 text-sm">
        <p className="font-medium">
          {STEP_LABELS[step.name] ?? step.name}
          {step.duration_ms !== null && (
            <span className="ml-2 font-normal text-app-muted">{formatMs(step.duration_ms)}</span>
          )}
        </p>
        {step.detail && <p className="break-words text-app-muted">{step.detail}</p>}
      </div>
    </li>
  );
}

function Destination({ outcome }: { outcome: DestinationOutcome }) {
  const timings = [
    outcome.store_ms !== undefined ? `stored in ${formatMs(outcome.store_ms)}` : null,
    outcome.verify_ms !== undefined ? `verified in ${formatMs(outcome.verify_ms)}` : null,
  ].filter(Boolean);
  return (
    <li className="flex items-start gap-2 text-sm">
      <StatusIcon status={outcome.status} />
      <div className="min-w-0">
        <p>
          <span className="font-medium">{outcome.destination_name ?? "Destination"}</span>
          {outcome.status === "skipped" && !outcome.error && (
            <span className="ml-2 text-app-muted">skipped because the dump failed</span>
          )}
          {timings.length > 0 && (
            <span className="ml-2 text-app-muted">{timings.join(", ")}</span>
          )}
          {outcome.retried_by && <span className="ml-2 text-app-muted">(stored by a retry)</span>}
        </p>
        {outcome.error && (
          <p className="break-words text-red-600 dark:text-red-400">
            {outcome.error_class && <span className="font-medium">{errorClassLabel(outcome.error_class)}: </span>}
            {outcome.error}
          </p>
        )}
      </div>
    </li>
  );
}

function Unit({ unit }: { unit: UnitResult }) {
  const dumpStatus = unit.dump.status ?? "running";
  return (
    <li className="border border-app-border p-3">
      <div className="flex items-start gap-2 text-sm">
        <StatusIcon status={dumpStatus} />
        <div className="min-w-0">
          <p className="font-medium">
            {unit.kind === "globals" ? "Roles and globals" : unit.database}
            <span className="ml-2 font-normal text-app-muted">
              {unit.dump.size_bytes !== undefined && `dumped ${formatBytes(unit.dump.size_bytes)}`}
              {unit.dump.duration_ms !== undefined && ` in ${formatMs(unit.dump.duration_ms)}`}
              {unit.dump.source && ` (copied from ${unit.dump.source})`}
            </span>
          </p>
          {unit.dump.error && <p className="break-words text-red-600 dark:text-red-400">{unit.dump.error}</p>}
        </div>
      </div>
      {(unit.dump.steps?.length ?? 0) > 0 && (
        <ol className="ml-6 mt-2 space-y-1.5" aria-label="Dump steps">
          {unit.dump.steps?.map((step) => <SubStep key={step.name} step={step} />)}
        </ol>
      )}
      {unit.destinations.length > 0 && (
        <ul className="ml-6 mt-2 space-y-1.5">
          {unit.destinations.map((outcome, index) => (
            <Destination key={outcome.destination_id ?? index} outcome={outcome} />
          ))}
        </ul>
      )}
    </li>
  );
}

export function RunTimeline({ results }: { results: StepResults }) {
  const steps = results.steps ?? [];
  const units = results.units ?? [];
  const warnings = results.warnings ?? [];
  if (steps.length === 0 && units.length === 0) {
    return <HelperText>Nothing has run yet.</HelperText>;
  }
  return (
    <div className="space-y-4">
      <ol className="space-y-3">
        {steps.map((step) => (
          <Step key={step.name} step={step} />
        ))}
      </ol>
      {units.length > 0 && (
        <div className="space-y-2">
          <h3 className="text-sm font-medium">Per database</h3>
          <ul className="space-y-2">
            {units.map((unit) => (
              <Unit key={`${unit.kind}:${unit.database}`} unit={unit} />
            ))}
          </ul>
        </div>
      )}
      {warnings.length > 0 && (
        <Alert tone="warning" title="Warnings">
          <ul className="list-disc space-y-1 pl-5">
            {warnings.map((warning) => (
              <li key={warning} className="break-words">
                {warning}
              </li>
            ))}
          </ul>
        </Alert>
      )}
    </div>
  );
}

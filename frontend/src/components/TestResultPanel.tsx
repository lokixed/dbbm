import { useEffect, useRef } from "react";
import Alert from "xedonium/Alert";
import Badge from "xedonium/Badge";
import Chip from "xedonium/Chip";
import Loader from "xedonium/Loader";
import type { TestResult } from "@/api/types";
import { formatBytes } from "@/lib/format";

interface TestResultPanelProps {
  pending: boolean;
  result: TestResult | null;
  error: string | null;
  /** What the test checks, shown while it runs. */
  pendingDescription?: string;
}

export function TestResultPanel({ pending, result, error, pendingDescription }: TestResultPanelProps) {
  const ref = useRef<HTMLDivElement>(null);
  // The panel sits below a long form: bring it into view when a test starts and when it ends.
  useEffect(() => {
    if (pending || result || error) ref.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [pending, result, error]);

  return (
    <div ref={ref}>
      <PanelBody pending={pending} result={result} error={error} pendingDescription={pendingDescription} />
    </div>
  );
}

function PanelBody({ pending, result, error, pendingDescription }: TestResultPanelProps) {
  if (pending) {
    return (
      <Loader
        variant="card"
        label="Testing connection..."
        description={pendingDescription ?? "Waiting for the result. This can take up to 2 minutes."}
      />
    );
  }
  if (error) return <Alert tone="danger">{error}</Alert>;
  if (!result) return null;

  return (
    <Alert tone={result.ok ? "success" : "danger"} title={result.message}>
      <div className="space-y-2 text-sm" aria-live="polite">
        {result.server_version && <p>Server version: {result.server_version}</p>}
        {result.free_bytes !== null && result.total_bytes !== null && (
          <p>
            Free space: {formatBytes(result.free_bytes)} of {formatBytes(result.total_bytes)}
          </p>
        )}
        {result.checks.length > 1 && (
          <ul className="space-y-1">
            {result.checks.map((check) => (
              <li key={check.name} className="flex items-start gap-2">
                <Badge badgeContent={check.ok ? "OK" : "Failed"} color={check.ok ? "success" : "danger"} />
                <span className="break-words">{check.message}</span>
              </li>
            ))}
          </ul>
        )}
        {result.databases && (
          <div>
            <p>Databases visible ({result.databases.length}):</p>
            <div className="mt-1 flex flex-wrap gap-1">
              {result.databases.map((name) => (
                <Chip key={name} size="sm">
                  {name}
                </Chip>
              ))}
            </div>
          </div>
        )}
      </div>
    </Alert>
  );
}

import { CheckCircle2, Loader2, XCircle } from "lucide-react";
import type { TestResult } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { formatBytes } from "@/lib/format";

interface TestResultPanelProps {
  pending: boolean;
  result: TestResult | null;
  error: string | null;
}

export function TestResultPanel({ pending, result, error }: TestResultPanelProps) {
  if (pending) {
    return (
      <div className="flex items-center gap-2 rounded-md border p-3 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" /> Testing connection...
      </div>
    );
  }
  if (error) {
    return (
      <div role="alert" className="rounded-md border border-destructive/50 p-3 text-sm text-destructive">
        {error}
      </div>
    );
  }
  if (!result) return null;

  return (
    <div className="space-y-2 rounded-md border p-3 text-sm" aria-live="polite">
      <div className="flex items-start gap-2">
        {result.ok ? (
          <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-green-600" />
        ) : (
          <XCircle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
        )}
        <p className="break-words font-medium">{result.message}</p>
      </div>
      {result.server_version && (
        <p className="text-muted-foreground">Server version: {result.server_version}</p>
      )}
      {result.free_bytes !== null && result.total_bytes !== null && (
        <p className="text-muted-foreground">
          Free space: {formatBytes(result.free_bytes)} of {formatBytes(result.total_bytes)}
        </p>
      )}
      {result.checks.length > 1 && (
        <ul className="space-y-1">
          {result.checks.map((check) => (
            <li key={check.name} className="flex items-start gap-2">
              <Badge variant={check.ok ? "success" : "danger"}>{check.ok ? "OK" : "Failed"}</Badge>
              <span className="break-words">{check.message}</span>
            </li>
          ))}
        </ul>
      )}
      {result.databases && (
        <div>
          <p className="text-muted-foreground">Databases visible ({result.databases.length}):</p>
          <div className="mt-1 flex flex-wrap gap-1">
            {result.databases.map((name) => (
              <Badge key={name} variant="outline">
                {name}
              </Badge>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

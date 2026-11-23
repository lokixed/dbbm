import { Loader2, Play, Search } from "lucide-react";
import { useState } from "react";
import type { FieldErrors, UseFormRegister } from "react-hook-form";
import { useRetentionPreview, useRunRetention } from "@/api/destinations";
import type { RetentionPreview, RetentionRunResult } from "@/api/types";
import { FormField } from "@/components/FormField";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { parsePolicyValue, type DestinationFormValues } from "@/lib/destinationForm";
import { formatBytes, formatTime } from "@/lib/format";

export function PreviewSummary({ preview }: { preview: RetentionPreview }) {
  if (preview.delete_runs === 0) {
    return <p className="text-sm text-muted-foreground">Nothing would be deleted. {preview.keep.length} backup(s) kept.</p>;
  }
  return (
    <div className="space-y-2 text-sm">
      <p>
        <strong>{preview.delete_runs}</strong> backup run(s), {preview.delete_artifacts} file(s),{" "}
        {formatBytes(preview.delete_bytes)} would be deleted; {preview.keep.length} kept.
      </p>
      <details>
        <summary className="cursor-pointer text-xs text-muted-foreground">Show what would be deleted</summary>
        <ul className="mt-1 max-h-48 space-y-1 overflow-auto font-mono text-xs">
          {preview.delete.map((copy) => (
            <li key={copy.run_id}>
              <span className="text-muted-foreground">
                {copy.job_name} - {formatTime(copy.started_at)} ({copy.status})
              </span>
              {copy.artifacts.map((path) => (
                <div key={path} className="pl-3">
                  {path}
                </div>
              ))}
            </li>
          ))}
        </ul>
      </details>
    </div>
  );
}

function RunSummary({ result }: { result: RetentionRunResult }) {
  if (result.skipped) {
    return (
      <p role="alert" className="text-sm text-destructive">
        Skipped: {result.skipped}
      </p>
    );
  }
  return (
    <div className="space-y-1 text-sm">
      <p>
        Deleted {result.deleted_runs} backup run(s), {result.deleted_artifacts} file(s),{" "}
        {formatBytes(result.deleted_bytes)}.
      </p>
      {result.failures.length > 0 && (
        <div role="alert" className="text-destructive">
          {result.failures.length} file(s) could not be deleted (retried at the next pass):
          <ul className="font-mono text-xs">
            {result.failures.slice(0, 10).map((f) => (
              <li key={f.path}>
                {f.path}: {f.error}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

interface RetentionSectionProps {
  destinationId: string | null;
  register: UseFormRegister<DestinationFormValues>;
  errors: FieldErrors<DestinationFormValues>;
  keepLast: string;
  keepDays: string;
  /** The policy in the form differs from the saved one. */
  unsaved: boolean;
}

export function RetentionSection({
  destinationId,
  register,
  errors,
  keepLast,
  keepDays,
  unsaved,
}: RetentionSectionProps) {
  const preview = useRetentionPreview();
  const runNow = useRunRetention();
  const [result, setResult] = useState<RetentionRunResult | null>(null);
  const keepLastValue = parsePolicyValue(keepLast);
  const keepDaysValue = parsePolicyValue(keepDays);
  const noPolicy = keepLastValue === null && keepDaysValue === null;

  return (
    <fieldset className="space-y-3 rounded-md border p-3">
      <legend className="px-1 text-sm font-medium">Retention</legend>
      <p className="text-xs text-muted-foreground">
        A backup is deleted only when it is outside the newest "keep last" successful runs and older
        than "keep days" (when both are set). The newest successful backup of each job is always kept.
        Leave both empty to keep everything.
      </p>
      <div className="grid grid-cols-2 gap-3">
        <FormField id="keepLast" label="Keep last (runs)" error={errors.keepLast?.message}>
          <Input id="keepLast" inputMode="numeric" {...register("keepLast")} placeholder="e.g. 7" />
        </FormField>
        <FormField id="keepDays" label="Keep days" error={errors.keepDays?.message}>
          <Input id="keepDays" inputMode="numeric" {...register("keepDays")} placeholder="e.g. 30" />
        </FormField>
      </div>
      {destinationId ? (
        <div className="space-y-2">
          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              size="sm"
              variant="secondary"
              disabled={preview.isPending || noPolicy}
              onClick={() =>
                preview.mutate({ id: destinationId, keepLast: keepLastValue, keepDays: keepDaysValue })
              }
            >
              {preview.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" />}
              Preview what would be deleted
            </Button>
            <Button
              type="button"
              size="sm"
              variant="secondary"
              disabled={runNow.isPending || unsaved}
              title={unsaved ? "Save the policy first" : undefined}
              onClick={() =>
                runNow.mutate(destinationId, { onSuccess: (data) => setResult(data) })
              }
            >
              {runNow.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
              Run retention now
            </Button>
          </div>
          {unsaved && <p className="text-xs text-muted-foreground">"Run retention now" uses the saved policy.</p>}
          {preview.isError && (
            <p role="alert" className="text-sm text-destructive">
              {preview.error.message}
            </p>
          )}
          {preview.data && <PreviewSummary preview={preview.data} />}
          {runNow.isError && (
            <p role="alert" className="text-sm text-destructive">
              {runNow.error.message}
            </p>
          )}
          {result && <RunSummary result={result} />}
        </div>
      ) : (
        <p className="text-xs text-muted-foreground">Save the destination to preview or run retention.</p>
      )}
    </fieldset>
  );
}

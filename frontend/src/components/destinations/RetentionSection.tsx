import { useState } from "react";
import type { UseFormReturn } from "react-hook-form";
import { AccordionSection } from "xedonium/Accordion";
import Alert from "xedonium/Alert";
import Button from "xedonium/Button";
import Field from "xedonium/Field";
import HelperText from "xedonium/HelperText";
import Label from "xedonium/Label";
import PlayIcon from "xedonium/icons/Play";
import SearchIcon from "xedonium/icons/Search";
import { useRetentionPreview, useRunRetention } from "@/api/destinations";
import type { RetentionPreview, RetentionRunResult } from "@/api/types";
import { parsePolicyValue, type DestinationFormValues } from "@/lib/destinationForm";
import { bindText } from "@/lib/formBinding";
import { formatBytes, formatTime } from "@/lib/format";

export function PreviewSummary({ preview }: { preview: RetentionPreview }) {
  if (preview.delete_runs === 0) {
    return <HelperText>Nothing would be deleted. {preview.keep.length} backup(s) kept.</HelperText>;
  }
  return (
    <div className="space-y-2 text-sm">
      <p>
        <strong>{preview.delete_runs}</strong> backup run(s), {preview.delete_artifacts} file(s),{" "}
        {formatBytes(preview.delete_bytes)} would be deleted; {preview.keep.length} kept.
      </p>
      <AccordionSection title="Show what would be deleted">
        <ul className="max-h-48 space-y-1 overflow-auto font-mono text-xs">
          {preview.delete.map((copy) => (
            <li key={copy.run_id}>
              <span className="text-app-muted">
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
      </AccordionSection>
    </div>
  );
}

function RunSummary({ result }: { result: RetentionRunResult }) {
  if (result.skipped) return <Alert tone="danger">Skipped: {result.skipped}</Alert>;
  return (
    <div className="space-y-1 text-sm">
      <p>
        Deleted {result.deleted_runs} backup run(s), {result.deleted_artifacts} file(s),{" "}
        {formatBytes(result.deleted_bytes)}.
      </p>
      {result.failures.length > 0 && (
        <Alert tone="danger">
          {result.failures.length} file(s) could not be deleted (retried at the next pass):
          <ul className="font-mono text-xs">
            {result.failures.slice(0, 10).map((f) => (
              <li key={f.path}>
                {f.path}: {f.error}
              </li>
            ))}
          </ul>
        </Alert>
      )}
    </div>
  );
}

interface RetentionSectionProps {
  destinationId: string | null;
  form: UseFormReturn<DestinationFormValues>;
  keepLast: string;
  keepDays: string;
  /** The policy in the form differs from the saved one. */
  unsaved: boolean;
}

export function RetentionSection({ destinationId, form, keepLast, keepDays, unsaved }: RetentionSectionProps) {
  const preview = useRetentionPreview();
  const runNow = useRunRetention();
  const [result, setResult] = useState<RetentionRunResult | null>(null);
  const errors = form.formState.errors;
  const keepLastValue = parsePolicyValue(keepLast);
  const keepDaysValue = parsePolicyValue(keepDays);
  const noPolicy = keepLastValue === null && keepDaysValue === null;

  return (
    <div className="space-y-3 border border-app-border p-3">
      <Label>Retention</Label>
      <HelperText>
        A backup is deleted only when it is outside the newest "keep last" successful runs and older
        than "keep days" (when both are set). The newest successful backup of each job is always kept.
        Leave both empty to keep everything.
      </HelperText>
      <div className="grid grid-cols-2 gap-3">
        <Field
          label="Keep last (runs)"
          inputMode="numeric"
          placeholder="e.g. 7"
          {...bindText(form, "keepLast")}
          error={errors.keepLast?.message}
        />
        <Field
          label="Keep days"
          inputMode="numeric"
          placeholder="e.g. 30"
          {...bindText(form, "keepDays")}
          error={errors.keepDays?.message}
        />
      </div>
      {destinationId ? (
        <div className="space-y-2">
          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              variant="secondary"
              disabled={preview.isPending || noPolicy}
              onClick={() =>
                preview.mutate({ id: destinationId, keepLast: keepLastValue, keepDays: keepDaysValue })
              }
            >
              <SearchIcon className="h-4 w-4" />
              Preview what would be deleted
            </Button>
            <Button
              type="button"
              variant="secondary"
              disabled={runNow.isPending || unsaved}
              tooltip={unsaved ? "Save the policy first" : undefined}
              onClick={() => runNow.mutate(destinationId, { onSuccess: (data) => setResult(data) })}
            >
              <PlayIcon className="h-4 w-4" />
              Run retention now
            </Button>
          </div>
          {unsaved && <HelperText>"Run retention now" uses the saved policy.</HelperText>}
          {preview.isError && <Alert tone="danger">{preview.error.message}</Alert>}
          {preview.data && <PreviewSummary preview={preview.data} />}
          {runNow.isError && <Alert tone="danger">{runNow.error.message}</Alert>}
          {result && <RunSummary result={result} />}
        </div>
      ) : (
        <HelperText>Save the destination to preview or run retention.</HelperText>
      )}
    </div>
  );
}

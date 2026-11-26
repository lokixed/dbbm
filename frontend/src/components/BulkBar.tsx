import { useState, type ReactNode } from "react";
import Button from "xedonium/Button";
import Loader from "xedonium/Loader";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { useToast } from "@/components/toast";

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : "Request failed";
}

/** One bulk action of a table: which selected rows it applies to and what it does to one row. */
export interface BulkAction<Row> {
  key: string;
  label: string;
  /** Shown while running: "Verifying 3 of 10...". */
  progressLabel: string;
  icon?: ReactNode;
  variant?: "secondary" | "danger";
  /** The rows of the selection it applies to; the others are skipped. */
  eligible: (rows: Row[]) => Row[];
  /** Why rows were skipped, shown as the button tooltip when some are. */
  skippedHint?: string;
  /** Ask before running; returns the dialog title and text. */
  confirm?: (rows: Row[]) => { title: string; description: string; confirmLabel: string };
  /** Run on one row; the returned string is the outcome counted in the summary ("ok", "deleted"). */
  perform: (row: Row) => Promise<string>;
  /** Summary title and line from the outcome counts and the number of failures. */
  summarize: (counts: Record<string, number>, failed: number) => { title: string; line: string; ok: boolean };
}

interface BulkBarProps<Row> {
  selected: Row[];
  actions: BulkAction<Row>[];
  /** Label of a row in error messages. */
  describe: (row: Row) => string;
  onClear: () => void;
  /** Called once after an action finished (refresh queries here). */
  onFinished: () => Promise<unknown> | void;
  /** More buttons that act on the whole selection at once (e.g. export). */
  extra?: ReactNode;
}

/**
 * The bar above a table while rows are ticked. An action runs on the eligible rows one at a time (each
 * call can be slow), a failure of one row does not stop the rest, and one toast summarizes the result.
 */
export function BulkBar<Row>({ selected, actions, describe, onClear, onFinished, extra }: BulkBarProps<Row>) {
  const { toast } = useToast();
  const [progress, setProgress] = useState<{ label: string; done: number; total: number } | null>(null);
  const [confirming, setConfirming] = useState<BulkAction<Row> | null>(null);
  if (selected.length === 0 && !progress) return null;

  const run = async (action: BulkAction<Row>) => {
    const rows = action.eligible(selected);
    const counts: Record<string, number> = {};
    const errors: string[] = [];
    setProgress({ label: action.progressLabel, done: 0, total: rows.length });
    for (const [index, row] of rows.entries()) {
      try {
        const outcome = await action.perform(row);
        counts[outcome] = (counts[outcome] ?? 0) + 1;
      } catch (error) {
        errors.push(`${describe(row)}: ${errorText(error)}`);
      }
      setProgress({ label: action.progressLabel, done: index + 1, total: rows.length });
    }
    await onFinished();
    setProgress(null);
    onClear();
    const summary = action.summarize(counts, errors.length);
    toast({
      title: summary.title,
      description: [summary.line, ...errors.slice(0, 3)].join(" "),
      variant: summary.ok && errors.length === 0 ? "success" : "error",
    });
  };

  const start = (action: BulkAction<Row>) => {
    if (action.confirm) setConfirming(action);
    else void run(action);
  };
  const dialog = confirming?.confirm?.(confirming.eligible(selected));

  return (
    <div className="flex flex-wrap items-center gap-3 border border-app-border bg-app-card px-4 py-3" aria-live="polite">
      {progress ? (
        <Loader
          variant="inline"
          size="sm"
          label={`${progress.label} ${Math.min(progress.done + 1, progress.total)} of ${progress.total}...`}
        />
      ) : (
        <>
          <span className="text-sm font-medium">{selected.length} selected</span>
          {actions.map((action) => {
            const count = action.eligible(selected).length;
            return (
              <Button
                key={action.key}
                variant={action.variant ?? "secondary"}
                onClick={() => start(action)}
                disabled={count === 0}
                tooltip={count < selected.length && action.skippedHint ? action.skippedHint : undefined}
              >
                {action.icon} {action.label}
                {/* The count only when the action skips some rows; at 0 the button is disabled. */}
                {count > 0 && count < selected.length ? ` (${count})` : ""}
              </Button>
            );
          })}
          {extra}
          <Button variant="flat" onClick={onClear}>
            Clear selection
          </Button>
        </>
      )}

      <ConfirmDialog
        open={dialog !== undefined}
        title={dialog?.title ?? ""}
        description={dialog?.description ?? ""}
        confirmLabel={dialog?.confirmLabel ?? "Confirm"}
        pending={false}
        onConfirm={() => {
          const action = confirming;
          setConfirming(null);
          if (action) void run(action);
        }}
        onCancel={() => setConfirming(null)}
      />
    </div>
  );
}

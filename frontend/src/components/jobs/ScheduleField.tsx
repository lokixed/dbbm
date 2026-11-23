import { ApiError } from "@/api/client";
import { useSchedulePreview, useTimezones } from "@/api/schedule";
import Alert from "xedonium/Alert";
import Button from "xedonium/Button";
import Field from "xedonium/Field";
import Label from "xedonium/Label";
import Loader from "xedonium/Loader";
import Select from "xedonium/Select";
import { formatTime } from "@/lib/format";
import { useDebounced } from "@/lib/useDebounced";

const SCHEDULE_PRESETS = [
  { label: "Hourly", cron: "0 * * * *" },
  { label: "Daily 03:00", cron: "0 3 * * *" },
  { label: "Weekly (Sun 03:00)", cron: "0 3 * * 0" },
] as const;

interface ScheduleFieldProps {
  cron: string;
  timezone: string;
  onCron: (cron: string) => void;
  onTimezone: (timezone: string) => void;
  error?: string;
  serverError?: string | null;
}

function localWallTime(iso: string): string {
  // "2026-11-24T03:00:00+01:00" -> "2026-11-24 03:00 (+01:00)"
  const match = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})(?::\d{2})?(.*)$/.exec(iso);
  return match ? `${match[1]} ${match[2]} (${match[3] || "UTC"})` : iso;
}

function previewError(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  return "Could not compute the next runs.";
}

export function ScheduleField({ cron, timezone, onCron, onTimezone, error, serverError }: ScheduleFieldProps) {
  const timezones = useTimezones();
  const debouncedCron = useDebounced(cron.trim());
  const debouncedZone = useDebounced(timezone);
  const preview = useSchedulePreview(debouncedCron, debouncedZone);
  const zones = timezones.data ?? [timezone];
  const zoneOptions = (zones.includes(timezone) ? zones : [timezone, ...zones]).map((zone) => ({
    value: zone,
    label: zone,
  }));

  return (
    <div className="space-y-3 border border-app-border p-3">
      <Label>Schedule</Label>
      <div className="flex flex-wrap gap-2">
        <Button type="button" variant={cron.trim() === "" ? "default" : "secondary"} onClick={() => onCron("")}>
          Manual only
        </Button>
        {SCHEDULE_PRESETS.map((preset) => (
          <Button
            key={preset.cron}
            type="button"
            variant={cron.trim() === preset.cron ? "default" : "secondary"}
            onClick={() => onCron(preset.cron)}
          >
            {preset.label}
          </Button>
        ))}
      </div>
      <div className="grid grid-cols-2 gap-3">
        <Field
          label="Cron expression"
          helperText="minute hour day-of-month month day-of-week (0 or 7 = Sunday). At most every 5 minutes."
          error={error ?? serverError ?? undefined}
          value={cron}
          onChange={onCron}
          placeholder="0 3 * * *"
          autoComplete="off"
          style={{ fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace" }}
        />
        <Select label="Time zone" value={timezone} onChange={onTimezone} options={zoneOptions} />
      </div>
      {cron.trim() !== "" && (
        <div className="text-sm" aria-live="polite">
          <p className="mb-1 font-medium">Next 5 runs</p>
          {(preview.isFetching || debouncedCron !== cron.trim()) && !preview.isError && (
            <Loader variant="inline" size="sm" label="Calculating..." />
          )}
          {preview.isError && !preview.isFetching && <Alert tone="danger">{previewError(preview.error)}</Alert>}
          {preview.isSuccess && !preview.isFetching && (
            <ul className="space-y-0.5 font-mono text-xs">
              {preview.data.fires.map((fire) => (
                <li key={fire.utc}>
                  {localWallTime(fire.local)}{" "}
                  <span className="text-app-muted">- your time {formatTime(fire.utc)}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

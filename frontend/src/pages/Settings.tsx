import { standardSchemaResolver } from "@hookform/resolvers/standard-schema";
import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import Alert from "xedonium/Alert";
import Button from "xedonium/Button";
import Card from "xedonium/Card";
import Field from "xedonium/Field";
import Loader from "xedonium/Loader";
import PageHeader from "xedonium/PageHeader";
import Select from "xedonium/Select";
import { z } from "zod";
import { useTimezones } from "@/api/schedule";
import { useAppSettings, useSaveSettings } from "@/api/settings";
import type { AppSettings, AppSettingsRecord } from "@/api/types";
import { EncryptionKeys } from "@/components/settings/EncryptionKeys";
import { useToast } from "@/components/toast";
import { bindText } from "@/lib/formBinding";

interface SettingsFormValues {
  publicBaseUrl: string;
  defaultTimezone: string;
  logRetentionDays: string;
  runHistoryDays: string;
  misfireGraceSeconds: string;
  housekeepingTime: string;
  lowDiskThresholdPercent: string;
  overdueGracePercent: string;
}

function intField(min: number, max: number, label: string) {
  return z
    .string()
    .trim()
    .refine((v) => /^\d+$/.test(v) && Number(v) >= min && Number(v) <= max, `${label} must be ${min}-${max}`);
}

const schema = z.object({
  publicBaseUrl: z
    .string()
    .trim()
    .max(512, "Max 512 characters")
    .refine(
      (v) => v === "" || /^https?:\/\/[^\s/?#@]+(\/[^\s?#]*)?$/.test(v),
      "Use http(s)://host[/path], without credentials, query or fragment",
    ),
  defaultTimezone: z.string().min(1, "Choose a time zone"),
  logRetentionDays: intField(1, 3650, "Log retention"),
  runHistoryDays: intField(1, 36500, "Run history"),
  misfireGraceSeconds: intField(0, 7 * 86400, "Grace period"),
  housekeepingTime: z.string().regex(/^([01]\d|2[0-3]):[0-5]\d$/, "Use HH:MM (24-hour)"),
  lowDiskThresholdPercent: intField(0, 90, "Threshold"),
  overdueGracePercent: intField(0, 1000, "Overdue grace"),
});

function toValues(s: AppSettingsRecord): SettingsFormValues {
  return {
    publicBaseUrl: s.public_base_url,
    defaultTimezone: s.default_timezone,
    logRetentionDays: String(s.log_retention_days),
    runHistoryDays: String(s.run_history_days),
    misfireGraceSeconds: String(s.misfire_grace_seconds),
    housekeepingTime: s.housekeeping_time,
    lowDiskThresholdPercent: String(s.low_disk_threshold_percent),
    overdueGracePercent: String(s.overdue_grace_percent),
  };
}

function toInput(v: SettingsFormValues): AppSettings {
  return {
    public_base_url: v.publicBaseUrl.trim(),
    default_timezone: v.defaultTimezone,
    log_retention_days: Number(v.logRetentionDays),
    run_history_days: Number(v.runHistoryDays),
    misfire_grace_seconds: Number(v.misfireGraceSeconds),
    housekeeping_time: v.housekeepingTime,
    low_disk_threshold_percent: Number(v.lowDiskThresholdPercent),
    overdue_grace_percent: Number(v.overdueGracePercent),
  };
}

function SettingsForm({ settings }: { settings: AppSettingsRecord }) {
  const { toast } = useToast();
  const save = useSaveSettings();
  const timezones = useTimezones();
  const [formError, setFormError] = useState<string | null>(null);
  const form = useForm<SettingsFormValues>({
    resolver: standardSchemaResolver(schema),
    defaultValues: toValues(settings),
  });
  const { formState, reset } = form;
  const errors = formState.errors;

  useEffect(() => reset(toValues(settings)), [settings, reset]);

  const submit = form.handleSubmit(async (values) => {
    setFormError(null);
    try {
      await save.mutateAsync(toInput(values));
      toast({ title: "Settings saved", variant: "success" });
    } catch (error) {
      setFormError(error instanceof Error ? error.message : "Save failed");
    }
  });

  const zones = timezones.data ?? [settings.default_timezone];

  const text = (name: keyof SettingsFormValues, props: { placeholder?: string; numeric?: boolean } = {}) => ({
    ...bindText(form, name),
    placeholder: props.placeholder,
    inputMode: props.numeric ? ("numeric" as const) : undefined,
    error: errors[name]?.message,
  });

  return (
    <form onSubmit={(event) => void submit(event)} className="space-y-6" noValidate>
      <Card title="General">
        <div className="space-y-4">
          <Field
            label="Public base URL"
            helperText="Used for links in notifications, e.g. https://backup.example.com. Leave empty for no links."
            {...text("publicBaseUrl", { placeholder: "https://backup.example.com" })}
          />
          <Select
            label="Default time zone"
            helperText="Pre-selected for new jobs and used for the housekeeping time."
            options={zones.map((zone) => ({ value: zone, label: zone }))}
            {...bindText(form, "defaultTimezone")}
            error={errors.defaultTimezone?.message}
          />
        </div>
      </Card>

      <Card title="Scheduling and housekeeping">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field
            label="Housekeeping time (HH:MM)"
            helperText="Daily retention, log and history cleanup and disk checks. Changes apply immediately."
            {...text("housekeepingTime", { placeholder: "03:30" })}
          />
          <Field
            label="Missed-run grace (seconds)"
            helperText="After a restart, a run missed within this window is caught up once."
            {...text("misfireGraceSeconds", { numeric: true })}
          />
          <Field
            label="Overdue grace (% of the interval)"
            helperText="A job is overdue when no success arrives within max(grace, interval x this %)."
            {...text("overdueGracePercent", { numeric: true })}
          />
          <Field
            label="Low-disk warning below (% free)"
            {...text("lowDiskThresholdPercent", { numeric: true })}
          />
          <Field label="Keep run logs (days)" {...text("logRetentionDays", { numeric: true })} />
          <Field
            label="Keep run history (days)"
            helperText="Runs whose backups still exist are always kept."
            {...text("runHistoryDays", { numeric: true })}
          />
        </div>
      </Card>

      {formError && <Alert tone="danger">{formError}</Alert>}
      <div className="flex justify-end">
        <Button type="submit" disabled={save.isPending || !formState.isDirty}>
          Save settings
        </Button>
      </div>
    </form>
  );
}

export default function SettingsPage() {
  const settings = useAppSettings();
  return (
    <div className="max-w-3xl space-y-6">
      <PageHeader
        title="Settings"
        subtitle="Application-wide behaviour for schedules, notifications and cleanup."
      />
      {settings.isPending && <Loader variant="inline" label="Loading settings..." />}
      {settings.isError && (
        <Alert tone="danger">
          <div className="flex items-center justify-between gap-4">
            <span>Could not load settings: {settings.error.message}</span>
            <Button variant="secondary" onClick={() => void settings.refetch()}>
              Retry
            </Button>
          </div>
        </Alert>
      )}
      {settings.isSuccess && <SettingsForm settings={settings.data} />}
      <EncryptionKeys />
    </div>
  );
}

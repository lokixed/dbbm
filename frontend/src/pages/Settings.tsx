import { standardSchemaResolver } from "@hookform/resolvers/standard-schema";
import { Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { useTimezones } from "@/api/schedule";
import { useAppSettings, useSaveSettings } from "@/api/settings";
import type { AppSettings, AppSettingsRecord } from "@/api/types";
import { FormField } from "@/components/FormField";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { useToast } from "@/components/ui/toast";

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
  const { register, formState, reset } = form;
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

  return (
    <form onSubmit={(event) => void submit(event)} className="space-y-6" noValidate>
      <Card>
        <CardHeader>
          <CardTitle className="text-base">General</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <FormField
            id="publicBaseUrl"
            label="Public base URL"
            hint="Used for links in notifications, e.g. https://backup.example.com. Leave empty for no links."
            error={errors.publicBaseUrl?.message}
          >
            <Input id="publicBaseUrl" {...register("publicBaseUrl")} placeholder="https://backup.example.com" />
          </FormField>
          <FormField
            id="defaultTimezone"
            label="Default time zone"
            hint="Pre-selected for new jobs and used for the housekeeping time."
            error={errors.defaultTimezone?.message}
          >
            <Select id="defaultTimezone" {...register("defaultTimezone")}>
              {zones.map((zone) => (
                <option key={zone} value={zone}>
                  {zone}
                </option>
              ))}
            </Select>
          </FormField>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Scheduling and housekeeping</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-4 sm:grid-cols-2">
          <FormField
            id="housekeepingTime"
            label="Housekeeping time (HH:MM)"
            hint="Daily retention, log and history cleanup and disk checks. Changes apply immediately."
            error={errors.housekeepingTime?.message}
          >
            <Input id="housekeepingTime" {...register("housekeepingTime")} placeholder="03:30" />
          </FormField>
          <FormField
            id="misfireGraceSeconds"
            label="Missed-run grace (seconds)"
            hint="After a restart, a run missed within this window is caught up once."
            error={errors.misfireGraceSeconds?.message}
          >
            <Input id="misfireGraceSeconds" inputMode="numeric" {...register("misfireGraceSeconds")} />
          </FormField>
          <FormField
            id="overdueGracePercent"
            label="Overdue grace (% of the interval)"
            hint="A job is overdue when no success arrives within max(grace, interval x this %)."
            error={errors.overdueGracePercent?.message}
          >
            <Input id="overdueGracePercent" inputMode="numeric" {...register("overdueGracePercent")} />
          </FormField>
          <FormField
            id="lowDiskThresholdPercent"
            label="Low-disk warning below (% free)"
            error={errors.lowDiskThresholdPercent?.message}
          >
            <Input id="lowDiskThresholdPercent" inputMode="numeric" {...register("lowDiskThresholdPercent")} />
          </FormField>
          <FormField id="logRetentionDays" label="Keep run logs (days)" error={errors.logRetentionDays?.message}>
            <Input id="logRetentionDays" inputMode="numeric" {...register("logRetentionDays")} />
          </FormField>
          <FormField
            id="runHistoryDays"
            label="Keep run history (days)"
            hint="Runs whose backups still exist are always kept."
            error={errors.runHistoryDays?.message}
          >
            <Input id="runHistoryDays" inputMode="numeric" {...register("runHistoryDays")} />
          </FormField>
        </CardContent>
      </Card>

      {formError && (
        <div role="alert" className="rounded-md border border-destructive/50 p-3 text-sm text-destructive">
          {formError}
        </div>
      )}
      <div className="flex justify-end">
        <Button type="submit" disabled={save.isPending || !formState.isDirty}>
          {save.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
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
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Settings</h1>
        <p className="text-sm text-muted-foreground">
          Application-wide behaviour for schedules, notifications and cleanup.
        </p>
      </div>
      {settings.isPending && (
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" /> Loading settings...
        </p>
      )}
      {settings.isError && (
        <div role="alert" className="flex items-center justify-between gap-4 text-sm text-destructive">
          <span>Could not load settings: {settings.error.message}</span>
          <Button variant="secondary" size="sm" onClick={() => void settings.refetch()}>
            Retry
          </Button>
        </div>
      )}
      {settings.isSuccess && <SettingsForm settings={settings.data} />}
    </div>
  );
}

import { z } from "zod";
import type { JobInput, JobRecord, NotifyOn } from "@/api/types";
import { nameField } from "@/lib/validation";

export interface JobFormValues {
  name: string;
  sourceId: string;
  destinationIds: string[];
  compress: boolean;
  timeoutSeconds: string;
  schedule: string;
  timezone: string;
  notifyOn: NotifyOn;
  notifyOnRecovery: boolean;
  channelIds: string[];
}

const MIN_TIMEOUT = 10;
const MAX_TIMEOUT = 7 * 24 * 3600;
const MAX_SCHEDULE = 100;

export const jobSchema = z.object({
  name: nameField,
  sourceId: z.string().min(1, "Choose a source"),
  destinationIds: z.array(z.string()).min(1, "Choose at least one destination"),
  compress: z.boolean(),
  timeoutSeconds: z
    .string()
    .trim()
    .regex(/^\d+$/, "Enter a whole number of seconds")
    .refine(
      (v) => Number(v) >= MIN_TIMEOUT && Number(v) <= MAX_TIMEOUT,
      `Timeout must be between ${MIN_TIMEOUT} and ${MAX_TIMEOUT} seconds`,
    ),
  schedule: z
    .string()
    .trim()
    .max(MAX_SCHEDULE, `Max ${MAX_SCHEDULE} characters`)
    .refine((v) => v === "" || v.split(/\s+/).length === 5, "A cron expression has exactly 5 fields"),
  timezone: z.string().min(1, "Choose a time zone"),
  notifyOn: z.enum(["failure", "always", "never"]),
  notifyOnRecovery: z.boolean(),
  channelIds: z.array(z.string()),
});

export function jobDefaults(timezone: string): JobFormValues {
  return {
    name: "",
    sourceId: "",
    destinationIds: [],
    compress: true,
    timeoutSeconds: "3600",
    schedule: "",
    timezone,
    notifyOn: "failure",
    notifyOnRecovery: true,
    channelIds: [],
  };
}

export function jobToValues(job: JobRecord): JobFormValues {
  return {
    name: job.name,
    sourceId: job.source.id,
    destinationIds: job.destinations.map((d) => d.id),
    compress: job.compress,
    timeoutSeconds: String(job.timeout_seconds),
    schedule: job.schedule ?? "",
    timezone: job.timezone,
    notifyOn: job.notify_on,
    notifyOnRecovery: job.notify_on_recovery,
    channelIds: job.notification_channels.map((c) => c.id),
  };
}

/** `heartbeat` undefined keeps the saved ping URL, null clears it, a string replaces it. */
export function toJobInput(
  values: JobFormValues,
  enabled: boolean,
  heartbeat: string | null | undefined,
): JobInput {
  const schedule = values.schedule.trim().split(/\s+/).join(" ");
  const input: JobInput = {
    name: values.name.trim(),
    source_id: values.sourceId,
    destination_ids: values.destinationIds,
    enabled,
    schedule: schedule === "" ? null : schedule,
    timezone: values.timezone,
    compress: values.compress,
    timeout_seconds: Number(values.timeoutSeconds),
    notify_on: values.notifyOn,
    notify_on_recovery: values.notifyOnRecovery,
    notification_channel_ids: values.channelIds,
  };
  if (heartbeat !== undefined) input.heartbeat_url = heartbeat;
  return input;
}

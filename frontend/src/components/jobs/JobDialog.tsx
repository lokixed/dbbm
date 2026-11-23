import { standardSchemaResolver } from "@hookform/resolvers/standard-schema";
import { Loader2 } from "lucide-react";
import { useState } from "react";
import { useForm, useWatch } from "react-hook-form";
import { Link } from "react-router-dom";
import { ApiError } from "@/api/client";
import { useDestinations } from "@/api/destinations";
import { useSaveJob } from "@/api/jobs";
import { useChannels } from "@/api/notifications";
import { useAppSettings } from "@/api/settings";
import { useSources } from "@/api/sources";
import type { JobRecord } from "@/api/types";
import { FormField } from "@/components/FormField";
import { ScheduleField } from "@/components/jobs/ScheduleField";
import { SecretField } from "@/components/SecretField";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { useToast } from "@/components/ui/toast";
import {
  jobDefaults,
  jobSchema,
  jobToValues,
  toJobInput,
  type JobFormValues,
} from "@/lib/jobForm";
import { initialSecret, secretPayload, type SecretState } from "@/lib/secrets";

const DESTINATION_TYPE_LABEL = { local: "Local", gdrive: "Google Drive" } as const;

/** The first 422 message about `field`, if the server rejected it. */
function fieldError(error: unknown, field: string): string | null {
  if (!(error instanceof ApiError) || !Array.isArray(error.detail)) return null;
  for (const item of error.detail as { loc?: unknown[]; msg?: string }[]) {
    if (Array.isArray(item.loc) && item.loc.includes(field) && item.msg) return item.msg;
  }
  return null;
}

function heartbeatError(state: SecretState): string | undefined {
  const value = secretPayload(state);
  if (typeof value !== "string") return undefined;
  return /^https?:\/\/[^\s/]+/.test(value.trim()) ? undefined : "Enter an http:// or https:// URL";
}

function JobForm({
  job,
  defaultTimezone,
  onClose,
}: {
  job: JobRecord | null;
  defaultTimezone: string;
  onClose: () => void;
}) {
  const { toast } = useToast();
  const save = useSaveJob();
  const sources = useSources();
  const destinations = useDestinations();
  const channels = useChannels();
  const [formError, setFormError] = useState<string | null>(null);
  const [heartbeat, setHeartbeat] = useState<SecretState>(() => initialSecret(job?.heartbeat_set ?? false));
  const [heartbeatTouched, setHeartbeatTouched] = useState(false);

  const form = useForm<JobFormValues>({
    resolver: standardSchemaResolver(jobSchema),
    defaultValues: job ? jobToValues(job) : jobDefaults(defaultTimezone),
  });
  const { register, formState, setValue, control } = form;
  const errors = formState.errors;
  const selectedDestinations = useWatch({ control, name: "destinationIds" });
  const selectedChannels = useWatch({ control, name: "channelIds" });
  const sourceId = useWatch({ control, name: "sourceId" });
  const schedule = useWatch({ control, name: "schedule" });
  const timezone = useWatch({ control, name: "timezone" });
  const notifyOn = useWatch({ control, name: "notifyOn" });
  const notifyOnRecovery = useWatch({ control, name: "notifyOnRecovery" });
  const sourceType = sources.data?.find((s) => s.id === sourceId)?.type;
  const heartbeatProblem = heartbeatTouched ? heartbeatError(heartbeat) : undefined;

  const toggleDestination = (id: string) =>
    setValue(
      "destinationIds",
      selectedDestinations.includes(id)
        ? selectedDestinations.filter((d) => d !== id)
        : [...selectedDestinations, id],
      { shouldDirty: true, shouldValidate: formState.isSubmitted },
    );

  const toggleChannel = (id: string) =>
    setValue(
      "channelIds",
      selectedChannels.includes(id) ? selectedChannels.filter((c) => c !== id) : [...selectedChannels, id],
      { shouldDirty: true },
    );

  const submit = form.handleSubmit(async (values) => {
    setFormError(null);
    setHeartbeatTouched(true);
    if (heartbeatError(heartbeat)) return;
    try {
      const saved = await save.mutateAsync({
        id: job?.id ?? null,
        input: toJobInput(values, job?.enabled ?? true, secretPayload(heartbeat)),
      });
      toast({ title: "Job saved", description: saved.name, variant: "success" });
      onClose();
    } catch (error) {
      setFormError(error instanceof ApiError || error instanceof Error ? error.message : "Save failed");
    }
  });

  const noSources = sources.isSuccess && sources.data.length === 0;
  const noDestinations = destinations.isSuccess && destinations.data.length === 0;

  return (
    <form onSubmit={(event) => void submit(event)} className="space-y-5" noValidate>
      <FormField id="job-name" label="Name" error={errors.name?.message}>
        <Input id="job-name" {...register("name")} autoComplete="off" />
      </FormField>

      <FormField
        id="job-source"
        label="Source"
        error={errors.sourceId?.message}
      >
        <Select id="job-source" {...register("sourceId")}>
          <option value="">Choose a source...</option>
          {sources.data?.map((source) => (
            <option key={source.id} value={source.id}>
              {source.name} ({source.type})
            </option>
          ))}
        </Select>
        {noSources && (
          <p className="text-xs text-muted-foreground">
            No sources yet. <Link className="underline" to="/sources">Add one first</Link>.
          </p>
        )}
      </FormField>

      {sourceType === "firestore" && (
        <p role="note" className="rounded-md border p-3 text-sm text-muted-foreground">
          Firestore exports can take a long time, incur document read charges, and may need a larger
          timeout than the default. The timeout applies to each step (export, download, pack)
          separately.
        </p>
      )}

      <fieldset className="space-y-2">
        <legend className="text-sm font-medium">Destinations</legend>
        <div className="space-y-1 rounded-md border p-3">
          {noDestinations && (
            <p className="text-xs text-muted-foreground">
              No destinations yet. <Link className="underline" to="/destinations">Add one first</Link>.
            </p>
          )}
          {destinations.data?.map((destination) => (
            <label key={destination.id} className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={selectedDestinations.includes(destination.id)}
                onChange={() => toggleDestination(destination.id)}
              />
              <span>{destination.name}</span>
              <span className="text-xs text-muted-foreground">
                {DESTINATION_TYPE_LABEL[destination.type]}
                {destination.type === "local" ? ` - ${destination.config.path}` : ""}
              </span>
            </label>
          ))}
        </div>
        {errors.destinationIds?.message && (
          <p role="alert" className="text-xs text-destructive">
            {errors.destinationIds.message}
          </p>
        )}
      </fieldset>

      <div className="grid grid-cols-2 gap-3">
        <FormField
          id="job-timeout"
          label="Timeout per step (seconds)"
          error={errors.timeoutSeconds?.message}
        >
          <Input id="job-timeout" inputMode="numeric" {...register("timeoutSeconds")} />
        </FormField>
        <div className="flex items-end pb-2">
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" {...register("compress")} /> Compress with gzip
          </label>
          {(sourceType === "mongo" || sourceType === "firestore") && (
            <span className="ml-2 text-xs text-muted-foreground">Always gzip for this source</span>
          )}
        </div>
      </div>

      <ScheduleField
        cron={schedule}
        timezone={timezone}
        onCron={(value) => setValue("schedule", value, { shouldDirty: true, shouldValidate: formState.isSubmitted })}
        onTimezone={(value) => setValue("timezone", value, { shouldDirty: true })}
        error={errors.schedule?.message ?? errors.timezone?.message}
        serverError={fieldError(save.error, "schedule") ?? fieldError(save.error, "timezone")}
      />
      {job?.schedule_error && schedule.trim() === job.schedule && (
        <p role="alert" className="text-xs text-destructive">
          The saved schedule is not active: {job.schedule_error}
        </p>
      )}

      <fieldset className="space-y-3 rounded-md border p-3">
        <legend className="px-1 text-sm font-medium">Notifications</legend>
        <div className="grid grid-cols-2 items-end gap-3">
          <FormField id="job-notify-on" label="Notify">
            <Select id="job-notify-on" {...register("notifyOn")}>
              <option value="failure">On failure (failed, partial, interrupted)</option>
              <option value="always">Always</option>
              <option value="never">Never</option>
            </Select>
          </FormField>
          <div className="pb-2">
            <Switch
              checked={notifyOnRecovery}
              disabled={notifyOn === "never"}
              onChange={(value) => setValue("notifyOnRecovery", value, { shouldDirty: true })}
              label="Notify on recovery"
            />
          </div>
        </div>
        <div className="space-y-1">
          <p className="text-sm">Channels</p>
          {channels.isSuccess && channels.data.length === 0 && (
            <p className="text-xs text-muted-foreground">
              No channels yet. <Link className="underline" to="/notifications">Add one</Link>.
            </p>
          )}
          {channels.data?.map((channel) => (
            <label key={channel.id} className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={selectedChannels.includes(channel.id)}
                onChange={() => toggleChannel(channel.id)}
              />
              <span>{channel.name}</span>
              <span className="text-xs text-muted-foreground">
                {channel.type}
                {channel.enabled ? "" : " (disabled)"}
              </span>
            </label>
          ))}
          {notifyOn !== "never" && selectedChannels.length === 0 && (channels.data?.length ?? 0) > 0 && (
            <p className="text-xs text-muted-foreground">Select a channel to receive this job's messages.</p>
          )}
        </div>
        <SecretField
          id="job-heartbeat"
          label="Heartbeat ping URL (optional)"
          isSet={job?.heartbeat_set ?? false}
          state={heartbeat}
          onChange={(state) => {
            setHeartbeat(state);
            setHeartbeatTouched(false);
          }}
          placeholder="https://hc-ping.com/your-check-uuid"
          error={heartbeatProblem}
        />
        <p className="text-xs text-muted-foreground">
          Healthchecks-style: /start when a run starts, the URL on success, /fail on failure. The URL is
          stored encrypted and never shown again.
        </p>
      </fieldset>

      {formError && (
        <div role="alert" className="rounded-md border border-destructive/50 p-3 text-sm text-destructive">
          {formError}
        </div>
      )}

      <div className="flex justify-end gap-2">
        <Button type="button" variant="secondary" onClick={onClose} disabled={save.isPending}>
          Cancel
        </Button>
        <Button type="submit" disabled={save.isPending}>
          {save.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
          Save job
        </Button>
      </div>
    </form>
  );
}

interface JobDialogProps {
  open: boolean;
  job: JobRecord | null;
  onClose: () => void;
}

export function JobDialog({ open, job, onClose }: JobDialogProps) {
  const settings = useAppSettings();
  const sources = useSources();
  const destinations = useDestinations();
  // Native selects keep their default only if the options exist when the form mounts.
  const ready = !settings.isPending && !sources.isPending && !destinations.isPending;
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={job ? "Edit job" : "Add job"}
      description="A job backs up every database of one source to one or more destinations."
    >
      {ready ? (
        <JobForm job={job} defaultTimezone={settings.data?.default_timezone ?? "UTC"} onClose={onClose} />
      ) : (
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" /> Loading...
        </p>
      )}
    </Dialog>
  );
}

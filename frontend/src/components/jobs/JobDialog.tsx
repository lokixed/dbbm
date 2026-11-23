import { standardSchemaResolver } from "@hookform/resolvers/standard-schema";
import { useState } from "react";
import { useForm, useWatch } from "react-hook-form";
import { Link } from "react-router-dom";
import Alert from "xedonium/Alert";
import Button from "xedonium/Button";
import Checkbox from "xedonium/Checkbox";
import Field from "xedonium/Field";
import HelperText from "xedonium/HelperText";
import Label from "xedonium/Label";
import Loader from "xedonium/Loader";
import Modal from "xedonium/Modal";
import Select from "xedonium/Select";
import Switch from "xedonium/Switch";
import { ApiError } from "@/api/client";
import { useDestinations } from "@/api/destinations";
import { useSaveJob } from "@/api/jobs";
import { useChannels } from "@/api/notifications";
import { useAppSettings } from "@/api/settings";
import { useSources } from "@/api/sources";
import type { JobRecord } from "@/api/types";
import { ScheduleField } from "@/components/jobs/ScheduleField";
import { SecretField } from "@/components/SecretField";
import { useToast } from "@/components/toast";
import { bindBool, bindText } from "@/lib/formBinding";
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
  const { formState, setValue, control } = form;
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
      <Field label="Name" autoComplete="off" {...bindText(form, "name")} error={errors.name?.message} />

      <div className="space-y-1.5">
        <Select
          label="Source"
          placeholder="Choose a source..."
          options={(sources.data ?? []).map((source) => ({
            value: source.id,
            label: `${source.name} (${source.type})`,
          }))}
          {...bindText(form, "sourceId")}
          error={errors.sourceId?.message}
        />
        {noSources && (
          <HelperText>
            No sources yet.{" "}
            <Link className="underline" to="/sources">
              Add one first
            </Link>
            .
          </HelperText>
        )}
      </div>

      {sourceType === "firestore" && (
        <Alert tone="info">
          Firestore exports can take a long time, incur document read charges, and may need a larger
          timeout than the default. The timeout applies to each step (export, download, pack)
          separately.
        </Alert>
      )}

      <div className="space-y-2">
        <Label>Destinations</Label>
        <div className="space-y-1 border border-app-border p-3">
          {noDestinations && (
            <HelperText>
              No destinations yet.{" "}
              <Link className="underline" to="/destinations">
                Add one first
              </Link>
              .
            </HelperText>
          )}
          {destinations.data?.map((destination) => (
            <Checkbox
              key={destination.id}
              checked={selectedDestinations.includes(destination.id)}
              onChange={() => toggleDestination(destination.id)}
              label={destination.name}
              description={`${DESTINATION_TYPE_LABEL[destination.type]}${
                destination.type === "local" ? ` - ${destination.config.path}` : ""
              }`}
            />
          ))}
        </div>
        {errors.destinationIds?.message && (
          <HelperText role="alert" className="!text-red-600 dark:!text-red-400">
            {errors.destinationIds.message}
          </HelperText>
        )}
      </div>

      <div className="grid grid-cols-2 gap-3">
        <Field
          label="Timeout per step (seconds)"
          inputMode="numeric"
          {...bindText(form, "timeoutSeconds")}
          error={errors.timeoutSeconds?.message}
        />
        <div className="flex items-end pb-2">
          <Checkbox label="Compress with gzip" {...bindBool(form, "compress")} />
          {(sourceType === "mongo" || sourceType === "firestore") && (
            <span className="ml-2 text-xs text-app-muted">Always gzip for this source</span>
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
        <Alert tone="danger">The saved schedule is not active: {job.schedule_error}</Alert>
      )}

      <div className="space-y-3 border border-app-border p-3">
        <Label>Notifications</Label>
        <div className="grid grid-cols-2 items-end gap-3">
          <Select
            label="Notify"
            options={[
              { value: "failure", label: "On failure (failed, partial, interrupted)" },
              { value: "always", label: "Always" },
              { value: "never", label: "Never" },
            ]}
            {...bindText(form, "notifyOn")}
          />
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
            <HelperText>
              No channels yet.{" "}
              <Link className="underline" to="/notifications">
                Add one
              </Link>
              .
            </HelperText>
          )}
          {channels.data?.map((channel) => (
            <Checkbox
              key={channel.id}
              checked={selectedChannels.includes(channel.id)}
              onChange={() => toggleChannel(channel.id)}
              label={channel.name}
              description={`${channel.type}${channel.enabled ? "" : " (disabled)"}`}
            />
          ))}
          {notifyOn !== "never" && selectedChannels.length === 0 && (channels.data?.length ?? 0) > 0 && (
            <HelperText>Select a channel to receive this job's messages.</HelperText>
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
        <HelperText>
          Healthchecks-style: /start when a run starts, the URL on success, /fail on failure. The URL is
          stored encrypted and never shown again.
        </HelperText>
      </div>

      {formError && <Alert tone="danger">{formError}</Alert>}

      <div className="flex justify-end gap-2">
        <Button type="button" variant="secondary" onClick={onClose} disabled={save.isPending}>
          Cancel
        </Button>
        <Button type="submit" disabled={save.isPending}>
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
  // The form takes its defaults on mount, so wait until the lookups it depends on have loaded.
  const ready = !settings.isPending && !sources.isPending && !destinations.isPending;
  return (
    <Modal open={open} onClose={onClose} title={job ? "Edit job" : "Add job"} maxWidth="max-w-2xl">
      <div className="px-6 pb-6 pt-4">
      <HelperText className="mb-4">
        A job backs up every database of one source to one or more destinations.
      </HelperText>
      {ready ? (
        <JobForm job={job} defaultTimezone={settings.data?.default_timezone ?? "UTC"} onClose={onClose} />
      ) : (
        <Loader variant="inline" label="Loading..." />
      )}
      </div>
    </Modal>
  );
}

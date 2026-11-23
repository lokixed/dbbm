import { standardSchemaResolver } from "@hookform/resolvers/standard-schema";
import { Loader2 } from "lucide-react";
import { useState } from "react";
import { useForm, useWatch } from "react-hook-form";
import { ApiError } from "@/api/client";
import { useRetentionPreview, useSaveDestination, useTestDestination } from "@/api/destinations";
import type { DestinationRecord, RetentionPreview, TestResult } from "@/api/types";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { GdriveFields } from "@/components/destinations/GdriveFields";
import { PreviewSummary, RetentionSection } from "@/components/destinations/RetentionSection";
import { FormField } from "@/components/FormField";
import { TestResultPanel } from "@/components/TestResultPanel";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { useToast } from "@/components/ui/toast";
import {
  destinationDefaults,
  destinationSchema,
  parsePolicyValue,
  recordToValues,
  toDestinationInput,
  type DestinationFormValues,
} from "@/lib/destinationForm";
import { buildSecrets, initialSecret, secretPayload, type SecretState } from "@/lib/secrets";

interface DriveSecrets {
  token: SecretState;
  client_secret: SecretState;
}

function initialDriveSecrets(record: DestinationRecord | null, focusToken: boolean): DriveSecrets {
  return {
    token: focusToken ? { action: "set", value: "" } : initialSecret(record?.secrets_set.token ?? false),
    client_secret: initialSecret(record?.secrets_set.client_secret ?? false),
  };
}

function hasSecret(state: SecretState, isSet: boolean): boolean {
  if (state.action === "keep") return isSet;
  return state.action === "set" && secretPayload(state) !== undefined;
}

function DestinationForm({
  destination,
  focusToken,
  onClose,
}: {
  destination: DestinationRecord | null;
  focusToken: boolean;
  onClose: () => void;
}) {
  const { toast } = useToast();
  const save = useSaveDestination();
  const test = useTestDestination();
  const [record, setRecord] = useState<DestinationRecord | null>(destination);
  const [formError, setFormError] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<TestResult | null>(null);
  const [testError, setTestError] = useState<string | null>(null);
  const [secrets, setSecrets] = useState(() => initialDriveSecrets(destination, focusToken));
  const [secretErrors, setSecretErrors] = useState<Record<string, string>>({});
  const retentionPreview = useRetentionPreview();
  const [confirm, setConfirm] = useState<{
    values: DestinationFormValues;
    andTest: boolean;
    preview: RetentionPreview;
  } | null>(null);

  const form = useForm<DestinationFormValues>({
    resolver: standardSchemaResolver(destinationSchema),
    defaultValues: destination ? recordToValues(destination) : destinationDefaults,
  });
  const { register, formState, control } = form;
  const errors = formState.errors;
  const busy = save.isPending || test.isPending || retentionPreview.isPending;
  const type = useWatch({ control, name: "type" });
  const scope = useWatch({ control, name: "scope" });
  const clientId = useWatch({ control, name: "clientId" });
  const keepLast = useWatch({ control, name: "keepLast" });
  const keepDays = useWatch({ control, name: "keepDays" });
  const secretsSet = record?.secrets_set ?? {};
  const retentionChanged =
    record !== null &&
    (parsePolicyValue(keepLast) !== record.retention.keep_last ||
      parsePolicyValue(keepDays) !== record.retention.keep_days);

  const validateSecrets = (values: DestinationFormValues): boolean => {
    const found: Record<string, string> = {};
    if (values.type === "gdrive") {
      if (!hasSecret(secrets.token, secretsSet.token ?? false)) {
        found.token = "Paste the token printed by rclone authorize.";
      }
      const secretGiven = hasSecret(secrets.client_secret, secretsSet.client_secret ?? false);
      if (values.clientId.trim() && !secretGiven) {
        found.client_secret = "Enter the client secret that belongs to this client ID.";
      } else if (!values.clientId.trim() && secrets.client_secret.action === "set" && secretGiven) {
        found.client_secret = "Enter the client ID too, or leave the secret empty.";
      }
    }
    setSecretErrors(found);
    return Object.keys(found).length === 0;
  };

  const secretsInput = (values: DestinationFormValues) => {
    // Without a client ID a saved client secret is meaningless, so it is cleared with it.
    const clientSecret: SecretState =
      !values.clientId.trim() && secrets.client_secret.action === "keep" && secretsSet.client_secret
        ? { action: "clear" }
        : secrets.client_secret;
    return buildSecrets({ token: secrets.token, client_secret: clientSecret });
  };

  const runTest = async (id: string) => {
    setTestError(null);
    setTestResult(null);
    try {
      const result = await test.mutateAsync(id);
      setTestResult(result);
      toast({
        title: result.ok ? "Destination OK" : "Destination check failed",
        description: result.message,
        variant: result.ok ? "success" : "error",
      });
    } catch (error) {
      setTestError(error instanceof Error ? error.message : "Test failed");
    }
  };

  const persist = async (values: DestinationFormValues, andTest: boolean) => {
      try {
        const saved = await save.mutateAsync({
          id: record?.id ?? null,
          input: toDestinationInput(values, secretsInput(values)),
        });
        setRecord(saved);
        form.reset(recordToValues(saved));
        setSecrets(initialDriveSecrets(saved, false));
        toast({ title: "Destination saved", description: saved.name, variant: "success" });
        if (andTest) await runTest(saved.id);
        else onClose();
      } catch (error) {
        setFormError(error instanceof ApiError || error instanceof Error ? error.message : "Save failed");
      }
  };

  const submit = (andTest: boolean) =>
    form.handleSubmit(async (values) => {
      setFormError(null);
      if (!validateSecrets(values)) return;
      const keepLastValue = parsePolicyValue(values.keepLast);
      const keepDaysValue = parsePolicyValue(values.keepDays);
      if (record && retentionChanged && (keepLastValue !== null || keepDaysValue !== null)) {
        try {
          const preview = await retentionPreview.mutateAsync({
            id: record.id,
            keepLast: keepLastValue,
            keepDays: keepDaysValue,
          });
          if (preview.delete_runs > 0) {
            setConfirm({ values, andTest, preview });
            return;
          }
        } catch (error) {
          setFormError(error instanceof Error ? error.message : "Could not preview the retention policy");
          return;
        }
      }
      await persist(values, andTest);
    });

  return (
    <form onSubmit={(event) => event.preventDefault()} className="space-y-5" noValidate>
      <FormField id="type" label="Type">
        <Select id="type" disabled={record !== null} {...register("type")}>
          <option value="local">Local drive</option>
          <option value="gdrive">Google Drive</option>
        </Select>
      </FormField>

      {record?.secrets_error && (
        <div role="alert" className="rounded-md border border-destructive/50 p-3 text-sm text-destructive">
          {record.secrets_error}
        </div>
      )}

      <FormField id="name" label="Name" error={errors.name?.message}>
        <Input id="name" {...register("name")} autoComplete="off" />
      </FormField>

      {type === "local" ? (
        <FormField
          id="path"
          label="Path"
          hint="Relative to the backup directory (e.g. daily/mysql). Absolute paths must be inside it."
          error={errors.path?.message}
        >
          <Input id="path" {...register("path")} placeholder="daily" autoComplete="off" />
        </FormField>
      ) : (
        <GdriveFields
          register={register}
          errors={errors}
          scope={scope}
          clientId={clientId}
          secretsSet={secretsSet}
          token={secrets.token}
          clientSecret={secrets.client_secret}
          onToken={(state) => setSecrets((s) => ({ ...s, token: state }))}
          onClientSecret={(state) => setSecrets((s) => ({ ...s, client_secret: state }))}
          secretErrors={secretErrors}
          focusToken={focusToken}
        />
      )}

      <RetentionSection
        destinationId={record?.id ?? null}
        register={register}
        errors={errors}
        keepLast={keepLast}
        keepDays={keepDays}
        unsaved={retentionChanged}
      />

      {formError && (
        <div role="alert" className="rounded-md border border-destructive/50 p-3 text-sm text-destructive">
          {formError}
        </div>
      )}

      <TestResultPanel pending={test.isPending} result={testResult} error={testError} />

      <ConfirmDialog
        open={confirm !== null}
        title="Apply the new retention policy?"
        description={
          confirm
            ? `${confirm.preview.delete_runs} backup run(s) (${confirm.preview.delete_artifacts} file(s)) ` +
              "will be deleted at the next retention pass (after the next run, at housekeeping, or when " +
              "you run retention now)."
            : ""
        }
        confirmLabel="Save policy"
        pendingLabel="Saving..."
        pending={save.isPending}
        onConfirm={() => {
          if (!confirm) return;
          const { values, andTest } = confirm;
          setConfirm(null);
          void persist(values, andTest);
        }}
        onCancel={() => setConfirm(null)}
      />
      {confirm && <PreviewSummary preview={confirm.preview} />}

      <div className="flex justify-end gap-2">
        <Button type="button" variant="secondary" onClick={onClose} disabled={busy}>
          {record && testResult ? "Close" : "Cancel"}
        </Button>
        <Button type="button" variant="secondary" onClick={submit(false)} disabled={busy}>
          Save
        </Button>
        <Button type="button" onClick={submit(true)} disabled={busy}>
          {busy && <Loader2 className="h-4 w-4 animate-spin" />}
          Save and test
        </Button>
      </div>
    </form>
  );
}

interface DestinationDialogProps {
  open: boolean;
  destination: DestinationRecord | null;
  /** Reconnect flow: open with the token field ready for a new token. */
  focusToken?: boolean;
  onClose: () => void;
}

export function DestinationDialog({ open, destination, focusToken = false, onClose }: DestinationDialogProps) {
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={focusToken ? "Reconnect Google Drive" : destination ? "Edit destination" : "Add destination"}
      description="Backups are copied to every destination of a job and verified there."
    >
      <DestinationForm destination={destination} focusToken={focusToken} onClose={onClose} />
    </Dialog>
  );
}

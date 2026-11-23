import { standardSchemaResolver } from "@hookform/resolvers/standard-schema";
import { useState } from "react";
import { useForm, useWatch } from "react-hook-form";
import Alert from "xedonium/Alert";
import Button from "xedonium/Button";
import Field from "xedonium/Field";
import HelperText from "xedonium/HelperText";
import Modal from "xedonium/Modal";
import Select from "xedonium/Select";
import { ApiError } from "@/api/client";
import { useRetentionPreview, useSaveDestination, useTestDestination } from "@/api/destinations";
import type { DestinationRecord, RetentionPreview, TestResult } from "@/api/types";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { GdriveFields } from "@/components/destinations/GdriveFields";
import { PreviewSummary, RetentionSection } from "@/components/destinations/RetentionSection";
import { TestResultPanel } from "@/components/TestResultPanel";
import { useToast } from "@/components/toast";
import { bindText } from "@/lib/formBinding";
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
  const { formState, control } = form;
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
      <Select
        label="Type"
        disabled={record !== null}
        options={[
          { value: "local", label: "Local drive" },
          { value: "gdrive", label: "Google Drive" },
        ]}
        {...bindText(form, "type")}
      />

      {record?.secrets_error && <Alert tone="danger">{record.secrets_error}</Alert>}

      <Field label="Name" autoComplete="off" {...bindText(form, "name")} error={errors.name?.message} />

      {type === "local" ? (
        <Field
          label="Path"
          helperText="Relative to the backup directory (e.g. daily/mysql). Absolute paths must be inside it."
          placeholder="daily"
          autoComplete="off"
          {...bindText(form, "path")}
          error={errors.path?.message}
        />
      ) : (
        <GdriveFields
          form={form}
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
        form={form}
        keepLast={keepLast}
        keepDays={keepDays}
        unsaved={retentionChanged}
      />

      {formError && <Alert tone="danger">{formError}</Alert>}

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
    <Modal
      open={open}
      onClose={onClose}
      title={focusToken ? "Reconnect Google Drive" : destination ? "Edit destination" : "Add destination"}
      maxWidth="max-w-2xl"
    >
      <HelperText className="mb-4">
        Backups are copied to every destination of a job and verified there.
      </HelperText>
      <DestinationForm destination={destination} focusToken={focusToken} onClose={onClose} />
    </Modal>
  );
}

import { standardSchemaResolver } from "@hookform/resolvers/standard-schema";
import { useState } from "react";
import { useForm, useWatch, type Resolver } from "react-hook-form";
import Alert from "xedonium/Alert";
import Button from "xedonium/Button";
import Checkbox from "xedonium/Checkbox";
import Field from "xedonium/Field";
import HelperText from "xedonium/HelperText";
import RadioGroup from "xedonium/RadioGroup";
import Select from "xedonium/Select";
import Switch from "xedonium/Switch";
import { DialogFooter, DialogFrame } from "@/components/DialogFrame";
import { ApiError } from "@/api/client";
import { useSaveSource, useTestSource } from "@/api/sources";
import type { SourceRecord, SourceType, TestResult } from "@/api/types";
import { DatabaseMultiSelect } from "@/components/DatabaseMultiSelect";
import { SecretField } from "@/components/SecretField";
import { TestResultPanel } from "@/components/TestResultPanel";
import { useToast } from "@/components/toast";
import { bindBool, bindText } from "@/lib/formBinding";
import { initialSecret, type SecretState } from "@/lib/secrets";
import {
  defaultValues,
  recordToValues,
  secretSpecs,
  SOURCE_TYPES,
  sourceSchema,
  TLS_MODES,
  toSourceInput,
  type MongoMode,
  type SourceFormValues,
} from "@/lib/sourceForm";
import { checkServiceAccount } from "@/lib/validation";

const SECRET_FIELDS = ["password", "uri", "service_account_json"] as const;

function initialSecrets(record: SourceRecord | null): Record<string, SecretState> {
  return Object.fromEntries(
    SECRET_FIELDS.map((field) => [field, initialSecret(record?.secrets_set[field] ?? false)]),
  );
}

function TypePicker({ onPick }: { onPick: (type: SourceType) => void }) {
  return (
    <div className="space-y-3">
      <HelperText>Choose the kind of database to back up.</HelperText>
      <div className="grid gap-3 sm:grid-cols-2">
        {SOURCE_TYPES.map(({ type, label, hint }) => (
          <Button
            key={type}
            type="button"
            variant="secondary"
            onClick={() => onPick(type)}
            className="!h-auto !items-start !justify-start !p-4 !text-left"
          >
            <span>
              <span className="block font-medium">{label}</span>
              <span className="block text-xs font-normal text-app-muted">{hint}</span>
            </span>
          </Button>
        ))}
      </div>
    </div>
  );
}

interface SourceFormProps {
  type: SourceType;
  source: SourceRecord | null;
  onClose: () => void;
  onBack?: () => void;
}

function SourceForm({ type, source, onClose, onBack }: SourceFormProps) {
  const { toast } = useToast();
  const save = useSaveSource();
  const test = useTestSource();

  const [record, setRecord] = useState<SourceRecord | null>(source);
  const [secrets, setSecrets] = useState(() => initialSecrets(source));
  const [secretErrors, setSecretErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<TestResult | null>(null);
  const [testError, setTestError] = useState<string | null>(null);
  const [discovered, setDiscovered] = useState<string[] | null>(null);

  const resolver: Resolver<SourceFormValues> = (values, context, options) =>
    (
      standardSchemaResolver(sourceSchema(type, values.mongoMode)) as unknown as Resolver<SourceFormValues>
    )(values, context, options);

  const form = useForm<SourceFormValues>({
    resolver,
    defaultValues: source ? recordToValues(source) : defaultValues(type),
  });
  const { control, setValue, formState } = form;
  const errors = formState.errors;
  const mongoMode: MongoMode = useWatch({ control, name: "mongoMode" });
  const allDatabases = useWatch({ control, name: "allDatabases" });
  const selectedDatabases = useWatch({ control, name: "databases" });
  const projectId = useWatch({ control, name: "projectId" });
  const mongoDumpMode = useWatch({ control, name: "mongoDumpMode" });

  const specs = secretSpecs(type, mongoMode);
  const busy = save.isPending || test.isPending;
  const hasDatabaseList = type === "mysql" || type === "postgres" || type === "mongo";

  const validateSecrets = (): boolean => {
    const found: Record<string, string> = {};
    for (const spec of specs) {
      const state = secrets[spec.field];
      const isSet = record?.secrets_set[spec.field] ?? false;
      const value = state.action === "set" ? state.value : "";
      if (spec.required && !isSet && value.trim() === "") {
        found[spec.field] = `${spec.label} is required`;
      } else if (spec.field === "service_account_json" && value !== "") {
        const problem = checkServiceAccount(value, projectId.trim());
        if (problem) found[spec.field] = problem;
      } else if (spec.field === "uri" && value !== "" && !/^mongodb(\+srv)?:\/\//.test(value)) {
        found[spec.field] = "URI must start with mongodb:// or mongodb+srv://";
      }
    }
    setSecretErrors(found);
    return Object.keys(found).length === 0;
  };

  const runTest = async (id: string) => {
    setTestError(null);
    setTestResult(null);
    try {
      const result = await test.mutateAsync(id);
      setTestResult(result);
      if (result.databases) setDiscovered(result.databases);
      toast({
        title: result.ok ? "Connection OK" : "Connection failed",
        description: result.message,
        variant: result.ok ? "success" : "error",
      });
    } catch (error) {
      setTestError(error instanceof Error ? error.message : "Test failed");
    }
  };

  const submit = (andTest: boolean) =>
    form.handleSubmit(async (values) => {
      setFormError(null);
      if (!validateSecrets()) return;
      try {
        const saved = await save.mutateAsync({
          id: record?.id ?? null,
          input: toSourceInput(type, values, secrets),
        });
        setRecord(saved);
        setSecrets(initialSecrets(saved));
        toast({ title: "Source saved", description: saved.name, variant: "success" });
        if (andTest) await runTest(saved.id);
        else onClose();
      } catch (error) {
        setFormError(error instanceof ApiError || error instanceof Error ? error.message : "Save failed");
      }
    });

  const setSecret = (field: string) => (state: SecretState) =>
    setSecrets((current) => ({ ...current, [field]: state }));

  const text = (name: keyof SourceFormValues) => ({
    ...bindText(form, name),
    error: (errors[name] as { message?: string } | undefined)?.message,
  });

  const typeLabel = SOURCE_TYPES.find((t) => t.type === type)?.label ?? type;
  const isSqlLike = type === "mysql" || type === "postgres";
  const showHostFields = type !== "firestore" && !(type === "mongo" && mongoMode === "uri");
  const fullInstance = type === "mongo" && mongoDumpMode === "full_oplog";

  return (
    <form onSubmit={(event) => event.preventDefault()} className="space-y-5" noValidate>
      <p className="flex items-center text-sm text-app-muted">
        {typeLabel}
        {onBack && record === null && (
          <Button type="button" variant="flat" className="ml-2" onClick={onBack}>
            Change type
          </Button>
        )}
      </p>

      {record?.secrets_error && <Alert tone="danger">{record.secrets_error}</Alert>}

      <Field label="Name" autoComplete="off" {...text("name")} />

      {type === "mongo" && (
        <RadioGroup
          label="Connection"
          name="mongoMode"
          direction="row"
          value={mongoMode}
          onChange={(value) => setValue("mongoMode", value as MongoMode, { shouldDirty: true })}
          options={[
            { value: "fields", label: "Host fields" },
            { value: "uri", label: "Connection URI" },
          ]}
        />
      )}

      {showHostFields && (
        <>
          <div className="grid grid-cols-[1fr_8rem] gap-3">
            <Field label="Host" placeholder="db.example.com" autoComplete="off" {...text("host")} />
            <Field label="Port" inputMode="numeric" {...text("port")} />
          </div>
          <Field
            label={type === "mongo" ? "Username (optional)" : "Username"}
            autoComplete="off"
            {...text("username")}
          />
        </>
      )}

      {type === "postgres" && (
        <Field
          label="Connection database"
          helperText="Used to log in and list databases."
          {...text("connectionDatabase")}
        />
      )}

      {type === "postgres" && (
        <Checkbox
          label="Also back up roles and other global objects"
          description="Needs a superuser. If it fails, the run still succeeds and shows a warning."
          {...bindBool(form, "includeGlobals")}
        />
      )}

      {type === "mongo" && mongoMode === "fields" && (
        <>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Auth database" {...text("authDatabase")} />
            <Field label="Replica set (optional)" {...text("replicaSet")} />
          </div>
          <Checkbox label="Use TLS" {...bindBool(form, "tls")} />
        </>
      )}

      {isSqlLike && (
        <div className="grid grid-cols-2 gap-3">
          <Select
            label="TLS mode"
            options={TLS_MODES[type].map((mode) => ({ value: mode, label: mode }))}
            {...bindText(form, "tlsMode")}
          />
          <Field
            label={type === "mysql" ? "Charset (optional)" : "Application name (optional)"}
            {...text("extraOption")}
          />
        </div>
      )}

      {type === "firestore" && (
        <>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Project ID" autoComplete="off" {...text("projectId")} />
            <Field label="Database ID" {...text("databaseId")} />
          </div>
          <Field
            label="Staging bucket"
            helperText="Where exports are written first, e.g. gs://my-backup-staging"
            placeholder="gs://"
            {...text("stagingBucket")}
          />
          <Field
            label="Collection IDs (optional)"
            helperText="Comma-separated. Leave empty to export everything."
            {...text("collectionIds")}
          />
          <Switch
            label="Delete staging export after download"
            {...bindBool(form, "deleteStagingAfterDownload")}
          />
          <HelperText>If deleting fails the run still succeeds and shows a warning.</HelperText>
          <Alert tone="info" title="Prerequisites">
            <ul className="list-disc space-y-1 pl-5 text-xs">
              <li>Billing is enabled on the Google Cloud project (exports need it).</li>
              <li>The staging bucket is in the same location as the Firestore database.</li>
              <li>
                The service account has <code>roles/datastore.importExportAdmin</code> and read and
                write access to the bucket (for example <code>roles/storage.objectAdmin</code>).
              </li>
              <li>
                Add a lifecycle rule to the bucket (for example delete objects after 1 to 7 days) so
                failed or cancelled exports do not pile up.
              </li>
            </ul>
          </Alert>
        </>
      )}

      {specs.map((spec) => (
        <SecretField
          key={spec.field}
          id={`secret-${spec.field}`}
          label={spec.label}
          isSet={record?.secrets_set[spec.field] ?? false}
          state={secrets[spec.field]}
          onChange={setSecret(spec.field)}
          required={spec.required}
          multiline={spec.multiline}
          allowFile={spec.field === "service_account_json"}
          placeholder={spec.field === "uri" ? "mongodb://user:password@host:27017/?authSource=admin" : undefined}
          error={secretErrors[spec.field]}
        />
      ))}

      {type === "mongo" && (
        <Select
          label="Dump mode"
          helperText={
            fullInstance
              ? "One archive of the whole instance with --oplog, consistent to a point in time. Needs a replica set; the run fails early on a standalone server."
              : "One archive per selected database. On a replica set the databases are not consistent with each other; use full instance for that."
          }
          options={[
            { value: "per_database", label: "Per database" },
            { value: "full_oplog", label: "Full instance with oplog" },
          ]}
          {...bindText(form, "mongoDumpMode")}
        />
      )}

      {fullInstance && (
        <Alert tone="info">
          The databases selection is ignored in full instance mode: every database on the server is
          dumped into a single archive.
        </Alert>
      )}

      {hasDatabaseList && !fullInstance && (
        <DatabaseMultiSelect
          all={allDatabases}
          selected={selectedDatabases}
          discovered={discovered}
          onChange={(all, selected) => {
            setValue("allDatabases", all, { shouldDirty: true });
            setValue("databases", selected, { shouldDirty: true, shouldValidate: formState.isSubmitted });
          }}
          error={(errors.databases as { message?: string } | undefined)?.message}
        />
      )}

      {formError && <Alert tone="danger">{formError}</Alert>}

      <TestResultPanel pending={test.isPending} result={testResult} error={testError} />

      <DialogFooter>
        <Button type="button" variant="secondary" onClick={onClose} disabled={busy}>
          {record && testResult ? "Close" : "Cancel"}
        </Button>
        <Button type="button" variant="secondary" onClick={submit(false)} disabled={busy}>
          Save
        </Button>
        <Button type="button" onClick={submit(true)} disabled={busy}>
          Save and test
        </Button>
      </DialogFooter>
    </form>
  );
}

interface SourceDialogProps {
  open: boolean;
  source: SourceRecord | null;
  onClose: () => void;
}

export function SourceDialog({ open, source, onClose }: SourceDialogProps) {
  return (
    <DialogFrame open={open} onClose={onClose} title={source ? "Edit source" : "Add source"} maxWidth="max-w-2xl">
      <div className="px-6 pb-6 pt-4">
      <HelperText className="mb-4">
        Credentials are encrypted at rest and never shown again after saving.
      </HelperText>
      <SourceDialogBody source={source} onClose={onClose} />
      </div>
    </DialogFrame>
  );
}

function SourceDialogBody({ source, onClose }: Omit<SourceDialogProps, "open">) {
  const [type, setType] = useState<SourceType | null>(source?.type ?? null);
  if (type === null) return <TypePicker onPick={setType} />;
  return (
    <SourceForm
      type={type}
      source={source}
      onClose={onClose}
      onBack={source ? undefined : () => setType(null)}
    />
  );
}

import { standardSchemaResolver } from "@hookform/resolvers/standard-schema";
import { Loader2 } from "lucide-react";
import { useState } from "react";
import { useForm, useWatch, type Resolver } from "react-hook-form";
import { ApiError } from "@/api/client";
import { useSaveSource, useTestSource } from "@/api/sources";
import type { SourceRecord, SourceType, TestResult } from "@/api/types";
import { DatabaseMultiSelect } from "@/components/DatabaseMultiSelect";
import { FormField } from "@/components/FormField";
import { SecretField } from "@/components/SecretField";
import { TestResultPanel } from "@/components/TestResultPanel";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { useToast } from "@/components/ui/toast";
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
      <p className="text-sm text-muted-foreground">Choose the kind of database to back up.</p>
      <div className="grid gap-3 sm:grid-cols-2">
        {SOURCE_TYPES.map(({ type, label, hint }) => (
          <button
            key={type}
            type="button"
            onClick={() => onPick(type)}
            className="rounded-md border p-4 text-left transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <span className="block font-medium">{label}</span>
            <span className="block text-xs text-muted-foreground">{hint}</span>
          </button>
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
  const { register, control, setValue, formState } = form;
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

  const typeLabel = SOURCE_TYPES.find((t) => t.type === type)?.label ?? type;
  const isSqlLike = type === "mysql" || type === "postgres";
  const showHostFields = type !== "firestore" && !(type === "mongo" && mongoMode === "uri");
  const fullInstance = type === "mongo" && mongoDumpMode === "full_oplog";

  return (
    <form onSubmit={(event) => event.preventDefault()} className="space-y-5" noValidate>
      <p className="text-sm text-muted-foreground">
        {typeLabel}
        {onBack && record === null && (
          <Button type="button" variant="ghost" size="sm" className="ml-2" onClick={onBack}>
            Change type
          </Button>
        )}
      </p>

      {record?.secrets_error && (
        <div role="alert" className="rounded-md border border-destructive/50 p-3 text-sm text-destructive">
          {record.secrets_error}
        </div>
      )}

      <FormField id="name" label="Name" error={errors.name?.message}>
        <Input id="name" {...register("name")} autoComplete="off" />
      </FormField>

      {type === "mongo" && (
        <fieldset className="flex gap-4 text-sm">
          <legend className="mb-1 text-sm font-medium">Connection</legend>
          <label className="flex items-center gap-2">
            <input type="radio" value="fields" {...register("mongoMode")} /> Host fields
          </label>
          <label className="flex items-center gap-2">
            <input type="radio" value="uri" {...register("mongoMode")} /> Connection URI
          </label>
        </fieldset>
      )}

      {showHostFields && (
        <>
          <div className="grid grid-cols-[1fr_8rem] gap-3">
            <FormField id="host" label="Host" error={errors.host?.message}>
              <Input id="host" {...register("host")} placeholder="db.example.com" autoComplete="off" />
            </FormField>
            <FormField id="port" label="Port" error={errors.port?.message}>
              <Input id="port" inputMode="numeric" {...register("port")} />
            </FormField>
          </div>
          <FormField
            id="username"
            label={type === "mongo" ? "Username (optional)" : "Username"}
            error={errors.username?.message}
          >
            <Input id="username" {...register("username")} autoComplete="off" />
          </FormField>
        </>
      )}

      {type === "postgres" && (
        <FormField
          id="connectionDatabase"
          label="Connection database"
          hint="Used to log in and list databases."
          error={errors.connectionDatabase?.message}
        >
          <Input id="connectionDatabase" {...register("connectionDatabase")} />
        </FormField>
      )}

      {type === "postgres" && (
        <label className="flex items-start gap-2 text-sm">
          <input type="checkbox" className="mt-1" {...register("includeGlobals")} />
          <span>
            Also back up roles and other global objects
            <span className="block text-xs text-muted-foreground">
              Needs a superuser. If it fails, the run still succeeds and shows a warning.
            </span>
          </span>
        </label>
      )}

      {type === "mongo" && mongoMode === "fields" && (
        <>
          <div className="grid grid-cols-2 gap-3">
            <FormField id="authDatabase" label="Auth database" error={errors.authDatabase?.message}>
              <Input id="authDatabase" {...register("authDatabase")} />
            </FormField>
            <FormField id="replicaSet" label="Replica set (optional)" error={errors.replicaSet?.message}>
              <Input id="replicaSet" {...register("replicaSet")} />
            </FormField>
          </div>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" {...register("tls")} /> Use TLS
          </label>
        </>
      )}

      {isSqlLike && (
        <div className="grid grid-cols-2 gap-3">
          <FormField id="tlsMode" label="TLS mode">
            <Select id="tlsMode" {...register("tlsMode")}>
              {TLS_MODES[type].map((mode) => (
                <option key={mode} value={mode}>
                  {mode}
                </option>
              ))}
            </Select>
          </FormField>
          <FormField
            id="extraOption"
            label={type === "mysql" ? "Charset (optional)" : "Application name (optional)"}
            error={errors.extraOption?.message}
          >
            <Input id="extraOption" {...register("extraOption")} />
          </FormField>
        </div>
      )}

      {type === "firestore" && (
        <>
          <div className="grid grid-cols-2 gap-3">
            <FormField id="projectId" label="Project ID" error={errors.projectId?.message}>
              <Input id="projectId" {...register("projectId")} autoComplete="off" />
            </FormField>
            <FormField id="databaseId" label="Database ID" error={errors.databaseId?.message}>
              <Input id="databaseId" {...register("databaseId")} />
            </FormField>
          </div>
          <FormField
            id="stagingBucket"
            label="Staging bucket"
            hint="Where exports are written first, e.g. gs://my-backup-staging"
            error={errors.stagingBucket?.message}
          >
            <Input id="stagingBucket" {...register("stagingBucket")} placeholder="gs://" />
          </FormField>
          <FormField
            id="collectionIds"
            label="Collection IDs (optional)"
            hint="Comma-separated. Leave empty to export everything."
            error={errors.collectionIds?.message}
          >
            <Input id="collectionIds" {...register("collectionIds")} />
          </FormField>
          <label className="flex items-start gap-2 text-sm">
            <input
              type="checkbox"
              role="switch"
              className="mt-1"
              {...register("deleteStagingAfterDownload")}
            />
            <span>
              Delete staging export after download
              <span className="block text-xs text-muted-foreground">
                If deleting fails the run still succeeds and shows a warning.
              </span>
            </span>
          </label>
          <div className="space-y-1 rounded-md border p-3 text-sm">
            <p className="font-medium">Prerequisites</p>
            <ul className="list-disc space-y-1 pl-5 text-xs text-muted-foreground">
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
          </div>
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
        <FormField
          id="mongoDumpMode"
          label="Dump mode"
          hint={
            fullInstance
              ? "One archive of the whole instance with --oplog, consistent to a point in time. Needs a replica set; the run fails early on a standalone server."
              : "One archive per selected database. On a replica set the databases are not consistent with each other; use full instance for that."
          }
        >
          <Select id="mongoDumpMode" {...register("mongoDumpMode")}>
            <option value="per_database">Per database</option>
            <option value="full_oplog">Full instance with oplog</option>
          </Select>
        </FormField>
      )}

      {fullInstance && (
        <p className="rounded-md border p-3 text-sm text-muted-foreground">
          The databases selection is ignored in full instance mode: every database on the server is
          dumped into a single archive.
        </p>
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

      {formError && (
        <div role="alert" className="rounded-md border border-destructive/50 p-3 text-sm text-destructive">
          {formError}
        </div>
      )}

      <TestResultPanel pending={test.isPending} result={testResult} error={testError} />

      <div className="flex justify-end gap-2">
        <Button type="button" variant="secondary" onClick={onClose} disabled={busy}>
          {record && testResult ? "Close" : "Cancel"}
        </Button>
        <Button type="button" variant="secondary" onClick={submit(false)} disabled={busy}>
          {save.isPending && !test.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
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

interface SourceDialogProps {
  open: boolean;
  source: SourceRecord | null;
  onClose: () => void;
}

export function SourceDialog({ open, source, onClose }: SourceDialogProps) {
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={source ? "Edit source" : "Add source"}
      description="Credentials are encrypted at rest and never shown again after saving."
    >
      <SourceDialogBody source={source} onClose={onClose} />
    </Dialog>
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

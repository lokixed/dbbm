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
import { DialogFooter } from "@/components/DialogFrame";
import { ApiError } from "@/api/client";
import { useSaveSource, useTestSourceDraft } from "@/api/sources";
import type { SourceRecord, SourceType, TestResult } from "@/api/types";
import { SourceTypeIcon, SourceTypeLabel } from "@/components/SourceTypeIcon";
import { DatabaseMultiSelect } from "@/components/DatabaseMultiSelect";
import { ConnectionStringFill } from "@/components/sources/ConnectionStringFill";
import { FirestoreSetup } from "@/components/sources/FirestoreSetup";
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
import { ConnectionStringError, parseConnectionString, type ParsedConnection } from "@/lib/connectionString";
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
            <span className="flex items-start gap-3">
              <SourceTypeIcon type={type} className="h-8 w-8 !p-1" />
              <span>
                <span className="block font-medium">{label}</span>
                <span className="block text-xs font-normal text-app-muted">{hint}</span>
              </span>
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
  const test = useTestSourceDraft();

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

  // MySQL / PostgreSQL: "string" shows the connection-string box, which fills the host fields.
  const [sqlMode, setSqlMode] = useState<"fields" | "string">("fields");
  const [connString, setConnString] = useState("");
  const [connStringError, setConnStringError] = useState<string | null>(null);
  const specs = secretSpecs(type, mongoMode);
  const busy = save.isPending || test.isPending;
  const hasDatabaseList = type === "mysql" || type === "postgres" || type === "mongo";

  type Secrets = Record<string, SecretState>;

  const validateSecrets = (current: Secrets): boolean => {
    const found: Record<string, string> = {};
    for (const spec of specs) {
      const state = current[spec.field];
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

  // Tests the values in the form as they are now, saved or not.
  const runTest = async (values: SourceFormValues, current: Secrets) => {
    setTestError(null);
    setTestResult(null);
    try {
      const result = await test.mutateAsync({
        id: record?.id ?? null,
        input: toSourceInput(type, values, current),
      });
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

  const saveSource = async (values: SourceFormValues, current: Secrets) => {
    try {
      const saved = await save.mutateAsync({
        id: record?.id ?? null,
        input: toSourceInput(type, values, current),
      });
      setRecord(saved);
      setSecrets(initialSecrets(saved));
      toast({ title: "Source saved", description: saved.name, variant: "success" });
      onClose();
    } catch (error) {
      setFormError(error instanceof ApiError || error instanceof Error ? error.message : "Save failed");
    }
  };

  // Fills the fields from a parsed string. Returns the secrets with its password, because the state
  // update is not visible until the next render and Test / Save need it now.
  const fillFromString = (parsed: ParsedConnection, announce: boolean): Secrets => {
    for (const [key, value] of Object.entries(parsed.values)) {
      setValue(key as keyof SourceFormValues, value as never, { shouldDirty: true, shouldValidate: true });
    }
    const next: Secrets =
      parsed.password === null ? secrets : { ...secrets, password: { action: "set", value: parsed.password } };
    setSecrets(next);
    setSqlMode("fields");
    if (announce) {
      toast({ title: "Fields filled in", description: "Check them, then click Test connection.", variant: "success" });
    }
    return next;
  };

  // In connection-string mode, parses the pasted string into the fields first. Returns the secrets to
  // use, or null when the string is missing or cannot be read.
  const applyConnectionString = (announce: boolean): Secrets | null => {
    if ((type !== "mysql" && type !== "postgres") || sqlMode !== "string") return secrets;
    if (connString.trim() === "") {
      setConnStringError("Paste a connection string, or switch to Host fields.");
      return null;
    }
    try {
      const next = fillFromString(parseConnectionString(type, connString), announce);
      setConnString("");
      setConnStringError(null);
      return next;
    } catch (exc) {
      setConnStringError(exc instanceof ConnectionStringError ? exc.message : "Could not read the connection string.");
      return null;
    }
  };

  // Validates the form, then runs `action`. A string pasted but not yet filled in is applied first,
  // so Test and Save use it.
  const withValidForm = (action: (values: SourceFormValues, current: Secrets) => Promise<void>) => () => {
    const current = applyConnectionString(false);
    if (current === null) return;
    void form.handleSubmit(async (values) => {
      setFormError(null);
      if (!validateSecrets(current)) return;
      await action(values, current);
    })();
  };

  const checkConnection = withValidForm(runTest);
  const submit = withValidForm(saveSource);

  const setSecret = (field: string) => (state: SecretState) =>
    setSecrets((current) => ({ ...current, [field]: state }));

  const text = (name: keyof SourceFormValues) => ({
    ...bindText(form, name),
    error: (errors[name] as { message?: string } | undefined)?.message,
  });

  const typeLabel = SOURCE_TYPES.find((t) => t.type === type)?.label ?? type;
  const isSqlLike = type === "mysql" || type === "postgres";
  const sqlString = isSqlLike && sqlMode === "string";
  const showHostFields = type !== "firestore" && !(type === "mongo" && mongoMode === "uri") && !sqlString;

  const fullInstance = type === "mongo" && mongoDumpMode === "full_oplog";

  return (
    <form onSubmit={(event) => event.preventDefault()} className="space-y-5" noValidate>
      <p className="flex items-center text-sm text-app-muted">
        <SourceTypeLabel type={type}>{typeLabel}</SourceTypeLabel>
        {onBack && record === null && (
          <Button type="button" variant="flat" className="ml-2" onClick={onBack}>
            Change type
          </Button>
        )}
      </p>

      {record?.secrets_error && <Alert tone="danger">{record.secrets_error}</Alert>}

      <Field label="Name" autoComplete="off" {...text("name")} />

      {isSqlLike && (
        <RadioGroup
          label="Connection"
          name="sqlMode"
          direction="row"
          value={sqlMode}
          onChange={(value) => setSqlMode(value === "string" ? "string" : "fields")}
          options={[
            { value: "fields", label: "Host fields" },
            { value: "string", label: "Connection string" },
          ]}
        />
      )}

      {sqlString && (type === "mysql" || type === "postgres") && (
        <ConnectionStringFill
          type={type}
          value={connString}
          error={connStringError}
          onChange={(next) => {
            setConnString(next);
            setConnStringError(null);
          }}
          onFill={() => void applyConnectionString(true)}
        />
      )}

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
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Field label="Auth database" {...text("authDatabase")} />
            <Field label="Replica set (optional)" {...text("replicaSet")} />
          </div>
          <Checkbox label="Use TLS" {...bindBool(form, "tls")} />
        </>
      )}

      {isSqlLike && (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
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
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
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
          <FirestoreSetup open={source === null} />
        </>
      )}

      {specs.filter(() => !sqlString).map((spec) => (
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
        <div className="flex w-full flex-wrap items-center justify-between gap-2">
          <Button type="button" variant="secondary" onClick={checkConnection} disabled={busy}>
            {test.isPending ? "Testing..." : "Test connection"}
          </Button>
          <div className="flex gap-2">
            <Button type="button" variant="secondary" onClick={onClose} disabled={busy}>
              Cancel
            </Button>
            <Button type="button" onClick={submit} disabled={busy}>
              Save
            </Button>
          </div>
        </div>
      </DialogFooter>
    </form>
  );
}

/** Add or edit a source: a type picker first when adding, then the form. `onDone` runs after a
 * save or on Cancel. */
export function SourceEditor({ source, onDone }: { source: SourceRecord | null; onDone: () => void }) {
  const [type, setType] = useState<SourceType | null>(source?.type ?? null);
  if (type === null) return <TypePicker onPick={setType} />;
  return (
    <SourceForm
      type={type}
      source={source}
      onClose={onDone}
      onBack={source ? undefined : () => setType(null)}
    />
  );
}

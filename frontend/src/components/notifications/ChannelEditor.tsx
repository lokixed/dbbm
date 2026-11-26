import { standardSchemaResolver } from "@hookform/resolvers/standard-schema";
import { useState } from "react";
import { useForm, useWatch } from "react-hook-form";
import Alert from "xedonium/Alert";
import Badge from "xedonium/Badge";
import Button from "xedonium/Button";
import Field from "xedonium/Field";
import Input from "xedonium/Input";
import Loader from "xedonium/Loader";
import Select from "xedonium/Select";
import Switch from "xedonium/Switch";
import HelperText from "xedonium/HelperText";
import PlusIcon from "xedonium/icons/Plus";
import SendIcon from "xedonium/icons/Send";
import Trash2Icon from "xedonium/icons/Trash2";
import { DialogFooter } from "@/components/DialogFrame";
import { useSaveChannel, useTestChannelDraft } from "@/api/notifications";
import type { ChannelRecord, ChannelTestResult } from "@/api/types";
import { SecretField } from "@/components/SecretField";
import { useToast } from "@/components/toast";
import {
  channelDefaults,
  channelSchema,
  channelSecrets,
  channelToValues,
  defaultPort,
  headerRowsError,
  toChannelInput,
  type ChannelFormValues,
  type HeadersState,
} from "@/lib/channelForm";
import { bindText } from "@/lib/formBinding";
import { initialSecret, secretPayload, type SecretState } from "@/lib/secrets";
import { ChannelTypeLabel } from "@/components/ChannelTypeIcon";
import { CHANNEL_TYPE_NAME } from "@/lib/channelType";

const INSECURE_EMAIL =
  "Security 'none' sends messages and the SMTP password unencrypted. Use it only on a trusted network.";

type SecretName = "webhook_url" | "url" | "hmac_secret" | "password";

function initialSecrets(record: ChannelRecord | null): Record<SecretName, SecretState> {
  const set = record?.secrets_set ?? {};
  return {
    webhook_url: initialSecret(set.webhook_url ?? false),
    url: initialSecret(set.url ?? false),
    hmac_secret: initialSecret(set.hmac_secret ?? false),
    password: initialSecret(set.password ?? false),
  };
}

function initialHeaders(record: ChannelRecord | null): HeadersState {
  return record?.secrets_set.headers ? { action: "keep" } : { action: "set", rows: [] };
}

function urlProblem(state: SecretState, isSet: boolean, httpsOnly: boolean): string | null {
  const value = secretPayload(state);
  if (value === undefined || value === null) {
    return state.action === "keep" && isSet ? null : "This URL is required";
  }
  const pattern = httpsOnly ? /^https:\/\/[^\s/]+/ : /^https?:\/\/[^\s/]+/;
  return pattern.test(value.trim()) ? null : `Enter a${httpsOnly ? "n https://" : "n http(s)://"} URL`;
}

function HeadersEditor({
  state,
  names,
  onChange,
  error,
}: {
  state: HeadersState;
  names: string[];
  onChange: (state: HeadersState) => void;
  error: string | null;
}) {
  if (state.action === "keep") {
    return (
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span>Headers:</span>
        {names.map((n) => (
          <Badge key={n} badgeContent={n} color="success" />
        ))}
        <Button
          type="button"
          variant="secondary"
          onClick={() => onChange({ action: "set", rows: names.map((name) => ({ name, value: "" })) })}
        >
          Change
        </Button>
        <Button type="button" variant="flat" onClick={() => onChange({ action: "clear" })}>
          Clear
        </Button>
      </div>
    );
  }
  if (state.action === "clear") {
    return (
      <div className="flex items-center gap-2 text-sm">
        <Badge badgeContent="Headers will be cleared when you save" color="danger" />
        <Button type="button" variant="flat" onClick={() => onChange({ action: "keep" })}>
          Undo
        </Button>
      </div>
    );
  }
  const rows = state.rows;
  const update = (index: number, field: "name" | "value", value: string) =>
    onChange({ action: "set", rows: rows.map((r, i) => (i === index ? { ...r, [field]: value } : r)) });
  return (
    <div className="space-y-2">
      <p className="text-sm font-medium">Extra headers (optional, stored encrypted)</p>
      {rows.map((row, index) => (
        <div key={index} className="flex gap-2">
          <Input
            aria-label={`Header ${index + 1} name`}
            value={row.name}
            placeholder="Authorization"
            onChange={(value) => update(index, "name", value)}
          />
          <Input
            aria-label={`Header ${index + 1} value`}
            type="password"
            autoComplete="new-password"
            value={row.value}
            onChange={(value) => update(index, "value", value)}
          />
          <Button
            type="button"
            variant="flat"
            aria-label={`Remove header ${index + 1}`}
            onClick={() => onChange({ action: "set", rows: rows.filter((_r, i) => i !== index) })}
          >
            <Trash2Icon className="h-4 w-4" />
          </Button>
        </div>
      ))}
      <div className="flex gap-2">
        <Button
          type="button"
          variant="secondary"
          disabled={rows.length >= 10}
          onClick={() => onChange({ action: "set", rows: [...rows, { name: "", value: "" }] })}
        >
          <PlusIcon className="h-4 w-4" /> Add header
        </Button>
        {names.length > 0 && (
          <Button type="button" variant="flat" onClick={() => onChange({ action: "keep" })}>
            Keep saved headers
          </Button>
        )}
      </div>
      {error && <Alert tone="danger">{error}</Alert>}
    </div>
  );
}

/** Add or edit a notification channel. `onClose` runs after a save or on Cancel. */
export function ChannelForm({ channel, onClose }: { channel: ChannelRecord | null; onClose: () => void }) {
  const { toast } = useToast();
  const save = useSaveChannel();
  const test = useTestChannelDraft();
  const record = channel;
  const [secrets, setSecrets] = useState(() => initialSecrets(channel));
  const [headers, setHeaders] = useState<HeadersState>(() => initialHeaders(channel));
  const [problems, setProblems] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<ChannelTestResult | null>(null);

  const form = useForm<ChannelFormValues>({
    resolver: standardSchemaResolver(channelSchema),
    defaultValues: channel ? channelToValues(channel) : channelDefaults,
  });
  const { formState, control, setValue } = form;
  const errors = formState.errors;
  const type = useWatch({ control, name: "type" });
  const security = useWatch({ control, name: "security" });
  const enabled = useWatch({ control, name: "enabled" });
  const set = record?.secrets_set ?? {};
  const busy = save.isPending || test.isPending;

  const setSecret = (name: SecretName) => (state: SecretState) => setSecrets((s) => ({ ...s, [name]: state }));

  const checkSecrets = (values: ChannelFormValues): boolean => {
    const found: Record<string, string> = {};
    if (values.type === "slack") {
      const problem = urlProblem(secrets.webhook_url, set.webhook_url ?? false, true);
      if (problem) found.webhook_url = problem;
    }
    if (values.type === "webhook") {
      const problem = urlProblem(secrets.url, set.url ?? false, false);
      if (problem) found.url = problem;
      const hmac = secretPayload(secrets.hmac_secret);
      if (typeof hmac === "string" && hmac.length < 16) found.hmac_secret = "Use at least 16 characters";
      const headerProblem = headers.action === "set" ? headerRowsError(headers.rows) : null;
      if (headerProblem) found.headers = headerProblem;
    }
    if (values.type === "email") {
      const hasPassword =
        (secrets.password.action === "keep" && (set.password ?? false)) ||
        typeof secretPayload(secrets.password) === "string";
      if (values.username.trim() && !hasPassword) found.password = "Enter the password for this username";
      if (!values.username.trim() && hasPassword && secrets.password.action === "set") {
        found.password = "Enter the username too, or leave the password empty";
      }
    }
    setProblems(found);
    return Object.keys(found).length === 0;
  };

  const secretsInput = (values: ChannelFormValues) => {
    if (values.type === "slack") return channelSecrets({ webhook_url: secrets.webhook_url }, null);
    if (values.type === "webhook") {
      return channelSecrets({ url: secrets.url, hmac_secret: secrets.hmac_secret }, headers);
    }
    // No username means no login, so a saved password is cleared with it.
    const password: SecretState =
      !values.username.trim() && secrets.password.action === "keep" && set.password
        ? { action: "clear" }
        : secrets.password;
    return channelSecrets({ password }, null);
  };

  const sendTest = form.handleSubmit(async (values) => {
    setFormError(null);
    setTestResult(null);
    if (!checkSecrets(values)) return;
    try {
      const result = await test.mutateAsync({
        id: record?.id ?? null,
        input: toChannelInput(values, secretsInput(values)),
      });
      setTestResult(result);
      toast({
        title: result.ok ? "Test message sent" : "Test message failed",
        description: result.message,
        variant: result.ok ? "success" : "error",
      });
    } catch (error) {
      setTestResult({ ok: false, message: error instanceof Error ? error.message : "Test failed", event: null });
    }
  });

  const submit = form.handleSubmit(async (values) => {
    setFormError(null);
    if (!checkSecrets(values)) return;
    try {
      const saved = await save.mutateAsync({
        id: record?.id ?? null,
        input: toChannelInput(values, secretsInput(values)),
      });
      toast({ title: "Channel saved", description: saved.name, variant: "success" });
      onClose();
    } catch (error) {
      setFormError(error instanceof Error ? error.message : "Save failed");
    }
  });

  const text = (name: keyof ChannelFormValues) => ({
    ...bindText(form, name),
    error: (errors[name] as { message?: string } | undefined)?.message,
  });

  return (
    <form onSubmit={(event) => event.preventDefault()} className="space-y-5" noValidate>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Select
          label="Type"
          disabled={record !== null}
          options={[
            { value: "slack", label: <ChannelTypeLabel type="slack">Slack (incoming webhook)</ChannelTypeLabel> },
            { value: "email", label: <ChannelTypeLabel type="email">Email (SMTP)</ChannelTypeLabel> },
            { value: "webhook", label: <ChannelTypeLabel type="webhook">Webhook (JSON POST)</ChannelTypeLabel> },
          ]}
          {...bindText(form, "type")}
        />
        <Field label="Name" autoComplete="off" {...text("name")} />
      </div>
      <HelperText>
        Need help setting up {CHANNEL_TYPE_NAME[type]}?{" "}
        <a
          className="underline"
          href={`/docs/notifications#${type}`}
          target="_blank"
          rel="noopener noreferrer"
        >
          Read the {CHANNEL_TYPE_NAME[type]} setup guide
        </a>
        .
      </HelperText>
      <Switch
        checked={enabled}
        onChange={(value) => setValue("enabled", value, { shouldDirty: true })}
        label="Enabled"
      />

      {record?.secrets_error && <Alert tone="danger">{record.secrets_error}</Alert>}

      {type === "slack" && (
        <SecretField
          id="channel-slack-url"
          label="Slack incoming webhook URL"
          isSet={set.webhook_url ?? false}
          state={secrets.webhook_url}
          onChange={setSecret("webhook_url")}
          required
          placeholder="https://hooks.slack.com/services/..."
          error={problems.webhook_url}
        />
      )}

      {type === "webhook" && (
        <div className="space-y-4">
          <SecretField
            id="channel-webhook-url"
            label="Webhook URL"
            isSet={set.url ?? false}
            state={secrets.url}
            onChange={setSecret("url")}
            required
            placeholder="https://example.com/hooks/dbbm"
            error={problems.url}
          />
          <SecretField
            id="channel-hmac"
            label="HMAC secret (optional, signs X-Signature-256)"
            isSet={set.hmac_secret ?? false}
            state={secrets.hmac_secret}
            onChange={setSecret("hmac_secret")}
            error={problems.hmac_secret}
          />
          <HeadersEditor
            state={headers}
            names={record?.header_names ?? []}
            onChange={setHeaders}
            error={problems.headers ?? null}
          />
        </div>
      )}

      {type === "email" && (
        <div className="space-y-4">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <div className="sm:col-span-2">
              <Field label="SMTP host" placeholder="smtp.example.com" autoComplete="off" {...text("host")} />
            </div>
            <Field label="Port" inputMode="numeric" {...text("port")} />
          </div>
          <Select
            label="Security"
            options={[
              { value: "starttls", label: "STARTTLS (port 587)" },
              { value: "ssl", label: "SSL/TLS (port 465)" },
              { value: "none", label: "None (unencrypted)" },
            ]}
            {...bindText(form, "security")}
            onChange={(value) => {
              setValue("security", value as ChannelFormValues["security"], { shouldDirty: true });
              setValue("port", defaultPort(value as ChannelFormValues["security"]));
            }}
          />
          {security === "none" && <Alert tone="warning">{INSECURE_EMAIL}</Alert>}
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Field label="Username (optional)" autoComplete="off" {...text("username")} />
            <SecretField
              id="channel-password"
              label="Password"
              isSet={set.password ?? false}
              state={secrets.password}
              onChange={setSecret("password")}
              error={problems.password}
            />
          </div>
          <Field label="From" placeholder="dbbm@example.com" autoComplete="off" {...text("from")} />
          <Field
            label="To"
            helperText="One or more addresses, separated by commas."
            placeholder="ops@example.com"
            autoComplete="off"
            {...text("to")}
          />
        </div>
      )}

      {formError && <Alert tone="danger">{formError}</Alert>}
      {test.isPending && <Loader variant="dots" label="Sending a test message" />}
      {testResult && (
        <Alert tone={testResult.ok ? "success" : "danger"}>
          {testResult.ok ? "Delivered: " : "Failed: "}
          {testResult.message}
        </Alert>
      )}

      <DialogFooter>
        <div className="flex w-full flex-wrap items-center justify-between gap-2">
          <Button type="button" variant="secondary" onClick={sendTest} disabled={busy}>
            <SendIcon className="h-4 w-4" />
            Send Test
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

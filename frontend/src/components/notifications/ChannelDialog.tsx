import { standardSchemaResolver } from "@hookform/resolvers/standard-schema";
import { Loader2, Plus, Send, Trash2 } from "lucide-react";
import { useState } from "react";
import { useForm, useWatch } from "react-hook-form";
import { useSaveChannel, useTestChannel } from "@/api/notifications";
import type { ChannelRecord, ChannelTestResult } from "@/api/types";
import { FormField } from "@/components/FormField";
import { SecretField } from "@/components/SecretField";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { useToast } from "@/components/ui/toast";
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
import { initialSecret, secretPayload, type SecretState } from "@/lib/secrets";

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
    return (state.action === "keep" && isSet) ? null : "This URL is required";
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
          <Badge key={n} variant="success">
            {n}
          </Badge>
        ))}
        <Button type="button" size="sm" variant="secondary" onClick={() => onChange({ action: "set", rows: names.map((name) => ({ name, value: "" })) })}>
          Change
        </Button>
        <Button type="button" size="sm" variant="ghost" onClick={() => onChange({ action: "clear" })}>
          Clear
        </Button>
      </div>
    );
  }
  if (state.action === "clear") {
    return (
      <div className="flex items-center gap-2 text-sm">
        <Badge variant="danger">Headers will be cleared when you save</Badge>
        <Button type="button" size="sm" variant="ghost" onClick={() => onChange({ action: "keep" })}>
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
            onChange={(event) => update(index, "name", event.target.value)}
          />
          <Input
            aria-label={`Header ${index + 1} value`}
            type="password"
            autoComplete="new-password"
            value={row.value}
            onChange={(event) => update(index, "value", event.target.value)}
          />
          <Button
            type="button"
            size="sm"
            variant="ghost"
            aria-label={`Remove header ${index + 1}`}
            onClick={() => onChange({ action: "set", rows: rows.filter((_r, i) => i !== index) })}
          >
            <Trash2 className="h-4 w-4" />
          </Button>
        </div>
      ))}
      <div className="flex gap-2">
        <Button
          type="button"
          size="sm"
          variant="secondary"
          disabled={rows.length >= 10}
          onClick={() => onChange({ action: "set", rows: [...rows, { name: "", value: "" }] })}
        >
          <Plus className="h-4 w-4" /> Add header
        </Button>
        {names.length > 0 && (
          <Button type="button" size="sm" variant="ghost" onClick={() => onChange({ action: "keep" })}>
            Keep saved headers
          </Button>
        )}
      </div>
      {error && (
        <p role="alert" className="text-xs text-destructive">
          {error}
        </p>
      )}
    </div>
  );
}

function ChannelForm({ channel, onClose }: { channel: ChannelRecord | null; onClose: () => void }) {
  const { toast } = useToast();
  const save = useSaveChannel();
  const test = useTestChannel();
  const [record, setRecord] = useState<ChannelRecord | null>(channel);
  const [secrets, setSecrets] = useState(() => initialSecrets(channel));
  const [headers, setHeaders] = useState<HeadersState>(() => initialHeaders(channel));
  const [problems, setProblems] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<ChannelTestResult | null>(null);

  const form = useForm<ChannelFormValues>({
    resolver: standardSchemaResolver(channelSchema),
    defaultValues: channel ? channelToValues(channel) : channelDefaults,
  });
  const { register, formState, control, setValue } = form;
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

  const runTest = async (id: string) => {
    setTestResult(null);
    try {
      const result = await test.mutateAsync(id);
      setTestResult(result);
      toast({
        title: result.ok ? "Test message sent" : "Test message failed",
        description: result.message,
        variant: result.ok ? "success" : "error",
      });
    } catch (error) {
      setTestResult({ ok: false, message: error instanceof Error ? error.message : "Test failed", event: null });
    }
  };

  const submit = (andTest: boolean) =>
    form.handleSubmit(async (values) => {
      setFormError(null);
      if (!checkSecrets(values)) return;
      try {
        const saved = await save.mutateAsync({ id: record?.id ?? null, input: toChannelInput(values, secretsInput(values)) });
        setRecord(saved);
        form.reset(channelToValues(saved));
        setSecrets(initialSecrets(saved));
        setHeaders(initialHeaders(saved));
        toast({ title: "Channel saved", description: saved.name, variant: "success" });
        if (andTest) await runTest(saved.id);
        else onClose();
      } catch (error) {
        setFormError(error instanceof Error ? error.message : "Save failed");
      }
    });

  return (
    <form onSubmit={(event) => event.preventDefault()} className="space-y-5" noValidate>
      <div className="grid grid-cols-2 gap-3">
        <FormField id="channel-type" label="Type">
          <Select id="channel-type" disabled={record !== null} {...register("type")}>
            <option value="slack">Slack (incoming webhook)</option>
            <option value="email">Email (SMTP)</option>
            <option value="webhook">Webhook (JSON POST)</option>
          </Select>
        </FormField>
        <FormField id="channel-name" label="Name" error={errors.name?.message}>
          <Input id="channel-name" {...register("name")} autoComplete="off" />
        </FormField>
      </div>
      <Switch
        checked={enabled}
        onChange={(value) => setValue("enabled", value, { shouldDirty: true })}
        label="Enabled"
      />

      {record?.secrets_error && (
        <div role="alert" className="rounded-md border border-destructive/50 p-3 text-sm text-destructive">
          {record.secrets_error}
        </div>
      )}

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
            placeholder="https://example.com/hooks/dbbackup"
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
          <div className="grid grid-cols-3 gap-3">
            <div className="col-span-2">
              <FormField id="channel-host" label="SMTP host" error={errors.host?.message}>
                <Input id="channel-host" {...register("host")} placeholder="smtp.example.com" autoComplete="off" />
              </FormField>
            </div>
            <FormField id="channel-port" label="Port" error={errors.port?.message}>
              <Input id="channel-port" inputMode="numeric" {...register("port")} />
            </FormField>
          </div>
          <FormField id="channel-security" label="Security">
            <Select
              id="channel-security"
              {...register("security", {
                onChange: (event: { target: { value: "starttls" | "ssl" | "none" } }) =>
                  setValue("port", defaultPort(event.target.value)),
              })}
            >
              <option value="starttls">STARTTLS (port 587)</option>
              <option value="ssl">SSL/TLS (port 465)</option>
              <option value="none">None (unencrypted)</option>
            </Select>
          </FormField>
          {security === "none" && (
            <div role="alert" className="rounded-md border border-amber-500/50 bg-amber-500/10 p-3 text-sm">
              {INSECURE_EMAIL}
            </div>
          )}
          <div className="grid grid-cols-2 gap-3">
            <FormField id="channel-username" label="Username (optional)">
              <Input id="channel-username" {...register("username")} autoComplete="off" />
            </FormField>
            <SecretField
              id="channel-password"
              label="Password"
              isSet={set.password ?? false}
              state={secrets.password}
              onChange={setSecret("password")}
              error={problems.password}
            />
          </div>
          <FormField id="channel-from" label="From" error={errors.from?.message}>
            <Input id="channel-from" {...register("from")} placeholder="dbbackup@example.com" autoComplete="off" />
          </FormField>
          <FormField id="channel-to" label="To" hint="One or more addresses, separated by commas." error={errors.to?.message}>
            <Input id="channel-to" {...register("to")} placeholder="ops@example.com" autoComplete="off" />
          </FormField>
        </div>
      )}

      {formError && (
        <div role="alert" className="rounded-md border border-destructive/50 p-3 text-sm text-destructive">
          {formError}
        </div>
      )}
      {test.isPending && (
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" /> Sending a test message...
        </p>
      )}
      {testResult && (
        <div
          role="status"
          className={`rounded-md border p-3 text-sm ${testResult.ok ? "border-green-600/40" : "border-destructive/50 text-destructive"}`}
        >
          {testResult.ok ? "Delivered: " : "Failed: "}
          {testResult.message}
        </div>
      )}

      <div className="flex justify-end gap-2">
        <Button type="button" variant="secondary" onClick={onClose} disabled={busy}>
          {record && testResult ? "Close" : "Cancel"}
        </Button>
        <Button type="button" variant="secondary" onClick={submit(false)} disabled={busy}>
          Save
        </Button>
        <Button type="button" onClick={submit(true)} disabled={busy}>
          {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
          Save and send test
        </Button>
      </div>
    </form>
  );
}

interface ChannelDialogProps {
  open: boolean;
  channel: ChannelRecord | null;
  onClose: () => void;
}

export function ChannelDialog({ open, channel, onClose }: ChannelDialogProps) {
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={channel ? "Edit notification channel" : "Add notification channel"}
      description="Secrets (webhook URLs, passwords, headers) are stored encrypted and never shown again."
    >
      <ChannelForm channel={channel} onClose={onClose} />
    </Dialog>
  );
}

import { z } from "zod";
import type {
  ChannelInput,
  ChannelRecord,
  ChannelSecretsInput,
  ChannelType,
  EmailSecurity,
} from "@/api/types";
import { buildSecrets, type SecretState } from "@/lib/secrets";
import { isValidHost, nameField } from "@/lib/validation";

export interface ChannelFormValues {
  type: ChannelType;
  name: string;
  enabled: boolean;
  host: string;
  port: string;
  security: EmailSecurity;
  username: string;
  from: string;
  to: string;
}

export interface HeaderRow {
  name: string;
  value: string;
}

export type HeadersState = { action: "keep" } | { action: "clear" } | { action: "set"; rows: HeaderRow[] };

const EMAIL = /^[^@\s<>,;"]{1,64}@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,63}$/;
const HEADER_NAME = /^[A-Za-z0-9!#$%&'*+.^_`|~-]{1,64}$/;
const RESERVED = new Set(["host", "content-length", "content-type", "transfer-encoding", "connection", "x-signature-256"]);
const DEFAULT_PORTS: Record<EmailSecurity, string> = { starttls: "587", ssl: "465", none: "25" };

export function splitRecipients(raw: string): string[] {
  return raw
    .split(/[,;\s]+/)
    .map((v) => v.trim())
    .filter(Boolean);
}

export const channelSchema = z
  .object({
    type: z.enum(["slack", "email", "webhook"]),
    name: nameField,
    enabled: z.boolean(),
    host: z.string(),
    port: z.string(),
    security: z.enum(["starttls", "ssl", "none"]),
    username: z.string(),
    from: z.string(),
    to: z.string(),
  })
  .superRefine((values, ctx) => {
    if (values.type !== "email") return;
    if (!isValidHost(values.host)) {
      ctx.addIssue({ code: "custom", path: ["host"], message: "Enter a host name or IP address" });
    }
    const port = Number(values.port);
    if (!/^\d+$/.test(values.port.trim()) || port < 1 || port > 65535) {
      ctx.addIssue({ code: "custom", path: ["port"], message: "Port must be 1-65535" });
    }
    if (!EMAIL.test(values.from.trim())) {
      ctx.addIssue({ code: "custom", path: ["from"], message: "Enter a valid sender address" });
    }
    const recipients = splitRecipients(values.to);
    if (recipients.length === 0 || recipients.length > 20) {
      ctx.addIssue({ code: "custom", path: ["to"], message: "Enter 1-20 recipient addresses" });
    } else if (recipients.some((r) => !EMAIL.test(r))) {
      ctx.addIssue({ code: "custom", path: ["to"], message: "One of the addresses is not valid" });
    }
  });

export const channelDefaults: ChannelFormValues = {
  type: "slack",
  name: "",
  enabled: true,
  host: "",
  port: DEFAULT_PORTS.starttls,
  security: "starttls",
  username: "",
  from: "",
  to: "",
};

export function defaultPort(security: EmailSecurity): string {
  return DEFAULT_PORTS[security];
}

export function channelToValues(record: ChannelRecord): ChannelFormValues {
  const base = { ...channelDefaults, type: record.type, name: record.name, enabled: record.enabled };
  if (record.type !== "email") return base;
  const config = record.config;
  return {
    ...base,
    host: config.host,
    port: String(config.port),
    security: config.security,
    username: config.username ?? "",
    from: config.from,
    to: config.to.join(", "),
  };
}

export function headerRowsError(rows: HeaderRow[]): string | null {
  const used = rows.filter((r) => r.name.trim() || r.value);
  if (used.length > 10) return "At most 10 headers";
  for (const row of used) {
    const name = row.name.trim();
    if (!HEADER_NAME.test(name)) return `Invalid header name: ${name || "(empty)"}`;
    if (RESERVED.has(name.toLowerCase())) return `The ${name} header is set automatically`;
    if (/[\r\n]/.test(row.value)) return `Invalid value for ${name}`;
  }
  return null;
}

export function channelSecrets(
  strings: Record<string, SecretState>,
  headers: HeadersState | null,
): ChannelSecretsInput {
  const secrets: ChannelSecretsInput = buildSecrets(strings);
  if (headers?.action === "clear") secrets.headers = null;
  if (headers?.action === "set") {
    const rows = headers.rows.filter((r) => r.name.trim());
    secrets.headers = rows.length ? Object.fromEntries(rows.map((r) => [r.name.trim(), r.value])) : null;
  }
  return secrets;
}

export function toChannelInput(values: ChannelFormValues, secrets: ChannelSecretsInput): ChannelInput {
  const name = values.name.trim();
  if (values.type === "email") {
    return {
      type: "email",
      name,
      enabled: values.enabled,
      config: {
        host: values.host.trim(),
        port: Number(values.port),
        security: values.security,
        username: values.username.trim() || null,
        from: values.from.trim(),
        to: splitRecipients(values.to),
      },
      secrets,
    };
  }
  return { type: values.type, name, enabled: values.enabled, secrets };
}

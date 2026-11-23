import { z } from "zod";
import type {
  DestinationInput,
  DestinationRecord,
  DestinationType,
  DriveScope,
  SecretsInput,
} from "@/api/types";
import { hasControlChars, nameField } from "@/lib/validation";

export const CHUNK_SIZES_MB = [8, 16, 32, 64, 128, 256] as const;

export interface DestinationFormValues {
  type: DestinationType;
  name: string;
  path: string;
  remoteFolder: string;
  scope: DriveScope;
  sharedDriveId: string;
  clientId: string;
  deletePermanently: boolean;
  chunkSizeMb: string;
  keepLast: string;
  keepDays: string;
}

function hasForbiddenChars(value: string): boolean {
  return hasControlChars(value) || value.includes("\\");
}

function optionalInt(max: number, label: string) {
  return z
    .string()
    .trim()
    .refine((v) => v === "" || (/^\d+$/.test(v) && Number(v) >= 1 && Number(v) <= max), {
      message: `${label} must be a whole number between 1 and ${max}`,
    });
}

/** Mirrors the backend: the same rules, so most mistakes show up before saving. */
export function folderError(raw: string): string | null {
  if (raw.length > 1024) return "Max 1024 characters";
  if (hasControlChars(raw) || /[\\:*?"<>|]/.test(raw)) {
    return 'Must not contain \\ : * ? " < > | or control characters';
  }
  const segments = raw.trim().split("/").filter(Boolean);
  if (segments.length === 0) return "Folder is required";
  if (segments.some((s) => s === "." || s === "..")) return "'.' and '..' are not allowed";
  if (segments.some((s) => s !== s.trim())) return "Folder names must not start or end with spaces";
  return null;
}

export function normalizeFolder(raw: string): string {
  return raw.trim().split("/").filter(Boolean).join("/");
}

/** The backend additionally resolves symlinks against BACKUP_DIR; this only catches the obvious cases. */
export const destinationSchema = z
  .object({
    type: z.enum(["local", "gdrive"]),
    name: nameField,
    path: z.string(),
    remoteFolder: z.string(),
    scope: z.enum(["drive.file", "drive"]),
    sharedDriveId: z
      .string()
      .trim()
      .refine((v) => v === "" || /^[A-Za-z0-9_-]{1,128}$/.test(v), "Only letters, digits, '-' and '_'"),
    clientId: z
      .string()
      .trim()
      .refine((v) => v === "" || /^[A-Za-z0-9._-]{1,256}$/.test(v), "Invalid OAuth client ID"),
    deletePermanently: z.boolean(),
    chunkSizeMb: z.string(),
    keepLast: optionalInt(100_000, "Keep last"),
    keepDays: optionalInt(36_500, "Keep days"),
  })
  .superRefine((values, ctx) => {
    if (values.type === "local") {
      const path = values.path.trim();
      let message: string | null = null;
      if (!path) message = "Path is required";
      else if (path.length > 255) message = "Max 255 characters";
      else if (hasForbiddenChars(path)) message = "Path contains forbidden characters";
      else if (path.split("/").includes("..")) message = "'..' is not allowed in paths";
      if (message) ctx.addIssue({ code: "custom", path: ["path"], message });
      return;
    }
    const message = folderError(values.remoteFolder);
    if (message) ctx.addIssue({ code: "custom", path: ["remoteFolder"], message });
  });

export const destinationDefaults: DestinationFormValues = {
  type: "local",
  name: "",
  path: "",
  remoteFolder: "db-backups",
  scope: "drive.file",
  sharedDriveId: "",
  clientId: "",
  deletePermanently: false,
  chunkSizeMb: "64",
  keepLast: "",
  keepDays: "",
};

export function recordToValues(record: DestinationRecord): DestinationFormValues {
  const retention = {
    keepLast: record.retention.keep_last === null ? "" : String(record.retention.keep_last),
    keepDays: record.retention.keep_days === null ? "" : String(record.retention.keep_days),
  };
  if (record.type === "local") {
    return { ...destinationDefaults, ...retention, type: "local", name: record.name, path: record.config.path };
  }
  const config = record.config;
  return {
    ...destinationDefaults,
    ...retention,
    type: "gdrive",
    name: record.name,
    remoteFolder: config.remote_folder,
    scope: config.scope,
    sharedDriveId: config.shared_drive_id ?? "",
    clientId: config.client_id ?? "",
    deletePermanently: config.delete_permanently,
    chunkSizeMb: String(config.chunk_size_mb),
  };
}

export function toDestinationInput(
  values: DestinationFormValues,
  secrets: SecretsInput,
): DestinationInput {
  const retention = {
    keep_last: values.keepLast.trim() === "" ? null : Number(values.keepLast),
    keep_days: values.keepDays.trim() === "" ? null : Number(values.keepDays),
  };
  const name = values.name.trim();
  if (values.type === "local") {
    return { type: "local", name, config: { path: values.path.trim() }, retention };
  }
  return {
    type: "gdrive",
    name,
    config: {
      remote_folder: normalizeFolder(values.remoteFolder),
      scope: values.scope,
      shared_drive_id: values.sharedDriveId.trim() || null,
      client_id: values.clientId.trim() || null,
      delete_permanently: values.deletePermanently,
      chunk_size_mb: Number(values.chunkSizeMb),
    },
    secrets,
    retention,
  };
}

function base64Url(text: string): string {
  const bytes = new TextEncoder().encode(text);
  let binary = "";
  bytes.forEach((b) => (binary += String.fromCharCode(b)));
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/**
 * `rclone authorize drive <blob>`: the blob is base64url JSON of rclone options, so the
 * token is issued for exactly this scope and OAuth client. Null when a secret is missing.
 */
export function authorizeCommand(
  scope: DriveScope,
  clientId: string,
  clientSecret: string,
): string | null {
  const options: Record<string, string> = {};
  if (clientId.trim()) {
    if (!clientSecret) return null;
    options.client_id = clientId.trim();
    options.client_secret = clientSecret;
  }
  options.scope = scope;
  return `rclone authorize "drive" "${base64Url(JSON.stringify(options))}"`;
}

/** A retention field as the API expects it: a positive integer, or null when empty. */
export function parsePolicyValue(raw: string): number | null {
  const value = raw.trim();
  return /^\d+$/.test(value) && Number(value) >= 1 ? Number(value) : null;
}

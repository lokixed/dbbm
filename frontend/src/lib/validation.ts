import { z } from "zod";

const LABEL = /^(?!-)[A-Za-z0-9_-]{1,63}(?<!-)$/;

export function hasControlChars(value: string): boolean {
  return [...value].some((ch) => ch.charCodeAt(0) < 32 || ch.charCodeAt(0) === 127);
}

function isIPv4(value: string): boolean {
  const parts = value.split(".");
  return parts.length === 4 && parts.every((p) => /^\d{1,3}$/.test(p) && Number(p) <= 255);
}

function isIPv6(value: string): boolean {
  return (
    value.includes(":") &&
    /^[0-9a-fA-F:.]+$/.test(value) &&
    value.split("::").length <= 2 &&
    value.length <= 45
  );
}

/** Mirrors the backend host validator: hostname or IP, no scheme, port or path. */
export function isValidHost(input: string): boolean {
  const host = input.trim();
  if (!host || host.length > 253) return false;
  const bare = host.startsWith("[") && host.endsWith("]") ? host.slice(1, -1) : host;
  if (isIPv4(bare) || isIPv6(bare)) return true;
  if (/^[\d.]+$/.test(host)) return false;
  return host
    .replace(/\.$/, "")
    .split(".")
    .every((label) => LABEL.test(label));
}

export const nameField = z.string().trim().min(1, "Name is required").max(64, "Max 64 characters");

export const hostField = z
  .string()
  .trim()
  .refine(isValidHost, "Enter a hostname or IP address (no scheme, port or path)");

export const portField = z
  .string()
  .trim()
  .regex(/^\d+$/, "Port must be a number")
  .refine((v) => Number(v) >= 1 && Number(v) <= 65535, "Port must be between 1 and 65535");

export const usernameField = z
  .string()
  .min(1, "Username is required")
  .max(128, "Max 128 characters")
  .refine((v) => !hasControlChars(v), "Control characters are not allowed");

export const dbNameField = z
  .string()
  .trim()
  .min(1, "Required")
  .max(128, "Max 128 characters")
  .refine((v) => !hasControlChars(v), "Control characters are not allowed");

const BUCKET = /^[a-z0-9][a-z0-9._-]{1,61}[a-z0-9]$/;
const PREFIX = /^[A-Za-z0-9._-]+(\/[A-Za-z0-9._-]+)*$/;

/** Returns an error message, or null when valid. */
export function checkStagingBucket(value: string): string | null {
  const text = value.trim().replace(/\/+$/, "");
  if (!text.startsWith("gs://")) return "Must start with gs://";
  const [bucket, ...rest] = text.slice(5).split("/");
  const prefix = rest.join("/");
  if (
    !BUCKET.test(bucket) ||
    bucket.includes("..") ||
    /^\d+\.\d+\.\d+\.\d+$/.test(bucket) ||
    bucket.startsWith("goog") ||
    bucket.includes("google")
  ) {
    return "Invalid Cloud Storage bucket name";
  }
  if (prefix && (!PREFIX.test(prefix) || prefix.split("/").some((p) => p === "." || p === ".."))) {
    return "Invalid prefix in bucket URL";
  }
  return null;
}

export const projectIdField = z
  .string()
  .trim()
  .regex(/^[a-z][a-z0-9-]{4,28}[a-z0-9]$/, "Invalid Google Cloud project ID");

export const databaseIdField = z
  .string()
  .trim()
  .regex(/^(\(default\)|[a-z][a-z0-9-]{2,61}[a-z0-9])$/, "Invalid Firestore database ID");

export function isValidCollectionId(id: string): boolean {
  return (
    id.length >= 1 &&
    id.length <= 100 &&
    !id.includes("/") &&
    !hasControlChars(id) &&
    id !== "." &&
    id !== ".." &&
    !/^__.*__$/.test(id)
  );
}

/** Returns an error message, or null when the pasted service-account JSON looks right. */
export function checkServiceAccount(text: string, projectId: string): string | null {
  let data: unknown;
  try {
    data = JSON.parse(text);
  } catch {
    return "Service account must be valid JSON";
  }
  if (typeof data !== "object" || data === null || Array.isArray(data)) {
    return "Service account JSON must be an object";
  }
  const account = data as Record<string, unknown>;
  if (account.type !== "service_account") return "Must be a Google service account key";
  if (typeof account.client_email !== "string" || !account.client_email.includes("@")) {
    return "Missing a valid client_email";
  }
  if (typeof account.private_key !== "string" || !account.private_key.includes("-----BEGIN")) {
    return "Missing a valid private_key";
  }
  if (projectId && account.project_id !== projectId) {
    return "The service account's project_id does not match the project ID above";
  }
  return null;
}

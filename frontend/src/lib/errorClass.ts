import type { ErrorClass } from "@/api/types";

const LABELS: Record<ErrorClass, string> = {
  auth_expired: "Authorization expired",
  quota_exceeded: "Quota exceeded",
  rate_limited: "Rate limited",
  folder_not_found: "Folder not found",
  network: "Network error",
  verify_mismatch: "Verification mismatch",
  other: "Error",
};

export function errorClassLabel(value: ErrorClass): string {
  return LABELS[value] ?? value;
}

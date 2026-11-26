import type { NotificationEventStatus, NotificationEventType } from "@/api/types";

export const EVENT_TYPE_LABEL: Record<NotificationEventType, string> = {
  run_finished: "Run finished",
  recovery: "Recovery",
  overdue: "Overdue",
  auth_expired: "Sign-in expired",
  retention_failed: "Retention failed",
  low_disk: "Low disk",
  test: "Test",
};

export const EVENT_STATUS_LABEL: Record<NotificationEventStatus, string> = {
  pending: "Pending",
  sent: "Sent",
  failed: "Failed",
  rate_limited: "Rate limited",
};

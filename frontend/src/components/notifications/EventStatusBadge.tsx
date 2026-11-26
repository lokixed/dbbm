import Badge from "xedonium/Badge";
import type { NotificationEventStatus } from "@/api/types";
import { EVENT_STATUS_LABEL } from "@/lib/notificationLabels";

const COLOR = { sent: "success", failed: "danger", rate_limited: "warning", pending: "info" } as const;

export function EventStatusBadge({ status }: { status: NotificationEventStatus }) {
  return <Badge badgeContent={EVENT_STATUS_LABEL[status]} color={COLOR[status]} />;
}

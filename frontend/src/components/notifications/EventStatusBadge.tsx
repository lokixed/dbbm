import Badge from "xedonium/Badge";
import type { NotificationEventStatus } from "@/api/types";

const COLOR = { sent: "success", failed: "danger", rate_limited: "warning", pending: "info" } as const;

export function EventStatusBadge({ status }: { status: NotificationEventStatus }) {
  return <Badge badgeContent={status.replace("_", " ")} color={COLOR[status]} />;
}

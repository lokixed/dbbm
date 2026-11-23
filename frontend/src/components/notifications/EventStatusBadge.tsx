import type { NotificationEventStatus } from "@/api/types";
import { Badge } from "@/components/ui/badge";

const VARIANT = { sent: "success", failed: "danger", rate_limited: "warning", pending: "info" } as const;

export function EventStatusBadge({ status }: { status: NotificationEventStatus }) {
  return <Badge variant={VARIANT[status]}>{status.replace("_", " ")}</Badge>;
}

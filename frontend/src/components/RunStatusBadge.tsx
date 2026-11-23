import Badge, { type BadgeColor } from "xedonium/Badge";
import type { RunStatus } from "@/api/types";

const VARIANTS: Record<RunStatus, { label: string; color: BadgeColor }> = {
  queued: { label: "Queued", color: "secondary" },
  running: { label: "Running", color: "info" },
  success: { label: "Success", color: "success" },
  partial: { label: "Partial", color: "warning" },
  failed: { label: "Failed", color: "danger" },
  cancelled: { label: "Cancelled", color: "secondary" },
  interrupted: { label: "Interrupted", color: "warning" },
};

export function RunStatusBadge({ status }: { status: RunStatus | null }) {
  if (!status) return <Badge badgeContent="Never run" color="secondary" />;
  const { label, color } = VARIANTS[status];
  return <Badge badgeContent={label} color={color} />;
}

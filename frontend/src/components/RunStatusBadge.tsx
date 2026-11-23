import type { RunStatus } from "@/api/types";
import { Badge, type BadgeProps } from "@/components/ui/badge";

const VARIANTS: Record<RunStatus, { label: string; variant: BadgeProps["variant"] }> = {
  queued: { label: "Queued", variant: "neutral" },
  running: { label: "Running", variant: "info" },
  success: { label: "Success", variant: "success" },
  partial: { label: "Partial", variant: "warning" },
  failed: { label: "Failed", variant: "danger" },
  cancelled: { label: "Cancelled", variant: "neutral" },
  interrupted: { label: "Interrupted", variant: "warning" },
};

export function RunStatusBadge({ status }: { status: RunStatus | null }) {
  if (!status) return <Badge>Never run</Badge>;
  const { label, variant } = VARIANTS[status];
  return <Badge variant={variant}>{label}</Badge>;
}

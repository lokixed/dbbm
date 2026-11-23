import Badge from "xedonium/Badge";
import Tooltip from "xedonium/Tooltip";
import type { LastTest } from "@/api/types";
import { formatTime } from "@/lib/format";

export function StatusBadge({ lastTest }: { lastTest: LastTest | null }) {
  if (!lastTest) return <Badge badgeContent="Never tested" color="secondary" />;
  const badge = (
    <Badge badgeContent={lastTest.ok ? "OK" : "Failed"} color={lastTest.ok ? "success" : "danger"} />
  );
  return (
    <span className="inline-flex items-center gap-2">
      {lastTest.message ? <Tooltip text={lastTest.message}>{badge}</Tooltip> : badge}
      <span className="text-xs text-app-muted">{formatTime(lastTest.at)}</span>
    </span>
  );
}

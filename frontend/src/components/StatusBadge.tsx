import Badge from "xedonium/Badge";
import Tooltip from "xedonium/Tooltip";
import RelativeTime from "xedonium/RelativeTime";
import type { LastTest } from "@/api/types";

/** The result of the last connection test; the failure message is in the tooltip. */
export function StatusBadge({ lastTest }: { lastTest: LastTest | null }) {
  if (!lastTest) return <Badge badgeContent="Never tested" color="secondary" />;
  const badge = (
    <Badge badgeContent={lastTest.ok ? "OK" : "Failed"} color={lastTest.ok ? "success" : "danger"} />
  );
  return lastTest.message ? <Tooltip text={lastTest.message}>{badge}</Tooltip> : badge;
}

/** When the connection was last tested, as a relative time. */
export function LastTested({ lastTest }: { lastTest: LastTest | null }) {
  if (!lastTest) return <span className="text-app-muted">-</span>;
  return <RelativeTime date={lastTest.at} className="whitespace-nowrap" />;
}

import type { LastTest } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { formatTime } from "@/lib/format";

export function StatusBadge({ lastTest }: { lastTest: LastTest | null }) {
  if (!lastTest) return <Badge>Never tested</Badge>;
  return (
    <span title={lastTest.message ?? undefined} className="inline-flex items-center gap-2">
      <Badge variant={lastTest.ok ? "success" : "danger"}>{lastTest.ok ? "OK" : "Failed"}</Badge>
      <span className="text-xs text-muted-foreground">{formatTime(lastTest.at)}</span>
    </span>
  );
}

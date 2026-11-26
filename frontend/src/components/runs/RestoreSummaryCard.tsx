import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import Card from "xedonium/Card";
import type { RestoreSummary } from "@/api/types";
import { SourceTypeLabel } from "@/components/SourceTypeIcon";

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <>
      <dt className="text-app-muted">{label}</dt>
      <dd className="min-w-0 break-all">{children}</dd>
    </>
  );
}

function options(restore: RestoreSummary): string {
  const parts = [
    restore.full_instance ? "Whole instance" : null,
    restore.drop ? "Drop existing objects first" : null,
    restore.only && restore.only.length > 0 ? `Only ${restore.only.join(", ")}` : null,
    restore.encrypted ? "Encrypted backup" : null,
  ].filter((part): part is string => part !== null);
  return parts.length > 0 ? parts.join("; ") : "None";
}

/** What a restore run read and where it wrote, from the summary saved with the run. `sourceType` is
 * the engine the API worked out for older runs that did not record `target_type`. */
export function RestoreSummaryCard({ restore, sourceType }: { restore: RestoreSummary; sourceType?: string | null }) {
  const target = restore.target_source ?? "-";
  return (
    <Card title="Restore" headingLevel={2}>
      <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 text-sm">
        <Row label="Backup">{restore.database ?? "-"}</Row>
        <Row label="Stored copy">
          {restore.copy ?? "-"}
          {restore.path && <span className="ml-2 font-mono text-xs text-app-muted">{restore.path}</span>}
        </Row>
        <Row label="Target source">
          <SourceTypeLabel type={restore.target_type ?? sourceType}>
            {restore.target_source_id ? (
              <Link to={`/sources/${encodeURIComponent(restore.target_source_id)}`} className="hover:underline">
                {target}
              </Link>
            ) : (
              target
            )}
          </SourceTypeLabel>
        </Row>
        <Row label="Target database">{restore.target_database ?? "-"}</Row>
        <Row label="Options">{options(restore)}</Row>
      </dl>
    </Card>
  );
}

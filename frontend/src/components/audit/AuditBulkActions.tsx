import Button from "xedonium/Button";
import DownloadIcon from "xedonium/icons/Download";
import FileTextIcon from "xedonium/icons/FileText";
import type { AuditEntry } from "@/api/audit";
import { BulkBar } from "@/components/BulkBar";

const CSV_COLUMNS = ["timestamp", "username", "action", "object_type", "object_id", "ip", "details"] as const;

function csvCell(value: unknown): string {
  const text = value === null || value === undefined ? "" : typeof value === "string" ? value : JSON.stringify(value);
  // Spreadsheet apps run cells that start with = + - @ as formulas; a leading quote keeps them text.
  const safe = /^[=+\-@\t\r]/.test(text) ? `'${text}` : text;
  return /[",\n\r]/.test(safe) ? `"${safe.replace(/"/g, '""')}"` : safe;
}

function toCsv(entries: AuditEntry[]): string {
  const lines = [CSV_COLUMNS.join(",")];
  for (const entry of entries) lines.push(CSV_COLUMNS.map((c) => csvCell(entry[c])).join(","));
  return lines.join("\r\n") + "\r\n";
}

function download(name: string, type: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function fileName(extension: string): string {
  return `dbbm-audit-${new Date().toISOString().replace(/[:.]/g, "-")}.${extension}`;
}

/**
 * The audit log is read-only (the API has no way to change or delete entries), so the bulk action is an
 * export of the ticked entries, built in the browser from what the table already loaded.
 */
export function AuditBulkActions({ selected, onClear }: { selected: AuditEntry[]; onClear: () => void }) {
  // Oldest first, like a log file.
  const ordered = [...selected].sort((a, b) => a.timestamp.localeCompare(b.timestamp));
  return (
    <BulkBar
      selected={selected}
      actions={[]}
      describe={(entry) => entry.action}
      onClear={onClear}
      onFinished={() => undefined}
      extra={
        <>
          <Button variant="secondary" onClick={() => download(fileName("csv"), "text/csv", toCsv(ordered))}>
            <FileTextIcon className="h-4 w-4" /> Export CSV
          </Button>
          <Button
            variant="secondary"
            onClick={() => download(fileName("json"), "application/json", JSON.stringify(ordered, null, 2) + "\n")}
          >
            <DownloadIcon className="h-4 w-4" /> Export JSON
          </Button>
        </>
      }
    />
  );
}

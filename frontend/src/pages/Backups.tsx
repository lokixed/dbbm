import { Download, Loader2, RefreshCw, RotateCcw, ShieldCheck, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  PAGE_SIZE,
  downloadUrl,
  useBackups,
  useDeleteBackup,
  useRescanDestination,
  useVerifyBackup,
  type Backup,
  type BackupFilters,
} from "@/api/backups";
import { useDestinations } from "@/api/destinations";
import { useJobs } from "@/api/jobs";
import { useSources } from "@/api/sources";
import { RestoreWizard } from "@/components/backups/RestoreWizard";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { TBody, TD, TH, THead, Table, TR } from "@/components/ui/table";
import { useToast } from "@/components/ui/toast";
import { formatBytes, formatTime } from "@/lib/format";

const EMPTY: BackupFilters = {
  jobId: "",
  sourceId: "",
  destinationId: "",
  database: "",
  status: "",
  from: "",
  to: "",
};

function monthOf(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { year: "numeric", month: "long" });
}

function CopyStatus({ backup }: { backup: Backup }) {
  if (backup.missing) {
    return (
      <Badge variant="danger" title="The file was not found at the destination.">
        Missing
      </Badge>
    );
  }
  const title = backup.verified_at
    ? `Re-verified ${formatTime(backup.verified_at)}`
    : "Checked against its checksum when it was stored.";
  return (
    <Badge variant="success" title={title}>
      Verified
    </Badge>
  );
}

export default function BackupsPage() {
  const { toast } = useToast();
  const [filters, setFilters] = useState<BackupFilters>(EMPTY);
  const [page, setPage] = useState(0);
  const [pendingDelete, setPendingDelete] = useState<Backup | null>(null);
  const [restoring, setRestoring] = useState<Backup | null>(null);
  const backups = useBackups(filters, page);
  const jobs = useJobs();
  const sources = useSources();
  const destinations = useDestinations();
  const verify = useVerifyBackup();
  const remove = useDeleteBackup();
  const rescan = useRescanDestination();

  const setFilter = (patch: Partial<BackupFilters>) => {
    setFilters({ ...filters, ...patch });
    setPage(0);
  };

  const groups = useMemo(() => {
    const byKey = new Map<string, Backup[]>();
    for (const item of backups.data?.items ?? []) {
      const key = `${item.job_name} · ${monthOf(item.created_at)}`;
      byKey.set(key, [...(byKey.get(key) ?? []), item]);
    }
    return [...byKey.entries()];
  }, [backups.data]);

  const onVerify = (backup: Backup) =>
    verify.mutate(backup.id, {
      onSuccess: (result) =>
        toast({
          title: result.status === "ok" ? "Backup verified" : `Backup ${result.status}`,
          description: result.detail,
          variant: result.status === "ok" ? "success" : "error",
        }),
      onError: (error) =>
        toast({ title: "Could not verify the backup", description: error.message, variant: "error" }),
    });

  const onDelete = () => {
    if (!pendingDelete) return;
    remove.mutate(pendingDelete.id, {
      onSuccess: () => {
        toast({ title: "Backup deleted", description: pendingDelete.path, variant: "success" });
        setPendingDelete(null);
      },
      onError: (error) => {
        toast({ title: "Could not delete the backup", description: error.message, variant: "error" });
        setPendingDelete(null);
      },
    });
  };

  const onRescan = () => {
    if (!filters.destinationId) return;
    rescan.mutate(filters.destinationId, {
      onSuccess: (result) =>
        toast({
          title: "Destination scanned",
          description: `${result.checked} checked, ${result.missing} missing.`,
          variant: result.missing ? "error" : "success",
        }),
      onError: (error) =>
        toast({ title: "Could not scan the destination", description: error.message, variant: "error" }),
    });
  };

  const total = backups.data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Backups</h1>
        <p className="text-sm text-muted-foreground">
          Every stored backup file, by job and month. Download, re-check or delete a copy.
        </p>
      </div>

      <div className="flex flex-wrap items-end gap-3">
        <Select
          aria-label="Filter by job"
          className="w-48"
          value={filters.jobId}
          onChange={(event) => setFilter({ jobId: event.target.value })}
        >
          <option value="">All jobs</option>
          {jobs.data?.map((job) => (
            <option key={job.id} value={job.id}>
              {job.name}
            </option>
          ))}
        </Select>
        <Select
          aria-label="Filter by source"
          className="w-48"
          value={filters.sourceId}
          onChange={(event) => setFilter({ sourceId: event.target.value })}
        >
          <option value="">All sources</option>
          {sources.data?.map((source) => (
            <option key={source.id} value={source.id}>
              {source.name}
            </option>
          ))}
        </Select>
        <Select
          aria-label="Filter by destination"
          className="w-48"
          value={filters.destinationId}
          onChange={(event) => setFilter({ destinationId: event.target.value })}
        >
          <option value="">All destinations</option>
          {destinations.data?.map((destination) => (
            <option key={destination.id} value={destination.id}>
              {destination.name}
            </option>
          ))}
        </Select>
        <Input
          aria-label="Filter by database"
          className="w-44"
          placeholder="Database name"
          value={filters.database}
          onChange={(event) => setFilter({ database: event.target.value })}
        />
        <Select
          aria-label="Filter by status"
          className="w-40"
          value={filters.status}
          onChange={(event) => setFilter({ status: event.target.value as BackupFilters["status"] })}
        >
          <option value="">Any status</option>
          <option value="ok">Present</option>
          <option value="missing">Missing</option>
        </Select>
        <Input
          aria-label="From date"
          type="date"
          className="w-40"
          value={filters.from}
          onChange={(event) => setFilter({ from: event.target.value })}
        />
        <Input
          aria-label="To date"
          type="date"
          className="w-40"
          value={filters.to}
          onChange={(event) => setFilter({ to: event.target.value })}
        />
        <Button
          variant="secondary"
          onClick={onRescan}
          disabled={!filters.destinationId || rescan.isPending}
          title={filters.destinationId ? undefined : "Choose a destination to rescan it"}
        >
          <RefreshCw className={rescan.isPending ? "mr-2 h-4 w-4 animate-spin" : "mr-2 h-4 w-4"} />
          Rescan destination
        </Button>
      </div>

      {backups.isPending && (
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" /> Loading backups...
        </p>
      )}
      {backups.isError && (
        <div role="alert" className="flex items-center justify-between gap-4 text-sm text-destructive">
          <span>Could not load backups: {backups.error.message}</span>
          <Button variant="secondary" size="sm" onClick={() => void backups.refetch()}>
            Retry
          </Button>
        </div>
      )}
      {backups.isSuccess && total === 0 && (
        <Card>
          <p className="p-6 text-sm text-muted-foreground">
            No backups match. Run a job from the Jobs page to create one.
          </p>
        </Card>
      )}

      {groups.map(([title, items]) => (
        <Card key={title}>
          <h2 className="border-b px-4 py-3 text-sm font-semibold">{title}</h2>
          <Table>
            <THead>
              <tr>
                <TH>Database</TH>
                <TH>Created</TH>
                <TH className="text-right">Size</TH>
                <TH>SHA-256</TH>
                <TH>Destination</TH>
                <TH>Copy</TH>
                <TH className="text-right">Actions</TH>
              </tr>
            </THead>
            <TBody>
              {items.map((backup) => (
                <TR key={backup.id} className={backup.missing ? "bg-destructive/5" : undefined}>
                  <TD className="font-medium">
                    <Link to={`/runs/${backup.run_id}`} className="hover:underline" title="Open the run">
                      {backup.source_database}
                    </Link>
                  </TD>
                  <TD>{formatTime(backup.created_at)}</TD>
                  <TD className="text-right">{formatBytes(backup.size_bytes)}</TD>
                  <TD>
                    <code className="text-xs" title={backup.sha256}>
                      {backup.sha256.slice(0, 12)}…
                    </code>
                  </TD>
                  <TD>{backup.destination_name ?? "(deleted)"}</TD>
                  <TD>
                    <CopyStatus backup={backup} />
                  </TD>
                  <TD className="space-x-1 text-right">
                    {backup.missing ? (
                      <Button variant="secondary" size="sm" disabled title="The file is missing">
                        <Download className="h-4 w-4" />
                      </Button>
                    ) : (
                      <Button asChild variant="secondary" size="sm">
                        <a href={downloadUrl(backup.id)} download aria-label={`Download ${backup.source_database}`}>
                          <Download className="h-4 w-4" />
                        </a>
                      </Button>
                    )}
                    <Button
                      variant="secondary"
                      size="sm"
                      aria-label={`Restore ${backup.source_database}`}
                      title="Restore"
                      disabled={backup.missing}
                      onClick={() => setRestoring(backup)}
                    >
                      <RotateCcw className="h-4 w-4" />
                    </Button>
                    <Button
                      variant="secondary"
                      size="sm"
                      aria-label={`Verify ${backup.source_database}`}
                      title="Verify now"
                      disabled={verify.isPending && verify.variables === backup.id}
                      onClick={() => onVerify(backup)}
                    >
                      <ShieldCheck className="h-4 w-4" />
                    </Button>
                    <Button
                      variant="secondary"
                      size="sm"
                      aria-label={`Delete ${backup.source_database}`}
                      title="Delete"
                      onClick={() => setPendingDelete(backup)}
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        </Card>
      ))}

      {total > PAGE_SIZE && (
        <div className="flex items-center justify-between text-sm text-muted-foreground">
          <span>
            Page {page + 1} of {pages} ({total} backups)
          </span>
          <div className="flex gap-2">
            <Button variant="secondary" size="sm" disabled={page === 0} onClick={() => setPage(page - 1)}>
              Previous
            </Button>
            <Button variant="secondary" size="sm" disabled={page + 1 >= pages} onClick={() => setPage(page + 1)}>
              Next
            </Button>
          </div>
        </div>
      )}

      <RestoreWizard backup={restoring} onClose={() => setRestoring(null)} />

      <ConfirmDialog
        open={pendingDelete !== null}
        title="Delete this backup?"
        description={`This permanently deletes ${pendingDelete?.path ?? ""} and its manifest from ${
          pendingDelete?.destination_name ?? "the destination"
        }. Other copies are not touched. This cannot be undone.`}
        confirmLabel="Delete backup"
        pending={remove.isPending}
        onConfirm={onDelete}
        onCancel={() => setPendingDelete(null)}
      />
    </div>
  );
}

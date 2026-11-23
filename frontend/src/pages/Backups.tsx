import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import Alert from "xedonium/Alert";
import Badge from "xedonium/Badge";
import Button from "xedonium/Button";
import Card from "xedonium/Card";
import HelperText from "xedonium/HelperText";
import Input from "xedonium/Input";
import Loader from "xedonium/Loader";
import PageHeader from "xedonium/PageHeader";
import Select from "xedonium/Select";
import Table, { type TableColumn } from "xedonium/Table";
import Tooltip from "xedonium/Tooltip";
import DownloadIcon from "xedonium/icons/Download";
import RefreshCwIcon from "xedonium/icons/RefreshCw";
import RotateCcwIcon from "xedonium/icons/RotateCcw";
import ShieldCheckIcon from "xedonium/icons/ShieldCheck";
import Trash2Icon from "xedonium/icons/Trash2";
import RelativeTime from "xedonium/RelativeTime";
import {
  PAGE_SIZE,
  requestDownloadLink,
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
import { useToast } from "@/components/toast";
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
      <Tooltip text="The file was not found at the destination.">
        <Badge badgeContent="Missing" color="danger" />
      </Tooltip>
    );
  }
  const encrypted = backup.encrypted ? (
    <Tooltip
      text={`Encrypted with age (keys ${backup.recipients.join(", ")}). Download gives the encrypted file as stored; open it with: age --decrypt -i key.txt -o output file.age`}
    >
      <Badge badgeContent="Encrypted" color="info" />
    </Tooltip>
  ) : null;
  const title = backup.verified_at
    ? `Re-verified ${formatTime(backup.verified_at)}`
    : "Checked against its checksum when it was stored.";
  return (
    <span className="flex flex-wrap gap-1">
      <Tooltip text={title}>
        <Badge badgeContent="Verified" color="success" />
      </Tooltip>
      {encrypted}
    </span>
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

  const onDownload = (backup: Backup) => {
    requestDownloadLink(backup.id)
      .then((link) => {
        window.location.assign(link.url);
      })
      .catch((error: Error) =>
        toast({ title: "Could not start the download", description: error.message, variant: "error" }),
      );
  };

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

  const columns: TableColumn<Backup>[] = [
    {
      key: "database",
      header: "Database",
      render: (backup) => (
        <Tooltip text="Open the run">
          <Link to={`/runs/${backup.run_id}`} className="font-medium hover:underline">
            {backup.source_database}
          </Link>
        </Tooltip>
      ),
    },
    {
      key: "created",
      header: "Created",
      render: (backup) => <span className="whitespace-nowrap"><RelativeTime date={backup.created_at} /></span>,
    },
    {
      key: "size",
      header: "Size",
      align: "right",
      render: (backup) => <span className="whitespace-nowrap">{formatBytes(backup.size_bytes)}</span>,
    },
    {
      key: "sha",
      header: "SHA-256",
      render: (backup) => (
        <Tooltip text={backup.sha256}>
          <code className="text-xs">{backup.sha256.slice(0, 12)}…</code>
        </Tooltip>
      ),
    },
    { key: "destination", header: "Destination", render: (backup) => backup.destination_name ?? "(deleted)" },
    { key: "copy", header: "Copy", render: (backup) => <CopyStatus backup={backup} /> },
    {
      key: "actions",
      header: "Actions",
      align: "right",
      render: (backup) => (
        <div className="flex justify-end gap-1 whitespace-nowrap">
          <Button
            variant="secondary"
            aria-label={`Download ${backup.source_database}`}
            tooltip="Download"
            disabled={backup.missing}
            onClick={() => onDownload(backup)}
          >
            <DownloadIcon className="h-4 w-4" />
          </Button>
          <Button
            variant="secondary"
            aria-label={`Restore ${backup.source_database}`}
            tooltip="Restore"
            disabled={backup.missing}
            onClick={() => setRestoring(backup)}
          >
            <RotateCcwIcon className="h-4 w-4" />
          </Button>
          <Button
            variant="secondary"
            aria-label={`Verify ${backup.source_database}`}
            tooltip="Verify now"
            disabled={verify.isPending && verify.variables === backup.id}
            onClick={() => onVerify(backup)}
          >
            <ShieldCheckIcon className="h-4 w-4" />
          </Button>
          <Button
            variant="secondary"
            aria-label={`Delete ${backup.source_database}`}
            tooltip="Delete"
            onClick={() => setPendingDelete(backup)}
          >
            <Trash2Icon className="h-4 w-4" />
          </Button>
        </div>
      ),
    },
  ];

  const total = backups.data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div className="space-y-6">
      <PageHeader
        title="Backups"
        subtitle="Every stored backup file, by job and month. Download, re-check or delete a copy."
      />

      <div className="flex flex-wrap items-end gap-3">
        <div className="w-48">
          <Select
          aria-label="Filter by job"
                    value={filters.jobId}
          onChange={(jobId) => setFilter({ jobId })}
          options={[{ value: "", label: "All jobs" }, ...(jobs.data ?? []).map((j) => ({ value: j.id, label: j.name }))]}
        />
        </div>
        <div className="w-48">
          <Select
          aria-label="Filter by source"
                    value={filters.sourceId}
          onChange={(sourceId) => setFilter({ sourceId })}
          options={[
            { value: "", label: "All sources" },
            ...(sources.data ?? []).map((s) => ({ value: s.id, label: s.name })),
          ]}
        />
        </div>
        <div className="w-48">
          <Select
          aria-label="Filter by destination"
                    value={filters.destinationId}
          onChange={(destinationId) => setFilter({ destinationId })}
          options={[
            { value: "", label: "All destinations" },
            ...(destinations.data ?? []).map((d) => ({ value: d.id, label: d.name })),
          ]}
        />
        </div>
        <div className="w-44">
          <Input
          aria-label="Filter by database"
                    placeholder="Database name"
          value={filters.database}
          onChange={(database) => setFilter({ database })}
        />
        </div>
        <div className="w-40">
          <Select
          aria-label="Filter by status"
                    value={filters.status}
          onChange={(status) => setFilter({ status: status as BackupFilters["status"] })}
          options={[
            { value: "", label: "Any status" },
            { value: "ok", label: "Present" },
            { value: "missing", label: "Missing" },
          ]}
        />
        </div>
        <div className="w-40">
          <Input
          aria-label="From date"
          type="date"
                    value={filters.from}
          onChange={(from) => setFilter({ from })}
        />
        </div>
        <div className="w-40">
          <Input
          aria-label="To date"
          type="date"
                    value={filters.to}
          onChange={(to) => setFilter({ to })}
        />
        </div>
        <Button
          variant="secondary"
          onClick={onRescan}
          disabled={!filters.destinationId || rescan.isPending}
          tooltip={filters.destinationId ? undefined : "Choose a destination to rescan it"}
        >
          <RefreshCwIcon className="mr-2 h-4 w-4" />
          Rescan destination
        </Button>
      </div>

      {backups.isPending && <Loader variant="inline" label="Loading backups..." />}
      {backups.isError && (
        <Alert tone="danger">
          <div className="flex items-center justify-between gap-4">
            <span>Could not load backups: {backups.error.message}</span>
            <Button variant="secondary" onClick={() => void backups.refetch()}>
              Retry
            </Button>
          </div>
        </Alert>
      )}
      {backups.isSuccess && total === 0 && (
        <Card>
          <HelperText>No backups match. Run a job from the Jobs page to create one.</HelperText>
        </Card>
      )}

      {groups.map(([title, items]) => (
        <Card key={title} title={title} headingLevel={2} padded={false}>
          <Table
            columns={columns}
            rows={items}
            rowProps={(backup) => ({ className: backup.missing ? "bg-red-500/5" : undefined })}
          />
        </Card>
      ))}

      {total > PAGE_SIZE && (
        <div className="flex items-center justify-between text-sm text-app-muted">
          <span>
            Page {page + 1} of {pages} ({total} backups)
          </span>
          <div className="flex gap-2">
            <Button variant="secondary" disabled={page === 0} onClick={() => setPage(page - 1)}>
              Previous
            </Button>
            <Button variant="secondary" disabled={page + 1 >= pages} onClick={() => setPage(page + 1)}>
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

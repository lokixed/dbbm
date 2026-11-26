import { DestinationLabel } from "@/components/DestinationIcon";
import { SourceTypeIcon, SourceTypeLabel } from "@/components/SourceTypeIcon";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import Alert from "xedonium/Alert";
import Badge from "xedonium/Badge";
import Button from "xedonium/Button";
import Card from "xedonium/Card";
import HelperText from "xedonium/HelperText";
import DataGrid, { type DataGridColumn } from "xedonium/DataGrid";
import Drawer from "xedonium/Drawer";
import Input from "xedonium/Input";
import PageHeader from "xedonium/PageHeader";
import Select from "xedonium/Select";
import Tooltip from "xedonium/Tooltip";
import CopyIcon from "xedonium/icons/Copy";
import DownloadIcon from "xedonium/icons/Download";
import FilterIcon from "xedonium/icons/Filter";
import SearchIcon from "xedonium/icons/Search";
import RefreshCwIcon from "xedonium/icons/RefreshCw";
import RotateCcwIcon from "xedonium/icons/RotateCcw";
import ShieldCheckIcon from "xedonium/icons/ShieldCheck";
import Trash2Icon from "xedonium/icons/Trash2";
import RelativeTime, { formatRelative } from "xedonium/RelativeTime";
import {
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
import { RowMenu } from "@/components/RowMenu";
import { RestoreWizard } from "@/components/backups/RestoreWizard";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { useToast } from "@/components/toast";
import { FilterCell } from "@/components/FilterCell";
import { LogoLoader } from "@/components/LogoLoader";
import { formatBytes } from "@/lib/format";

const EMPTY: BackupFilters = {
  jobId: "",
  sourceId: "",
  destinationId: "",
  search: "",
  status: "",
  from: "",
  to: "",
};

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
    ? `Re-verified ${formatRelative(backup.verified_at)}`
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
  const [draft, setDraft] = useState<BackupFilters>(EMPTY);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [pendingDelete, setPendingDelete] = useState<Backup | null>(null);
  const [restoring, setRestoring] = useState<Backup | null>(null);
  const backups = useBackups(filters);
  const jobs = useJobs();
  const sources = useSources();
  const destinations = useDestinations();
  const verify = useVerifyBackup();
  const remove = useDeleteBackup();
  const rescan = useRescanDestination();

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setFilters((current) => (current.search === search ? current : { ...current, search }));
    }, 300);
    return () => window.clearTimeout(timer);
  }, [search]);

  const countActive = (f: BackupFilters) =>
    (Object.keys(EMPTY) as (keyof BackupFilters)[]).filter((k) => f[k] !== EMPTY[k]).length;
  const activeCount = countActive({ ...filters, search: "" });
  const filtersActive = activeCount > 0 || filters.search !== "";
  const draftActive = countActive({ ...draft, search: "" }) > 0;

  // A job belongs to exactly one source, so the two filters must agree: picking a job selects its
  // source, picking a different source drops a job that does not belong to it, and the job list
  // only offers the jobs of the chosen source.
  const jobChoices = (jobs.data ?? []).filter((j) => !draft.sourceId || j.source.id === draft.sourceId);
  const pickJob = (jobId: string) => {
    const job = (jobs.data ?? []).find((j) => j.id === jobId);
    setDraft({ ...draft, jobId, sourceId: job ? job.source.id : draft.sourceId });
  };
  const pickSource = (sourceId: string) => {
    const job = (jobs.data ?? []).find((j) => j.id === draft.jobId);
    const keepJob = job !== undefined && (sourceId === "" || job.source.id === sourceId);
    setDraft({ ...draft, sourceId, jobId: keepJob ? draft.jobId : "" });
  };

  const openFilters = () => {
    setDraft(filters);
    setFiltersOpen(true);
  };
  const applyFilters = () => {
    setFilters(draft);
    setFiltersOpen(false);
  };
  const clearFilters = () => {
    setFilters(EMPTY);
    setDraft(EMPTY);
    setSearch("");
  };

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

  const onCopyHash = (backup: Backup) => {
    navigator.clipboard.writeText(backup.sha256).then(
      () => toast({ title: "SHA-256 copied", description: backup.sha256, variant: "success" }),
      () => toast({ title: "Could not copy", description: "Clipboard access was denied.", variant: "error" }),
    );
  };

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

  const columns: DataGridColumn<Backup>[] = [
    {
      key: "database",
      header: "Database",
      sortable: true,
      accessor: (backup) => backup.source_database.toLowerCase(),
      render: (backup) => (
        <Tooltip text="Open the run">
          <Link to={`/runs/${backup.run_id}`} className="inline-flex items-center gap-2 font-medium hover:underline">
            <SourceTypeIcon type={backup.source_type} />
            {backup.source_database}
          </Link>
        </Tooltip>
      ),
    },
    {
      key: "destination",
      header: "Destination",
      sortable: true,
      accessor: (backup) => (backup.destination_name ?? "").toLowerCase(),
      render: (backup) => (
        <DestinationLabel type={backup.destination_type}>
          {backup.destination_name ?? "(deleted destination)"}
        </DestinationLabel>
      ),
    },
    { key: "job", header: "Job", sortable: true, accessor: (backup) => backup.job_name.toLowerCase(), render: (backup) => backup.job_name },
    {
      key: "created",
      header: "Created",
      sortable: true,
      accessor: (backup) => backup.created_at,
      render: (backup) => <span className="whitespace-nowrap"><RelativeTime date={backup.created_at} /></span>,
    },
    {
      key: "size",
      header: "Size",
      align: "right",
      sortable: true,
      accessor: (backup) => backup.size_bytes,
      render: (backup) => <span className="whitespace-nowrap">{formatBytes(backup.size_bytes)}</span>,
    },
    { key: "copy", header: "Status", sortable: true, accessor: (backup) => (backup.missing ? 0 : 1), render: (backup) => <CopyStatus backup={backup} /> },
    {
      key: "actions",
      header: "Actions",
      align: "right",
      render: (backup) => (
        <RowMenu
          label={`Actions for ${backup.source_database}`}
          items={[
            { key: "download", label: "Download", icon: <DownloadIcon className="h-4 w-4" />, disabled: backup.missing, onClick: () => onDownload(backup) },
            { key: "restore", label: "Restore", icon: <RotateCcwIcon className="h-4 w-4" />, disabled: backup.missing, hasDialog: true, onClick: () => setRestoring(backup) },
            { key: "verify", label: "Verify now", icon: <ShieldCheckIcon className="h-4 w-4" />, disabled: verify.isPending && verify.variables === backup.id, onClick: () => onVerify(backup) },
            { key: "copy-hash", label: "Copy SHA-256", icon: <CopyIcon className="h-4 w-4" />, onClick: () => onCopyHash(backup) },
            { key: "delete", label: "Delete", tone: "danger", icon: <Trash2Icon className="h-4 w-4" />, onClick: () => setPendingDelete(backup) },
          ]}
        />
      ),
    },
  ];

  const total = backups.data?.total ?? 0;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Backups"
        subtitle="Every stored backup file, by job and month. Download, re-check or delete a copy."
      />

      <div className="flex flex-wrap items-stretch gap-2">
        <div className="w-full sm:w-64">
          <Input
            aria-label="Search backups by database, destination or job"
            placeholder="Search database, destination or job"
            value={search}
            onChange={setSearch}
            clearable
            startAdornment={<SearchIcon className="h-4 w-4" />}
          />
        </div>
        <div className="flex flex-wrap items-stretch gap-2 sm:ml-auto">
          <Button variant="secondary" className="!h-auto" onClick={openFilters}>
            <FilterIcon className="mr-2 h-4 w-4" />
            Filters
            {activeCount > 0 && <Badge badgeContent={String(activeCount)} color="default" className="ml-2" />}
          </Button>
          {filtersActive && (
            <Button variant="flat" className="!h-auto" onClick={clearFilters}>
              Clear filters
            </Button>
          )}
          <Button
            variant="secondary"
            className="!h-auto"
            onClick={onRescan}
            disabled={!filters.destinationId || rescan.isPending}
            tooltip={
              filters.destinationId
                ? "Checks that every backup file recorded for this destination still exists there, and flags the ones that are gone as Missing. Nothing is deleted or downloaded."
                : "Filter by a destination first. Rescan checks that its recorded backup files still exist and flags the missing ones."
            }
          >
            <RefreshCwIcon className="mr-2 h-4 w-4" />
            Rescan destination
          </Button>
        </div>
      </div>

      <Drawer
        open={filtersOpen}
        onClose={() => setFiltersOpen(false)}
        title="Filter backups"
        side="right"
        width="max-w-sm"
        animated
        footer={
          <div className="flex justify-between gap-2">
            <Button variant="flat" onClick={() => setDraft({ ...EMPTY, search: draft.search })} disabled={!draftActive}>
              Reset
            </Button>
            <Button onClick={applyFilters}>Apply</Button>
          </div>
        }
      >
        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault();
            applyFilters();
          }}
        >
          <FilterCell label="Job">
            <Select
              aria-label="Filter by job"
              value={draft.jobId}
              onChange={pickJob}
              options={[
                { value: "", label: "All jobs" },
                ...jobChoices.map((j) => ({ value: j.id, label: j.name })),
              ]}
            />
          </FilterCell>
          <FilterCell label="Source">
            <Select
              aria-label="Filter by source"
              value={draft.sourceId}
              onChange={pickSource}
              options={[
                { value: "", label: "All sources" },
                ...(sources.data ?? []).map((s) => ({ value: s.id, label: <SourceTypeLabel type={s.type}>{s.name}</SourceTypeLabel> })),
              ]}
            />
          </FilterCell>
          <FilterCell label="Destination">
            <Select
              aria-label="Filter by destination"
              value={draft.destinationId}
              onChange={(destinationId) => setDraft({ ...draft, destinationId })}
              options={[
                { value: "", label: "All destinations" },
                ...(destinations.data ?? []).map((d) => ({
                  value: d.id,
                  label: <DestinationLabel type={d.type}>{d.name}</DestinationLabel>,
                })),
              ]}
            />
          </FilterCell>
          <FilterCell label="Status">
            <Select
              aria-label="Filter by status"
              value={draft.status}
              onChange={(status) => setDraft({ ...draft, status: status as BackupFilters["status"] })}
              options={[
                { value: "", label: "Any status" },
                { value: "ok", label: "Present" },
                { value: "missing", label: "Missing" },
              ]}
            />
          </FilterCell>
          <div className="grid grid-cols-2 gap-3">
            <FilterCell label="From">
              <Input aria-label="From date" type="date" value={draft.from} onChange={(from) => setDraft({ ...draft, from })} />
            </FilterCell>
            <FilterCell label="To">
              <Input aria-label="To date" type="date" value={draft.to} onChange={(to) => setDraft({ ...draft, to })} />
            </FilterCell>
          </div>
        </form>
      </Drawer>

      {backups.isPending && <LogoLoader />}
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

      {(backups.data?.items.length ?? 0) > 0 && (
        <DataGrid
          columns={columns}
          rows={backups.data?.items ?? []}
          density="compact"
          pageSize={10}
          pageSizeOptions={[10, 20, 50, 100]}
          hideFooterWhenSinglePage
        />
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

import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import Alert from "xedonium/Alert";
import Badge from "xedonium/Badge";
import Button from "xedonium/Button";
import PageHeader from "xedonium/PageHeader";
import Switch from "xedonium/Switch";
import DataGrid, { type DataGridColumn } from "xedonium/DataGrid";
import Tooltip from "xedonium/Tooltip";
import PencilIcon from "xedonium/icons/Pencil";
import PlayIcon from "xedonium/icons/Play";
import PlusIcon from "xedonium/icons/Plus";
import Trash2Icon from "xedonium/icons/Trash2";
import RelativeTime from "xedonium/RelativeTime";
import { useDeleteJob, useJobs, useRunJob, useSetJobEnabled } from "@/api/jobs";
import type { JobRecord } from "@/api/types";
import { RowMenu } from "@/components/RowMenu";
import { DestinationLabel } from "@/components/DestinationIcon";
import { SourceTypeLabel } from "@/components/SourceTypeIcon";
import { useDestinationType } from "@/lib/typeLookups";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { RunStatusBadge } from "@/components/RunStatusBadge";
import { useToast } from "@/components/toast";
import { LogoLoader } from "@/components/LogoLoader";

function ScheduleBadge({ job }: { job: JobRecord }) {
  if (!job.schedule) return <Badge badgeContent="Manual" color="secondary" />;
  if (job.schedule_error) {
    return (
      <Tooltip text={job.schedule_error}>
        <Badge badgeContent={`Invalid schedule: ${job.schedule}`} color="danger" />
      </Tooltip>
    );
  }
  return (
    <Tooltip text={`Time zone ${job.timezone}`}>
      <Badge
        badgeContent={`${job.schedule} (${job.timezone})${job.enabled ? "" : " paused"}`}
        color={job.enabled ? "info" : "secondary"}
      />
    </Tooltip>
  );
}

export default function JobsPage() {
  const { toast } = useToast();
  const navigate = useNavigate();
  const jobs = useJobs();
  const destinationType = useDestinationType();
  const run = useRunJob();
  const remove = useDeleteJob();
  const setEnabled = useSetJobEnabled();
  const [deleting, setDeleting] = useState<JobRecord | null>(null);

  const runNow = (job: JobRecord) =>
    run.mutate(job.id, {
      onSuccess: (started) => {
        toast({ title: "Run started", description: job.name, variant: "success" });
        navigate(`/runs/${started.id}`);
      },
      onError: (error) =>
        toast({ title: `Could not start ${job.name}`, description: error.message, variant: "error" }),
    });

  const toggle = (job: JobRecord) =>
    setEnabled.mutate(
      { id: job.id, enabled: !job.enabled },
      {
        onError: (error) =>
          toast({ title: "Could not change the job", description: error.message, variant: "error" }),
      },
    );

  const confirmDelete = () => {
    if (!deleting) return;
    remove.mutate(deleting.id, {
      onSuccess: () => {
        toast({
          title: "Job deleted",
          description: `${deleting.name}. Its run history is kept.`,
          variant: "success",
        });
        setDeleting(null);
      },
      onError: (error) => {
        toast({ title: "Could not delete job", description: error.message, variant: "error" });
        setDeleting(null);
      },
    });
  };

  const columns: DataGridColumn<JobRecord>[] = [
    {
      key: "name", sortable: true, accessor: (job) => job.name,
      header: "Name",
      render: (job) => <span className="font-medium">{job.name}</span>,
    },
    { key: "schedule", sortable: true, accessor: (job) => job.schedule ?? "", header: "Schedule", render: (job) => <ScheduleBadge job={job} /> },
    {
      key: "source", sortable: true, accessor: (job) => job.source.name,
      header: "Source",
      render: (job) => (
        <SourceTypeLabel type={job.source.type}>{job.source.name}</SourceTypeLabel>
      ),
    },
    { key: "destinations", sortable: true, accessor: (job) => job.destinations.map((d) => d.name).join(", "), header: "Destinations", render: (job) => (
        <span className="inline-flex flex-wrap gap-x-4 gap-y-1">
          {job.destinations.map((d) => (
            <DestinationLabel key={d.id} type={destinationType(d.id)}>{d.name}</DestinationLabel>
          ))}
        </span>
      ),
    },
    {
      key: "enabled", sortable: true, accessor: (job) => (job.enabled ? 1 : 0),
      header: "Enabled",
      render: (job) => (
        <Switch
          checked={job.enabled}
          disabled={setEnabled.isPending}
          onChange={() => toggle(job)}
          aria-label={`${job.enabled ? "Disable" : "Enable"} ${job.name}`}
          label={job.enabled ? "On" : "Off"}
        />
      ),
    },
    {
      key: "next", sortable: true, accessor: (job) => job.next_run_at ?? "",
      header: "Next run",
      render: (job) =>
        job.next_run_at ? <RelativeTime date={job.next_run_at} /> : <span className="text-app-muted">-</span>,
    },
    {
      key: "last-status", sortable: true, accessor: (job) => job.last_run?.status ?? "",
      header: "Last status",
      render: (job) =>
        job.last_run ? (
          <Link to={`/runs/${job.last_run.id}`} className="inline-flex hover:opacity-80">
            <RunStatusBadge status={job.last_run.status} />
          </Link>
        ) : (
          <RunStatusBadge status={null} />
        ),
    },
    {
      key: "last", sortable: true, accessor: (job) => job.last_run?.created_at ?? "",
      header: "Last run",
      render: (job) =>
        job.last_run ? (
          <Link to={`/runs/${job.last_run.id}`} className="whitespace-nowrap hover:underline">
            <RelativeTime date={job.last_run.created_at} />
          </Link>
        ) : (
          <span className="text-app-muted">-</span>
        ),
    },
    {
      key: "actions",
      header: "Actions",
      align: "right",
      render: (job) => {
        const starting = run.isPending && run.variables === job.id;
        const active = job.last_run?.status === "queued" || job.last_run?.status === "running";
        return (
          <RowMenu
            label={`Actions for ${job.name}`}
            items={[
              { key: "run", label: "Run now", icon: <PlayIcon className="h-4 w-4" />, disabled: starting || active, onClick: () => runNow(job) },
              { key: "edit", label: "Edit", icon: <PencilIcon className="h-4 w-4" />, onClick: () => navigate(`/jobs/${encodeURIComponent(job.id)}`) },
              { key: "delete", label: "Delete", tone: "danger", icon: <Trash2Icon className="h-4 w-4" />, onClick: () => setDeleting(job) },
            ]}
          />
        );
      },
    },
  ];

  return (
    <div className="space-y-6">
      <PageHeader title="Jobs" subtitle="What to back up, where it goes and when it runs.">
        <Button onClick={() => navigate("/jobs/new")}>
          <PlusIcon className="h-4 w-4" /> Add job
        </Button>
      </PageHeader>

      {jobs.isPending && <LogoLoader />}
      {jobs.isError && (
        <Alert tone="danger">
          <div className="flex items-center justify-between gap-4">
            <span>Could not load jobs: {jobs.error.message}</span>
            <Button variant="secondary" onClick={() => void jobs.refetch()}>
              Retry
            </Button>
          </div>
        </Alert>
      )}
      {jobs.isSuccess && (
        <DataGrid
          density="compact"
          pageSize={10}
          pageSizeOptions={[10, 20, 50, 100]}
          searchable
          hideFooterWhenSinglePage
          columns={columns}
          rows={jobs.data}
          empty="No jobs yet. Add a source and a destination, then create a job to back them up."
        />
      )}

      <ConfirmDialog
        open={deleting !== null}
        title="Delete job?"
        description={`"${deleting?.name ?? ""}" will be removed. Its run history and existing backup files are kept.`}
        confirmLabel="Delete"
        pending={remove.isPending}
        onConfirm={confirmDelete}
        onCancel={() => setDeleting(null)}
      />
    </div>
  );
}

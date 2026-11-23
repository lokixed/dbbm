import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import Alert from "xedonium/Alert";
import Badge from "xedonium/Badge";
import Button from "xedonium/Button";
import Card from "xedonium/Card";
import Loader from "xedonium/Loader";
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
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { JobDialog } from "@/components/jobs/JobDialog";
import { RunStatusBadge } from "@/components/RunStatusBadge";
import { useToast } from "@/components/toast";

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
  const run = useRunJob();
  const remove = useDeleteJob();
  const setEnabled = useSetJobEnabled();
  const [editor, setEditor] = useState<{ open: boolean; job: JobRecord | null }>({
    open: false,
    job: null,
  });
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
        <>
          {job.source.name} <span className="text-xs text-app-muted">({job.source.type})</span>
        </>
      ),
    },
    { key: "destinations", sortable: true, accessor: (job) => job.destinations.map((d) => d.name).join(", "), header: "Destinations", render: (job) => job.destinations.map((d) => d.name).join(", ") },
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
      key: "last", sortable: true, accessor: (job) => job.last_run?.created_at ?? "",
      header: "Last run",
      render: (job) =>
        job.last_run ? (
          <Link to={`/runs/${job.last_run.id}`} className="inline-flex items-center gap-2 hover:underline">
            <RunStatusBadge status={job.last_run.status} />
            <span className="whitespace-nowrap text-xs text-app-muted"><RelativeTime date={job.last_run.created_at} /></span>
          </Link>
        ) : (
          <RunStatusBadge status={null} />
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
              { key: "edit", label: "Edit", icon: <PencilIcon className="h-4 w-4" />, onClick: () => setEditor({ open: true, job }) },
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
        <Button onClick={() => setEditor({ open: true, job: null })}>
          <PlusIcon className="h-4 w-4" /> Add job
        </Button>
      </PageHeader>

      <Card padded={false}>
        {jobs.isPending && <Loader variant="inline" label="Loading jobs..." className="p-6" />}
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
            pageSize={25}
            hideFooterWhenSinglePage
            columns={columns}
            rows={jobs.data}
            empty="No jobs yet. Add a source and a destination, then create a job to back them up."
          />
        )}
      </Card>

      <JobDialog
        open={editor.open}
        job={editor.job}
        onClose={() => setEditor({ open: false, job: null })}
      />
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

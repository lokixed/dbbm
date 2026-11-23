import { Loader2, Pencil, Play, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useDeleteJob, useJobs, useRunJob, useSetJobEnabled } from "@/api/jobs";
import type { JobRecord } from "@/api/types";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { JobDialog } from "@/components/jobs/JobDialog";
import { RunStatusBadge } from "@/components/RunStatusBadge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { TBody, TD, TH, THead, Table, TR } from "@/components/ui/table";
import { useToast } from "@/components/ui/toast";
import { formatTime } from "@/lib/format";

function ScheduleBadge({ job }: { job: JobRecord }) {
  if (!job.schedule) return <Badge variant="neutral">Manual</Badge>;
  if (job.schedule_error) {
    return (
      <Badge variant="danger" title={job.schedule_error}>
        Invalid schedule: <span className="ml-1 font-mono">{job.schedule}</span>
      </Badge>
    );
  }
  return (
    <Badge variant={job.enabled ? "info" : "neutral"} title={`Time zone ${job.timezone}`}>
      <span className="font-mono">{job.schedule}</span>
      <span className="ml-1">({job.timezone})</span>
      {!job.enabled && <span className="ml-1">paused</span>}
    </Badge>
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

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Jobs</h1>
          <p className="text-sm text-muted-foreground">
            What to back up, where it goes and when it runs.
          </p>
        </div>
        <Button onClick={() => setEditor({ open: true, job: null })}>
          <Plus className="h-4 w-4" /> Add job
        </Button>
      </div>

      <Card>
        {jobs.isPending && (
          <p className="flex items-center gap-2 p-6 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" /> Loading jobs...
          </p>
        )}
        {jobs.isError && (
          <div role="alert" className="flex items-center justify-between gap-4 p-6 text-sm text-destructive">
            <span>Could not load jobs: {jobs.error.message}</span>
            <Button variant="secondary" size="sm" onClick={() => void jobs.refetch()}>
              Retry
            </Button>
          </div>
        )}
        {jobs.isSuccess && jobs.data.length === 0 && (
          <p className="p-6 text-sm text-muted-foreground">
            No jobs yet. Add a source and a destination, then create a job to back them up.
          </p>
        )}
        {jobs.isSuccess && jobs.data.length > 0 && (
          <Table>
            <THead>
              <tr>
                <TH>Name</TH>
                <TH>Source</TH>
                <TH>Destinations</TH>
                <TH>Enabled</TH>
                <TH>Next run</TH>
                <TH>Last run</TH>
                <TH className="text-right">Actions</TH>
              </tr>
            </THead>
            <TBody>
              {jobs.data.map((job) => {
                const starting = run.isPending && run.variables === job.id;
                const active = job.last_run?.status === "queued" || job.last_run?.status === "running";
                return (
                  <TR key={job.id}>
                    <TD className="font-medium">
                      {job.name}
                      <div className="mt-1">
                        <ScheduleBadge job={job} />
                      </div>
                    </TD>
                    <TD>
                      {job.source.name}{" "}
                      <span className="text-xs text-muted-foreground">({job.source.type})</span>
                    </TD>
                    <TD>{job.destinations.map((d) => d.name).join(", ")}</TD>
                    <TD>
                      <label className="flex items-center gap-2 text-sm">
                        <input
                          type="checkbox"
                          checked={job.enabled}
                          disabled={setEnabled.isPending}
                          onChange={() => toggle(job)}
                          aria-label={`${job.enabled ? "Disable" : "Enable"} ${job.name}`}
                        />
                        {job.enabled ? "On" : "Off"}
                      </label>
                    </TD>
                    <TD className="text-sm">
                      {job.next_run_at ? (
                        formatTime(job.next_run_at)
                      ) : (
                        <span className="text-muted-foreground">-</span>
                      )}
                    </TD>
                    <TD>
                      {job.last_run ? (
                        <Link
                          to={`/runs/${job.last_run.id}`}
                          className="inline-flex items-center gap-2 hover:underline"
                        >
                          <RunStatusBadge status={job.last_run.status} />
                          <span className="text-xs text-muted-foreground">
                            {formatTime(job.last_run.created_at)}
                          </span>
                        </Link>
                      ) : (
                        <RunStatusBadge status={null} />
                      )}
                    </TD>
                    <TD>
                      <div className="flex justify-end gap-1">
                        <Button
                          variant="ghost"
                          size="sm"
                          disabled={starting || active}
                          onClick={() => runNow(job)}
                          aria-label={`Run ${job.name} now`}
                        >
                          {starting ? (
                            <Loader2 className="h-4 w-4 animate-spin" />
                          ) : (
                            <Play className="h-4 w-4" />
                          )}
                          Run now
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => setEditor({ open: true, job })}
                          aria-label={`Edit ${job.name}`}
                        >
                          <Pencil className="h-4 w-4" /> Edit
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => setDeleting(job)}
                          aria-label={`Delete ${job.name}`}
                        >
                          <Trash2 className="h-4 w-4" /> Delete
                        </Button>
                      </div>
                    </TD>
                  </TR>
                );
              })}
            </TBody>
          </Table>
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

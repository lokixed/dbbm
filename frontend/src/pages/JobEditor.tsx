import { Link, useNavigate, useParams } from "react-router-dom";
import Alert from "xedonium/Alert";
import { useJobs } from "@/api/jobs";
import { EditorPage } from "@/components/EditorPage";
import { JobEditor } from "@/components/jobs/JobEditor";
import { ViewRunsButton } from "@/components/ViewRunsButton";
import { LogoLoader } from "@/components/LogoLoader";

const FRAME = {
  subtitle: "What to back up, where it goes and when it runs.",
  backTo: "/jobs",
  backLabel: "All jobs",
  note: "A job backs up every database of one source to one or more destinations.",
};

/** Add or edit a job on its own page: /jobs/new and /jobs/:id. */
export default function JobEditorPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const jobs = useJobs();
  const back = () => navigate("/jobs");

  if (!id) {
    return (
      <EditorPage title="Add job" {...FRAME}>
        <JobEditor job={null} onDone={back} />
      </EditorPage>
    );
  }

  if (jobs.isPending) return <LogoLoader />;
  if (jobs.isError) return <Alert tone="danger">Could not load the job: {jobs.error.message}</Alert>;
  const job = jobs.data.find((j) => j.id === id);
  if (!job) {
    return (
      <Alert tone="danger">
        This job does not exist. <Link to="/jobs" className="underline">Back to Jobs</Link>
      </Alert>
    );
  }
  return (
    <EditorPage title={`Edit ${job.name}`} {...FRAME} actions={<ViewRunsButton filter="job" id={job.id} />}>
      <JobEditor key={job.id} job={job} onDone={back} />
    </EditorPage>
  );
}

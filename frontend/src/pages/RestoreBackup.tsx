import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import Alert from "xedonium/Alert";
import { useBackup, useBackupCopies } from "@/api/backups";
import { RestoreWizard } from "@/components/backups/RestoreWizard";
import { EditorPage } from "@/components/EditorPage";
import { LogoLoader } from "@/components/LogoLoader";

/** Restore a backup on its own page: /backups/:id/restore?run=<run id>&db=<database>. Without
 * `run` and `db` (a shared or bookmarked link) the backup is looked up by its id first. */
export default function RestoreBackupPage() {
  const { id = "" } = useParams<{ id: string }>();
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const fromLink = params.get("run") !== null && params.get("db") !== null;
  const single = useBackup(id, !fromLink && id !== "");
  const run = fromLink ? (params.get("run") ?? "") : (single.data?.run_id ?? "");
  const database = fromLink ? (params.get("db") ?? "") : (single.data?.source_database ?? "");
  const copies = useBackupCopies(run, database, run !== "" && database !== "");
  const back = () => navigate("/backups");

  const missing = (
    <Alert tone="danger">
      This backup could not be found. <Link to="/backups" className="underline">Back to Backups</Link>
    </Alert>
  );
  if (!fromLink && single.isPending) return <LogoLoader />;
  if (!run || !database) return missing;
  if (copies.isPending) return <LogoLoader />;
  if (copies.isError) return <Alert tone="danger">Could not load the backup: {copies.error.message}</Alert>;
  const backup = copies.data.find((c) => c.id === id);
  if (!backup) return missing;

  return (
    <EditorPage
      title={`Restore ${backup.source_database}`}
      subtitle="Restore a backup into a registered source."
      backTo="/backups"
      backLabel="All backups"
      note="The restore runs like a backup, with a live log. The file's SHA-256 is checked before anything is changed."
    >
      <RestoreWizard backup={backup} onClose={back} />
    </EditorPage>
  );
}

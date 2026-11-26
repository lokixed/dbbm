import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import Alert from "xedonium/Alert";
import { useBackupCopies } from "@/api/backups";
import { RestoreWizard } from "@/components/backups/RestoreWizard";
import { EditorPage } from "@/components/EditorPage";
import { LogoLoader } from "@/components/LogoLoader";

/** Restore a backup on its own page: /backups/:id/restore?run=<run id>&db=<database>. */
export default function RestoreBackupPage() {
  const { id = "" } = useParams<{ id: string }>();
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const run = params.get("run") ?? "";
  const database = params.get("db") ?? "";
  const copies = useBackupCopies(run, database, run !== "" && database !== "");
  const back = () => navigate("/backups");

  const missing = (
    <Alert tone="danger">
      This backup could not be found. <Link to="/backups" className="underline">Back to Backups</Link>
    </Alert>
  );
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
      backLabel="Backups"
      note="The restore runs like a backup, with a live log. The file's SHA-256 is checked before anything is changed."
    >
      <RestoreWizard backup={backup} onClose={back} />
    </EditorPage>
  );
}

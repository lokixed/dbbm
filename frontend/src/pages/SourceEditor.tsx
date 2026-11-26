import { Link, useNavigate, useParams } from "react-router-dom";
import Alert from "xedonium/Alert";
import { useSources } from "@/api/sources";
import { EditorPage } from "@/components/EditorPage";
import { LogoLoader } from "@/components/LogoLoader";
import { SourceEditor } from "@/components/sources/SourceEditor";

const FRAME = {
  subtitle: "Databases to back up.",
  backTo: "/sources",
  backLabel: "Sources",
  note: "Credentials are encrypted at rest and never shown again after saving.",
};

/** Add or edit a source on its own page: /sources/new and /sources/:id. */
export default function SourceEditorPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const sources = useSources();
  const back = () => navigate("/sources");

  if (!id) {
    return (
      <EditorPage title="Add source" {...FRAME}>
        <SourceEditor source={null} onDone={back} />
      </EditorPage>
    );
  }

  if (sources.isPending) return <LogoLoader />;
  if (sources.isError) return <Alert tone="danger">Could not load the source: {sources.error.message}</Alert>;
  const source = sources.data.find((s) => s.id === id);
  if (!source) {
    return (
      <Alert tone="danger">
        This source does not exist. <Link to="/sources" className="underline">Back to Sources</Link>
      </Alert>
    );
  }
  return (
    <EditorPage title={`Edit ${source.name}`} {...FRAME}>
      <SourceEditor key={source.id} source={source} onDone={back} />
    </EditorPage>
  );
}

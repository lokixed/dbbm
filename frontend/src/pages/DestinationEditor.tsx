import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import Alert from "xedonium/Alert";
import { useDestinations } from "@/api/destinations";
import { DestinationForm } from "@/components/destinations/DestinationForm";
import { ViewRunsButton } from "@/components/ViewRunsButton";
import { EditorPage } from "@/components/EditorPage";
import { LogoLoader } from "@/components/LogoLoader";

const FRAME = {
  subtitle: "Where backups are stored.",
  backTo: "/destinations",
  backLabel: "All destinations",
  note: "Backups are copied to every destination of a job and verified there.",
};

/**
 * Add or edit a destination on its own page:
 * /destinations/new, /destinations/:id, and /destinations/:id?reconnect=1 (token field ready).
 */
export default function DestinationEditorPage() {
  const { id } = useParams<{ id: string }>();
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const destinations = useDestinations();
  const reconnect = params.get("reconnect") === "1";
  const back = () => navigate("/destinations");

  if (!id) {
    return (
      <EditorPage title="Add destination" {...FRAME}>
        <DestinationForm destination={null} focusToken={false} onDone={back} />
      </EditorPage>
    );
  }

  if (destinations.isPending) return <LogoLoader />;
  if (destinations.isError) {
    return <Alert tone="danger">Could not load the destination: {destinations.error.message}</Alert>;
  }
  const destination = destinations.data.find((d) => d.id === id);
  if (!destination) {
    return (
      <Alert tone="danger">
        This destination does not exist. <Link to="/destinations" className="underline">Back to Destinations</Link>
      </Alert>
    );
  }
  const focusToken = reconnect && destination.type === "gdrive";
  return (
    <EditorPage title={focusToken ? `Reconnect ${destination.name}` : `Edit ${destination.name}`} {...FRAME} actions={<ViewRunsButton filter="destination" id={destination.id} />}>
      <DestinationForm key={`${destination.id}-${focusToken}`} destination={destination} focusToken={focusToken} onDone={back} />
    </EditorPage>
  );
}

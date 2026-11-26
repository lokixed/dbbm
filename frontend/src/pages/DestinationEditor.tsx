import type { ReactNode } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import Alert from "xedonium/Alert";
import Button from "xedonium/Button";
import Card from "xedonium/Card";
import HelperText from "xedonium/HelperText";
import ArrowLeftIcon from "xedonium/icons/ArrowLeft";
import PageHeader from "xedonium/PageHeader";
import { useDestinations } from "@/api/destinations";
import { DestinationForm } from "@/components/destinations/DestinationForm";
import { LogoLoader } from "@/components/LogoLoader";

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

  const backLink = (
    <Button variant="secondary" onClick={back}>
      <ArrowLeftIcon className="h-4 w-4" /> Destinations
    </Button>
  );

  if (!id) {
    return (
      <Layout title="Add destination" action={backLink}>
        <DestinationForm destination={null} focusToken={false} onDone={back} />
      </Layout>
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
    <Layout title={focusToken ? `Reconnect ${destination.name}` : `Edit ${destination.name}`} action={backLink}>
      <DestinationForm key={`${destination.id}-${focusToken}`} destination={destination} focusToken={focusToken} onDone={back} />
    </Layout>
  );
}

function Layout({ title, action, children }: { title: string; action: ReactNode; children: ReactNode }) {
  return (
    <div className="space-y-6">
      <PageHeader title={title} subtitle="Where backups are stored.">
        {action}
      </PageHeader>
      <Card className="max-w-3xl">
        <HelperText className="mb-4">Backups are copied to every destination of a job and verified there.</HelperText>
        {children}
      </Card>
    </div>
  );
}

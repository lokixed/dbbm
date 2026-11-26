import { Link, useNavigate, useParams } from "react-router-dom";
import Alert from "xedonium/Alert";
import { useChannels } from "@/api/notifications";
import { EditorPage } from "@/components/EditorPage";
import { LogoLoader } from "@/components/LogoLoader";
import { ChannelForm } from "@/components/notifications/ChannelEditor";

const FRAME = {
  subtitle: "Where failures, recoveries, overdue jobs and system warnings are sent.",
  backTo: "/notifications",
  backLabel: "Notifications",
  note: "Secrets (webhook URLs, passwords, headers) are stored encrypted and never shown again.",
};

/** Add or edit a notification channel on its own page: /notifications/new and /notifications/:id. */
export default function ChannelEditorPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const channels = useChannels();
  const back = () => navigate("/notifications");

  if (!id) {
    return (
      <EditorPage title="Add notification channel" {...FRAME}>
        <ChannelForm channel={null} onClose={back} />
      </EditorPage>
    );
  }

  if (channels.isPending) return <LogoLoader />;
  if (channels.isError) return <Alert tone="danger">Could not load the channel: {channels.error.message}</Alert>;
  const channel = channels.data.find((c) => c.id === id);
  if (!channel) {
    return (
      <Alert tone="danger">
        This channel does not exist. <Link to="/notifications" className="underline">Back to Notifications</Link>
      </Alert>
    );
  }
  return (
    <EditorPage title={`Edit ${channel.name}`} {...FRAME}>
      <ChannelForm key={channel.id} channel={channel} onClose={back} />
    </EditorPage>
  );
}

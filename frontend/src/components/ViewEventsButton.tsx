import { useNavigate } from "react-router-dom";
import Button from "xedonium/Button";
import HistoryIcon from "xedonium/icons/History";

/** Opens the Notifications page with one channel's delivery events shown. */
export function ViewEventsButton({ channelId }: { channelId: string }) {
  const navigate = useNavigate();
  return (
    <Button variant="secondary" onClick={() => navigate(`/notifications?${new URLSearchParams({ events: channelId }).toString()}`)}>
      <HistoryIcon className="h-4 w-4" /> View events
    </Button>
  );
}

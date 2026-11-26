import { useNavigate } from "react-router-dom";
import Button from "xedonium/Button";
import HistoryIcon from "xedonium/icons/History";

/** Opens the Runs page filtered to one job, source or destination. */
export function ViewRunsButton({ filter, id }: { filter: "job" | "source" | "destination"; id: string }) {
  const navigate = useNavigate();
  return (
    <Button variant="secondary" onClick={() => navigate(`/runs?${new URLSearchParams({ [filter]: id }).toString()}`)}>
      <HistoryIcon className="h-4 w-4" /> View runs
    </Button>
  );
}

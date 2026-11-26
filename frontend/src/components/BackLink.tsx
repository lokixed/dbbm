import { Link } from "react-router-dom";
import ArrowLeftIcon from "xedonium/icons/ArrowLeft";

/** The "← All runs" link above a detail or edit page's title: every such page uses this one. */
export function BackLink({ to, label }: { to: string; label: string }) {
  return (
    <Link to={to} className="inline-flex items-center gap-1 text-sm text-app-muted hover:underline">
      <ArrowLeftIcon className="h-4 w-4" /> {label}
    </Link>
  );
}

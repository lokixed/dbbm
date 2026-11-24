import { Link } from "react-router-dom";
import { useVersion } from "@/api/auth";

// The bottom line of the sign-in and setup cards: a link to the docs on the left, the app version
// on the right. The version comes from the backend, so it matches the running image.
export function AuthFooter() {
  const { data } = useVersion();
  return (
    <div className="mt-5 flex items-center justify-between border-t border-app-border pt-3 text-xs text-app-muted">
      <Link to="/docs" className="hover:text-app-text hover:underline">
        Docs
      </Link>
      {data && <span>DBBM v{data.version}</span>}
    </div>
  );
}

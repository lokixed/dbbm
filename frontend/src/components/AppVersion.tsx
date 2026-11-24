import { useVersion } from "@/api/auth";
import { releaseNotesUrl } from "@/lib/version";

interface AppVersionProps {
  // Replaces the default muted text style; pass the colour as well as any layout classes.
  className?: string;
}

// "DBBM v1.2.3", read from the backend so it always matches the running image, and linked to that
// version's release notes. It is placed inside each page's own layout (sidebar footer, Docs), not
// floated over it.
export function AppVersion({ className = "text-app-muted" }: AppVersionProps) {
  const { data } = useVersion();
  if (!data) return null;
  return (
    <a
      href={releaseNotesUrl(data.version)}
      target="_blank"
      rel="noopener noreferrer"
      title="Release notes"
      className={`text-xs hover:text-app-text hover:underline ${className}`}
    >
      DBBM v{data.version}
    </a>
  );
}

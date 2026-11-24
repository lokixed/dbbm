import { useVersion } from "@/api/auth";

interface AppVersionProps {
  // Replaces the default muted text style; pass the colour as well as any layout classes.
  className?: string;
}

// "DBBM v1.2.3", read from the backend so it always matches the running image. It is placed inside
// each page's own layout (sidebar footer, sign-in and setup pages, Docs), not floated over it.
export function AppVersion({ className = "text-app-muted" }: AppVersionProps) {
  const { data } = useVersion();
  if (!data) return null;
  return <span className={`text-xs ${className}`}>DBBM v{data.version}</span>;
}

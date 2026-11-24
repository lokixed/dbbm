import { useVersion } from "@/api/auth";

// Shown on every page, signed in or not (sign-in, setup, About and the app layout): it is rendered
// once in App. The version comes from the backend, so it always matches the running image.
export function VersionBadge() {
  const { data } = useVersion();
  if (!data) return null;
  return (
    <footer className="pointer-events-none fixed bottom-2 left-3 z-10 select-none text-xs text-app-muted">
      DBBM v{data.version}
    </footer>
  );
}

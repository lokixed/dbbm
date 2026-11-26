// The name of the page at a path, for the document title and the page loader. The first prefix
// that matches wins, so "/runs/" (one run) comes before "/runs" (the list).
const PAGE_TITLES: readonly [string, string][] = [
  ["/runs/", "Run"],
  ["/runs", "Runs"],
  ["/sources/new", "Add source"],
  ["/sources/", "Source"],
  ["/sources", "Sources"],
  ["/destinations/new", "Add destination"],
  ["/destinations/", "Destination"],
  ["/destinations", "Destinations"],
  ["/jobs/new", "Add job"],
  ["/jobs/", "Job"],
  ["/jobs", "Jobs"],
  ["/backups/", "Restore"],
  ["/backups", "Backups"],
  ["/notifications/new", "Add notification channel"],
  ["/notifications/", "Notification channel"],
  ["/notifications", "Notifications"],
  ["/audit", "Audit log"],
  ["/settings", "Settings"],
  ["/docs", "Docs"],
  ["/login", "Sign in"],
  ["/setup", "Setup"],
  ["/oauth/google/done", "Google Drive"],
];

export function pageTitle(pathname: string): string {
  return PAGE_TITLES.find(([prefix]) => pathname.startsWith(prefix))?.[1] ?? "Dashboard";
}

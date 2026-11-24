// The name of the page at a path, for the document title and the page loader. The first prefix
// that matches wins, so "/runs/" (one run) comes before "/runs" (the list).
const PAGE_TITLES: readonly [string, string][] = [
  ["/runs/", "Run"],
  ["/runs", "Runs"],
  ["/sources", "Sources"],
  ["/destinations", "Destinations"],
  ["/jobs", "Jobs"],
  ["/backups", "Backups"],
  ["/notifications", "Notifications"],
  ["/audit", "Audit log"],
  ["/settings", "Settings"],
  ["/docs", "Docs"],
  ["/login", "Sign in"],
  ["/setup", "Setup"],
];

export function pageTitle(pathname: string): string {
  return PAGE_TITLES.find(([prefix]) => pathname.startsWith(prefix))?.[1] ?? "Dashboard";
}

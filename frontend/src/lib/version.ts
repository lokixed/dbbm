// Where release notes for a version live. Used by the version labels in the web UI.
export const RELEASES_URL = "https://github.com/lokixed/dbbm/releases";

export function releaseNotesUrl(version: string): string {
  return `${RELEASES_URL}/tag/v${version}`;
}

import type { Backup } from "@/api/backups";

/** The restore page of a backup. Run and database find its copies again after a reload. */
export function restorePath(backup: Backup): string {
  const query = new URLSearchParams({ run: backup.run_id, db: backup.source_database });
  return `/backups/${encodeURIComponent(backup.id)}/restore?${query.toString()}`;
}

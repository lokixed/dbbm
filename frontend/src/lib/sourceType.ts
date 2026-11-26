import type { SourceType } from "@/api/types";

export const SOURCE_TYPE_NAME: Record<SourceType, string> = {
  mysql: "MySQL / MariaDB",
  postgres: "PostgreSQL",
  mongo: "MongoDB",
  firestore: "Firebase Firestore",
};

export function sourceTypeName(type: string | null | undefined): string {
  return (type && SOURCE_TYPE_NAME[type as SourceType]) || "Database";
}

/** Databases that "All databases" never dumps (backend drivers' EXCLUDED_FROM_ALL; keep in sync).
 * They can still be picked under "Selected databases". */
export const SKIPPED_UNDER_ALL: Partial<Record<SourceType, string[]>> = {
  mysql: ["information_schema", "mysql", "performance_schema", "sys"],
  postgres: ["postgres", "template0", "template1"],
  mongo: ["admin", "config", "local"],
};

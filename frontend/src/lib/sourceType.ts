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

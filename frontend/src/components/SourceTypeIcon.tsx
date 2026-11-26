import DatabaseIcon from "xedonium/icons/Database";
import FirebaseIcon from "xedonium/icons/Firebase";
import MongodbIcon from "xedonium/icons/Mongodb";
import MysqlIcon from "xedonium/icons/MysqlLogo";
import PostgresqlIcon from "xedonium/icons/Postgresql";
import { sourceTypeName } from "@/lib/sourceType";

// xedonium ships the brand marks. Each is drawn white on a solid tile in the brand colour.
const ICONS = {
  mysql: { Icon: MysqlIcon, tile: "bg-[#00758f]" },
  postgres: { Icon: PostgresqlIcon, tile: "bg-[#336791]" },
  mongo: { Icon: MongodbIcon, tile: "bg-[#47a248]" },
  firestore: { Icon: FirebaseIcon, tile: "bg-[#f57c00]" },
} as const;

/** The brand mark for a source type on a solid tile (MySQL, PostgreSQL, MongoDB, Firebase); a database glyph for anything else. `className` sizes the tile. */
export function SourceTypeIcon({ type, className = "h-5 w-5" }: { type: string | null | undefined; className?: string }) {
  const entry = type ? ICONS[type as keyof typeof ICONS] : undefined;
  const Icon = entry?.Icon ?? DatabaseIcon;
  const tile = entry?.tile ?? "bg-app-muted";
  return (
    <span className={`inline-flex shrink-0 items-center justify-center p-[3px] text-white ${tile} ${className}`}>
      <Icon className="h-full w-full" />
    </span>
  );
}

/** Icon followed by text (the type's name when no text is given). */
export function SourceTypeLabel({
  type,
  children,
  className = "",
}: {
  type: string | null | undefined;
  children?: React.ReactNode;
  className?: string;
}) {
  return (
    <span className={`inline-flex h-5 min-w-0 items-center gap-2 align-top leading-5 ${className}`.trim()}>
      <SourceTypeIcon type={type} />
      <span className="truncate">{children ?? sourceTypeName(type)}</span>
    </span>
  );
}

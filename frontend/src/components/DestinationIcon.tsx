import HardDriveIcon from "xedonium/icons/HardDrive";
import { destinationTypeName } from "@/lib/destinationType";

function GoogleDriveMark({ className }: { className: string }) {
  return (
    <svg viewBox="0 0 87.3 78" className={className} aria-hidden="true" focusable="false">
      <path d="m6.6 66.85 3.85 6.65c.8 1.4 1.95 2.5 3.3 3.3l13.75-23.8h-27.5c0 1.55.4 3.1 1.2 4.5z" fill="#0066da" />
      <path d="m43.65 25-13.75-23.8c-1.35.8-2.5 1.9-3.3 3.3l-25.4 44a9.06 9.06 0 0 0-1.2 4.5h27.5z" fill="#00ac47" />
      <path d="m73.55 76.8c1.35-.8 2.5-1.9 3.3-3.3l1.6-2.75 7.65-13.25c.8-1.4 1.2-2.95 1.2-4.5h-27.502l5.852 11.5z" fill="#ea4335" />
      <path d="m43.65 25 13.75-23.8c-1.35-.8-2.9-1.2-4.5-1.2h-18.5c-1.6 0-3.15.45-4.5 1.2z" fill="#00832d" />
      <path d="m59.8 53h-32.3l-13.75 23.8c1.35.8 2.9 1.2 4.5 1.2h50.8c1.6 0 3.15-.45 4.5-1.2z" fill="#2684fc" />
      <path d="m73.4 26.5-12.7-22c-.8-1.4-1.95-2.5-3.3-3.3l-13.75 23.8 16.15 28h27.45c0-1.55-.4-3.1-1.2-4.5z" fill="#ffba00" />
    </svg>
  );
}

/** The icon for a destination type: a drive for local storage, the Google Drive mark for gdrive. */
export function DestinationIcon({ type, className = "h-4 w-4" }: { type: string | null | undefined; className?: string }) {
  if (type === "gdrive") return <GoogleDriveMark className={`shrink-0 ${className}`} />;
  return <HardDriveIcon className={`shrink-0 text-app-muted ${className}`} />;
}

/** Icon followed by text (the destination's name, or the type's name when no text is given). */
export function DestinationLabel({
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
      <DestinationIcon type={type} />
      <span className="truncate">{children ?? destinationTypeName(type)}</span>
    </span>
  );
}

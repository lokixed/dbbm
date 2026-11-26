import ArchiveIcon from "xedonium/icons/Archive";
import CalendarClockIcon from "xedonium/icons/CalendarClock";
import KeyIcon from "xedonium/icons/Key";
import PlayIcon from "xedonium/icons/Play";
import SettingsIcon from "xedonium/icons/Settings";
import { ChannelTypeIcon } from "@/components/ChannelTypeIcon";
import { DestinationIcon } from "@/components/DestinationIcon";
import { SourceTypeIcon } from "@/components/SourceTypeIcon";
import { CHANNEL_TYPE_NAME, type ChannelTypeKey } from "@/lib/channelType";
import { useChannelType, useDestinationType, useSourceType } from "@/lib/typeLookups";

const PLAIN = "h-4 w-4 shrink-0 text-app-muted";

function isChannelType(value: string | undefined): value is ChannelTypeKey {
  return value !== undefined && value in CHANNEL_TYPE_NAME;
}

/**
 * The icon for what an audit entry touched. Sources, destinations and channels show their own type's
 * icon: from the record while it exists, else from the type the entry recorded (so a deleted
 * source still shows its engine instead of the neutral fallback).
 */
function AuditObjectIcon({
  objectType,
  objectId,
  recordedType,
}: {
  objectType: string;
  objectId: string | null;
  recordedType?: string;
}) {
  const sourceType = useSourceType();
  const destinationType = useDestinationType();
  const channelType = useChannelType();
  switch (objectType) {
    case "source":
      return <SourceTypeIcon type={sourceType(objectId) ?? recordedType} />;
    case "destination":
      return <DestinationIcon type={destinationType(objectId) ?? recordedType} />;
    case "notification_channel": {
      const type = channelType(objectId) ?? (isChannelType(recordedType) ? recordedType : undefined);
      return <ChannelTypeIcon type={type ?? "webhook"} />;
    }
    case "job":
      return <CalendarClockIcon className={PLAIN} />;
    case "run":
      return <PlayIcon className={PLAIN} />;
    case "artifact":
      return <ArchiveIcon className={PLAIN} />;
    case "recipient":
      return <KeyIcon className={PLAIN} />;
    case "settings":
      return <SettingsIcon className={PLAIN} />;
    default:
      return null;
  }
}

/** Icon followed by text. */
export function AuditObjectLabel({
  objectType,
  objectId,
  recordedType,
  children,
}: {
  objectType: string;
  objectId: string | null;
  /** The type stored in the entry's details, used once the record is deleted. */
  recordedType?: string;
  children: React.ReactNode;
}) {
  return (
    <span className="inline-flex min-w-0 items-center gap-2 align-top">
      <AuditObjectIcon objectType={objectType} objectId={objectId} recordedType={recordedType} />
      <span className="truncate">{children}</span>
    </span>
  );
}

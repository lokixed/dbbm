import ArchiveIcon from "xedonium/icons/Archive";
import CalendarClockIcon from "xedonium/icons/CalendarClock";
import KeyIcon from "xedonium/icons/Key";
import PlayIcon from "xedonium/icons/Play";
import SettingsIcon from "xedonium/icons/Settings";
import { ChannelTypeIcon } from "@/components/ChannelTypeIcon";
import { DestinationIcon } from "@/components/DestinationIcon";
import { SourceTypeIcon } from "@/components/SourceTypeIcon";
import { useChannelType, useDestinationType, useSourceType } from "@/lib/typeLookups";

const PLAIN = "h-4 w-4 shrink-0 text-app-muted";

/** The icon for what an audit entry touched. Sources, destinations and channels show their own type's icon when the record still exists. */
function AuditObjectIcon({ objectType, objectId }: { objectType: string; objectId: string | null }) {
  const sourceType = useSourceType();
  const destinationType = useDestinationType();
  const channelType = useChannelType();
  switch (objectType) {
    case "source":
      return <SourceTypeIcon type={sourceType(objectId)} />;
    case "destination":
      return <DestinationIcon type={destinationType(objectId)} />;
    case "notification_channel": {
      const type = channelType(objectId);
      return type ? <ChannelTypeIcon type={type} /> : <ChannelTypeIcon type="webhook" />;
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
  children,
}: {
  objectType: string;
  objectId: string | null;
  children: React.ReactNode;
}) {
  return (
    <span className="inline-flex min-w-0 items-center gap-2 align-top">
      <AuditObjectIcon objectType={objectType} objectId={objectId} />
      <span className="truncate">{children}</span>
    </span>
  );
}

import Alert from "xedonium/Alert";
import Badge, { type BadgeColor } from "xedonium/Badge";
import Button from "xedonium/Button";
import type { DestinationAuthStatus, DestinationType } from "@/api/types";

const AUTH: Record<DestinationAuthStatus, { label: string; color: BadgeColor }> = {
  ok: { label: "Authorized", color: "success" },
  needs_reauth: { label: "Reconnect needed", color: "danger" },
  unknown: { label: "Not checked yet", color: "secondary" },
};

export function AuthBadge({ type, status }: { type: DestinationType; status: DestinationAuthStatus }) {
  if (type !== "gdrive") return null;
  const { label, color } = AUTH[status];
  return <Badge badgeContent={label} color={color} />;
}

interface ReauthBannerProps {
  destinations: { id: string; name: string }[];
  onReconnect: (id: string) => void;
}

/** Shown while any Drive destination's authorization has expired or was revoked. */
export function ReauthBanner({ destinations, onReconnect }: ReauthBannerProps) {
  if (destinations.length === 0) return null;
  return (
    <Alert tone="danger" title="Google Drive authorization expired">
      <p>
        Backups to these destinations are skipped until you paste a new token. Local copies are still
        made, and failed uploads can be retried from the run page afterwards.
      </p>
      <div className="mt-2 flex flex-wrap gap-2">
        {destinations.map((d) => (
          <Button key={d.id} onClick={() => onReconnect(d.id)}>
            Reconnect {d.name}
          </Button>
        ))}
      </div>
    </Alert>
  );
}

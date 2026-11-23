import { AlertTriangle } from "lucide-react";
import type { DestinationAuthStatus, DestinationType } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

const AUTH: Record<DestinationAuthStatus, { label: string; variant: "success" | "danger" | "neutral" }> = {
  ok: { label: "Authorized", variant: "success" },
  needs_reauth: { label: "Reconnect needed", variant: "danger" },
  unknown: { label: "Not checked yet", variant: "neutral" },
};

export function AuthBadge({ type, status }: { type: DestinationType; status: DestinationAuthStatus }) {
  if (type !== "gdrive") return null;
  const { label, variant } = AUTH[status];
  return <Badge variant={variant}>{label}</Badge>;
}

interface ReauthBannerProps {
  destinations: { id: string; name: string }[];
  onReconnect: (id: string) => void;
}

/** Shown while any Drive destination's authorization has expired or was revoked. */
export function ReauthBanner({ destinations, onReconnect }: ReauthBannerProps) {
  if (destinations.length === 0) return null;
  return (
    <div
      role="alert"
      className="space-y-2 rounded-md border border-destructive/60 bg-destructive/10 p-4 text-sm"
    >
      <p className="flex items-center gap-2 font-medium text-destructive">
        <AlertTriangle className="h-4 w-4" /> Google Drive authorization expired
      </p>
      <p className="text-muted-foreground">
        Backups to these destinations are skipped until you paste a new token. Local copies are still
        made, and failed uploads can be retried from the run page afterwards.
      </p>
      <div className="flex flex-wrap gap-2">
        {destinations.map((d) => (
          <Button key={d.id} size="sm" onClick={() => onReconnect(d.id)}>
            Reconnect {d.name}
          </Button>
        ))}
      </div>
    </div>
  );
}

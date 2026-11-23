import { Loader2, Pencil, Play, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useDeleteDestination, useDestinations, useTestDestination } from "@/api/destinations";
import type { DestinationRecord } from "@/api/types";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { AuthBadge, ReauthBanner } from "@/components/destinations/AuthStatus";
import { DestinationDialog } from "@/components/destinations/DestinationDialog";
import { StatusBadge } from "@/components/StatusBadge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { TBody, TD, TH, THead, Table, TR } from "@/components/ui/table";
import { useToast } from "@/components/ui/toast";

interface EditorState {
  open: boolean;
  destination: DestinationRecord | null;
  focusToken: boolean;
}

const CLOSED: EditorState = { open: false, destination: null, focusToken: false };

function location(destination: DestinationRecord): string {
  if (destination.type === "local") return destination.config.path;
  const drive = destination.config.shared_drive_id ? `shared drive ${destination.config.shared_drive_id}` : "My Drive";
  return `${drive}: ${destination.config.remote_folder}`;
}

export default function DestinationsPage() {
  const { toast } = useToast();
  const destinations = useDestinations();
  const test = useTestDestination();
  const remove = useDeleteDestination();
  const [params, setParams] = useSearchParams();
  const [editor, setEditor] = useState<EditorState>(CLOSED);
  const [deleting, setDeleting] = useState<DestinationRecord | null>(null);

  // Arriving from the dashboard's Reconnect action: /destinations?reconnect=<id>
  const reconnectId = params.get("reconnect");
  const reconnectTarget = reconnectId
    ? (destinations.data?.find((d) => d.id === reconnectId) ?? null)
    : null;
  const current: EditorState = reconnectTarget
    ? { open: true, destination: reconnectTarget, focusToken: true }
    : editor;

  const closeEditor = () => {
    setEditor(CLOSED);
    if (reconnectId) setParams({}, { replace: true });
  };
  const reconnect = (id: string) => {
    const destination = destinations.data?.find((d) => d.id === id) ?? null;
    if (destination) setEditor({ open: true, destination, focusToken: true });
  };
  const needsReauth = (destinations.data ?? []).filter((d) => d.auth_status === "needs_reauth");

  const runTest = (destination: DestinationRecord) =>
    test.mutate(destination.id, {
      onSuccess: (result) =>
        toast({
          title: result.ok ? `${destination.name}: OK` : `${destination.name}: check failed`,
          description: result.message,
          variant: result.ok ? "success" : "error",
        }),
      onError: (error) =>
        toast({
          title: `${destination.name}: test failed`,
          description: error.message,
          variant: "error",
        }),
    });

  const confirmDelete = () => {
    if (!deleting) return;
    remove.mutate(deleting.id, {
      onSuccess: () => {
        toast({ title: "Destination deleted", description: deleting.name, variant: "success" });
        setDeleting(null);
      },
      onError: (error) => {
        toast({ title: "Could not delete destination", description: error.message, variant: "error" });
        setDeleting(null);
      },
    });
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Destinations</h1>
          <p className="text-sm text-muted-foreground">Where backups are stored.</p>
        </div>
        <Button onClick={() => setEditor({ open: true, destination: null, focusToken: false })}>
          <Plus className="h-4 w-4" /> Add destination
        </Button>
      </div>

      <ReauthBanner destinations={needsReauth} onReconnect={reconnect} />

      <Card>
        {destinations.isPending && (
          <p className="flex items-center gap-2 p-6 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" /> Loading destinations...
          </p>
        )}
        {destinations.isError && (
          <div role="alert" className="flex items-center justify-between gap-4 p-6 text-sm text-destructive">
            <span>Could not load destinations: {destinations.error.message}</span>
            <Button variant="secondary" size="sm" onClick={() => void destinations.refetch()}>
              Retry
            </Button>
          </div>
        )}
        {destinations.isSuccess && destinations.data.length === 0 && (
          <p className="p-6 text-sm text-muted-foreground">
            No destinations yet. Add a local folder or a Google Drive folder to store backups.
          </p>
        )}
        {destinations.isSuccess && destinations.data.length > 0 && (
          <Table>
            <THead>
              <tr>
                <TH>Name</TH>
                <TH>Type</TH>
                <TH>Location</TH>
                <TH>Status</TH>
                <TH className="text-right">Actions</TH>
              </tr>
            </THead>
            <TBody>
              {destinations.data.map((destination) => {
                const testing = test.isPending && test.variables === destination.id;
                return (
                  <TR key={destination.id}>
                    <TD className="font-medium">{destination.name}</TD>
                    <TD>{destination.type === "gdrive" ? "Google Drive" : "Local drive"}</TD>
                    <TD className="font-mono text-xs">{location(destination)}</TD>
                    <TD>
                      <div className="flex flex-wrap items-center gap-2">
                        <StatusBadge lastTest={destination.last_test} />
                        <AuthBadge type={destination.type} status={destination.auth_status} />
                      </div>
                    </TD>
                    <TD>
                      <div className="flex justify-end gap-1">
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => setEditor({ open: true, destination, focusToken: false })}
                          aria-label={`Edit ${destination.name}`}
                        >
                          <Pencil className="h-4 w-4" /> Edit
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          disabled={testing}
                          onClick={() => runTest(destination)}
                          aria-label={`Test ${destination.name}`}
                        >
                          {testing ? (
                            <Loader2 className="h-4 w-4 animate-spin" />
                          ) : (
                            <Play className="h-4 w-4" />
                          )}
                          Test
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => setDeleting(destination)}
                          aria-label={`Delete ${destination.name}`}
                        >
                          <Trash2 className="h-4 w-4" /> Delete
                        </Button>
                      </div>
                    </TD>
                  </TR>
                );
              })}
            </TBody>
          </Table>
        )}
      </Card>

      <DestinationDialog
        key={`${current.destination?.id ?? "new"}-${current.focusToken}-${current.open}`}
        open={current.open}
        destination={current.destination}
        focusToken={current.focusToken}
        onClose={closeEditor}
      />
      <ConfirmDialog
        open={deleting !== null}
        title="Delete destination?"
        description={`"${deleting?.name ?? ""}" will be removed. Files already written to disk are not deleted.`}
        confirmLabel="Delete"
        pending={remove.isPending}
        onConfirm={confirmDelete}
        onCancel={() => setDeleting(null)}
      />
    </div>
  );
}

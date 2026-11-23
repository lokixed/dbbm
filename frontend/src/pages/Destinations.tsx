import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import Alert from "xedonium/Alert";
import Button from "xedonium/Button";
import Card from "xedonium/Card";
import Loader from "xedonium/Loader";
import PageHeader from "xedonium/PageHeader";
import Table, { type TableColumn } from "xedonium/Table";
import PencilIcon from "xedonium/icons/Pencil";
import PlayIcon from "xedonium/icons/Play";
import PlusIcon from "xedonium/icons/Plus";
import Trash2Icon from "xedonium/icons/Trash2";
import { useDeleteDestination, useDestinations, useTestDestination } from "@/api/destinations";
import type { DestinationRecord } from "@/api/types";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { AuthBadge, ReauthBanner } from "@/components/destinations/AuthStatus";
import { DestinationDialog } from "@/components/destinations/DestinationDialog";
import { StatusBadge } from "@/components/StatusBadge";
import { useToast } from "@/components/toast";

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

  const columns: TableColumn<DestinationRecord>[] = [
    { key: "name", header: "Name", render: (d) => <span className="font-medium">{d.name}</span> },
    { key: "type", header: "Type", render: (d) => (d.type === "gdrive" ? "Google Drive" : "Local drive") },
    { key: "location", header: "Location", render: (d) => <span className="font-mono text-xs">{location(d)}</span> },
    {
      key: "status",
      header: "Status",
      render: (d) => (
        <div className="flex flex-wrap items-center gap-2">
          <StatusBadge lastTest={d.last_test} />
          <AuthBadge type={d.type} status={d.auth_status} />
        </div>
      ),
    },
    {
      key: "actions",
      header: "Actions",
      align: "right",
      render: (d) => {
        const testing = test.isPending && test.variables === d.id;
        return (
          <div className="flex justify-end gap-1">
            <Button
              variant="flat"
              onClick={() => setEditor({ open: true, destination: d, focusToken: false })}
              aria-label={`Edit ${d.name}`}
            >
              <PencilIcon className="h-4 w-4" /> Edit
            </Button>
            <Button variant="flat" disabled={testing} onClick={() => runTest(d)} aria-label={`Test ${d.name}`}>
              <PlayIcon className="h-4 w-4" /> Test
            </Button>
            <Button variant="flat" onClick={() => setDeleting(d)} aria-label={`Delete ${d.name}`}>
              <Trash2Icon className="h-4 w-4" /> Delete
            </Button>
          </div>
        );
      },
    },
  ];

  return (
    <div className="space-y-6">
      <PageHeader title="Destinations" subtitle="Where backups are stored.">
        <Button onClick={() => setEditor({ open: true, destination: null, focusToken: false })}>
          <PlusIcon className="h-4 w-4" /> Add destination
        </Button>
      </PageHeader>

      <ReauthBanner destinations={needsReauth} onReconnect={reconnect} />

      <Card padded={false}>
        {destinations.isPending && <Loader variant="inline" label="Loading destinations..." className="p-6" />}
        {destinations.isError && (
          <Alert tone="danger">
            <div className="flex items-center justify-between gap-4">
              <span>Could not load destinations: {destinations.error.message}</span>
              <Button variant="secondary" onClick={() => void destinations.refetch()}>
                Retry
              </Button>
            </div>
          </Alert>
        )}
        {destinations.isSuccess && (
          <Table
            columns={columns}
            rows={destinations.data}
            empty="No destinations yet. Add a local folder or a Google Drive folder to store backups."
          />
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

import { DestinationLabel } from "@/components/DestinationIcon";
import { useState } from "react";
import { Link, Navigate, useNavigate, useSearchParams } from "react-router-dom";
import Alert from "xedonium/Alert";
import Button from "xedonium/Button";
import Loader from "xedonium/Loader";
import PageHeader from "xedonium/PageHeader";
import DataGrid, { type DataGridColumn } from "xedonium/DataGrid";
import PencilIcon from "xedonium/icons/Pencil";
import PlayIcon from "xedonium/icons/Play";
import PlusIcon from "xedonium/icons/Plus";
import Trash2Icon from "xedonium/icons/Trash2";
import { useDeleteDestination, useDestinations, useTestDestination } from "@/api/destinations";
import type { DestinationRecord } from "@/api/types";
import { RowMenu } from "@/components/RowMenu";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { AuthBadge, ReauthBanner } from "@/components/destinations/AuthStatus";
import { LastTested, StatusBadge } from "@/components/StatusBadge";
import { useToast } from "@/components/toast";
import { ConnectionBulkActions } from "@/components/ConnectionBulkActions";
import { LogoLoader } from "@/components/LogoLoader";
import { SearchBox } from "@/components/SearchBox";
import { matchesSearch } from "@/lib/search";
import { destinationTypeName } from "@/lib/destinationType";

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
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [deleting, setDeleting] = useState<DestinationRecord | null>(null);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [query, setQuery] = useState("");

  const reconnect = (id: string) => navigate(`/destinations/${encodeURIComponent(id)}?reconnect=1`);
  const needsReauth = (destinations.data ?? []).filter((d) => d.auth_status === "needs_reauth");

  const runTest = (destination: DestinationRecord) => {
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
  };

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

  const columns: DataGridColumn<DestinationRecord>[] = [
    { key: "name", sortable: true, accessor: (d) => d.name, header: "Name", render: (d) => <Link to={`/destinations/${encodeURIComponent(d.id)}`} className="whitespace-nowrap font-medium hover:underline">{d.name}</Link> },
    { key: "type", sortable: true, accessor: (d) => d.type, header: "Type", render: (d) => <DestinationLabel type={d.type} /> },
    { key: "location", sortable: true, accessor: (d) => location(d), header: "Location", render: (d) => <span className="font-mono text-xs">{location(d)}</span> },
    {
      key: "status", sortable: true, accessor: (d) => (d.last_test ? (d.last_test.ok ? "OK" : "Failed") : "Never tested"),
      header: "Status",
      render: (d) =>
        test.isPending && test.variables === d.id ? (
          <Loader variant="inline" size="sm" label="Testing..." />
        ) : (
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge lastTest={d.last_test} />
            <AuthBadge type={d.type} status={d.auth_status} />
          </div>
        ),
    },
    { key: "tested", sortable: true, accessor: (d) => d.last_test?.at ?? "", header: "Last tested", render: (d) => <LastTested lastTest={d.last_test} /> },
    {
      key: "actions",
      header: "Actions",
      align: "right",
      render: (d) => {
        const testing = test.isPending && test.variables === d.id;
        return (
          <RowMenu
            label={`Actions for ${d.name}`}
            items={[
              { key: "edit", label: "Edit", icon: <PencilIcon className="h-4 w-4" />, onClick: () => navigate(`/destinations/${encodeURIComponent(d.id)}`) },
              { key: "test", label: "Test connection", icon: <PlayIcon className="h-4 w-4" />, disabled: testing, onClick: () => runTest(d) },
              { key: "delete", label: "Delete", tone: "danger", icon: <Trash2Icon className="h-4 w-4" />, onClick: () => setDeleting(d) },
            ]}
          />
        );
      },
    },
  ];

  // Older links (notifications, bookmarks): /destinations?reconnect=<id>
  const reconnectId = params.get("reconnect");
  if (reconnectId) {
    return <Navigate to={`/destinations/${encodeURIComponent(reconnectId)}?reconnect=1`} replace />;
  }

  return (
    <div className="space-y-6">
      <PageHeader title="Destinations" subtitle="Where backups are stored.">
        <Button onClick={() => navigate("/destinations/new")}>
          <PlusIcon className="h-4 w-4" /> Add destination
        </Button>
      </PageHeader>

      <SearchBox label="Search destinations" placeholder="Search name, type or location" value={query} onChange={setQuery} />

      <ReauthBanner destinations={needsReauth} onReconnect={reconnect} />

      {destinations.isPending && <LogoLoader />}
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
      <ConnectionBulkActions
        kind="destination"
        selected={(destinations.data ?? []).filter((row) => selectedIds.includes(row.id))}
        onClear={() => setSelectedIds([])}
      />

      {destinations.isSuccess && (
        <DataGrid
          columns={columns}
          selectable
          selected={selectedIds}
          onSelectionChange={(keys) => setSelectedIds(keys.map(String))}
          density="compact"
          pageSize={10}
          pageSizeOptions={[10, 20, 50, 100]}
          hideFooterWhenSinglePage
          rows={destinations.data.filter((d) => matchesSearch(query, [d.name, destinationTypeName(d.type), location(d)]))}
          empty={query.trim() ? "No destinations match." : "No destinations yet. Add a local folder or a Google Drive folder to store backups."}
        />
      )}

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

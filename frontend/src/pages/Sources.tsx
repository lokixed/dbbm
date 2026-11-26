import { useState } from "react";
import { useNavigate } from "react-router-dom";
import Alert from "xedonium/Alert";
import Button from "xedonium/Button";
import Loader from "xedonium/Loader";
import PageHeader from "xedonium/PageHeader";
import DataGrid, { type DataGridColumn } from "xedonium/DataGrid";
import PencilIcon from "xedonium/icons/Pencil";
import PlayIcon from "xedonium/icons/Play";
import PlusIcon from "xedonium/icons/Plus";
import Trash2Icon from "xedonium/icons/Trash2";
import { useDeleteSource, useSources, useTestSource } from "@/api/sources";
import type { SourceRecord } from "@/api/types";
import { RowMenu } from "@/components/RowMenu";
import { SourceTypeLabel } from "@/components/SourceTypeIcon";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { LastTested, StatusBadge } from "@/components/StatusBadge";
import { useToast } from "@/components/toast";
import { LogoLoader } from "@/components/LogoLoader";
import { SOURCE_TYPES } from "@/lib/sourceForm";

const typeLabel = (type: string) => SOURCE_TYPES.find((t) => t.type === type)?.label ?? type;

export default function SourcesPage() {
  const { toast } = useToast();
  const sources = useSources();
  const test = useTestSource();
  const remove = useDeleteSource();
  const navigate = useNavigate();
  const [deleting, setDeleting] = useState<SourceRecord | null>(null);

  const runTest = (source: SourceRecord) =>
    test.mutate(source.id, {
      onSuccess: (result) =>
        toast({
          title: result.ok ? `${source.name}: connection OK` : `${source.name}: connection failed`,
          description: result.message,
          variant: result.ok ? "success" : "error",
        }),
      onError: (error) =>
        toast({ title: `${source.name}: test failed`, description: error.message, variant: "error" }),
    });

  const confirmDelete = () => {
    if (!deleting) return;
    remove.mutate(deleting.id, {
      onSuccess: () => {
        toast({ title: "Source deleted", description: deleting.name, variant: "success" });
        setDeleting(null);
      },
      onError: (error) => {
        toast({ title: "Could not delete source", description: error.message, variant: "error" });
        setDeleting(null);
      },
    });
  };

  const columns: DataGridColumn<SourceRecord>[] = [
    { key: "name", sortable: true, accessor: (s) => s.name, header: "Name", render: (source) => <span className="font-medium">{source.name}</span> },
    { key: "type", sortable: true, accessor: (s) => typeLabel(s.type), header: "Type", render: (source) => <SourceTypeLabel type={source.type}>{typeLabel(source.type)}</SourceTypeLabel> },
    { key: "status", sortable: true, accessor: (s) => (s.last_test ? (s.last_test.ok ? "OK" : "Failed") : "Never tested"), header: "Status", render: (source) =>
        test.isPending && test.variables === source.id ? (
          <Loader variant="inline" size="sm" label="Testing..." />
        ) : (
          <StatusBadge lastTest={source.last_test} />
        ) },
    { key: "tested", sortable: true, accessor: (s) => s.last_test?.at ?? "", header: "Last tested", render: (s) => <LastTested lastTest={s.last_test} /> },
    {
      key: "actions",
      header: "Actions",
      align: "right",
      render: (source) => {
        const testing = test.isPending && test.variables === source.id;
        return (
          <RowMenu
            label={`Actions for ${source.name}`}
            items={[
              { key: "edit", label: "Edit", icon: <PencilIcon className="h-4 w-4" />, onClick: () => navigate(`/sources/${encodeURIComponent(source.id)}`) },
              { key: "test", label: "Test connection", icon: <PlayIcon className="h-4 w-4" />, disabled: testing, onClick: () => runTest(source) },
              { key: "delete", label: "Delete", tone: "danger", icon: <Trash2Icon className="h-4 w-4" />, onClick: () => setDeleting(source) },
            ]}
          />
        );
      },
    },
  ];

  return (
    <div className="space-y-6">
      <PageHeader title="Sources" subtitle="Databases to back up.">
        <Button onClick={() => navigate("/sources/new")}>
          <PlusIcon className="h-4 w-4" /> Add source
        </Button>
      </PageHeader>

      {sources.isPending && <LogoLoader />}
      {sources.isError && (
        <Alert tone="danger">
          <div className="flex items-center justify-between gap-4">
            <span>Could not load sources: {sources.error.message}</span>
            <Button variant="secondary" onClick={() => void sources.refetch()}>
              Retry
            </Button>
          </div>
        </Alert>
      )}
      {sources.isSuccess && (
        <DataGrid
          columns={columns}
          density="compact"
          pageSize={10}
          pageSizeOptions={[10, 20, 50, 100]}
          searchable
          hideFooterWhenSinglePage
          rows={sources.data}
          empty="No sources yet. Add a database to get started."
        />
      )}

      <ConfirmDialog
        open={deleting !== null}
        title="Delete source?"
        description={`"${deleting?.name ?? ""}" and its saved credentials will be permanently removed.`}
        confirmLabel="Delete"
        pending={remove.isPending}
        onConfirm={confirmDelete}
        onCancel={() => setDeleting(null)}
      />
    </div>
  );
}

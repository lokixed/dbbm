import { useState } from "react";
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
import { useDeleteSource, useSources, useTestSource } from "@/api/sources";
import type { SourceRecord } from "@/api/types";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { SourceDialog } from "@/components/sources/SourceDialog";
import { StatusBadge } from "@/components/StatusBadge";
import { useToast } from "@/components/toast";
import { SOURCE_TYPES } from "@/lib/sourceForm";

const typeLabel = (type: string) => SOURCE_TYPES.find((t) => t.type === type)?.label ?? type;

export default function SourcesPage() {
  const { toast } = useToast();
  const sources = useSources();
  const test = useTestSource();
  const remove = useDeleteSource();
  const [editor, setEditor] = useState<{ open: boolean; source: SourceRecord | null }>({
    open: false,
    source: null,
  });
  const [deleting, setDeleting] = useState<SourceRecord | null>(null);

  const closeEditor = () => setEditor({ open: false, source: null });

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

  const columns: TableColumn<SourceRecord>[] = [
    { key: "name", header: "Name", render: (source) => <span className="font-medium">{source.name}</span> },
    { key: "type", header: "Type", render: (source) => typeLabel(source.type) },
    { key: "status", header: "Status", render: (source) => <StatusBadge lastTest={source.last_test} /> },
    {
      key: "actions",
      header: "Actions",
      align: "right",
      render: (source) => {
        const testing = test.isPending && test.variables === source.id;
        return (
          <div className="flex justify-end gap-1 whitespace-nowrap">
            <Button
              variant="flat"
              onClick={() => setEditor({ open: true, source })}
              aria-label={`Edit ${source.name}`}
            >
              <PencilIcon className="h-4 w-4" /> Edit
            </Button>
            <Button
              variant="flat"
              disabled={testing}
              onClick={() => runTest(source)}
              aria-label={`Test ${source.name}`}
            >
              <PlayIcon className="h-4 w-4" /> Test
            </Button>
            <Button variant="flat" onClick={() => setDeleting(source)} aria-label={`Delete ${source.name}`}>
              <Trash2Icon className="h-4 w-4" /> Delete
            </Button>
          </div>
        );
      },
    },
  ];

  return (
    <div className="space-y-6">
      <PageHeader title="Sources" subtitle="Databases to back up.">
        <Button onClick={() => setEditor({ open: true, source: null })}>
          <PlusIcon className="h-4 w-4" /> Add source
        </Button>
      </PageHeader>

      <Card padded={false}>
        {sources.isPending && <Loader variant="inline" label="Loading sources..." className="p-6" />}
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
          <Table
            columns={columns}
            rows={sources.data}
            empty="No sources yet. Add a database to get started."
          />
        )}
      </Card>

      <SourceDialog open={editor.open} source={editor.source} onClose={closeEditor} />
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

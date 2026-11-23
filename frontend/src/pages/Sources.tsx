import { Loader2, Pencil, Play, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { useDeleteSource, useSources, useTestSource } from "@/api/sources";
import type { SourceRecord } from "@/api/types";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { SourceDialog } from "@/components/sources/SourceDialog";
import { StatusBadge } from "@/components/StatusBadge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { TBody, TD, TH, THead, Table, TR } from "@/components/ui/table";
import { useToast } from "@/components/ui/toast";
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

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Sources</h1>
          <p className="text-sm text-muted-foreground">Databases to back up.</p>
        </div>
        <Button onClick={() => setEditor({ open: true, source: null })}>
          <Plus className="h-4 w-4" /> Add source
        </Button>
      </div>

      <Card>
        {sources.isPending && (
          <p className="flex items-center gap-2 p-6 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" /> Loading sources...
          </p>
        )}
        {sources.isError && (
          <div role="alert" className="flex items-center justify-between gap-4 p-6 text-sm text-destructive">
            <span>Could not load sources: {sources.error.message}</span>
            <Button variant="secondary" size="sm" onClick={() => void sources.refetch()}>
              Retry
            </Button>
          </div>
        )}
        {sources.isSuccess && sources.data.length === 0 && (
          <p className="p-6 text-sm text-muted-foreground">
            No sources yet. Add a database to get started.
          </p>
        )}
        {sources.isSuccess && sources.data.length > 0 && (
          <Table>
            <THead>
              <tr>
                <TH>Name</TH>
                <TH>Type</TH>
                <TH>Status</TH>
                <TH className="text-right">Actions</TH>
              </tr>
            </THead>
            <TBody>
              {sources.data.map((source) => {
                const testing = test.isPending && test.variables === source.id;
                return (
                  <TR key={source.id}>
                    <TD className="font-medium">{source.name}</TD>
                    <TD>{typeLabel(source.type)}</TD>
                    <TD>
                      <StatusBadge lastTest={source.last_test} />
                    </TD>
                    <TD>
                      <div className="flex justify-end gap-1">
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => setEditor({ open: true, source })}
                          aria-label={`Edit ${source.name}`}
                        >
                          <Pencil className="h-4 w-4" /> Edit
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          disabled={testing}
                          onClick={() => runTest(source)}
                          aria-label={`Test ${source.name}`}
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
                          onClick={() => setDeleting(source)}
                          aria-label={`Delete ${source.name}`}
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

import { useState } from "react";
import Alert from "xedonium/Alert";
import Button from "xedonium/Button";
import Card from "xedonium/Card";
import CodeDisplay from "xedonium/CodeDisplay";
import Field from "xedonium/Field";
import HelperText from "xedonium/HelperText";
import Loader from "xedonium/Loader";
import Table, { type TableColumn } from "xedonium/Table";
import { useAddRecipient, useDeleteRecipient, useRecipients, type Recipient } from "@/api/recipients";
import { RowMenu } from "@/components/RowMenu";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { useToast } from "@/components/toast";

const KEYGEN = `age-keygen -o backup-key.txt
# Prints the public key (age1...). Paste that here.
# Keep backup-key.txt, the private key, somewhere safe and OFF this server.`;

const DECRYPT = `age --decrypt -i backup-key.txt -o backup.sql.gz backup.sql.gz.age`;

/** Backup encryption: the age public keys that jobs can encrypt to. The server never holds a private key. */
export function EncryptionKeys() {
  const { toast } = useToast();
  const recipients = useRecipients();
  const add = useAddRecipient();
  const remove = useDeleteRecipient();
  const [name, setName] = useState("");
  const [publicKey, setPublicKey] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pendingDelete, setPendingDelete] = useState<Recipient | null>(null);

  const submit = () => {
    setError(null);
    add.mutate(
      { name: name.trim(), public_key: publicKey.trim() },
      {
        onSuccess: (key) => {
          toast({ title: "Key added", description: `${key.name} (${key.fingerprint})`, variant: "success" });
          setName("");
          setPublicKey("");
        },
        onError: (e) => setError(e.message),
      },
    );
  };

  const columns: TableColumn<Recipient>[] = [
    { key: "name", header: "Name", render: (r) => <span className="font-medium">{r.name}</span> },
    { key: "fingerprint", header: "Fingerprint", render: (r) => <code className="text-xs">{r.fingerprint}</code> },
    {
      key: "key",
      header: "Public key",
      render: (r) => <code className="text-xs">{r.public_key.slice(0, 16)}…{r.public_key.slice(-6)}</code>,
    },
    { key: "used", header: "Used by", render: (r) => (r.used_by.length ? r.used_by.join(", ") : "No job") },
    {
      key: "actions",
      header: "",
      align: "right",
      render: (r) => (
        <RowMenu
          label={`Actions for ${r.name}`}
          items={[{ key: "remove", label: "Remove", tone: "danger", onClick: () => setPendingDelete(r) }]}
        />
      ),
    },
  ];

  return (
    <Card
      title="Backup encryption keys"
      subtitle="Jobs can encrypt every backup with age before it is stored or uploaded."
    >
      <div className="space-y-4">
        <Alert tone="warning" title="Losing the private key means losing the backups">
          Encrypted backups cannot be opened without the private key, and this server never has it. If you
          lose it, nobody can recover those backups, not even the administrator. Save the private key (and a
          recovery key on a second key) somewhere safe before you turn encryption on.
        </Alert>

        {recipients.isPending && <Loader variant="inline" label="Loading keys..." />}
        {recipients.isError && <Alert tone="danger">Could not load keys: {recipients.error.message}</Alert>}
        {recipients.isSuccess && (
          <Table
            caption="Encryption keys"
            columns={columns}
            rows={recipients.data}
            empty="No keys yet. Add a public key to be able to encrypt backups."
          />
        )}

        <div className="space-y-3 border border-app-border p-3">
          <p className="text-sm font-medium">Add a public key</p>
          <Field label="Name" value={name} onChange={setName} placeholder="personal, recovery..." autoComplete="off" />
          <Field
            label="Public key"
            value={publicKey}
            onChange={setPublicKey}
            placeholder="age1..."
            autoComplete="off"
            helperText="Starts with age1. Never paste the private key (AGE-SECRET-KEY-...) here; it is refused."
          />
          {error && <Alert tone="danger">{error}</Alert>}
          <div className="flex justify-end">
            <Button onClick={submit} disabled={add.isPending || !name.trim() || !publicKey.trim()}>
              Add key
            </Button>
          </div>
        </div>

        <div className="space-y-2">
          <p className="text-sm font-medium">Create a key pair on your own machine</p>
          <CodeDisplay code={KEYGEN} language="bash" />
          <HelperText>
            Use two keys: your own and a recovery key kept elsewhere. A backup can be opened with any one of
            the keys it was encrypted to. To read a backup yourself, download it from Backups ("as stored")
            and run:
          </HelperText>
          <CodeDisplay code={DECRYPT} language="bash" />
        </div>
      </div>

      <ConfirmDialog
        open={pendingDelete !== null}
        title="Remove this key?"
        description={`Backups already encrypted to ${pendingDelete?.name ?? "this key"} stay readable with its private key. New backups will not use it.`}
        confirmLabel="Remove key"
        onConfirm={() => {
          if (!pendingDelete) return;
          remove.mutate(pendingDelete.id, {
            onError: (e) => toast({ title: "Could not remove the key", description: e.message, variant: "error" }),
            onSettled: () => setPendingDelete(null),
          });
        }}
        onCancel={() => setPendingDelete(null)}
      />
    </Card>
  );
}

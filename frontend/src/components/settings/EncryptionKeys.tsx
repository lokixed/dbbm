import { useRef, useState } from "react";
import { AccordionSection } from "xedonium/Accordion";
import Alert from "xedonium/Alert";
import Button from "xedonium/Button";
import CodeDisplay from "xedonium/CodeDisplay";
import Field from "xedonium/Field";
import HelperText from "xedonium/HelperText";
import Loader from "xedonium/Loader";
import DownloadIcon from "xedonium/icons/Download";
import KeyIcon from "xedonium/icons/Key";
import DataGrid, { type DataGridColumn } from "xedonium/DataGrid";
import { useAddRecipient, useDeleteRecipient, useRecipients, type Recipient } from "@/api/recipients";
import { RowMenu } from "@/components/RowMenu";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { useToast } from "@/components/toast";
import { canGenerateInBrowser, generateAgeKeyPair } from "@/lib/ageKeygen";

const KEYGEN = `age-keygen -o backup-key.txt
# Prints the public key (age1...). Paste that here.
# Keep backup-key.txt, the private key, somewhere safe and OFF this server.`;

const DECRYPT = `age --decrypt -i backup-key.txt -o backup.sql.gz backup.sql.gz.age`;

/** backup-key.txt, or backup-key-<name>.txt so a second (recovery) key does not overwrite the first. */
function keyFileName(name: string): string {
  const slug = name.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
  return slug ? `backup-key-${slug}.txt` : "backup-key.txt";
}

/** Hand a text file to the browser's download, without it ever leaving the page. */
function saveTextFile(fileName: string, text: string): void {
  const url = URL.createObjectURL(new Blob([text], { type: "text/plain" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = fileName;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

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
  // A key pair made in this browser. The private key stays in page memory (for "Download again")
  // until its public key is added or the page is left; it is never sent to the server.
  const identity = useRef<string | null>(null);
  const [generatedFile, setGeneratedFile] = useState<string | null>(null);
  const [generating, setGenerating] = useState(false);
  const browserKeygen = canGenerateInBrowser();

  const generate = async () => {
    setError(null);
    setGenerating(true);
    try {
      const pair = await generateAgeKeyPair();
      const fileName = keyFileName(name);
      identity.current = pair.identityFile;
      setPublicKey(pair.publicKey);
      setGeneratedFile(fileName);
      saveTextFile(fileName, pair.identityFile);
    } catch {
      setError("This browser cannot create age keys. Use the age commands below instead.");
    } finally {
      setGenerating(false);
    }
  };

  const forgetGenerated = () => {
    identity.current = null;
    setGeneratedFile(null);
  };

  const submit = () => {
    setError(null);
    add.mutate(
      { name: name.trim(), public_key: publicKey.trim() },
      {
        onSuccess: (key) => {
          toast({ title: "Key added", description: `${key.name} (${key.fingerprint})`, variant: "success" });
          setName("");
          setPublicKey("");
          forgetGenerated();
        },
        onError: (e) => setError(e.message),
      },
    );
  };

  const columns: DataGridColumn<Recipient>[] = [
    { key: "name", sortable: true, accessor: (r) => r.name, header: "Name", render: (r) => <span className="font-medium">{r.name}</span> },
    { key: "fingerprint", sortable: true, accessor: (r) => r.fingerprint, header: "Fingerprint", render: (r) => <code className="text-xs">{r.fingerprint}</code> },
    {
      key: "key",
      header: "Public key",
      render: (r) => <code className="text-xs">{r.public_key.slice(0, 16)}…{r.public_key.slice(-6)}</code>,
    },
    { key: "used", sortable: true, accessor: (r) => r.used_by.length, header: "Used by", render: (r) => (r.used_by.length ? r.used_by.join(", ") : "No job") },
    {
      key: "actions",
      header: <span className="sr-only">Actions</span>,
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
    // Collapsed by default, like Change password: it is set up once and rarely changed.
    <AccordionSection title="Backup encryption keys" headingLevel={2} className="bg-app-card">
      <div className="space-y-4">
        <HelperText>Jobs can encrypt every backup with age before it is stored or uploaded.</HelperText>
        <Alert tone="warning" title="Losing the private key means losing the backups">
          Encrypted backups cannot be opened without the private key, and this server never has it. If you
          lose it, nobody can recover those backups, not even the administrator. Save the private key (and a
          recovery key on a second key) somewhere safe before you turn encryption on.
        </Alert>

        {recipients.isPending && <Loader variant="dots" label="Loading keys" />}
        {recipients.isError && <Alert tone="danger">Could not load keys: {recipients.error.message}</Alert>}
        {recipients.isSuccess && (
          <DataGrid
            hideFooterWhenSinglePage
            caption="Encryption keys"
            columns={columns}
            rows={recipients.data}
            density="compact"
            empty="No keys yet. Add a public key to be able to encrypt backups."
          />
        )}

        <div className="space-y-3 border border-app-border p-3">
          <p className="text-sm font-medium">Add a public key</p>
          <Field label="Name" value={name} onChange={setName} placeholder="personal, recovery..." autoComplete="off" />
          <div className="flex flex-wrap items-center gap-3">
            <Button variant="secondary" onClick={() => void generate()} disabled={!browserKeygen || generating}>
              <KeyIcon className="h-4 w-4" /> {generating ? "Generating..." : "Generate a key pair"}
            </Button>
            <HelperText>
              {browserKeygen
                ? "Made in this browser: the private key downloads as a file and is never sent to the server."
                : "Needs HTTPS (or localhost). Use the age commands below instead."}
            </HelperText>
          </div>
          {generatedFile && (
            <Alert tone="success" title={`Downloaded ${generatedFile}`}>
              <p>
                This file is the private key. Move it, and a copy, somewhere safe and off this server: without
                it the backups encrypted to this key cannot be opened. Its public key is filled in below; click
                Add key to use it.
              </p>
              <Button
                variant="flat"
                className="mt-2"
                onClick={() => identity.current && saveTextFile(generatedFile, identity.current)}
              >
                <DownloadIcon className="h-4 w-4" /> Download again
              </Button>
            </Alert>
          )}
          <Field
            label="Public key"
            value={publicKey}
            onChange={(value) => {
              // A hand-edited key no longer belongs to the downloaded file.
              if (generatedFile) forgetGenerated();
              setPublicKey(value);
            }}
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
          <p className="text-sm font-medium">Or create a key pair on your own machine</p>
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
    </AccordionSection>
  );
}

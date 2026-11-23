import { useState } from "react";
import { useNavigate } from "react-router-dom";
import Alert from "xedonium/Alert";
import Button from "xedonium/Button";
import Checkbox from "xedonium/Checkbox";
import Field from "xedonium/Field";
import HelperText from "xedonium/HelperText";
import RadioGroup from "xedonium/RadioGroup";
import Select from "xedonium/Select";
import TextArea from "xedonium/TextArea";
import Stepper from "xedonium/Stepper";
import RelativeTime from "xedonium/RelativeTime";
import { DialogFooter, DialogFrame } from "@/components/DialogFrame";
import { useBackupCopies, useRestoreBackup, type Backup } from "@/api/backups";
import { useSources } from "@/api/sources";
import type { SourceType } from "@/api/types";
import { useToast } from "@/components/toast";
import { formatBytes } from "@/lib/format";

const FULL_INSTANCE = "full-instance";
const STEPS = ["Backup copy", "Target", "Options", "Confirm"] as const;

// Engines whose restore is implemented; the server refuses the others too.
const SUPPORTED: SourceType[] = ["mongo", "mysql", "postgres", "firestore"];
const NAME_PATTERN = /^[A-Za-z0-9_][A-Za-z0-9_-]{0,62}$/;

/** A Firestore import goes into the target source's own database; "(default)" is shown as "default". */
function firestoreDatabase(config: Record<string, unknown>): string {
  return String(config.database_id ?? "(default)").replace(/^\(|\)$/g, "");
}

const DROP_TEXT: Record<string, string> = {
  mongo:
    "Drops each collection in the target before restoring it. Without this, documents that already exist are skipped and the rest are added.",
  mysql:
    "Drops the whole target database and creates it again. Without this, tables from the backup replace tables with the same name and other tables are kept.",
  postgres:
    "Drops the objects in the backup (tables, views, ...) before recreating them. Objects that are not in the backup are kept, and can stop their schema from being dropped. Without this, objects that already exist cause errors.",
};

interface Props {
  backup: Backup | null;
  onClose: () => void;
}

export function RestoreWizard({ backup, onClose }: Props) {
  return (
    <DialogFrame open={backup !== null} onClose={onClose} title="Restore a backup" maxWidth="max-w-xl">
      <div className="px-6 pb-6 pt-4">
      {backup && <WizardBody key={backup.id} backup={backup} onClose={onClose} />}
      </div>
    </DialogFrame>
  );
}

const ONLY_PATTERN = /^[A-Za-z0-9_][A-Za-z0-9_.-]{0,126}$/;

function WizardBody({ backup, onClose }: { backup: Backup; onClose: () => void }) {
  const navigate = useNavigate();
  const { toast } = useToast();
  const sources = useSources();
  const copies = useBackupCopies(backup.run_id, backup.source_database, true);
  const restore = useRestoreBackup();
  const type = backup.source_type as SourceType | null;
  const full = backup.source_database === FULL_INSTANCE;

  const [step, setStep] = useState(0);
  const [copyId, setCopyId] = useState(backup.id);
  const [targetId, setTargetId] = useState("");
  const [typedName, setDatabase] = useState(full ? FULL_INSTANCE : `${backup.source_database}_restore`);
  const [drop, setDrop] = useState(false);
  const [onlyText, setOnlyText] = useState("");
  const [typed, setTyped] = useState("");
  const [overwrite, setOverwrite] = useState(false);
  // The age private key lives only in this component's state: it is sent with the restore request
  // and is gone when the wizard closes. It is never put in storage, a URL or the query cache.
  const [privateKey, setPrivateKey] = useState("");
  const needsKey = backup.encrypted;

  const candidates = (sources.data ?? []).filter((s) => s.type === type);
  const target = candidates.find((s) => s.id === targetId);
  const isFirestore = type === "firestore";
  const database = isFirestore && target ? firestoreDatabase(target.config) : typedName;
  const overwritesOriginal =
    target !== undefined && target.id === backup.source_id && (full || database === backup.source_database);
  const nameValid = NAME_PATTERN.test(database);
  const canSelect = (type === "mongo" || type === "postgres") && !full;
  const only = canSelect
    ? onlyText
        .split(/[,\s]+/)
        .map((n) => n.trim())
        .filter((n) => n !== "")
    : [];
  const onlyValid = only.every((n) => ONLY_PATTERN.test(n));
  const usable = (copies.data ?? [backup]).filter((c) => !c.missing);
  const rolesFile = type === "postgres" && backup.source_database === "globals";
  const supported = type !== null && SUPPORTED.includes(type) && !rolesFile;

  const canNext =
    [
      usable.some((c) => c.id === copyId),
      target !== undefined && nameValid,
      !needsKey || (privateKey.trim() !== "" && onlyValid),
    ][step] ?? false;
  const canRun = typed === database && (!overwritesOriginal || overwrite) && !restore.isPending;

  const submit = () =>
    restore.mutate(
      {
        id: copyId,
        input: {
          target_source_id: targetId,
          target_database: database,
          drop: isFirestore ? false : drop,
          ...(only.length > 0 ? { only } : {}),
          confirm_database: typed,
          confirm_overwrite_original: overwrite,
          ...(needsKey ? { private_key: privateKey } : {}),
        },
      },
      {
        onSuccess: (run) => {
          toast({
            title: "Restore started",
            description: `${backup.source_database} into ${database}`,
            variant: "success",
          });
          onClose();
          navigate(`/runs/${run.id}`);
        },
        onError: (error) =>
          toast({ title: "Could not start the restore", description: error.message, variant: "error" }),
      },
    );

  if (!supported) {
    return (
      <div className="space-y-4 text-sm">
        <p>
          {rolesFile
            ? "The roles file cannot be restored here. Download it and apply it with psql."
            : `Restoring ${type ?? "this kind of"} backups is not available yet. Download the file and restore it with the database's own tools.`}
        </p>
        <div className="flex justify-end">
          <Button variant="secondary" onClick={onClose}>
            Close
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-5">
      <Stepper steps={STEPS.map((label) => ({ label }))} current={step} />

      {step === 0 && (
        <div className="space-y-2">
          <RadioGroup
            label="Which stored copy should be used?"
            name="copy"
            value={copyId}
            onChange={setCopyId}
            options={usable.map((copy) => ({
              value: copy.id,
              label: (
                <span>
                  <span className="font-medium">{copy.destination_name}</span> ({copy.destination_type})
                  <br />
                  <span className="text-app-muted">
                    {copy.source_database}, {formatBytes(copy.size_bytes)}, <RelativeTime date={copy.created_at} />
                  </span>
                </span>
              ),
            }))}
          />
          <HelperText>The copy's checksum is verified before anything is written to the target.</HelperText>
        </div>
      )}

      {step === 1 && (
        <div className="space-y-4">
          <Select
            label="Target source"
            value={targetId}
            onChange={setTargetId}
            placeholder={`Choose a ${type} source`}
            helperText="The source's credentials are used, so they need write access. A read-only backup user cannot restore."
            options={candidates.map((s) => ({
              value: s.id,
              label: `${s.name}${s.id === backup.source_id ? " (original source)" : ""}`,
            }))}
          />
          <Field
            label="Target database name"
            value={database}
            disabled={full || isFirestore}
            onChange={setDatabase}
            error={nameValid ? undefined : 'Use letters, digits, "_" or "-" (up to 63 characters).'}
            helperText={
              isFirestore
                ? "A Firestore import goes into the database of the target source and cannot be renamed."
                : `${
                    type === "postgres"
                      ? "Owners and privileges are not restored; objects belong to the connecting user. "
                      : ""
                  }${
                    full
                      ? "A full-instance backup restores the whole server."
                      : "Defaults to a new name so the original is not touched."
                  }`
            }
          />
        </div>
      )}

      {step === 2 && (
        <div className="space-y-3 text-sm">
          {needsKey && (
            <div className="space-y-2">
              <Alert tone="info">
                This backup is encrypted with age (keys {backup.recipients.join(", ")}). Provide a private
                key that matches one of them. It is used only for this restore, held in memory and never
                stored.
              </Alert>
              <TextArea
                label="Private key"
                value={privateKey}
                onChange={setPrivateKey}
                placeholder="AGE-SECRET-KEY-1..."
                autoComplete="off"
                spellCheck={false}
                rows={3}
                helperText="Paste the key or the whole key file; lines starting with # are ignored."
              />
              <label className="block text-xs text-app-muted">
                Or load a key file{" "}
                <input
                  type="file"
                  accept=".txt,text/plain"
                  onChange={(event) => {
                    const file = event.target.files?.[0];
                    if (file) void file.text().then(setPrivateKey);
                    event.target.value = "";
                  }}
                />
              </label>
            </div>
          )}
          {isFirestore ? (
            <Alert tone="warning">
              Importing overwrites documents that have the same ID as documents in the backup, including
              changes made since the backup. Documents that are not in the backup are not deleted.
            </Alert>
          ) : (
            <Checkbox
              label="Replace existing data"
              description={DROP_TEXT[type ?? ""] ?? ""}
              checked={drop}
              onChange={setDrop}
            />
          )}
          {canSelect && (
            <Field
              label={type === "mongo" ? "Only these collections (optional)" : "Only these tables (optional)"}
              value={onlyText}
              onChange={setOnlyText}
              placeholder="Leave empty to restore everything"
              error={onlyValid ? undefined : 'Use names made of letters, digits, "_", "-" or "."; separate them with commas.'}
              helperText="Separate names with commas or spaces. Everything else in the backup is skipped."
            />
          )}
          {full && (
            <Alert tone="warning">
              This restores every database in the backup, replaying the oplog to a consistent point.
            </Alert>
          )}
        </div>
      )}

      {step === 3 && target && (
        <div className="space-y-4 text-sm">
          <dl className="grid grid-cols-[8rem_1fr] gap-y-1">
            <dt className="text-app-muted">Backup</dt>
            <dd>
              {backup.source_database} ({backup.destination_name})
            </dd>
            <dt className="text-app-muted">Target</dt>
            <dd>
              {target.name} / {database}
            </dd>
            {only.length > 0 && (
              <>
                <dt className="text-app-muted">Only</dt>
                <dd>{only.join(", ")}</dd>
              </>
            )}
            <dt className="text-app-muted">Replace existing</dt>
            <dd>
              {isFirestore
                ? "Documents with the same ID are overwritten"
                : drop
                  ? "Yes, existing data is replaced"
                  : "No"}
            </dd>
          </dl>
          {overwritesOriginal && (
            <Alert tone="danger">
              <Checkbox
                label="This restores into the original database. Its current data may be overwritten."
                checked={overwrite}
                onChange={setOverwrite}
              />
            </Alert>
          )}
          <Field
            label={`Type ${database} to confirm`}
            value={typed}
            autoComplete="off"
            onChange={setTyped}
          />
        </div>
      )}

      <DialogFooter>
        <Button
          className="mr-auto"
          variant="secondary"
          onClick={step === 0 ? onClose : () => setStep(step - 1)}
          disabled={restore.isPending}
        >
          {step === 0 ? "Cancel" : "Back"}
        </Button>
        {step < STEPS.length - 1 ? (
          <Button onClick={() => setStep(step + 1)} disabled={!canNext}>
            Next
          </Button>
        ) : (
          <Button variant="danger" onClick={submit} disabled={!canRun}>
            {restore.isPending ? "Starting..." : "Restore"}
          </Button>
        )}
      </DialogFooter>
    </div>
  );
}

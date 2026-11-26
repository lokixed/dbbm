import type { UseFormReturn } from "react-hook-form";
import { AccordionSection } from "xedonium/Accordion";
import Alert from "xedonium/Alert";
import Button from "xedonium/Button";
import Checkbox from "xedonium/Checkbox";
import CodeDisplay from "xedonium/CodeDisplay";
import Field from "xedonium/Field";
import HelperText from "xedonium/HelperText";
import Select from "xedonium/Select";
import type { DriveScope } from "@/api/types";
import { GdriveClientSetup } from "@/components/destinations/GdriveClientSetup";
import { SecretField } from "@/components/SecretField";
import {
  authorizeCommand,
  CHUNK_SIZES_MB,
  type DestinationFormValues,
} from "@/lib/destinationForm";
import { bindBool, bindText } from "@/lib/formBinding";
import type { ConnectState } from "@/lib/gdriveConnect";
import type { SecretState } from "@/lib/secrets";
import { FieldAlign } from "@/components/FieldAlign";

const SCOPE_HELP: Record<DriveScope, string> = {
  "drive.file":
    "Recommended. rclone can only see files and folders it created itself, so a folder you made by hand in Drive is invisible to it (it creates its own).",
  drive: "Full access to every file in your Drive. Use only if you need rclone to see existing folders.",
};

export interface GdriveConnectProps {
  state: ConnectState;
  /** The token from a finished Connect is the one that will be saved. */
  connected: boolean;
  /** Why Connect is not possible yet, or null when it is. */
  blockedReason: string | null;
  redirectUrl: string | null;
  onConnect: () => void;
}

interface GdriveFieldsProps {
  form: UseFormReturn<DestinationFormValues>;
  scope: DriveScope;
  clientId: string;
  secretsSet: Record<string, boolean>;
  token: SecretState;
  clientSecret: SecretState;
  onToken: (state: SecretState) => void;
  onClientSecret: (state: SecretState) => void;
  secretErrors: Record<string, string>;
  focusToken: boolean;
  connect: GdriveConnectProps;
}

function AuthorizeCommand({
  scope,
  clientId,
  clientSecret,
}: {
  scope: DriveScope;
  clientId: string;
  clientSecret: SecretState;
}) {
  const typedSecret = clientSecret.action === "set" ? clientSecret.value : "";
  const command = authorizeCommand(scope, clientId, typedSecret);
  if (!command) {
    return (
      <HelperText>
        Type the client secret above (saved secrets are never shown again) to generate the command.
      </HelperText>
    );
  }
  return <CodeDisplay code={command} language="bash" copyable wrap highlight={false} />;
}

function ConnectStatus({ state, connected }: { state: ConnectState; connected: boolean }) {
  if (connected) {
    return <Alert tone="info">Google Drive connected. Save the destination to keep this connection.</Alert>;
  }
  if (state.phase === "waiting" || state.phase === "starting") {
    return <HelperText>Finish signing in to Google in the new tab. This form waits for it.</HelperText>;
  }
  if (state.phase === "failed") return <Alert tone="danger">{state.message}</Alert>;
  return null;
}

function ConnectSection({ connect, hasToken }: { connect: GdriveConnectProps; hasToken: boolean }) {
  const { state, connected, blockedReason, onConnect } = connect;
  const busy = state.phase === "starting" || state.phase === "waiting";
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <Button type="button" onClick={onConnect} disabled={busy || blockedReason !== null}>
          {busy ? "Waiting for Google..." : connected || hasToken ? "Reconnect Google Drive" : "Connect Google Drive"}
        </Button>
        {blockedReason && !busy && <HelperText>{blockedReason}</HelperText>}
      </div>
      <ConnectStatus state={state} connected={connected} />
    </div>
  );
}

function PasteTokenSteps({
  scope,
  clientId,
  clientSecret,
  open,
}: Pick<GdriveFieldsProps, "scope" | "clientId" | "clientSecret"> & { open: boolean }) {
  return (
    <AccordionSection title="Or paste a token from rclone" defaultOpen={open}>
      <ol className="list-decimal space-y-2 pl-5 text-sm text-app-muted">
        <li>
          Use this without an OAuth client of your own (rclone&apos;s shared default client: slower, rate
          limited), or with a client of type <strong>Desktop app</strong>.
        </li>
        <li>
          On a computer with a browser and rclone installed, run this command (it matches the scope and
          client above; it contains your client secret, so run it only on your own machine):
          <AuthorizeCommand scope={scope} clientId={clientId} clientSecret={clientSecret} />
        </li>
        <li>
          Sign in, then paste everything rclone prints between <code>---&gt;</code> and{" "}
          <code>&lt;---End paste</code> into the token field.
        </li>
      </ol>
    </AccordionSection>
  );
}

export function GdriveFields({
  form,
  scope,
  clientId,
  secretsSet,
  token,
  clientSecret,
  onToken,
  onClientSecret,
  secretErrors,
  focusToken,
  connect,
}: GdriveFieldsProps) {
  const errors = form.formState.errors;
  const hasClient = clientId.trim() !== "";
  const hasToken = secretsSet.token ?? false;
  return (
    <>
      <Field
        label="Folder in Google Drive"
        helperText="Created if missing, e.g. db-backups or backups/prod."
        autoComplete="off"
        {...bindText(form, "remoteFolder")}
        error={errors.remoteFolder?.message}
      />

      <Select
        label="Access scope"
        helperText={SCOPE_HELP[scope]}
        options={[
          { value: "drive.file", label: "drive.file (only files rclone creates)" },
          { value: "drive", label: "drive (full Drive access)" },
        ]}
        {...bindText(form, "scope")}
      />

      <Field
        label="Shared drive ID (optional)"
        helperText="Leave empty to use My Drive."
        autoComplete="off"
        {...bindText(form, "sharedDriveId")}
        error={errors.sharedDriveId?.message}
      />

      <div className="grid gap-3 sm:grid-cols-2">
        <Field
          label="OAuth client ID"
          helperText="Needed for Connect. Optional when pasting a token."
          autoComplete="off"
          {...bindText(form, "clientId")}
          error={errors.clientId?.message}
        />
        <SecretField
          id="secret-client_secret"
          label="OAuth client secret"
          isSet={secretsSet.client_secret ?? false}
          state={clientSecret}
          onChange={onClientSecret}
          error={secretErrors.client_secret}
        />
      </div>

      <GdriveClientSetup redirectUrl={connect.redirectUrl} open={!hasClient && !hasToken} />

      <ConnectSection connect={connect} hasToken={hasToken} />

      {!connect.connected && (
        <>
          <PasteTokenSteps
            scope={scope}
            clientId={clientId}
            clientSecret={clientSecret}
            open={focusToken && !hasClient}
          />
          <SecretField
            id="secret-token"
            label="Token"
            isSet={hasToken}
            state={token}
            onChange={onToken}
            multiline
            allowFile
            autoFocus={focusToken && !hasClient}
            placeholder='{"access_token":"...","token_type":"Bearer","refresh_token":"...","expiry":"..."}'
            error={secretErrors.token}
          />
        </>
      )}

      <div className="grid gap-3 sm:grid-cols-2">
        <Select
          label="Upload chunk size"
          helperText="Memory used per upload."
          options={CHUNK_SIZES_MB.map((size) => ({ value: String(size), label: `${size} MiB` }))}
          {...bindText(form, "chunkSizeMb")}
        />
        <FieldAlign align="start">
          <Checkbox
            label="Delete permanently"
            description="Off: deleted backups go to the Drive trash (and still count against your quota until it is emptied)."
            {...bindBool(form, "deletePermanently")}
          />
        </FieldAlign>
      </div>
    </>
  );
}

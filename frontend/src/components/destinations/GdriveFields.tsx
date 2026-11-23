import type { UseFormReturn } from "react-hook-form";
import { AccordionSection } from "xedonium/Accordion";
import Checkbox from "xedonium/Checkbox";
import CodeDisplay from "xedonium/CodeDisplay";
import Field from "xedonium/Field";
import HelperText from "xedonium/HelperText";
import Select from "xedonium/Select";
import type { DriveScope } from "@/api/types";
import { SecretField } from "@/components/SecretField";
import {
  authorizeCommand,
  CHUNK_SIZES_MB,
  type DestinationFormValues,
} from "@/lib/destinationForm";
import { bindBool, bindText } from "@/lib/formBinding";
import type { SecretState } from "@/lib/secrets";

const SCOPE_HELP: Record<DriveScope, string> = {
  "drive.file":
    "Recommended. rclone can only see files and folders it created itself, so a folder you made by hand in Drive is invisible to it (it creates its own).",
  drive: "Full access to every file in your Drive. Use only if you need rclone to see existing folders.",
};

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

function SetupSteps({ scope, clientId, clientSecret }: Pick<GdriveFieldsProps, "scope" | "clientId" | "clientSecret">) {
  return (
    <AccordionSection title="How to get a token" defaultOpen>
      <ol className="list-decimal space-y-2 pl-5 text-sm text-app-muted">
        <li>
          Optional but recommended: in Google Cloud Console create an OAuth client of type{" "}
          <strong>Desktop app</strong>, enable the Google Drive API, and enter its client ID and secret
          above. Without one, rclone&apos;s shared default client is used (slower, rate limited).
        </li>
        <li>
          On the OAuth consent screen, <strong>publish the app (&quot;In production&quot;)</strong>. Apps left
          in &quot;Testing&quot; get refresh tokens that expire after 7 days. An unverified app is fine for
          personal use.
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
}: GdriveFieldsProps) {
  const errors = form.formState.errors;
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
          label="OAuth client ID (optional)"
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

      <SetupSteps scope={scope} clientId={clientId} clientSecret={clientSecret} />

      <SecretField
        id="secret-token"
        label="Token"
        isSet={secretsSet.token ?? false}
        state={token}
        onChange={onToken}
        required
        multiline
        allowFile
        autoFocus={focusToken}
        placeholder='{"access_token":"...","token_type":"Bearer","refresh_token":"...","expiry":"..."}'
        error={secretErrors.token}
      />

      <div className="grid gap-3 sm:grid-cols-2">
        <Select
          label="Upload chunk size"
          helperText="Memory used per upload."
          options={CHUNK_SIZES_MB.map((size) => ({ value: String(size), label: `${size} MiB` }))}
          {...bindText(form, "chunkSizeMb")}
        />
        <div className="pt-6">
          <Checkbox
            label="Delete permanently"
            description="Off: deleted backups go to the Drive trash (and still count against your quota until it is emptied)."
            {...bindBool(form, "deletePermanently")}
          />
        </div>
      </div>
    </>
  );
}

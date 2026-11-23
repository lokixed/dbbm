import { Copy } from "lucide-react";
import type { FieldErrors, UseFormRegister } from "react-hook-form";
import type { DriveScope } from "@/api/types";
import { FormField } from "@/components/FormField";
import { SecretField } from "@/components/SecretField";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { useToast } from "@/components/ui/toast";
import {
  authorizeCommand,
  CHUNK_SIZES_MB,
  type DestinationFormValues,
} from "@/lib/destinationForm";
import type { SecretState } from "@/lib/secrets";

const SCOPE_HELP: Record<DriveScope, string> = {
  "drive.file":
    "Recommended. rclone can only see files and folders it created itself, so a folder you made by hand in Drive is invisible to it (it creates its own).",
  drive: "Full access to every file in your Drive. Use only if you need rclone to see existing folders.",
};

interface GdriveFieldsProps {
  register: UseFormRegister<DestinationFormValues>;
  errors: FieldErrors<DestinationFormValues>;
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
  const { toast } = useToast();
  const typedSecret = clientSecret.action === "set" ? clientSecret.value : "";
  const command = authorizeCommand(scope, clientId, typedSecret);
  if (!command) {
    return (
      <p className="text-xs text-muted-foreground">
        Type the client secret above (saved secrets are never shown again) to generate the command.
      </p>
    );
  }
  const copy = () =>
    void navigator.clipboard
      .writeText(command)
      .then(() => toast({ title: "Command copied", variant: "success" }))
      .catch(() => toast({ title: "Could not copy; select the text instead", variant: "error" }));
  return (
    <div className="flex items-start gap-2">
      <code className="block flex-1 break-all rounded-md bg-muted p-2 font-mono text-xs">{command}</code>
      <Button type="button" size="sm" variant="secondary" onClick={copy} aria-label="Copy command">
        <Copy className="h-4 w-4" />
      </Button>
    </div>
  );
}

function SetupSteps({ scope, clientId, clientSecret }: Pick<GdriveFieldsProps, "scope" | "clientId" | "clientSecret">) {
  return (
    <details className="rounded-md border p-3 text-sm" open>
      <summary className="cursor-pointer font-medium">How to get a token</summary>
      <ol className="mt-2 list-decimal space-y-2 pl-5 text-muted-foreground">
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
    </details>
  );
}

export function GdriveFields({
  register,
  errors,
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
  return (
    <>
      <FormField
        id="remoteFolder"
        label="Folder in Google Drive"
        hint="Created if missing, e.g. db-backups or backups/prod."
        error={errors.remoteFolder?.message}
      >
        <Input id="remoteFolder" {...register("remoteFolder")} autoComplete="off" />
      </FormField>

      <FormField id="scope" label="Access scope" hint={SCOPE_HELP[scope]}>
        <Select id="scope" {...register("scope")}>
          <option value="drive.file">drive.file (only files rclone creates)</option>
          <option value="drive">drive (full Drive access)</option>
        </Select>
      </FormField>

      <FormField
        id="sharedDriveId"
        label="Shared drive ID (optional)"
        hint="Leave empty to use My Drive."
        error={errors.sharedDriveId?.message}
      >
        <Input id="sharedDriveId" {...register("sharedDriveId")} autoComplete="off" />
      </FormField>

      <div className="grid gap-3 sm:grid-cols-2">
        <FormField id="clientId" label="OAuth client ID (optional)" error={errors.clientId?.message}>
          <Input id="clientId" {...register("clientId")} autoComplete="off" />
        </FormField>
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
        <FormField id="chunkSizeMb" label="Upload chunk size" hint="Memory used per upload.">
          <Select id="chunkSizeMb" {...register("chunkSizeMb")}>
            {CHUNK_SIZES_MB.map((size) => (
              <option key={size} value={String(size)}>
                {size} MiB
              </option>
            ))}
          </Select>
        </FormField>
        <label className="flex items-start gap-2 pt-6 text-sm">
          <input type="checkbox" className="mt-1" {...register("deletePermanently")} />
          <span>
            Delete permanently
            <span className="block text-xs text-muted-foreground">
              Off: deleted backups go to the Drive trash (and still count against your quota until it is
              emptied).
            </span>
          </span>
        </label>
      </div>
    </>
  );
}

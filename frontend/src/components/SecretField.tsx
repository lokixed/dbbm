import { useState } from "react";
import Badge from "xedonium/Badge";
import Button from "xedonium/Button";
import FileUpload from "xedonium/FileUpload";
import Input from "xedonium/Input";
import TextArea from "xedonium/TextArea";
import { FormField } from "@/components/FormField";
import type { SecretState } from "@/lib/secrets";

interface SecretFieldProps {
  id: string;
  label: string;
  isSet: boolean;
  state: SecretState;
  onChange: (state: SecretState) => void;
  required?: boolean;
  multiline?: boolean;
  allowFile?: boolean;
  placeholder?: string;
  error?: string;
  autoFocus?: boolean;
}

/** Write-only: a stored secret is never shown, only replaced or cleared. */
export function SecretField({
  id,
  label,
  isSet,
  state,
  onChange,
  required = false,
  multiline = false,
  allowFile = false,
  placeholder,
  error,
  autoFocus = false,
}: SecretFieldProps) {
  const [fileName, setFileName] = useState<string | null>(null);

  if (isSet && state.action === "keep") {
    return (
      <FormField id={id} label={label}>
        <div className="flex items-center gap-2">
          <Badge badgeContent="Saved" color="success" />
          <Button type="button" variant="secondary" onClick={() => onChange({ action: "set", value: "" })}>
            Change
          </Button>
          {!required && (
            <Button type="button" variant="flat" onClick={() => onChange({ action: "clear" })}>
              Clear
            </Button>
          )}
        </div>
      </FormField>
    );
  }

  if (state.action === "clear") {
    return (
      <FormField id={id} label={label}>
        <div className="flex items-center gap-2">
          <Badge badgeContent="Will be cleared when you save" color="danger" />
          <Button type="button" variant="flat" onClick={() => onChange({ action: "keep" })}>
            Undo
          </Button>
        </div>
      </FormField>
    );
  }

  const value = state.action === "set" ? state.value : "";
  const update = (next: string) => onChange({ action: "set", value: next });

  const readFile = async (file: File | undefined) => {
    if (!file) return;
    update(await file.text());
    setFileName(file.name);
  };

  return (
    <FormField id={id} label={label} error={error}>
      {multiline ? (
        <TextArea
          id={id}
          value={value}
          onChange={update}
          placeholder={placeholder}
          spellCheck={false}
          autoComplete="off"
          autoFocus={autoFocus}
        />
      ) : (
        <Input
          id={id}
          type="password"
          value={value}
          onChange={update}
          placeholder={placeholder}
          autoComplete="new-password"
          autoFocus={autoFocus}
        />
      )}
      <div className="flex flex-wrap items-center gap-2">
        {allowFile && (
          // Full width, like the field above: shrunk to its text the drop zone was a narrow box.
          <div className="w-full">
            <FileUpload
              label="Upload .json file"
              accept=".json,application/json"
              clearOnSelect
              onChange={(files) => void readFile(files[0])}
            />
          </div>
        )}
        {fileName && <span className="text-xs text-app-muted">Loaded {fileName}</span>}
        {isSet && (
          <Button type="button" variant="flat" onClick={() => onChange({ action: "keep" })}>
            Keep saved value
          </Button>
        )}
      </div>
    </FormField>
  );
}

import { Upload } from "lucide-react";
import { useState } from "react";
import { FormField } from "@/components/FormField";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/select";
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
          <Badge variant="success">Saved</Badge>
          <Button
            type="button"
            size="sm"
            variant="secondary"
            onClick={() => onChange({ action: "set", value: "" })}
          >
            Change
          </Button>
          {!required && (
            <Button type="button" size="sm" variant="ghost" onClick={() => onChange({ action: "clear" })}>
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
          <Badge variant="danger">Will be cleared when you save</Badge>
          <Button type="button" size="sm" variant="ghost" onClick={() => onChange({ action: "keep" })}>
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
        <Textarea
          id={id}
          value={value}
          onChange={(event) => update(event.target.value)}
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
          onChange={(event) => update(event.target.value)}
          placeholder={placeholder}
          autoComplete="new-password"
          autoFocus={autoFocus}
        />
      )}
      <div className="flex flex-wrap items-center gap-2">
        {allowFile && (
          <label className="inline-flex cursor-pointer items-center gap-1.5 text-xs font-medium text-primary hover:underline">
            <Upload className="h-3.5 w-3.5" />
            Upload .json file
            <input
              type="file"
              accept=".json,application/json"
              className="sr-only"
              onChange={(event) => {
                void readFile(event.target.files?.[0]);
                event.target.value = "";
              }}
            />
          </label>
        )}
        {fileName && <span className="text-xs text-muted-foreground">Loaded {fileName}</span>}
        {isSet && (
          <Button type="button" size="sm" variant="ghost" onClick={() => onChange({ action: "keep" })}>
            Keep saved value
          </Button>
        )}
      </div>
    </FormField>
  );
}

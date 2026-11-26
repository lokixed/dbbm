import { useState } from "react";
import Button from "xedonium/Button";
import HelperText from "xedonium/HelperText";
import PasswordInput from "xedonium/PasswordInput";
import {
  CONNECTION_STRING_EXAMPLES,
  ConnectionStringError,
  parseConnectionString,
  type ParsedConnection,
  type SqlType,
} from "@/lib/connectionString";

interface ConnectionStringFillProps {
  type: SqlType;
  /** Called with what the string contained; the form fills its fields from it. */
  onParsed: (parsed: ParsedConnection) => void;
}

/**
 * Paste a MySQL / MariaDB or PostgreSQL connection URL and fill the form's fields from it. The string
 * itself is not stored: the password goes to the encrypted password field like a typed one.
 */
export function ConnectionStringFill({ type, onParsed }: ConnectionStringFillProps) {
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);

  const fill = () => {
    try {
      onParsed(parseConnectionString(type, value));
      setValue("");
      setError(null);
    } catch (exc) {
      setError(exc instanceof ConnectionStringError ? exc.message : "Could not read the connection string.");
    }
  };

  return (
    <div className="space-y-2">
      <PasswordInput
        label="Connection string"
        autoComplete="off"
        placeholder={CONNECTION_STRING_EXAMPLES[type]}
        value={value}
        onChange={(next) => {
          setValue(next);
          setError(null);
        }}
        onKeyDown={(event) => {
          if (event.key === "Enter") {
            event.preventDefault();
            fill();
          }
        }}
        error={error ?? undefined}
        helperText={
          type === "mysql"
            ? "mysql:// or mariadb:// URL. Understands ssl-mode and charset; a database in the path selects that database."
            : "postgres:// or postgresql:// URL. Understands sslmode and application_name; the database in the path becomes the connection database."
        }
      />
      <div className="flex flex-wrap items-center gap-3">
        <Button type="button" variant="secondary" onClick={fill} disabled={!value.trim()}>
          Fill in the fields
        </Button>
        <HelperText>
          The string is not saved. Its parts go into the fields below, so check them before you save.
        </HelperText>
      </div>
    </div>
  );
}

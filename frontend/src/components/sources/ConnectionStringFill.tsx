import Button from "xedonium/Button";
import HelperText from "xedonium/HelperText";
import PasswordInput from "xedonium/PasswordInput";
import { CONNECTION_STRING_EXAMPLES, type SqlType } from "@/lib/connectionString";

interface ConnectionStringFillProps {
  type: SqlType;
  value: string;
  error: string | null;
  onChange: (value: string) => void;
  /** Parse the string and fill the form's fields from it. */
  onFill: () => void;
}

/**
 * Paste a MySQL / MariaDB or PostgreSQL connection URL and fill the form's fields from it. The string
 * itself is not stored: the password goes to the encrypted password field like a typed one. The form
 * owns the value, so Test connection and Save can use a string that was pasted but not filled in yet.
 */
export function ConnectionStringFill({ type, value, error, onChange, onFill }: ConnectionStringFillProps) {
  return (
    <div className="space-y-2">
      <PasswordInput
        label="Connection string"
        autoComplete="off"
        placeholder={CONNECTION_STRING_EXAMPLES[type]}
        value={value}
        onChange={onChange}
        onKeyDown={(event) => {
          if (event.key === "Enter") {
            event.preventDefault();
            onFill();
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
        <Button type="button" variant="secondary" onClick={onFill} disabled={!value.trim()}>
          Fill in the fields
        </Button>
        <HelperText>
          The string is not saved. Its parts go into the fields below, so check them before you save.
        </HelperText>
      </div>
    </div>
  );
}

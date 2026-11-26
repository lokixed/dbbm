import { TLS_MODES, type SourceFormValues } from "@/lib/sourceForm";

export type SqlType = "mysql" | "postgres";

/** What a connection string fills in. The password goes to the (write-only) password field. */
export interface ParsedConnection {
  values: Partial<SourceFormValues>;
  password: string | null;
}

export const CONNECTION_STRING_EXAMPLES: Record<SqlType, string> = {
  mysql: "mysql://backup:password@db.example.com:3306/?ssl-mode=REQUIRED&charset=utf8mb4",
  postgres: "postgresql://backup:password@db.example.com:5432/postgres?sslmode=require",
};

const SCHEMES: Record<SqlType, string[]> = {
  mysql: ["mysql:", "mariadb:"],
  postgres: ["postgres:", "postgresql:"],
};

export class ConnectionStringError extends Error {}

function decode(part: string, what: string): string {
  try {
    return decodeURIComponent(part);
  } catch {
    throw new ConnectionStringError(`The ${what} is not valid percent-encoding.`);
  }
}

function tlsMode(type: SqlType, raw: string): string {
  // MySQL clients write REQUIRED / VERIFY_CA; the form uses required / verify_ca.
  const value = type === "mysql" ? raw.toLowerCase().replace(/-/g, "_") : raw.toLowerCase();
  if (!TLS_MODES[type].includes(value)) {
    throw new ConnectionStringError(`Unsupported TLS mode "${raw}". Use one of: ${TLS_MODES[type].join(", ")}.`);
  }
  return value;
}

/** Query parameters each type understands, mapped onto form values. */
function applyParam(type: SqlType, key: string, value: string, out: ParsedConnection): boolean {
  const name = key.toLowerCase();
  if (type === "postgres") {
    if (name === "sslmode") out.values.tlsMode = tlsMode(type, value);
    else if (name === "application_name") out.values.extraOption = value;
    else if (name === "user") out.values.username = value;
    else if (name === "password") out.password = value;
    else if (name === "host") out.values.host = value;
    else if (name === "port") out.values.port = value;
    else if (name === "dbname") out.values.connectionDatabase = value;
    else if (name === "connect_timeout") return true; // harmless, the app sets its own timeouts
    else return false;
    return true;
  }
  if (["ssl-mode", "sslmode", "ssl_mode"].includes(name)) out.values.tlsMode = tlsMode(type, value);
  else if (name === "charset") out.values.extraOption = value;
  else if (name === "ssl" || name === "usessl") {
    out.values.tlsMode = ["true", "1"].includes(value.toLowerCase()) ? "required" : "disabled";
  } else return false;
  return true;
}

/**
 * Parse a MySQL / MariaDB or PostgreSQL connection URL into form values, e.g.
 * mysql://user:pass@host:3306/db?ssl-mode=REQUIRED or postgresql://user:pass@host/db?sslmode=require.
 * A leading "jdbc:" is accepted. Unknown parameters are refused so nothing is silently dropped.
 */
export function parseConnectionString(type: SqlType, raw: string): ParsedConnection {
  const text = raw.trim().replace(/^jdbc:/i, "");
  if (!text) throw new ConnectionStringError("Paste a connection string first.");
  if (!text.includes("://")) {
    throw new ConnectionStringError(
      `Use the URL form, for example ${CONNECTION_STRING_EXAMPLES[type].replace("password", "…")}`,
    );
  }
  let url: URL;
  try {
    url = new URL(text);
  } catch {
    throw new ConnectionStringError("This is not a valid connection URL.");
  }
  if (!SCHEMES[type].includes(url.protocol)) {
    throw new ConnectionStringError(`The URL must start with ${SCHEMES[type].map((s) => `${s}//`).join(" or ")}.`);
  }
  if (url.hostname.includes(",") || url.host.includes(",")) {
    throw new ConnectionStringError("Several hosts are not supported. Use one host.");
  }

  const out: ParsedConnection = { values: {}, password: null };
  const host = url.hostname.replace(/^\[(.*)\]$/, "$1");
  if (host) out.values.host = host;
  if (url.port) out.values.port = url.port;
  if (url.username) out.values.username = decode(url.username, "user name");
  if (url.password) out.password = decode(url.password, "password");

  const database = decode(url.pathname.replace(/^\/+/, ""), "database name");
  if (database.includes("/")) throw new ConnectionStringError("The database name must not contain '/'.");
  if (database) {
    if (type === "postgres") out.values.connectionDatabase = database;
    else {
      out.values.allDatabases = false;
      out.values.databases = [database];
    }
  }

  const unknown: string[] = [];
  url.searchParams.forEach((value, key) => {
    if (!applyParam(type, key, value, out)) unknown.push(key);
  });
  if (unknown.length > 0) {
    throw new ConnectionStringError(`Unsupported parameter(s): ${unknown.join(", ")}. Remove them and try again.`);
  }
  if (!out.values.host) throw new ConnectionStringError("The connection string has no host.");
  return out;
}

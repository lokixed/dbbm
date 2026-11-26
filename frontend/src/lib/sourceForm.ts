import { z } from "zod";
import type { SourceInput, SourceRecord, SourceType } from "@/api/types";
import { buildSecrets, type SecretState } from "@/lib/secrets";
import {
  checkStagingBucket,
  databaseIdField,
  dbNameField,
  hostField,
  isValidCollectionId,
  nameField,
  portField,
  projectIdField,
  usernameField,
} from "@/lib/validation";

export type MongoMode = "fields" | "uri";
export type MongoDumpMode = "per_database" | "full_oplog";

export interface SourceFormValues {
  name: string;
  host: string;
  port: string;
  username: string;
  tlsMode: string;
  connectionDatabase: string;
  includeGlobals: boolean;
  extraOption: string;
  mongoMode: MongoMode;
  authDatabase: string;
  tls: boolean;
  replicaSet: string;
  mongoDumpMode: MongoDumpMode;
  projectId: string;
  databaseId: string;
  stagingBucket: string;
  collectionIds: string;
  deleteStagingAfterDownload: boolean;
  allDatabases: boolean;
  databases: string[];
}

export const SOURCE_TYPES: { type: SourceType; label: string; hint: string }[] = [
  { type: "mysql", label: "MySQL / MariaDB", hint: "Host fields or a pasted connection string" },
  { type: "postgres", label: "PostgreSQL", hint: "Host fields or a pasted connection string" },
  { type: "mongo", label: "MongoDB", hint: "Host fields or a full connection URI" },
  { type: "firestore", label: "Firebase Firestore", hint: "Service account and staging bucket" },
];

export const TLS_MODES: Record<"mysql" | "postgres" | "mongo", string[]> = {
  mysql: ["disabled", "preferred", "required", "verify_ca", "verify_identity"],
  postgres: ["disable", "allow", "prefer", "require", "verify-ca", "verify-full"],
  mongo: [],
};

export const DEFAULT_PORTS: Partial<Record<SourceType, string>> = {
  mysql: "3306",
  postgres: "5432",
  mongo: "27017",
};

export interface SecretSpec {
  field: string;
  label: string;
  required: boolean;
  multiline: boolean;
}

export function secretSpecs(type: SourceType, mode: MongoMode): SecretSpec[] {
  if (type === "firestore") {
    return [
      { field: "service_account_json", label: "Service account JSON", required: true, multiline: true },
    ];
  }
  if (type === "mongo" && mode === "uri") {
    return [{ field: "uri", label: "Connection URI", required: true, multiline: false }];
  }
  return [{ field: "password", label: "Password", required: false, multiline: false }];
}

export function defaultValues(type: SourceType): SourceFormValues {
  return {
    name: "",
    host: "",
    port: DEFAULT_PORTS[type] ?? "",
    username: "",
    tlsMode: type === "mysql" ? "preferred" : type === "postgres" ? "prefer" : "",
    connectionDatabase: "postgres",
    includeGlobals: false,
    extraOption: "",
    mongoMode: "fields",
    authDatabase: "admin",
    tls: false,
    replicaSet: "",
    mongoDumpMode: "per_database",
    projectId: "",
    databaseId: "(default)",
    stagingBucket: "",
    collectionIds: "",
    deleteStagingAfterDownload: true,
    allDatabases: true,
    databases: [],
  };
}

function str(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

export function recordToValues(record: SourceRecord): SourceFormValues {
  const config = record.config;
  const base = defaultValues(record.type);
  const databases = config.databases;
  const options = (config.extra_options ?? {}) as Record<string, string>;
  return {
    ...base,
    name: record.name,
    host: str(config.host),
    port: config.port === undefined ? base.port : String(config.port),
    username: str(config.username),
    tlsMode: str(config.tls_mode, base.tlsMode),
    connectionDatabase: str(config.connection_database, base.connectionDatabase),
    includeGlobals: config.include_globals === true,
    extraOption: record.type === "mysql" ? str(options.charset) : str(options.application_name),
    mongoMode: config.mode === "uri" ? "uri" : "fields",
    authDatabase: str(config.auth_database, base.authDatabase),
    tls: config.tls === true,
    replicaSet: str(config.replica_set),
    mongoDumpMode: config.dump_mode === "full_oplog" ? "full_oplog" : "per_database",
    projectId: str(config.project_id),
    databaseId: str(config.database_id, base.databaseId),
    stagingBucket: str(config.staging_bucket),
    collectionIds: Array.isArray(config.collection_ids) ? config.collection_ids.join(", ") : "",
    deleteStagingAfterDownload: config.delete_staging_after_download !== false,
    allDatabases: !Array.isArray(databases),
    databases: Array.isArray(databases) ? (databases as string[]) : [],
  };
}

export function parseCollectionIds(text: string): string[] {
  return text
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

const databaseSelection = {
  allDatabases: z.boolean(),
  databases: z.array(dbNameField),
};

function needDatabases(
  values: { allDatabases: boolean; databases: string[]; mongoDumpMode?: string },
  ctx: z.RefinementCtx,
) {
  // The selection is ignored (and hidden) when the whole instance is dumped.
  if (values.mongoDumpMode === "full_oplog") return;
  if (!values.allDatabases && values.databases.length === 0) {
    ctx.addIssue({
      code: "custom",
      path: ["databases"],
      message: "Select at least one database, or choose all",
    });
  }
}

/** Per-type validation mirroring the backend; unknown keys pass through untouched. */
export function sourceSchema(type: SourceType, mode: MongoMode) {
  const common = { name: nameField, ...databaseSelection };
  switch (type) {
    case "mysql":
      return z
        .looseObject({
          ...common,
          host: hostField,
          port: portField,
          username: usernameField,
          extraOption: z.string().regex(/^[\w.-]*$/, "Letters, digits, '.', '_' and '-' only"),
        })
        .superRefine(needDatabases);
    case "postgres":
      return z
        .looseObject({
          ...common,
          host: hostField,
          port: portField,
          username: usernameField,
          connectionDatabase: dbNameField,
          extraOption: z.string().regex(/^[\w.-]*$/, "Letters, digits, '.', '_' and '-' only"),
        })
        .superRefine(needDatabases);
    case "mongo":
      if (mode === "uri") return z.looseObject(common).superRefine(needDatabases);
      return z
        .looseObject({
          ...common,
          host: hostField,
          port: portField,
          username: z.string().max(128),
          authDatabase: dbNameField,
          replicaSet: z.string().max(64),
        })
        .superRefine(needDatabases);
    case "firestore":
      return z.looseObject({
        name: nameField,
        projectId: projectIdField,
        databaseId: databaseIdField,
        stagingBucket: z
          .string()
          .superRefine((value, ctx) => {
            const problem = checkStagingBucket(value);
            if (problem) ctx.addIssue({ code: "custom", message: problem });
          }),
        collectionIds: z.string().superRefine((value, ctx) => {
          if (!parseCollectionIds(value).every(isValidCollectionId)) {
            ctx.addIssue({ code: "custom", message: "Invalid collection ID" });
          }
        }),
      });
  }
}

function databasesValue(values: SourceFormValues): "all" | string[] {
  return values.allDatabases ? "all" : [...new Set(values.databases)];
}

function toConfig(type: SourceType, v: SourceFormValues): Record<string, unknown> {
  switch (type) {
    case "mysql":
      return {
        host: v.host.trim(),
        port: Number(v.port),
        username: v.username,
        databases: databasesValue(v),
        tls_mode: v.tlsMode,
        extra_options: v.extraOption ? { charset: v.extraOption } : {},
      };
    case "postgres":
      return {
        host: v.host.trim(),
        port: Number(v.port),
        username: v.username,
        connection_database: v.connectionDatabase.trim(),
        include_globals: v.includeGlobals,
        databases: databasesValue(v),
        tls_mode: v.tlsMode,
        extra_options: v.extraOption ? { application_name: v.extraOption } : {},
      };
    case "mongo": {
      const databases = v.mongoDumpMode === "full_oplog" ? "all" : databasesValue(v);
      if (v.mongoMode === "uri") return { mode: "uri", databases, dump_mode: v.mongoDumpMode };
      return {
        mode: "fields",
        host: v.host.trim(),
        port: Number(v.port),
        username: v.username || null,
        auth_database: v.authDatabase.trim(),
        tls: v.tls,
        replica_set: v.replicaSet.trim() || null,
        databases,
        dump_mode: v.mongoDumpMode,
      };
    }
    case "firestore": {
      const ids = parseCollectionIds(v.collectionIds);
      return {
        project_id: v.projectId.trim(),
        database_id: v.databaseId.trim(),
        staging_bucket: v.stagingBucket.trim().replace(/\/+$/, ""),
        collection_ids: ids.length > 0 ? ids : null,
        delete_staging_after_download: v.deleteStagingAfterDownload,
      };
    }
  }
}

export function toSourceInput(
  type: SourceType,
  values: SourceFormValues,
  secrets: Record<string, SecretState>,
): SourceInput {
  return {
    type,
    name: values.name.trim(),
    config: toConfig(type, values),
    secrets: buildSecrets(secrets),
  };
}

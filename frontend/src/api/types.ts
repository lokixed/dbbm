// Hand-written until these are generated from the OpenAPI schema (openapi-typescript).
export interface AuthStatus {
  setup_required: boolean;
}

export interface User {
  id: string;
  username: string;
}

export interface Credentials {
  username: string;
  password: string;
}

export type SourceType = "mysql" | "postgres" | "mongo" | "firestore";
export type DestinationType = "local" | "gdrive";
export type DestinationAuthStatus = "ok" | "needs_reauth" | "unknown";
export type DriveScope = "drive.file" | "drive";

export interface LastTest {
  at: string;
  ok: boolean;
  message: string | null;
}

export interface CheckResult {
  name: string;
  ok: boolean;
  message: string;
}

export interface TestResult {
  ok: boolean;
  message: string;
  server_version: string | null;
  databases: string[] | null;
  free_bytes: number | null;
  total_bytes: number | null;
  checks: CheckResult[];
}

/** Secrets are write-only: omit a key to keep it, send null to clear it, send a string to replace it. */
export type SecretsInput = Record<string, string | null>;

export interface SourceRecord {
  id: string;
  name: string;
  type: SourceType;
  config: Record<string, unknown>;
  secrets_set: Record<string, boolean>;
  secrets_error: string | null;
  last_test: LastTest | null;
  created_at: string;
  updated_at: string;
}

export interface SourceInput {
  type: SourceType;
  name: string;
  config: Record<string, unknown>;
  secrets: SecretsInput;
}

export interface Retention {
  keep_last: number | null;
  keep_days: number | null;
}

export interface LocalConfig {
  path: string;
}

export interface GdriveConfig {
  remote_folder: string;
  scope: DriveScope;
  shared_drive_id: string | null;
  client_id: string | null;
  delete_permanently: boolean;
  chunk_size_mb: number;
}

interface DestinationBase {
  id: string;
  name: string;
  retention: Retention;
  secrets_set: Record<string, boolean>;
  secrets_error: string | null;
  auth_status: DestinationAuthStatus;
  auth_checked_at: string | null;
  last_test: LastTest | null;
  created_at: string;
  updated_at: string;
}

export type DestinationRecord =
  | (DestinationBase & { type: "local"; config: LocalConfig })
  | (DestinationBase & { type: "gdrive"; config: GdriveConfig });

export type DestinationInput =
  | { type: "local"; name: string; config: LocalConfig; retention: Retention }
  | {
      type: "gdrive";
      name: string;
      config: GdriveConfig;
      secrets: SecretsInput;
      retention: Retention;
      /** A finished "Connect Google Drive" flow to take the token from instead of secrets.token. */
      token_flow_id?: string;
    };

export type GdriveConnectStatus = "pending" | "connected" | "failed";

export interface GdriveConnectStart {
  flow_id: string;
  authorize_url: string;
}

export interface GdriveConnectState {
  status: GdriveConnectStatus;
  error: string | null;
  message: string | null;
}

export type RunStatus =
  | "queued"
  | "running"
  | "success"
  | "partial"
  | "failed"
  | "cancelled"
  | "interrupted";

export const ACTIVE_STATUSES: readonly RunStatus[] = ["queued", "running"];

export interface RefRecord {
  id: string;
  name: string;
}

export interface RunSummary {
  id: string;
  job_id: string | null;
  job_name: string;
  trigger: string;
  status: RunStatus;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
  created_at: string;
  artifact_count: number;
  total_size: number;
  parent_run_id: string | null;
  scheduled_for: string | null;
  kind: "backup" | "restore";
  /** Engine of a restore's target source; backups take theirs from the job. */
  source_type?: string | null;
}

export type NotifyOn = "failure" | "always" | "never";

export interface JobRecord {
  id: string;
  name: string;
  source: RefRecord & { type: SourceType };
  destinations: RefRecord[];
  enabled: boolean;
  schedule: string | null;
  timezone: string;
  /** Set when a stored schedule cannot be used (e.g. free text saved before scheduling existed). */
  schedule_error: string | null;
  next_run_at: string | null;
  compress: boolean;
  /** Backups are encrypted with age to these keys. */
  encrypt: boolean;
  recipients: { id: string; name: string; fingerprint: string }[];
  timeout_seconds: number;
  notify_on: NotifyOn;
  notify_on_recovery: boolean;
  notification_channels: RefRecord[];
  heartbeat_set: boolean;
  last_run: RunSummary | null;
  created_at: string;
  updated_at: string;
}

export interface JobInput {
  name: string;
  source_id: string;
  destination_ids: string[];
  enabled: boolean;
  schedule: string | null;
  timezone: string | null;
  compress: boolean;
  encrypt: boolean;
  recipient_ids: string[];
  timeout_seconds: number;
  notify_on: NotifyOn;
  notify_on_recovery: boolean;
  notification_channel_ids: string[];
  /** Write-only: omit to keep, null to clear, a string to replace. */
  heartbeat_url?: string | null;
}

export interface ArtifactRecord {
  id: string;
  destination_id: string;
  destination_name: string | null;
  source_database: string;
  path: string;
  size_bytes: number;
  sha256: string;
  md5: string | null;
  compressed: boolean;
  encrypted: boolean;
  recipients: string[];
  created_at: string;
}

export interface StepEntry {
  name: string;
  status: string;
  started_at: string;
  duration_ms: number | null;
  detail: string | null;
}

export interface SubStepEntry {
  name: string;
  /** ok, warning, failed, cancelled or running */
  status: string;
  started_at: string;
  duration_ms: number | null;
  detail: string | null;
}

export type ErrorClass =
  | "auth_expired"
  | "quota_exceeded"
  | "rate_limited"
  | "folder_not_found"
  | "network"
  | "verify_mismatch"
  | "other";

export interface DestinationStep {
  name: "put" | "verify";
  status: string;
  duration_ms: number | null;
}

export interface DestinationOutcome {
  destination_id?: string;
  destination_name?: string;
  type?: DestinationType;
  status: "ok" | "failed" | "skipped";
  path?: string;
  bytes?: number;
  md5?: string;
  steps?: DestinationStep[];
  store_ms?: number;
  verify_ms?: number;
  error?: string;
  error_class?: ErrorClass;
  previous_error?: string | null;
  retried_by?: string;
  last_retry_run_id?: string;
}

export interface UnitResult {
  database: string;
  kind: "database" | "globals";
  dump: {
    status?: string;
    duration_ms?: number;
    size_bytes?: number;
    error?: string;
    source?: string;
    steps?: SubStepEntry[];
  };
  destinations: DestinationOutcome[];
}

/** What a restore run read and where it wrote. Older runs lack the newer fields. */
export interface RestoreSummary {
  artifact_id?: string;
  database?: string;
  copy?: string;
  path?: string;
  target_source?: string;
  target_source_id?: string;
  backup_source_id?: string | null;
  target_type?: string;
  target_database?: string;
  drop?: boolean;
  full_instance?: boolean;
  only?: string[];
  encrypted?: boolean;
}

export interface StepResults {
  steps?: StepEntry[];
  units?: UnitResult[];
  warnings?: string[];
  restore?: RestoreSummary;
}

export interface RunNotification {
  id: string;
  channel_id: string;
  channel_name: string | null;
  event_type: NotificationEventType;
  status: NotificationEventStatus;
  attempts: number;
  error: string | null;
  created_at: string;
}

export interface RunDetail extends RunSummary {
  step_results: StepResults;
  artifacts: ArtifactRecord[];
  retry_run_ids: string[];
  notifications: RunNotification[];
}

export interface RunPage {
  items: RunSummary[];
  total: number;
}

export interface DashboardEntry {
  job_id: string;
  job_name: string;
  enabled: boolean;
  schedule: string | null;
  next_run_at: string | null;
  overdue: boolean;
  overdue_since: string | null;
  skipped_schedules_24h: number;
  latest_run_id: string | null;
  latest_status: RunStatus | null;
  latest_finished_at: string | null;
  last_success_at: string | null;
  last_artifact_size: number | null;
}

export interface DestinationHealth {
  id: string;
  name: string;
  type: DestinationType;
  auth_status: DestinationAuthStatus;
  auth_checked_at: string | null;
  last_success_at: string | null;
  last_error_class: ErrorClass | null;
}

export interface DashboardNextRun {
  job_id: string;
  job_name: string;
  next_run_at: string;
  schedule: string;
  timezone: string;
}

export interface DashboardFailure {
  run_id: string;
  job_id: string | null;
  job_name: string;
  status: RunStatus;
  finished_at: string | null;
  error: string | null;
  source_type?: string | null;
}

export interface DiskUsage {
  key: string;
  label: string;
  kind: "data" | "destination";
  destination_id: string | null;
  total_bytes: number;
  used_bytes: number;
  free_bytes: number;
  free_percent: number;
  below_threshold: boolean;
}

export type SchedulerState = "running" | "stopped" | "not_owner";

export interface Dashboard {
  jobs: DashboardEntry[];
  destinations: DestinationHealth[];
  next_runs: DashboardNextRun[];
  failures_24h: DashboardFailure[];
  disks: DiskUsage[];
  low_disk_threshold_percent: number;
  scheduler: { state: SchedulerState; owner: boolean } | null;
}

export interface AppSettings {
  public_base_url: string;
  default_timezone: string;
  log_retention_days: number;
  run_history_days: number;
  audit_retention_days: number;
  misfire_grace_seconds: number;
  housekeeping_time: string;
  low_disk_threshold_percent: number;
  overdue_grace_percent: number;
}

export interface AppSettingsRecord extends AppSettings {
  updated_at: string | null;
}

export interface SchedulePreview {
  cron: string;
  timezone: string;
  fires: { utc: string; local: string }[];
}

export type ChannelType = "slack" | "email" | "webhook";
export type EmailSecurity = "starttls" | "ssl" | "none";

export interface EmailConfig {
  host: string;
  port: number;
  security: EmailSecurity;
  username: string | null;
  from: string;
  to: string[];
}

interface ChannelBase {
  id: string;
  name: string;
  enabled: boolean;
  secrets_set: Record<string, boolean>;
  secrets_error: string | null;
  header_names: string[];
  warnings: string[];
  last_test: LastTest | null;
  jobs: RefRecord[];
  created_at: string;
  updated_at: string;
}

export type ChannelRecord =
  | (ChannelBase & { type: "slack"; config: Record<string, never> })
  | (ChannelBase & { type: "email"; config: EmailConfig })
  | (ChannelBase & { type: "webhook"; config: Record<string, never> });

/** Webhook headers are sent whole (or null to clear); other secrets are strings. */
export type ChannelSecretsInput = Record<string, string | null | Record<string, string>>;

export type ChannelInput =
  | { type: "slack"; name: string; enabled: boolean; secrets: ChannelSecretsInput }
  | {
      type: "email";
      name: string;
      enabled: boolean;
      config: EmailConfig;
      secrets: ChannelSecretsInput;
    }
  | { type: "webhook"; name: string; enabled: boolean; secrets: ChannelSecretsInput };

export type NotificationEventType =
  | "run_finished"
  | "recovery"
  | "overdue"
  | "auth_expired"
  | "retention_failed"
  | "low_disk"
  | "test";

export type NotificationEventStatus = "pending" | "sent" | "failed" | "rate_limited";

export interface NotificationEvent {
  id: string;
  channel_id: string;
  channel_name: string | null;
  run_id: string | null;
  job_id: string | null;
  event_type: NotificationEventType;
  status: NotificationEventStatus;
  error: string | null;
  attempts: number;
  created_at: string;
}

export interface NotificationEventPage {
  items: NotificationEvent[];
  total: number;
}

export interface ChannelTestResult {
  ok: boolean;
  message: string;
  event: NotificationEvent | null;
}

export interface RetentionCopy {
  run_id: string;
  job_name: string;
  status: RunStatus;
  started_at: string;
  counted: boolean;
  protected: boolean;
  artifacts: string[];
  bytes: number;
}

export interface RetentionPreview {
  keep_last: number | null;
  keep_days: number | null;
  delete: RetentionCopy[];
  keep: RetentionCopy[];
  delete_runs: number;
  delete_artifacts: number;
  delete_bytes: number;
}

export interface RetentionRunResult {
  destination_id: string;
  deleted_runs: number;
  deleted_artifacts: number;
  deleted_bytes: number;
  failures: { path: string; error: string }[];
  skipped: string | null;
}

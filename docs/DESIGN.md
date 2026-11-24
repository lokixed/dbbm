# DBBM (Database Backup & Management) - Design Document

A self-hosted, Docker-installed tool with a web UI that schedules and runs backups of **MySQL, PostgreSQL, MongoDB and Firebase Firestore**, and stores every backup in **two places: a local drive and Google Drive**.

- **Stack:** Python + FastAPI (backend), React + Vite + TypeScript (frontend), SQLite (metadata)
- **Packaging:** one Docker image, one `docker compose up`
- **Status:** design / pre-implementation
- **Date:** 17 November 2026

---

## 1. Goals and non-goals

### Goals
1. Back up MySQL, PostgreSQL, MongoDB and Firestore from one place.
2. Every job writes to a **local volume** and **Google Drive**, with per-destination status. (Local comes first; Google Drive is added in milestone 5.)
3. Web UI to manage sources, destinations, jobs, runs, backups and restores.
4. Runs unattended: scheduling, retention, notifications, recovery after restart.
5. Safe by default: encrypted credentials at rest, optional encrypted backups, least-privilege guidance.
6. Install and upgrade with Docker only.

### Non-goals (for the MVP)
- Physical or incremental backups (XtraBackup, `pg_basebackup`, WAL archiving, point-in-time recovery).
- Multi-user roles and SSO (single admin account in the MVP).
- Storage targets other than local and Google Drive (the design keeps the interface open for S3, SFTP, etc.).
- Running as a multi-node / HA service.
- Backing up databases the container cannot reach over the network.

---

## 2. Key decisions

| Topic | Decision | Reason |
|---|---|---|
| Process model | One container, one server worker process | Simple install. Several workers would each start a scheduler and fire jobs multiple times |
| Dump logic | Wrap the official CLI tools, don't reimplement | Vendor tools are the most reliable and best understood |
| Metadata store | SQLite (WAL mode) on a volume | The backup tool should not depend on a database server |
| Offsite transfer | `rclone` | Mature Google Drive support, checksums, retries |
| Live logs | Server-Sent Events | One-way streaming, simpler than WebSockets |
| Firestore | Firestore REST `exportDocuments` to a GCS staging bucket, then download through the Cloud Storage JSON API | Firestore has no dump-over-connection; export goes to Cloud Storage. Plain HTTPS calls need no CLI in the image (the Google Cloud CLI was about 1 GB) |
| Google Drive auth (MVP) | Paste an rclone token | Avoids OAuth redirect setup; in-app OAuth comes later |
| Backup type | Logical dumps | Portable, simple, adequate for small and medium databases |

---

## 3. Architecture

```
 Browser
    │  HTTPS (via reverse proxy)
    ▼
┌──────────────────────── Docker container ────────────────────────┐
│                                                                  │
│  Web UI (static React build)                                     │
│       │                                                          │
│  API server (FastAPI) ───────────► Metadata DB (SQLite, /data)   │
│       │                                                          │
│  Scheduler (APScheduler) ──► Job runner ──► Notifier             │
│                                  │                               │
│                                  ▼                               │
│        Bundled CLI tools: mysqldump · pg_dump · mongodump ·      │
│                           rclone · age (Firestore: REST only)    │
└───────┬───────────────────────────┬───────────────────┬──────────┘
        ▼                           ▼                   ▼
  Your databases              Local volume          Google Drive
  (sources)                   (/backups)            (offsite, rclone)
```

### Components

| Component | Responsibility |
|---|---|
| Web UI | Dashboard, CRUD for sources/destinations/jobs, live run logs, backup browser, restore |
| API server | REST API, authentication, validation, serves the built frontend as static files |
| Metadata DB | Users, sources, destinations, jobs, runs, artifacts, audit log |
| Scheduler | Turns each job's cron expression into triggers that enqueue runs |
| Job runner | Executes the run lifecycle, enforces locks and concurrency, streams logs |
| Drivers | Per-database logic: test connection, dump, restore |
| Storage adapters | Local and Google Drive: put, list, delete, verify |
| Notifier | Sends success/failure messages (Slack, email, webhook) |
| Retention manager | Deletes expired artifacts per destination |

### Single-process rule
Run uvicorn with **one worker**. The scheduler, run queue and lock table live in that process. If scaling is ever needed, move the scheduler and runner into a separate worker container with a shared database.

---

## 4. Technology stack

| Layer | Choice |
|---|---|
| Language / framework | Python 3.12+, FastAPI, Uvicorn |
| ORM / migrations | SQLModel (SQLAlchemy) + Alembic |
| Scheduler | APScheduler (in-process, cron triggers) |
| Subprocesses | `asyncio.create_subprocess_exec` with process groups for clean cancellation |
| Password hashing | `argon2-cffi` |
| Secrets encryption | `cryptography` (Fernet, or AES-GCM) |
| Google APIs | `google-auth` (service-account token) and `httpx` (Firestore REST and Cloud Storage JSON API) |
| Backup encryption (optional) | `age` |
| Frontend | React, Vite, TypeScript, TanStack Query, React Router, Tailwind CSS, shadcn/ui |
| Node package manager | Yarn 4 (Berry) via Corepack, `nodeLinker: node-modules`, committed `yarn.lock` and pinned `packageManager` field |
| API client | Generated from FastAPI's OpenAPI schema (`openapi-typescript`) |
| Tests | pytest (with pytest-timeout), Testcontainers (MySQL, Postgres, Mongo), Playwright for UI smoke tests |
| Lint / format | Ruff, mypy, ESLint, Prettier |
| CI | GitHub Actions: lint, test, build and push image |

---

## 5. Repository layout

```
dbbm/
├── Dockerfile                  # multi-stage: node build → python runtime
├── docker-compose.yml
├── README.md
├── backend/
│   ├── pyproject.toml
│   ├── alembic/                # migrations
│   └── app/
│       ├── main.py             # FastAPI app; mounts built frontend
│       ├── core/               # config, security, crypto, db session, logging
│       ├── models/             # SQLModel tables
│       ├── schemas/            # Pydantic request/response models
│       ├── api/                # auth, sources, destinations, jobs, runs,
│       │                       # backups, notifications, settings
│       ├── services/
│       │   ├── scheduler.py    # APScheduler wiring, job sync
│       │   ├── runner.py       # run lifecycle, locks, concurrency
│       │   ├── retention.py
│       │   ├── notifier.py
│       │   └── logstream.py    # per-run log buffer + SSE
│       ├── drivers/            # base.py mysql.py postgres.py mongo.py firestore.py
│       │                       # firestore_api.py (Google REST client)
│       └── storage/            # base.py local.py gdrive.py
├── frontend/
│   ├── package.json
│   └── src/
│       ├── pages/              # Dashboard, Sources, Destinations, Jobs,
│       │                       # Runs, Backups, Settings, Login, Setup
│       ├── components/
│       └── api/                # typed client
└── tests/
    ├── unit/
    ├── integration/            # real DB containers
    └── e2e/
```

---

## 6. Data model

All primary keys are UUIDs. Timestamps are stored in UTC.

### `users`
| Column | Type | Notes |
|---|---|---|
| id | uuid | |
| username | text | unique |
| password_hash | text | argon2 |
| created_at | datetime | |

### `sources`
| Column | Type | Notes |
|---|---|---|
| id | uuid | |
| name | text | unique, display name |
| type | enum | `mysql`, `postgres`, `mongo`, `firestore` |
| config | json | non-secret fields: host, port, database list, project ID, options |
| secrets_enc | blob | encrypted: passwords, connection URIs, service-account JSON |
| last_test_at / last_test_ok | datetime / bool | result of the last "Test connection" |

### `destinations`
| Column | Type | Notes |
|---|---|---|
| id | uuid | |
| name | text | |
| type | enum | `local`, `gdrive` |
| config | json | local path, or Drive remote name and folder path |
| secrets_enc | blob | encrypted rclone token for Drive |
| retention | json | `keep_last`, `keep_days` |

### `jobs`
| Column | Type | Notes |
|---|---|---|
| id | uuid | |
| name | text | |
| source_id | fk | |
| destination_ids | fk[] | at least one; default local + Drive |
| schedule | text | cron expression |
| timezone | text | default UTC |
| enabled | bool | |
| compress | bool | default true |
| encrypt | bool | optional `age` encryption |
| timeout_seconds | int | per-step timeout |
| notify_on | enum | `failure`, `always`, `never` |

### `runs`
| Column | Type | Notes |
|---|---|---|
| id | uuid | |
| job_id | fk | null for restore runs |
| kind | text | `backup` (default) or `restore` |
| trigger | enum | `schedule`, `manual`, `retry` |
| status | enum | `queued`, `running`, `success`, `partial`, `failed`, `cancelled`, `interrupted` |
| started_at / finished_at | datetime | |
| error | text | summarized failure reason |
| log_path | text | full log stored as a file under `/data/logs` |
| step_results | json | per-step and per-destination outcomes |

### `artifacts`
| Column | Type | Notes |
|---|---|---|
| id | uuid | |
| run_id | fk | |
| destination_id | fk | |
| path | text | key within the destination |
| size_bytes | bigint | |
| sha256 | text | |
| encrypted | bool | the stored file is age ciphertext (path ends in `.age`) |
| recipients | json | fingerprints of the keys it was encrypted to (public) |
| created_at / expires_at | datetime | |
| deleted_at | datetime | soft delete for audit |
| verified_at | datetime | last successful "Verify now"; null until then (it was verified at upload) |
| missing_at | datetime | set when the file is not found at the destination (drift); cleared when it is back |

### `notification_channels`, `audit_log`
- `notification_channels`: type (`slack`, `email`, `webhook`), config, encrypted secrets, enabled.
- `audit_log`: timestamp, user, action, object type and ID, details. Used for logins, config changes, restores, deletions.

---

## 7. Run lifecycle

```
queued → dumping → compressing → [encrypting] → storing (per destination)
       → verifying → pruning → success | partial | failed
```

| Step | Details |
|---|---|
| Preflight | Check free disk space against the last backup size × 1.5; test source connection |
| Dump | Run the driver; stream stdout through the compressor; write to a temp work dir |
| Compress / encrypt | gzip (or zstd), then optional `age` encryption |
| Checksum | Compute SHA-256 and write a small manifest file next to the artifact |
| Store | Copy to each destination independently |
| Verify | Local: compare size and checksum. Drive: `rclone check` / hash comparison |
| Prune | Apply retention per destination |
| Notify | Send per the job's `notify_on` setting |
| Cleanup | Always delete the temp work dir |

### Rules
- **Per-destination results.** "Local OK, Drive failed" is `partial`, with a retry button for only the failed destination.
- **Exit codes.** Check the exit code of every process in a pipeline (`set -o pipefail` semantics). A failed dump piped into gzip must never appear as a success.
- **Locks.** One active run per job; a global concurrency limit (default 2).
- **Timeouts and cancel.** Every step has a timeout. Cancel kills the whole process group.
- **Restart recovery.** On startup, any run still marked `running` becomes `interrupted` and its temp files are cleaned.
- **Missed schedules.** If the container was down at trigger time, run once on startup when within a configurable grace period (APScheduler `misfire_grace_time`).
- **No overwrite.** Artifact names include a timestamp; existing files are never replaced.

### Artifact naming
```
<job-name>/<YYYY>/<MM>/<job-name>_<YYYYMMDDTHHMMSSZ>.<ext>[.age]
<job-name>/<YYYY>/<MM>/<job-name>_<YYYYMMDDTHHMMSSZ>.manifest.json
```

Extensions: `.sql.gz` (MySQL), `.dump` (PostgreSQL custom format), `.archive.gz` (MongoDB), `.tar.gz` (Firestore export).

---

## 8. Drivers

Every driver implements the same interface:

```python
class Driver(Protocol):
    def test_connection(self) -> None: ...
    def dump(self, out_dir: Path, log: Logger) -> list[Path]: ...
    def restore(self, artifact: Path, target: Target, log: Logger) -> None: ...
```

### 8.1 MySQL
- Tool: `mysqldump` (MySQL or MariaDB client matching your server family).
- Typical command:
  ```
  mysqldump --defaults-extra-file=<tmp 0600 file> --single-transaction \
            --routines --triggers --events --databases db1 db2 | gzip > out.sql.gz
  ```
- `--single-transaction` gives a consistent snapshot for InnoDB without locking. MyISAM tables are not covered by it.
- Credentials go in a temporary options file with `0600` permissions, not on the command line.
- Backup user needs: `SELECT`, `SHOW VIEW`, `TRIGGER`, `EVENT`, `LOCK TABLES` (and `PROCESS` if dumping tablespaces).
- Restore user needs: `ALL` on the target databases, plus `CREATE` and `DROP` on `*.*` (the restore creates the target database and drops it with "replace existing"). Keep it separate from the backup user.
- Restore: `gunzip -c file | mysql ...` into a target chosen by the user.

### 8.2 PostgreSQL
- Tool: `pg_dump` (custom format) plus `pg_dumpall --globals-only` for roles and tablespaces.
- Typical command:
  ```
  pg_dump -Fc -h HOST -p PORT -U USER -d DB -f out.dump
  ```
- Custom format is already compressed and supports selective restore with `pg_restore`.
- Credentials via `PGPASSWORD` in the subprocess environment only, or a temporary `.pgpass` file.
- `pg_dump` must be at least as new as the server. Bundle the latest client, or several major versions if you back up mixed server versions.
- Backup user: `CONNECT` plus `SELECT` on everything (e.g. membership in `pg_read_all_data` on PostgreSQL 14+).
- Restore user: `CREATEDB` (or an existing database it owns). Restores use `--no-owner --no-privileges`, so objects belong to the restore user.
- Restore: `pg_restore --clean --if-exists -d TARGET out.dump`.

### 8.3 MongoDB
- Tool: `mongodump` from MongoDB Database Tools.
- Typical command:
  ```
  mongodump --uri="mongodb://..." --archive=out.archive --gzip
  ```
- For a full replica-set backup add `--oplog` for a point-in-time consistent dump. `--oplog` cannot be combined with a single `--db` selection.
- Backup user role: `backup` (or `readAnyDatabase` for simple cases).
- Restore user role: `restore` on the target databases (a full-instance restore with `--oplogReplay` needs more, usually `root`).
- Restore: `mongorestore --archive=out.archive --gzip`.
- Pass the URI via a config file or environment, not process arguments, so it doesn't appear in `ps`.

### 8.4 Firebase Firestore
Firestore can't be dumped over a connection. The driver calls Google's REST APIs over HTTPS with
`httpx`; no Google command-line tool is installed. The flow is:

1. Authenticate with the service account. `google-auth` signs a JWT with the key and exchanges it
   for a short-lived access token at the pinned token URL (`token_uri` in the file is ignored). The
   key stays in memory: it is never written to disk. The token is refreshed when it expires.
2. Start the export: `POST https://firestore.googleapis.com/v1/projects/P/databases/DB:exportDocuments`
   with `outputUriPrefix` = `gs://STAGING_BUCKET/<base>/<job>/<run>/<timestamp>` and, if set,
   `collectionIds`.
3. Poll the long-running operation (`GET /v1/<operation name>`) until `done`, respecting the job's
   timeout. A cancelled or timed-out run calls `POST /v1/<operation name>:cancel`.
4. List the export prefix (Cloud Storage JSON API, `objects.list`) and download every object
   (`objects.get?alt=media`) into the work directory, one at a time. Each size is checked against
   the listing, and object names that could leave the work directory are refused.
5. Pack it into a `.tar.gz` and treat it like any other artifact.
6. Optionally delete the staging objects (`objects.delete`), or rely on a bucket lifecycle rule.

Calls are retried a few times on network errors, throttling (429) and server errors (5xx), except
the calls that start an export or import (a retry could start it twice). Error messages from Google
are redacted before they reach a log or the UI.

Requirements:
- The Cloud Firestore API enabled in the project.
- A GCS staging bucket in a location compatible with the Firestore database.
- Service account roles: `roles/datastore.importExportAdmin` plus read/write access to the staging bucket (and `storage.objects.delete` to remove the staging copy).
- Add a bucket lifecycle rule (e.g. delete after 1–7 days) so staging copies don't accumulate cost.
- Restore uses `importDocuments` and requires the export to be in a bucket again. The driver re-uploads the artifact to the staging bucket first (one upload request per file) and deletes the copy afterwards. Importing can overwrite existing documents, so the restore screen must warn about this.
- Optionally also enable Firestore's own scheduled backups / point-in-time recovery in Google Cloud as a second safety net.

---

## 9. Storage adapters

```python
class Storage(Protocol):
    def put(self, local: Path, key: str) -> StoredObject: ...
    def list(self, prefix: str) -> list[StoredObject]: ...
    def get(self, key: str, dest: Path) -> None: ...
    def delete(self, key: str) -> None: ...
    def verify(self, local: Path, key: str) -> bool: ...
```

### Local
- Writes under the mounted `/backups` volume, using temp-file-then-rename so partial files never look complete.
- The path comes from the destination config but must stay within `/backups` (reject `..` and symlink escapes).
- Set file permissions to `0600`.

### Google Drive (via rclone)
- Each run generates a temporary rclone config from the decrypted token (kept in a tmpfs path, deleted afterwards) and passes it with `RCLONE_CONFIG`.
- Upload with `rclone copyto`, verify with `rclone check` (Drive provides MD5 checksums).
- Retention deletes with `rclone delete` / `rclone deletefile` by key.
- Use a dedicated folder (e.g. `db-backups/`) and restrict rclone's scope to it where possible.
- Known issue: Google OAuth apps left in **Testing** status issue refresh tokens that expire after 7 days. Publish the OAuth app (it can stay unverified for personal use) or use a Shared Drive with a service account.
- A service account has no Drive storage quota of its own, so it only works with a Shared Drive.
- Surface token failure clearly in the UI ("Drive authorization expired - reconnect") and in notifications.

---

## 10. Scheduling

- Each enabled job registers a cron trigger in APScheduler with its own timezone.
- Editing or disabling a job updates the scheduler immediately.
- The UI shows the next five fire times for a cron expression before saving.
- Default timezone is UTC; displayed times follow the browser locale.
- Triggers enqueue a run; the runner decides when it actually starts (locks and concurrency limit).

---

## 11. Retention

Per destination, configurable:
- `keep_last` - keep the newest N successful artifacts
- `keep_days` - delete artifacts older than N days

Rules:
- An artifact is deleted only if **both** policies allow it (it is outside the newest N **and** older than the day limit), when both are set.
- Never delete the **last successful** backup of a job, regardless of policy.
- Failed or partial runs don't count toward `keep_last`.
- Retention runs after each successful run and as a daily housekeeping task.
- Deletions are recorded in the audit log.

---

## 12. Security

### Application
- First-run setup creates the admin account; there is no default password.
- argon2 password hashes; HttpOnly, SameSite, Secure session cookies; CSRF protection on state-changing requests; login rate limiting and lockout.
- All inputs validated with Pydantic; no shell string concatenation (always `exec` with argument lists); path inputs sanitized.
- Secrets are redacted from logs, error messages and API responses (the API never returns stored passwords or tokens).
- Every response carries a strict Content-Security-Policy, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `nosniff`; HSTS when `COOKIE_SECURE=true`; `Cache-Control: no-store` on `/api`. `/api/docs` is off unless `ENABLE_DOCS=true`.
- The admin can change their password (all other sessions are signed out). A lost password is reset from the host with `python -m app.cli reset-password`.
- Backup downloads use a one-use signed link (60 seconds) so a browser or proxy can fetch large files with a plain GET.

### Credentials at rest
- Source passwords, Drive tokens, service-account JSON and notification secrets are encrypted before they go in SQLite.
- The master key comes from an env var or Docker secret (`APP_SECRET_KEY` / `APP_SECRET_KEY_FILE`), never from the database or image.
- Document clearly: **losing the master key means re-entering all credentials.**
- Offline admin commands (`python -m app.cli`): `rotate-key` (all-or-nothing, dry run, keeps a pre-rotation copy of the database), `backup-db` (SQLite online backup API), `export-config` (no secrets), `reset-password`.

### Backup encryption
- Optional per-job `age` encryption using public keys (recipients) stored in the `age_recipients` table and linked to jobs in `job_recipients`; the private key is **not** kept on the server.
- Encrypted artifacts can't be restored from the UI without the user supplying the private key at restore time.
- Pipeline: dump, compress, **encrypt**, checksum, store. The `age` CLI reads and writes the files itself (constant memory); the plaintext is deleted as soon as the ciphertext exists. Size, SHA-256 and the Drive MD5 describe the ciphertext.
- A job set to encrypt fails closed: no key, `age` missing or an `age` error fails the unit and nothing is stored.
- Decryption runs `age --decrypt -i -` with the private key on standard input only (never a file, an argument, a log or the database). The plaintext goes to the run's work directory and is removed with it.
- Public keys are validated (`age1` plus 58 bech32 characters); a private key pasted into the public-key field is refused. Only classic X25519 keys are accepted.

### Container and network
- Run as a non-root user; no Docker socket mount; read-only root filesystem with writable `/data`, `/backups` and a tmpfs for temp work.
- Bind to `127.0.0.1` and put Caddy or Traefik in front for HTTPS. Never expose the app port directly.
- Provide least-privilege, read-only database users per engine (see section 8) and document them.
- Pin base-image and tool versions; scan the image in CI.

---

## 13. REST API

All routes under `/api`, JSON bodies, session-cookie auth (except setup/login/health).

```
POST   /auth/setup            create first admin (only if no users exist)
POST   /auth/login | /auth/logout
GET    /auth/me

GET/POST         /sources              PUT/DELETE /sources/{id}
POST             /sources/{id}/test

GET/POST         /destinations         PUT/DELETE /destinations/{id}
POST             /destinations/{id}/test

GET/POST         /jobs                 PUT/DELETE /jobs/{id}
POST             /jobs/{id}/run        run now
POST             /jobs/{id}/enable | /disable

GET              /runs                 filters: job, status, date range
GET              /runs/{id}
GET              /runs/{id}/logs       Server-Sent Events (live) or full text
POST             /runs/{id}/cancel
POST             /runs/{id}/retry      retry failed destinations only

GET              /backups              list artifacts; filters: job, source, database, destination, status, date range
GET              /backups/{id}/download  verifies the SHA-256 first; local copy preferred, else Drive
POST             /backups/{id}/download-link  one-use URL, valid 60 seconds
GET              /backups/download/{token}    serves the file for a link (the token is the credential)
POST             /backups/{id}/verify    re-read the stored copy (local SHA-256, Drive MD5)
POST             /backups/rescan?destination_id=   mark artifacts whose files are gone
DELETE           /backups/{id}
POST             /backups/{id}/restore  body: target_source_id, target_database, drop, only (collections/tables; MongoDB and PostgreSQL per-database backups), confirm_database, confirm_overwrite_original, private_key (encrypted backups only); starts a run of kind `restore`
GET/POST         /recipients           age public keys for backup encryption; DELETE /recipients/{id} (refused while a job uses the key)

GET/POST/PUT/DELETE /notifications
POST             /notifications/{id}/test

GET              /dashboard            summary stats
GET              /settings | PUT /settings
GET              /audit

GET              /healthz              unauthenticated liveness check
```

OpenAPI docs are served at `/api/docs` (disable or protect in production).

---

## 14. Web UI

| Page | Contents |
|---|---|
| Setup / Login | First-run admin creation, login |
| Dashboard | Per-job last status, next scheduled run, failures in the last 24h, disk usage, destination health |
| Sources | List and add/edit form per DB type with a **Test connection** button; credentials are write-only (shown as "••••" after saving) |
| Destinations | Local path check; Google Drive token paste with **Test**; retention settings |
| Jobs | Create/edit with source, destinations, cron picker with next-fire preview, options; **Run now**, enable/disable |
| Runs | History table with filters; run detail with step timeline, per-destination results and live log viewer; cancel and retry |
| Backups | Browse artifacts by job/date; download, delete; **Restore** wizard |
| Notifications | Add Slack/email/webhook channels with a test message |
| Settings | Admin password, timezone, backup-encryption public key, key status, version info |
| Audit | Read-only log of sensitive actions |

UX rules:
- Destructive actions require confirmation; restore requires typing the target database name.
- Every long action shows progress and is cancelable.
- Errors show a plain-language cause and the relevant log excerpt.

---

## 15. Restore

- Restore reads from the local copy if present, otherwise downloads from Google Drive.
- Verify the checksum before restoring.
- Default target is a **different** database/instance than the source; restoring over the original needs an explicit extra confirmation.
- Encrypted artifacts prompt for the `age` private key (not stored).
- All restores are written to the audit log with user, artifact and target.
- MVP fallback: "download artifact + copy-paste restore command" before the full wizard is built.
- A restore is a tracked run (`kind = restore`) with live log, cancel and timeout. It uses the copy the user picks (no silent fallback), verifies its SHA-256, then runs the driver's restore. A target source is never restored while a backup or restore runs on it.
- The server enforces the typed database name and the extra confirmation for restoring over the original; the wizard only mirrors them. Restores are limited to 5 starts per minute per user.
- Implemented for MongoDB (`mongorestore`, credentials in a 0600 config file, `--nsFrom/--nsTo` renames, optional `--drop`), MySQL (the target database is created, or dropped and recreated with "replace existing"; the gunzipped dump is streamed into the `mysql` client with an options file) and PostgreSQL (`pg_restore --no-owner --no-privileges`, in one transaction where possible; "replace existing" adds `--clean --if-exists`). A `pg_restore` 17+ against a server before 17 cannot run in one transaction because it sets `transaction_timeout`, so that case runs without it and ignores only that error. The PostgreSQL roles file is refused. Firestore (the export is unpacked, copied to the staging bucket, imported with the Firestore `importDocuments` call and the staging copy is deleted, also after a failure or cancel; the import goes into the target source's own database, overwrites documents with the same ID and never deletes, so "replace existing" is refused).
- Target database names must match `^[A-Za-z0-9_][A-Za-z0-9_-]{0,62}$`, so they are safe as command-line arguments.
- A scheduled **restore test** is a good later feature: restore to a scratch container and run a sanity query.

---

## 16. Notifications and observability

- Channels: Slack webhook, SMTP email, generic webhook. Messages include job, status, duration, size, error summary and a link to the run.
- **Dead-man's switch:** optional ping URL (e.g. Healthchecks.io) called after each successful run, so you're alerted if the whole system silently stops.
- Structured JSON logs to stdout; per-run log files under `/data/logs` with a retention setting.
- `/healthz` checks DB access and scheduler liveness; Docker `HEALTHCHECK` uses it.
- Optional Prometheus `/metrics` endpoint (later): last success timestamp, duration, size per job.

---

## 17. Docker packaging

### Image build (outline)
```
Stage 1 (node):    corepack enable && yarn install --immutable && yarn build  → /frontend/dist
Stage 2 (python):  base image (Debian slim), then install:
                     - mysql/mariadb client (mysqldump)
                     - postgresql-client (pg_dump; latest major, add older if needed)
                     - mongodump, mongorestore (built from source in a Go stage)
                     - rclone, age (age is built from source in a Go stage)
                   pip install backend; copy frontend/dist
                   create non-root user; EXPOSE 8080
                   CMD uvicorn app.main:app --host 0.0.0.0 --port 8080 --workers 1
```
Firestore uses Google's REST APIs, so the image holds no Google Cloud CLI. The Go tools are cross-compiled on the build machine. The image is built for linux/amd64 only: MySQL's apt repository has no arm64 packages for Debian, and the design uses the official MySQL client. Adding arm64 later means choosing another MySQL client for it.

The tools are added to the image incrementally: `mysqldump` and `pg_dump` in milestone 3, `mongodump` in milestone 4, `rclone` in milestone 5 (`age` when backup encryption is built).

### docker-compose.yml (example)
```yaml
services:
  dbbm:
    image: yourname/dbbm:latest
    restart: unless-stopped
    ports: ["127.0.0.1:8080:8080"]
    environment:
      APP_SECRET_KEY_FILE: /run/secrets/app_key
      TZ: UTC
    volumes:
      - ./data:/data            # SQLite, logs
      - /srv/backups:/backups   # local backups
    read_only: true
    tmpfs: [/tmp]
    secrets: [app_key]
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://localhost:8080/healthz"]
      interval: 30s
secrets:
  app_key:
    file: ./app_key.txt
```
If your databases run in other containers, attach this service to the same Docker network. If they run on the host, use `host.docker.internal` / the host's address and make sure the DB allows the connection.

### Environment variables
| Variable | Purpose |
|---|---|
| `APP_SECRET_KEY` / `APP_SECRET_KEY_FILE` | Master key for encrypting stored credentials |
| `DATA_DIR` | Default `/data` |
| `BACKUP_DIR` | Default `/backups` |
| `TZ` | Container timezone |
| `MAX_CONCURRENT_RUNS` | Default 2 |
| `LOG_LEVEL` | Default `info` |
| `SESSION_TTL_HOURS` | Default 12 |

---

## 18. Testing strategy

| Level | What |
|---|---|
| Unit | Cron parsing, retention rules, path sanitization, redaction, naming |
| Driver integration | Testcontainers: real MySQL, PostgreSQL, MongoDB; seed data, dump, restore into a new instance, compare |
| Firestore | Firebase emulator for logic tests; a real throwaway project for a periodic end-to-end export test |
| Storage | Local adapter tests; rclone against a test Drive account or a local rclone remote as a stand-in |
| Failure injection | Kill the container mid-run, fill the disk, break the Drive token, drop the DB connection, check status and cleanup |
| API / UI | pytest API tests; Playwright smoke tests for login → add source → run now → see log |
| Security | Dependency and image scanning, auth/CSRF tests, secret-redaction tests |

A backup tool is only trustworthy if **restores are tested** - treat the restore round-trip test as a release gate.

---

## 19. Build milestones

| # | Milestone | Done when |
|---|---|---|
| 1 | Skeleton: FastAPI + SQLite + Alembic, auth with first-run setup, React shell, Dockerfile and compose | You can log in to an empty dashboard running in Docker |
| 2 | Sources CRUD and **local** destinations CRUD, encrypted credentials, "Test connection" (Google Drive deferred to milestone 5) | All four DB types and the local destination can be configured and tested |
| 3 | PostgreSQL and MySQL drivers, local storage, manual "Run now", live logs | First real end-to-end backup |
| 4 | MongoDB and Firestore drivers | All four databases back up |
| 5 | All Google Drive work: install rclone in the image, add the `gdrive` destination type (migration), Drive token form and connection test, Drive adapter, verification, per-destination status, retry | Offsite copy works; partial failures are visible and retryable |
| 6 | Scheduler, retention, notifications, interrupted-run recovery | Runs unattended |
| 7 | Backups browser, download, restore wizard | Full round trip tested |
| 8 | Backup encryption (age) | Encrypted backups restore with the private key |
| 9 | Hardening: audit log, key rotation, security headers, scans in CI, docs, release image | Production-ready 1.0 (see docs/TODO.md for the open acceptance checks) |

Milestones 1–3 produce something usable quickly; 4 and 5 hold the harder integrations.

---

## 20. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Dump tool version mismatch (`pg_dump` older than server) | Bundle current clients; check versions in "Test connection" and warn |
| Large databases make logical dumps slow or large | Show duration and size trends; document the limitation; consider physical backups later |
| Drive token expires or app stuck in Testing mode | Clear UI error, notification, documented setup steps |
| Master key lost | Document key backup; key-rotation tooling; export-free design means credentials are re-entered, backups remain intact |
| Disk fills with backups or temp files | Preflight check, retention, per-run temp cleanup, dashboard disk usage alert |
| Silent failure of the whole tool | Dead-man's-switch ping and failure notifications |
| Credentials in logs or process lists | Config files/env for secrets, redaction filter, tests |
| Staging-bucket cost for Firestore | Lifecycle rule on the bucket, optional delete after download |
| Image size and build time | Multi-stage build, cache layers, no Google Cloud CLI (Firestore uses REST) |

---

## 21. Future enhancements

- Additional destinations: S3-compatible, SFTP, Azure Blob.
- Additional engines: MariaDB profile, SQLite, Redis, SQL Server.
- In-app Google Drive OAuth connect flow.
- Multi-user roles, SSO, API tokens.
- Physical and incremental backups, WAL/binlog archiving, point-in-time recovery.
- Scheduled automatic restore tests with verification queries.
- Prometheus metrics and a Grafana dashboard.
- Backup verification reports and weekly summary emails.
- Split scheduler and workers for multi-node setups.

---

## 22. Open questions

1. Preferred Python version and base image (Debian slim vs. Ubuntu)?
2. Will the databases run on the same Docker host, in other containers, or remote/managed (RDS, Atlas, Cloud SQL)?
3. Is a staging GCS bucket already available for Firestore exports?
4. Approximate database sizes, to decide whether logical dumps are adequate?
5. Preferred notification channel (Slack, email, other)?
6. Should backups be encrypted with `age` from the start?

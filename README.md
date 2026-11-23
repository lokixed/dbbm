# DBBackup

Self-hosted Docker app that backs up MySQL, PostgreSQL, MongoDB and Firestore to a local volume and Google Drive, with a web UI.

> **Status: Milestone 5 (Google Drive).** MySQL, PostgreSQL, MongoDB and Firestore sources can be backed up to local and Google Drive destinations with **Run now**, with live logs, per-destination verification and retry of failed uploads. Schedules, retention, notifications, the backups browser and restore arrive in later milestones. See [docs/DESIGN.md](docs/DESIGN.md) for the full design and milestone plan.

## Quick start

```bash
# 1. Create the master key (encrypts stored credentials; keep a backup of it).
(umask 077; openssl rand -base64 32 > app_key.txt)

# 2. Data directories, and run the container as your user so it can use them.
mkdir -p data backups
printf 'DBBACKUP_UID=%s\nDBBACKUP_GID=%s\n' "$(id -u)" "$(id -g)" > .env

# 3. Build and start.
docker compose up --build -d
```

Open <http://127.0.0.1:8080>, create the admin account, then sign in.

- The port is bound to `127.0.0.1` only. Put Caddy or Traefik in front for HTTPS and set `COOKIE_SECURE=true` in `.env`.
- Losing `app_key.txt` means re-entering every stored credential.
- Health check: `curl http://127.0.0.1:8080/healthz`.

## Add your first source and destination

1. Open **Sources**, click **Add source**, pick a type and fill in the connection details.
2. Click **Save and test**. The result shows the server version and the databases the user can see; for MySQL, PostgreSQL and MongoDB you can then choose "Selected databases" and tick the ones to back up. Firestore reports separately whether the database and the staging bucket are reachable.
3. Open **Destinations**, click **Add destination** and enter a path such as `daily` (or add a Google Drive folder, see below). Paths are relative to `/backups`; `..`, absolute paths outside it, and symlinks that point outside it are rejected. **Save and test** confirms the folder is writable and shows free space.

Passwords, connection URIs and service-account keys are write-only: once saved they show as "Saved" and are never returned by the API. Leave a secret untouched when editing to keep it, or use **Change** / **Clear**. Secrets are encrypted with the master key, so changing the key makes stored credentials unreadable until you re-enter them.

No databases to try? `make dev-dbs-up` starts sample MySQL, PostgreSQL and MongoDB servers; see [dev/README.md](dev/README.md).

## Run your first backup

1. Add a **source** (MySQL, PostgreSQL, MongoDB or Firestore) and a local **destination** (above).
2. Open **Jobs**, click **Add job**, give it a name, choose the source and one or more destinations, and save. The schedule field is stored but not active yet.
3. Click **Run now**. The run page shows the progress and streams the log live; **Cancel run** stops the dump and removes its temporary files.
4. When the run is `success`, each database has an artifact under `./backups/<destination path>/<job>/<YYYY>/<MM>/` (`.sql.gz` for MySQL, `.dump` for PostgreSQL, `.archive.gz` for MongoDB, `.tar.gz` for Firestore) next to a `.manifest.json`. The job's compress switch does not change the MongoDB, Firestore and PostgreSQL `.dump` formats, which are always compressed. Files are mode `0600`, never overwritten, and the size and SHA-256 on the run page match the file.

Backup user privileges: MySQL needs `SELECT`, `SHOW VIEW`, `TRIGGER`, `EVENT` on the databases and the global `SHOW_ROUTINE` privilege (8.0.20+) if they contain stored routines; a dump that cannot read a routine fails instead of silently leaving it out. PostgreSQL needs `CONNECT` and `SELECT` on everything (for example membership in `pg_read_all_data`).

Restoring: open **Backups**, click the restore button on a copy and follow the wizard: choose the copy, the target source and database name, the options, then type the database name to confirm. The target is a registered source and its credentials are used. The default is a new database name; restoring into the original database needs an extra confirmation. A restore is a run like a backup (live log, **Cancel run**), and it checks the file's SHA-256 before it changes anything. MongoDB, MySQL, PostgreSQL and Firestore are supported. Firestore imports go into the target source's own database, overwrite documents with the same ID and never delete documents. The PostgreSQL roles file is not restored: download it and apply it with `psql`.

Restore user privileges: restoring needs write access, so create a separate restore user and use it for the target source instead of the read-only backup user. MySQL needs `ALL` on the target databases plus `CREATE` and `DROP` on `*.*` (the restore creates the database, and drops it with "replace existing"). PostgreSQL needs `CREATEDB` (or an existing, owned target database); objects are created with the restore user as owner. MongoDB needs the `restore` role on the target databases. Firestore needs `roles/datastore.importExportAdmin` and permission to create and delete objects in the staging bucket.

A run is `success` when every database reached every destination and was verified, `partial` when some did, and `failed` when none did. A second run of the same job cannot start while one is active.

## Backup encryption

Jobs can encrypt every backup with [age](https://github.com/FiloSottile/age) before it is checksummed, stored or uploaded, so a leaked Google Drive account or disk does not expose your data. The server only ever holds **public keys**. It never has a private key.

**Losing the private key means the backups are unrecoverable.** Nobody can open an encrypted backup without it, not even the administrator.

1. Generate a key pair on your own machine, not on the server: `age-keygen -o backup-key.txt`. It prints the public key (`age1...`) and writes the private key to the file. Store that file somewhere safe and offline, for example in a password manager.
2. Make a second key pair as a recovery key and keep it somewhere else. A backup can be opened with any one of the keys it was encrypted to.
3. Open **Settings**, section **Backup encryption keys**, and add the public keys. A private key (`AGE-SECRET-KEY-...`) is refused.
4. Edit the job, turn on **Encrypt backups with age** and choose the keys.

Encrypted artifacts end in `.age` (for example `.sql.gz.age`). The checksum, size and Google Drive MD5 describe the encrypted file, and the manifest records `encrypted: true` and the key fingerprints, never any private material. If a job is set to encrypt and no key is set, `age` is missing or encryption fails, the run fails and no unencrypted backup is stored.

To restore an encrypted backup, the restore wizard asks for the private key. Paste it or load the file. It is used for that restore only, held in memory, and never stored or logged. A wrong key, a truncated file or a damaged file stops the restore before the target is touched.

To read a backup yourself, download it from **Backups** (it downloads as stored, still encrypted) and run:

```
age --decrypt -i backup-key.txt -o backup.sql.gz backup.sql.gz.age
```

Rotating keys: add the new public key, add it to the jobs, then remove the old one from the jobs. Backups made earlier stay readable only with the key they were encrypted to, so keep the old private key as long as you keep those backups. To re-encrypt an old backup, decrypt it and encrypt it to the new key: `age -d -i old-key.txt backup.age | age -r age1NEWPUBLICKEY -o backup-new.age`.

Recovery drill, at least once per quarter: take a backup from your storage (not from the server's disk), fetch the private key from where you keep it, decrypt it with `age`, and restore it into a scratch database. If any step fails, fix it while you still have the original data.

## Supported databases

| Source | Tool in the image | What you get |
|---|---|---|
| MySQL | `mysqldump` 8.4 | One `.sql.gz` per database |
| PostgreSQL | `pg_dump` 18 (optional `pg_dumpall --globals-only`) | One `.dump` (custom format) per database |
| MongoDB | `mongodump` (MongoDB Database Tools) | **Per database** (default): one `.archive.gz` per selected database. **Full instance with oplog**: one `.archive.gz` of the whole replica set (`--oplog`); fails early on a standalone server. The databases selection is ignored in this mode |
| Firestore | `gcloud` (Google Cloud CLI) | One `.tar.gz` of a managed export, downloaded from a staging bucket |

MongoDB: the connection string and password go to `mongodump` through a temporary `0600` config file, never the command line. The `backup` role is enough. On a replica set, per-database dumps are not point-in-time consistent across collections (the run log warns); use full-instance mode for a consistent copy.

Firestore needs, in your Google Cloud project: billing enabled; a Cloud Storage staging bucket in the same location as the database; a service account with `roles/datastore.importExportAdmin` plus read/write on the bucket (and delete permission if you let the app remove the staging export); and ideally a bucket lifecycle rule (for example delete after 1 to 7 days) so failed or cancelled exports do not accumulate cost. Exports can take a long time and incur read charges; raise the job timeout for large databases (it applies to each step). If deleting the staging export fails, the run still succeeds and shows a warning. Each run uses an isolated gcloud configuration inside its work directory, removed afterwards; the service-account key is deleted right after login.

Image size: MongoDB tools plus the Google Cloud CLI add about 1.1 GB and rclone about 70 MB, so the image is roughly 1.9 GB.

The image bundles the official MySQL 8.4 client and the PostgreSQL 18 client. `pg_dump` must be at least as new as the server it backs up; otherwise the run fails early and tells you which version to build with (`docker build --build-arg PG_MAJOR=<n> .`). Backups are not encrypted yet. Restoring is done by hand for now; see [dev/README.md](dev/README.md).

## Google Drive destination

Backups can also be copied to a folder in Google Drive (through [rclone](https://rclone.org), bundled in the image, pinned to v1.76.0 and checksum-verified at build time). A job with a local and a Drive destination stores each file locally first, then uploads it, then verifies the Drive copy's size and MD5. A Drive failure never affects the local copy: the run ends as `partial`, and **Retry failed destinations** on the run page uploads again from the stored local copy (after checking its SHA-256). When the retry succeeds the original run becomes `success`.

### Setup

1. **OAuth client (recommended).** In the [Google Cloud Console](https://console.cloud.google.com/) create or pick a project, enable the **Google Drive API**, and create an OAuth client ID of type **Desktop app**. Without your own client, rclone's shared client is used, which is heavily rate limited.
2. **Publish the consent screen.** Under *OAuth consent screen*, set the publishing status to **In production**. Apps left in *Testing* get refresh tokens that expire after 7 days, and backups then start failing with "authorization expired". An unverified app is fine for personal use (Google shows a warning the first time you sign in).
3. **Create a token.** In **Destinations**, choose **Google Drive**, fill in the folder, scope and (optionally) client ID and secret. The form shows the exact command to run on a computer with a browser and rclone installed, for example:

   ```bash
   rclone authorize "drive" "eyJzY29wZSI6ImRyaXZlLmZpbGUifQ"
   ```

   The argument is base64url-encoded JSON of the scope (and client ID/secret), so the token matches the form. Sign in, then paste everything rclone prints between `--->` and `<---End paste` into **Token** (or upload it as a file).
4. **Save and test** checks three things: the folder can be listed (it is created if missing), free space (not reported for some shared drives, which is not an error), and a write probe that uploads a tiny file, reads its MD5 back and deletes it permanently.

**Scopes.** `drive.file` (default, recommended) lets rclone see only files and folders it created; a folder you created by hand in the Drive web UI is invisible to it, so rclone creates its own folder of the same name. `drive` grants full access to your whole Drive. For a shared drive, enter its ID (the last part of its URL).

The token, refresh token and client secret are encrypted with the master key, never returned by the API and never put on a command line: every rclone call gets a private `0600` config file in a `0700` directory under `/tmp` (a tmpfs), removed right afterwards. When rclone refreshes the access token, the new token is saved back (calls for one Drive destination run one at a time so concurrent jobs cannot overwrite each other's refreshed token; the price is that two jobs never upload to the same Drive destination in parallel). If Google rejects the token (revoked, expired, wrong client), the destination is marked **Reconnect needed**, a banner appears on the dashboard and the Destinations page, and runs skip that destination until you paste a new token.

### Known limits

- **Daily upload quota.** Google Drive allows about 750 GB of uploads per user per day. Beyond that uploads fail with `quota_exceeded` until the quota resets; retry the run later.
- **Duplicate names.** Drive allows several files with the same name in one folder. The app refuses to upload when a file with the target name already exists (it never overwrites and never creates a twin); backup names contain a timestamp, so this only happens if something else wrote there.
- **Trash.** Backups deleted by retention go to the Drive trash unless **Delete permanently** is on. Trashed files still count against your storage quota until the trash is emptied (Drive empties it after 30 days).
- **Storage quota.** A full Drive fails uploads with `quota_exceeded`; the local copy is kept.

Errors are classified on the run page as `auth_expired`, `quota_exceeded`, `rate_limited`, `folder_not_found`, `network`, `verify_mismatch` or `other`.

## Schedule your backups

Edit a job and pick a preset (hourly, daily, weekly) or type a 5-field cron expression
(`minute hour day-of-month month day-of-week`, `0` or `7` = Sunday). The dialog shows the next
five run times before you save; the jobs list shows the next run and a schedule badge.

- **Time zones.** Every job has its own IANA time zone (default: Settings > Default time zone).
  `0 3 * * *` in `Europe/Berlin` runs at 03:00 Berlin time all year. The container's `TZ` does
  not change when jobs run.
- **Daylight saving time.** A local time that does not exist (spring forward, e.g. 02:30 on the
  last Sunday of March in Europe) runs once, an hour later by the wall clock (03:30). A local time
  that happens twice (fall back) runs for both occurrences, so a daily 02:30 job runs twice that
  night. Pick a time outside 01:00-03:00 if that matters.
- **Limits.** At most one run every 5 minutes. Setting both day-of-month and day-of-week is
  rejected (cron would run when *either* matches; that is easy to get wrong).
- **Overlap.** If the previous run of a job is still active when the next one is due, the new
  one is skipped and shown as "skipped" on the dashboard.
- **Restart.** If the container was down when a run was due and comes back within the missed-run
  grace (Settings, default 1 hour), exactly one catch-up run starts. Older misses are logged
  ("outside the grace window") and skipped.
- **One scheduler.** Only one process may schedule per data directory (lock file
  `/data/scheduler.lock`). A second container on the same `./data` serves the UI but runs nothing
  and reports `"state": "not_owner"` in `/healthz` (HTTP 503). The image runs exactly one uvicorn
  worker and refuses to start with `--workers` > 1.

## Retention

Set **Keep last** (runs) and/or **Keep days** on each destination. Retention works per job and
destination: a backup run is deleted only when it is outside the newest *keep last* successful
runs **and** older than *keep days* (when only one is set, that one decides; with neither,
everything is kept). The newest successful backup of each job is always kept. Failed runs and
destinations that failed in a partial run never count toward *keep last*.

Retention runs after every finished run, daily at the housekeeping time and on demand
(**Run retention now**). **Preview what would be deleted** shows the runs, files and bytes first,
and saving a policy that would delete something asks for confirmation. Only files the app recorded
are deleted (with their `.manifest.json`); other files in the folder are never touched. A file that
cannot be deleted stays recorded and is retried next time; repeated failures send a
`retention_failed` notification. Every pass is in the audit log.

Daily housekeeping (Settings > Housekeeping time) also removes run logs older than *Keep run logs*,
run history older than *Keep run history* (runs whose backups still exist are kept), leftover temp
directories, and warns when a local destination or the data volume falls below the low-disk
threshold (at most once a day per volume).

## Notifications and heartbeat pings

Add channels under **Notifications**: Slack (incoming webhook URL), email (SMTP with STARTTLS,
SSL/TLS or, on trusted networks only, none) or a generic webhook. **Send test** delivers a test
message and shows the result; the events table lists every delivery with its status. Webhook URLs,
SMTP passwords, headers and the HMAC secret are stored encrypted and never shown again.

Per job choose **Notify**: *On failure* (failed, partial, interrupted), *Always* or *Never*, plus
**Notify on recovery** (one message for the first success after a failure) and the channels.
Overdue warnings (no success within `max(grace, interval x overdue %)` after a scheduled time; one
per episode) also go to the job's channels. System warnings (Google Drive authorization expired,
low disk, retention failing) go to every enabled channel.

Delivery runs in the background and never fails or delays a backup: up to 3 attempts with backoff,
at most 20 messages per channel per hour (the rest are summarized in one message later).

Webhook body (`Content-Type: application/json`):

```json
{"event": "run_finished", "status": "failed", "title": "...", "timestamp": "2026-11-23T03:00:05Z",
 "job": {"id": "...", "name": "nightly"},
 "run": {"id": "...", "status": "failed", "trigger": "schedule", "scheduled_for": "...",
         "started_at": "...", "finished_at": "...", "duration_seconds": 5,
         "artifacts": {"count": 0, "total_bytes": 0},
         "destinations": [{"name": "local", "type": "local", "status": "failed", "error": "..."}],
         "error": "..."},
 "url": "https://backup.example.com/runs/..."}
```

`event` is one of `run_finished`, `recovery`, `overdue`, `auth_expired`, `retention_failed`,
`low_disk`, `test`. With an HMAC secret, `X-Signature-256: sha256=<hex>` is the HMAC-SHA256 of the
raw body; verify it before trusting the message.

**Heartbeat (dead-man's switch).** Give a job a Healthchecks-style ping URL. The app calls
`<url>/start` when a run starts, `<url>` on success and `<url>/fail` on failure, partial or
interrupted runs (best effort, 5 s timeout). If the whole service stops, the ping service alerts
you. The URL is a secret: it is never returned by the API or written to logs.

**Outbound safety.** Targets are resolved and checked before every request: loopback and
link-local addresses (including `169.254.169.254`) are always refused, private networks unless
`ALLOW_PRIVATE_NOTIFICATION_TARGETS=true`. Redirects are not followed; timeouts are 10 s.

## Running unattended checklist

- Set **Settings > Public base URL** so messages link to the run.
- Add at least one notification channel, **Send test**, and select it on every job.
- Give important jobs a heartbeat URL (e.g. healthchecks.io) and set the check's period to the
  job's schedule plus some grace. Verify: **Run now** shows a start and a success ping in the check's
  log; stopping the container makes the check go late and alert you.
- Set retention on every destination and look at the preview.
- Check `/healthz`: `"scheduler": {"state": "running", "owner": true}`. Docker's healthcheck marks
  the container unhealthy when the scheduler stops ticking or another instance owns it.
- Graceful shutdown: `docker compose stop` gives running backups 30 s (`stop_grace_period`); they
  are marked `interrupted`, notified, and their temp files removed. After a restart, a missed
  scheduled run within the grace window runs once.
- Keep `app_key.txt` backed up; without it the stored credentials (including channel secrets) must
  be re-entered.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `APP_SECRET_KEY` / `APP_SECRET_KEY_FILE` | required | Master key (at least 32 characters). Set one, not both |
| `DATA_DIR` | `/data` | SQLite database, run logs (`logs/`) and temporary dump files (`work/`) |
| `BACKUP_DIR` | `/backups` | Local backup target |
| `MAX_CONCURRENT_RUNS` | `2` | Runs executing at once; extra runs wait as "queued" |
| `SESSION_TTL_HOURS` | `12` | Session lifetime |
| `LOG_LEVEL` | `INFO` | Log level |
| `COOKIE_SECURE` | `true` (compose: `false`) | `Secure` flag on cookies; set `true` behind HTTPS |
| `ENABLE_DOCS` | `false` | Serve OpenAPI docs at `/api/docs` |
| `LOGIN_MAX_ATTEMPTS` / `LOGIN_LOCKOUT_SECONDS` | `5` / `900` | Login rate limiting |
| `TZ` | `UTC` | Container clock and log time zone (jobs use their own time zone) |
| `PUBLIC_BASE_URL` | empty | Initial value of Settings > Public base URL (links in notifications) |
| `ALLOW_PRIVATE_NOTIFICATION_TARGETS` | `false` | Allow webhook, Slack, SMTP and ping targets on private networks |

## Development

Requirements: Python 3.12, Node 22+, Docker (for image builds).

```bash
# Backend on :8080 (creates backend/.venv; data in ./.dev)
make dev-backend

# Frontend on :5173, proxying /api to :8080
corepack enable
cd frontend && yarn install && yarn dev
```

Other targets: `make test` / `make test-unit` (unit tests), `make test-integration` (real databases via Testcontainers; needs Docker and the MySQL, PostgreSQL and MongoDB client tools on PATH, otherwise those tests are skipped), `make test-firestore-fake` (the Firestore flow against a fake `gcloud`; also part of `make test-unit`), `make test-integration-docker` (the same, run inside the application image, which has the client tools), `make dev-dbs-up` / `make dev-dbs-down` (sample databases, Mailpit and an HTTP echo server), `make up-dev` (the app on the dev network), `make dev-echo-logs`, `make smoke-tz`, `make lint` (ruff, mypy, eslint, tsc), `make build`, `make up`, `make down`.

The frontend uses Yarn 4 via Corepack only; do not use npm or pnpm.

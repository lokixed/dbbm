# DBBackup — Remaining Work

Last updated: 23 November 2026

This file tracks everything left after milestone 6. Save it as `docs/TODO.md` in the repo.

When you are ready to start a milestone, ask for its Copilot prompt (e.g. "write the milestone 7 prompt") and paste in the final summary Copilot gave for the previous milestone, so the prompt matches your actual code.

---

## Status overview

| # | Milestone | Status |
|---|---|---|
| 1 | Skeleton: auth, React shell, Docker | Prompt written |
| 2 | Sources and local destinations, encrypted credentials | Prompt written |
| 3 | PostgreSQL and MySQL dumps, local storage, Run now, live logs | Prompt written |
| 4 | MongoDB and Firestore drivers | Prompt written |
| 5 | Google Drive via rclone, verification, retry | Prompt written |
| 6 | Scheduler, retention, notifications, heartbeat pings | Prompt written |
| 7 | Backups browser, download, restore | **Nearly done**; left are signed download URLs, per-collection restore, a general tool-version check and the manual Firestore check on a real project |
| 8 | Backup encryption (`age`) | **Done** (not yet committed) |
| 9 | Hardening and 1.0 release | **TODO** |

Tick off milestones 1 to 6 here as you finish and commit them:

- [ ] Milestone 1 working and committed
- [ ] Milestone 2 working and committed
- [ ] Milestone 3 working and committed
- [ ] Milestone 4 working and committed
- [ ] Milestone 5 working and committed
- [ ] Milestone 6 working and committed

---

## Before starting milestone 7: update the docs

Copilot reports deviations from `DESIGN.md` at the end of each milestone. Apply them before moving on so later prompts and Copilot's own reading of the doc stay consistent.

- [ ] Section 6 (data model): add the tables and columns added in milestones 3 to 6 (`jobs` timezone/notify fields, `job_destinations`, `job_notification_channels`, `notification_events`, `app_settings`, `runs.parent_run_id` and `runs.scheduled_for`, `artifacts.md5`, destination `auth_status`)
- [ ] Section 7 (run lifecycle): note that retry re-uploads from a stored artifact and that heartbeat pings fire at run start and end
- [ ] Section 10 (scheduling): scheduler lock file in `/data`, catch-up on startup within the grace window, minimum interval of 5 minutes
- [ ] Section 11 (retention): retention counts runs per destination, never deletes the last successful run, and only touches tracked artifacts
- [ ] Section 16 (observability): heartbeat ping is **per job**, not global, and the ping URL is stored as an encrypted secret
- [ ] Section 17 (Docker): final list of tools installed in the image and the final environment variable list
- [ ] Section 19 (milestones): replace milestones 7 and 8 with the three milestones in this file
- [ ] `.github/copilot-instructions.md`: update the "Current scope" section for the next milestone

---

## Milestone 7 — Backups browser, download and restore

**Goal:** a full round trip works. Back up a database, restore the artifact into a scratch database, and the data matches.

**Depends on:** milestones 3 to 6 (artifacts, local and Drive storage `get`, checksums).

### Download
- [ ] Consider short-lived signed download URLs so large downloads work with browsers and reverse proxies

### Restore engine (all four drivers implement `restore()`)
- [ ] Preflight: the target is reachable, the tool version is compatible, there is enough disk space for temporary files, and the checksum verifies before anything is touched (done: reachable, tool versions logged, checksum verified before the restore, disk space checked before a Drive download; a general tool-version compatibility check is not built, only the PostgreSQL client/server case is handled)

### Restore wizard (UI)
- [ ] Step 3: options (drop and recreate, specific collections or tables where supported) with plain-language warnings (`drop` is done; per-collection selection is not) (`drop` is done for MongoDB, MySQL and PostgreSQL and hidden for Firestore; per-collection or per-table selection is not built)

### Tests
- [ ] Firestore restore tests with the fake `gcloud` executable, plus a manual end-to-end check on the throwaway project (the fake `gcloud` tests are done in `tests/test_restore_firestore.py`; the manual end-to-end check on the throwaway project is still TODO)

---

## Milestone 9 — Hardening and 1.0 release

**Goal:** safe to rely on in production, with documentation and an automated release process.

### Security review
- [ ] Audit log page in the UI (read-only, filterable) and retention for audit entries
- [ ] **Master key rotation command** that re-encrypts every stored secret, with a dry run and a safe rollback path
- [ ] Review login rate limiting and lockout, session expiry and cookie flags, CSRF coverage on every state-changing route, and clickjacking and security headers (CSP, `X-Frame-Options`, referrer policy)
- [ ] Make sure `/api/docs` is disabled by default in production
- [ ] Password change flow for the admin and a documented recovery path if the admin password is lost
- [ ] Dependency and image scanning in CI: `pip-audit`, `yarn npm audit`, and an image scanner such as Trivy; fail the build on high severity findings
- [ ] Secret scanning (for example gitleaks) in CI
- [ ] Re-test that no secret appears in logs, API responses, audit entries, manifests, notification payloads, process arguments or image layers
- [ ] Review container hardening: non-root, read-only root filesystem, dropped capabilities (`cap_drop: ALL`), `no-new-privileges`, and resource limits

### Reliability
- [ ] Failure-injection test suite: kill the container mid-backup and mid-restore, fill the disk, break the Drive token, drop the database connection, corrupt a stored file, clock changes, and a very large database
- [ ] Load and endurance test: many jobs on schedules over several days, watching memory, open files, and leftover work directories
- [ ] Graceful shutdown: running jobs are marked `interrupted`, child processes are killed, and temp files are removed
- [ ] Review SQLite behavior: WAL checkpointing, busy timeouts, and a safe procedure for backing up the tool's own database

### Protecting the tool itself
- [ ] Document and test how to back up and restore the tool's own data (`/data`: SQLite database and settings) and the **master key**, since losing either means re-entering every credential
- [ ] Document the disaster recovery drill: new server, restore `/data` and the master key, confirm schedules and destinations work
- [ ] Add an optional built-in export of the configuration without secrets, for rebuilding after a loss

### Upgrades and releases
- [ ] Versioning (semantic versions), changelog, and tested database migration on upgrade (including a downgrade note)
- [ ] CI release workflow: build a multi-architecture image (amd64 and arm64), tag by version, publish to a registry, attach an SBOM, and sign the image if desired
- [ ] Healthcheck and startup behavior documented for orchestrators (compose, Portainer)
- [ ] Version shown in the UI, with an optional "update available" hint

### Documentation
- [ ] README: overview, screenshots, quick start, upgrade procedure
- [ ] Setup guides: each database engine (least-privilege backup and restore users), Firestore (bucket, roles, lifecycle rule, costs), Google Drive (OAuth client, publishing the consent screen, scope choice), reverse proxy with HTTPS (Caddy and Traefik examples)
- [ ] Operations guide: monitoring, alerts, retention planning, disk sizing, restore drills
- [ ] Troubleshooting guide: the most common errors and their fixes
- [ ] Security notes: threat model, what the tool protects against, and what it does not
- [ ] CONTRIBUTING and LICENSE

### Acceptance criteria
- [ ] A fresh install on a clean server works from the README alone
- [ ] CI is green with security scans, and a tagged release produces a published image
- [ ] The master key rotation and the disaster recovery drill have both been performed successfully
- [ ] The failure-injection suite passes

---

## Not planned for 1.0 (backlog)

- [ ] In-app Google Drive OAuth ("Connect Google Drive" button) instead of pasting an rclone token
- [ ] Multi-user roles, SSO, and API tokens
- [ ] Scheduled automatic restore tests (restore into a scratch container and run a sanity query)
- [ ] Prometheus metrics endpoint and a Grafana dashboard
- [ ] More storage targets: S3-compatible, SFTP, Azure Blob
- [ ] More database engines: MariaDB profile, SQLite, Redis, SQL Server
- [ ] Physical and incremental backups, WAL or binlog archiving, point-in-time recovery
- [ ] Weekly summary email
- [ ] Split the scheduler and workers into separate containers for multi-node setups

---

## Working method (reminder)

1. Commit the previous milestone, then create a branch for the next one.
2. Open a new Copilot Chat in **Agent mode** and paste that milestone's prompt.
3. Ask Copilot to propose the plan and file tree first. Approve or correct it before it writes code.
4. Run the tests and the manual checks listed for the milestone.
5. Update `docs/DESIGN.md` and this file, then commit.

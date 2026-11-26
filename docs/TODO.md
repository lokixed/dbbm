# DBBM (Database Backup & Management) - Remaining Work

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
| 7 | Backups browser, download, restore | Open: manual Firestore check |
| 9 | Hardening and 1.0 release | Code and docs done (1.0.0-rc.1); drills, failure-injection suite, load test and first release run are open |

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

## Milestone 7 - Backups browser, download and restore

- [ ] Manual end-to-end Firestore backup and restore check on the throwaway project (the fake Google REST tests are done; the driver calls the REST APIs directly, so run both flows once against a real project)
- [ ] MySQL restore has no tool-version compatibility check (PostgreSQL and MongoDB do)

---

## Milestone 9 - Hardening and 1.0 release

The code and docs are done (version 1.0.0-rc.1). What is left:

### Security review
- [ ] Check the Content-Security-Policy in a real browser against the production build
- [ ] Re-test that no secret appears in logs, API responses, audit entries, manifests, notification payloads, process arguments or image layers
- [ ] Run the CI scans on GitHub. `pip-audit` has no severity filter, so it fails on any known vulnerability

### Reliability
- [ ] Failure-injection test suite: kill the container mid-backup and mid-restore, fill the disk, break the Drive token, drop the database connection, corrupt a stored file, clock changes, and a very large database
- [ ] Load and endurance test: many jobs on schedules over several days, watching memory, open files, and leftover work directories

### Upgrades and releases
- [ ] A repository admin turns on GitHub Pages (Settings > Pages > Source: GitHub Actions) so the `pages` job can publish `site/`
- [ ] Cut the first stable release by running the CI/CD workflow on `main` with version `1.0.0` (releases are automatic after that; amd64 only, arm64 needs another MySQL client, see DESIGN.md section 17)
- [ ] Optional "update available" hint in the UI

### Documentation
- [ ] Screenshots in the README
- [ ] Firestore lifecycle rule and cost notes (currently brief)
- [ ] LICENSE (the licence is your decision)

### Acceptance criteria
- [ ] A fresh install on a clean server works from the README alone
- [ ] CI is green with security scans, and a tagged release produces a published image
- [ ] The master key rotation and the disaster recovery drill have both been performed successfully
- [ ] The failure-injection suite passes

---

## Not planned for 1.0 (backlog)

- [x] In-app Google Drive OAuth ("Connect Google Drive" button) instead of pasting an rclone token, through the GitHub Pages relay (docs/DESIGN.md section 9, "Google Drive connect flow")
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

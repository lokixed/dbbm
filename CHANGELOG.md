# Changelog

Format: [Keep a Changelog](https://keepachangelog.com). Versions follow [semver](https://semver.org).

## [1.0.0-rc.1]

First release candidate. Not yet declared 1.0: the manual acceptance checks in `docs/TODO.md`
(Firestore restore on a real project, master key rotation and disaster recovery drills, the
failure-injection suite, CI release run) are still open.

### Added
- One-use download links (valid 60 seconds) for backups.
- Restore only selected collections (MongoDB) or tables (PostgreSQL).
- Restore checks the installed tool versions against the target server (PostgreSQL refuses an older
  `pg_restore`; MongoDB warns).
- Audit log page with filters, and an audit retention setting (default 365 days).
- Change password in Settings; `python -m app.cli reset-password` for a lost password.
- `python -m app.cli rotate-key` (dry run, rollback copy), `backup-db` and `export-config`.
- Security headers (CSP, frame, referrer, nosniff, HSTS behind HTTPS) on every response.
- Version in the UI, `/healthz` and `/api/version`.
- CI: `pip-audit`, `yarn npm audit`, gitleaks, Trivy. Tag-triggered multi-architecture release
  workflow with SBOM, provenance and a keyless signature.
- Container hardening in `docker-compose.yml` (`cap_drop`, `no-new-privileges`, limits).
- Operations and security guides.

### Changed
- The SQLite write-ahead log is checkpointed on clean shutdown.
- Firestore backups and restores call Google's REST APIs (Firestore and Cloud Storage) instead of
  the Google Cloud CLI, which is no longer in the image. The service-account key is no longer
  written to disk.
- `age`, `mongodump` and `mongorestore` are built from source with a current Go toolchain, and pip
  is removed from the final image.

### Upgrade notes
- No schema migration in this release. New setting `audit_retention_days` defaults to 365.
- To go back to an older version, restore the `app.db` copy made before upgrading.

# Changelog

Format: [Keep a Changelog](https://keepachangelog.com). Versions follow [semver](https://semver.org).

## [Unreleased]

### Added
- **Connect Google Drive** in the destination form: sign in to Google in a new tab instead of running
  `rclone authorize` and pasting a token. It needs DBBM on https and your own OAuth client of type
  *Web application* with `https://<your DBBM address>/api/oauth/google/callback` as a redirect URI.
  Pasting a token still works, also for installs without https.
- The version is calculated from Conventional Commits when the CI/CD run passes on `main`: a breaking change is
  a major, `feat` a minor, anything else a patch. The release job writes it into
  `backend/app/version.py`, `backend/pyproject.toml` and `frontend/package.json`, rotates this
  changelog, tags `vX.Y.Z` and publishes the image and the GitHub release.
- The version is shown on every page of the web UI, placed in the page layout (sidebar footer, and
  the sign-in, setup and Docs pages), and `/healthz` reports it in every response.
- The version in the web UI (sidebar footer, sign-in and setup cards) and on the landing page links to
  that version's release notes on GitHub.
- The in-app About page is now the Docs page at `/docs`; `/about` redirects to it. The sign-in and
  setup pages end with a footer line inside the card: a Docs link on the left, `DBBM vX.Y.Z` on the right.
- A landing page for the image (`site/`), published to GitHub Pages by the CI/CD workflow.
- CI runs only the jobs whose files changed (backend, frontend, image, site, Docker Hub page), and
  only changes to what goes into the image publish a release.

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
- CI: `pip-audit`, `yarn npm audit`, gitleaks, Trivy. Tag-triggered release
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

### Release process
- The release job is part of the CI/CD workflow: when the CI/CD run passes on `main` it publishes the image to
  Docker Hub and GHCR (`latest`, `edge`, `sha-<commit>`, and the version tags for a new release) and creates
  the GitHub release. The Docker Hub page shows `docs/DOCKERHUB.md`.

### Known limits
- The image is for linux/amd64 only. MySQL's apt repository has no arm64 packages for Debian.

### Upgrade notes
- No schema migration in this release. New setting `audit_retention_days` defaults to 365.
- To go back to an older version, restore the `app.db` copy made before upgrading.

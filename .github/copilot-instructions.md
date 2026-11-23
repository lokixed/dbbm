# Project: DBBM (Database Backup & Management)

Self-hosted Docker app that backs up MySQL, PostgreSQL, MongoDB and Firestore to a
local volume and Google Drive, with a web UI. The full design is in docs/DESIGN.md.
It is the source of truth: follow it, and if you must deviate, say so and explain why.

## Stack
- Backend: Python 3.12, FastAPI, SQLModel, Alembic, SQLite (WAL), APScheduler
- Frontend: React + Vite + TypeScript, TanStack Query, React Router, Tailwind, shadcn/ui
- Node package manager: Yarn 4 (Berry) via Corepack. Never use npm or pnpm.
- Single Docker image, single uvicorn worker

## General rules
- Build only the milestone I ask for. Do not implement later milestones.
- Never build shell commands by string concatenation. Use argument lists with
  asyncio.create_subprocess_exec / subprocess, never shell=True.
- Never log or return secrets (passwords, tokens, connection strings). Redact them.
- Secrets are encrypted at rest with the master key from APP_SECRET_KEY or
  APP_SECRET_KEY_FILE. The master key is never stored in the database.
- Type hints everywhere. Backend must pass ruff and mypy. Frontend must pass eslint and tsc.
- Write tests with every feature (pytest for backend). Keep functions small.
- All timestamps in UTC. All primary keys are UUIDs.
- Ask me before adding a dependency that is not in docs/DESIGN.md.
- After each task, summarize what you changed and how to run it.

## Frontend package rules
- Use only yarn commands (yarn add, yarn install, yarn build, yarn lint).
- Commit yarn.lock. Never create package-lock.json or pnpm-lock.yaml.
- package.json must pin "packageManager": "yarn@4.x.x" (exact version).
- .yarnrc.yml must set nodeLinker: node-modules.
- In CI and Docker use: yarn install --immutable

## Current scope (temporary)
- The LOCAL and Google Drive (via rclone) storage destinations are in scope.
- rclone may be installed in the Docker image (pinned version, checksum verified).
- Build only the features of the milestone I ask for; do not implement later milestones.
- Do NOT implement other remote storage (S3, SFTP, Azure, ...).
- Keep the Storage interface generic so more destinations can be added without refactoring.

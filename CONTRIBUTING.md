# Contributing

Read [docs/DESIGN.md](docs/DESIGN.md) first: it is the source of truth. If a change deviates from
it, update the design in the same pull request and say why.

## Set up

Python 3.12, Node 22 with Corepack, Docker. See "Development" in the [README](README.md).

```bash
make install-backend
corepack enable && cd frontend && yarn install
```

Use **yarn only** for the frontend (Yarn 4 via Corepack); never npm or pnpm. Commit `yarn.lock`.

## Before you open a pull request

```bash
cd backend && ruff check . && ruff format --check . && mypy && pytest -m "not integration"
cd frontend && yarn lint && yarn typecheck && yarn build
```

- Add tests with every feature (pytest for the backend).
- Type hints everywhere; keep functions small.
- Never build shell commands from strings. Use argument lists, never `shell=True`.
- Never log or return secrets. Redact them.
- Timestamps are UTC and primary keys are UUIDs.
- Ask before adding a dependency that is not in DESIGN.md.
- Update the docs in the same change as the behavior they describe.

## What CI/CD runs

One workflow, `.github/workflows/ci-cd.yml` ("CI/CD"), runs as a single chain:

```
changes -> security -> backend + frontend -> docker -> release -> pages + hub
```

The first job (`changes`) lists the files a push or pull request touched. The checks and the image
build run only when their files changed and are skipped otherwise. Release, pages and hub run on
every push to `main`, whatever changed. A failure stops every stage after it. Pull requests run the
checks but never publish.

| Changed files | Runs |
|---|---|
| `backend/`, `scripts/` | backend tests, image build and tests, security checks, then publish |
| `frontend/` | frontend checks, image build and tests, security checks, then publish |
| `Dockerfile`, `.dockerignore` | image build and tests, security checks, then publish |
| anything else (`site/`, `docs/`, `README.md`, ...) | publish only |
| `.github/workflows/ci-cd.yml` | everything |

"Publish" is release (image tags and, when the commits call for it, a new version), the GitHub Pages
deploy and the Docker Hub description. A manual run, a new branch or a force push runs everything.
A failed GitHub Pages deploy or Docker Hub description update fails the run.

## Commit messages

Use [Conventional Commits](https://www.conventionalcommits.org): `feat: ...`, `fix: ...`, `docs: ...`,
`chore: ...`. The release version is calculated from them, so the type matters:

| Commit | Next version |
|---|---|
| `feat!: ...`, `fix(api)!: ...`, or a `BREAKING CHANGE:` line in the body | major |
| `feat: ...` / `feat(scope): ...` | minor |
| anything else (`fix`, `docs`, `chore`, ...) | patch |

## Releases

Releases are automatic and nobody types a version number. When every CI job passes on `main`, the
`release` job in `.github/workflows/ci-cd.yml` publishes the image to Docker Hub and GHCR.

- Every green push that changes the image updates the `latest`, `edge` and `sha-<commit>` tags.
- It then calculates the next version with `scripts/release.py`: the latest stable `vX.Y.Z` tag plus
  the bump the commits since that tag call for. If nothing qualifies (no new commits), only the
  `latest`, `edge` and `sha` tags change.
- A release writes the version into `backend/app/version.py`, `backend/pyproject.toml` and
  `frontend/package.json`, moves the notes under `## [Unreleased]` in `CHANGELOG.md` to a dated
  section (or writes them from the commit subjects if that section is empty), builds and smoke-tests
  the image, pushes the image tags (`X.Y.Z` and `X.Y`, on top of `latest`, `edge` and `sha-<commit>`), commits `chore(release): vX.Y.Z`,
  tags it and creates the GitHub release.
- The job refuses instead of guessing: a malformed version, a tag that already exists, or a version
  that is not greater than the latest one stops the run with an error.
- **The first stable release is manual.** No stable tag exists yet, so run the CI/CD workflow from the
  Actions tab on `main` with `version` set (for example `1.0.0`). From then on it is automatic. The
  same form can force a `bump` (major, minor or patch) instead of reading the commit log.
- The release commit is pushed to `main` by the workflow, so run `git pull --rebase` before you
  push. If main moved while a run was in progress, that run skips the release and the newer run
  publishes it.
- Write release notes by hand under `## [Unreleased]` in `CHANGELOG.md` when the commit subjects are
  not enough.
- The landing page is the static `site/index.html`, deployed to GitHub Pages by the `pages` job of the
  same workflow. Keep its tags and compose example in step with `docs/DOCKERHUB.md`.
- Do not create version tags by hand. The Docker Hub page shows `docs/DOCKERHUB.md`; it is updated
  on every release job.

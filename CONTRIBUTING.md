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

## Releases

Releases are automatic: when every CI job passes on `main`, the `release` job in
`.github/workflows/ci.yml` publishes the image to Docker Hub and GHCR.

- Every green push updates the `edge` and `sha-<commit>` tags.
- To cut a release, update `backend/app/version.py`, `backend/pyproject.toml` and
  `frontend/package.json`, add a section for the version to `CHANGELOG.md`, and merge to `main`. When
  CI passes, the job tags `vX.Y.Z`, publishes the version tags and creates the GitHub release. A
  stable version (no `-rc.1` style suffix) also moves `latest` and `X.Y`.
- Do not push version tags by hand. A version that already has a tag is not released again.
- The Docker Hub page shows `docs/DOCKERHUB.md`; it is updated on every release job.

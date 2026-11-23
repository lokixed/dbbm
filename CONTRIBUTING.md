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

1. Update `backend/app/version.py`, `backend/pyproject.toml` and `frontend/package.json`.
2. Add a section to `CHANGELOG.md`.
3. Tag `vX.Y.Z` and push the tag. The release workflow builds and publishes the image.

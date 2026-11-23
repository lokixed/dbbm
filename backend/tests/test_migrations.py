import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from sqlmodel import SQLModel

from app.core.db import get_engine
from app.core.migrations import BACKEND_DIR, run_migrations


def test_migration_creates_tables_and_matches_models() -> None:
    run_migrations()
    engine = get_engine()
    names = set(inspect(engine).get_table_names())
    expected = {"users", "audit_log", "sources", "destinations", "alembic_version"}
    assert expected | {"jobs", "job_destinations", "runs", "artifacts"} <= names

    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), SQLModel.metadata)
    assert diff == []


def test_migrations_are_idempotent() -> None:
    run_migrations()
    run_migrations()


def _alembic(action: str, revision: str) -> None:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    getattr(command, action)(config, revision)


def _sql(statement: str) -> list[tuple[object, ...]]:
    with get_engine().begin() as conn:
        result = conn.execute(text(statement))
        return [tuple(r) for r in result] if result.returns_rows else []


def _seed_m4_rows() -> None:
    now = "'2026-01-01 00:00:00'"
    _sql(
        "INSERT INTO sources (id, name, type, config, created_at, updated_at) "
        f"VALUES ('s1', 'src', 'mysql', '{{}}', {now}, {now})"
    )
    _sql(
        "INSERT INTO destinations (id, name, type, config, retention, created_at, updated_at) "
        f"VALUES ('d1', 'loc', 'local', '{{}}', '{{}}', {now}, {now})"
    )
    _sql(
        "INSERT INTO jobs (id, name, source_id, enabled, compress, timeout_seconds, "
        f"created_at, updated_at) VALUES ('j1', 'job', 's1', 1, 1, 60, {now}, {now})"
    )
    _sql("INSERT INTO job_destinations (job_id, destination_id) VALUES ('j1', 'd1')")
    _sql(
        "INSERT INTO runs (id, job_id, job_name, trigger, status, step_results, created_at) "
        f"VALUES ('r1', 'j1', 'job', 'manual', 'partial', '{{}}', {now})"
    )
    _sql(
        "INSERT INTO artifacts (id, run_id, destination_id, source_database, path, size_bytes, "
        f"sha256, compressed, created_at) VALUES ('a1', 'r1', 'd1', 'db', 'p', 1, 'x', 1, {now})"
    )


def test_0004_keeps_rows_and_downgrades() -> None:
    _alembic("upgrade", "0003")
    _seed_m4_rows()
    _alembic("upgrade", "head")

    assert _sql("SELECT id FROM artifacts") == [("a1",)]
    assert _sql("SELECT destination_id FROM job_destinations") == [("d1",)]
    assert _sql("SELECT auth_status, auth_checked_at FROM destinations") == [("unknown", None)]
    assert _sql("SELECT parent_run_id FROM runs") == [(None,)]
    assert _sql("PRAGMA foreign_keys") == [(1,)]
    with pytest.raises(IntegrityError):
        _sql("UPDATE destinations SET type = 'ftp'")
    with pytest.raises(IntegrityError):
        _sql("UPDATE destinations SET auth_status = 'bogus'")

    _sql("UPDATE destinations SET type = 'gdrive'")
    with pytest.raises(RuntimeError, match="Google Drive destination"):
        _alembic("downgrade", "0003")
    _sql("UPDATE destinations SET type = 'local'")
    _alembic("downgrade", "0003")
    assert _sql("SELECT id FROM artifacts") == [("a1",)]
    with pytest.raises(IntegrityError):
        _sql("UPDATE destinations SET type = 'gdrive'")
    _alembic("upgrade", "head")
    assert _sql("SELECT id FROM artifacts") == [("a1",)]

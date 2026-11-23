"""End-to-end backups against real MySQL and PostgreSQL servers (Testcontainers).

They need the client tools (mysqldump, mysql, pg_dump, pg_restore) on PATH and are skipped
without them; `make test-integration-docker` runs them inside the application image.
The restore helpers below exist for these tests only: the application has no restore feature.
"""

import gzip
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import psycopg
import pymysql
import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.drivers.base import ConnectionTestResult
from app.drivers.mysql import MySQLDriver
from app.drivers.postgres import PostgresDriver
from tests.conftest import api_post
from tests.integration.conftest import Endpoint
from tests.runs_helpers import make_destination, make_job, run_now

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not all(shutil.which(t) for t in ("mysqldump", "mysql", "pg_dump", "pg_restore")),
        reason="mysqldump, mysql, pg_dump and pg_restore must be installed",
    ),
]

MYSQL_ROOT_PW = "root-pw"
PG_ADMIN = ("admin", "admin-pw")
WRONG_PW = "definitely-the-wrong-pw"


# ---------- seeding ----------


def _mysql_root(ep: Endpoint, database: str | None = None) -> Any:
    return pymysql.connect(
        host=ep.host, port=ep.port, user="root", password=MYSQL_ROOT_PW, database=database,
        autocommit=True,
    )  # fmt: skip


@pytest.fixture(scope="module")
def mysql_seeded(mysql_server: Endpoint) -> dict[str, dict[str, int]]:
    """Creates tables with rows in `shop` and `blog`; returns the expected row counts."""
    plan = {
        "shop": {"customers": 25, "orders": 40},
        "blog": {"posts": 10},
    }
    with _mysql_root(mysql_server) as root, root.cursor() as cur:
        # SHOW_ROUTINE lets mysqldump read stored routine bodies; without it the dump fails loudly.
        cur.execute("GRANT SHOW_ROUTINE ON *.* TO 'backup'@'%'")
        cur.execute("GRANT SELECT, SHOW VIEW, TRIGGER, EVENT ON blog.* TO 'backup'@'%'")
        cur.execute("GRANT SELECT, SHOW VIEW, TRIGGER, EVENT ON shop.* TO 'backup'@'%'")
        for database, tables in plan.items():
            cur.execute(f"USE {database}")
            for table, rows in tables.items():
                cur.execute(f"DROP TABLE IF EXISTS {table}")
                cur.execute(
                    f"CREATE TABLE {table} (id INT PRIMARY KEY AUTO_INCREMENT, v VARCHAR(40))"
                )
                cur.executemany(
                    f"INSERT INTO {table} (v) VALUES (%s)", [(f"row-{i}",) for i in range(rows)]
                )
        cur.execute("USE shop")
        cur.execute("DROP PROCEDURE IF EXISTS hello")
        cur.execute("CREATE PROCEDURE hello() SELECT 1")
    return plan


def _pg_admin(ep: Endpoint, database: str) -> Any:
    return psycopg.connect(
        host=ep.host, port=ep.port, user=PG_ADMIN[0], password=PG_ADMIN[1], dbname=database,
        autocommit=True,
    )  # fmt: skip


@pytest.fixture(scope="module")
def postgres_seeded(postgres_server: Endpoint) -> dict[str, dict[str, int]]:
    plan = {"shop": {"customers": 25, "orders": 40}, "blog": {"posts": 10}}
    with _pg_admin(postgres_server, "shop") as conn:
        conn.execute("GRANT pg_read_all_data TO reader")
    for database, tables in plan.items():
        with _pg_admin(postgres_server, database) as conn:
            for table, rows in tables.items():
                conn.execute(f"DROP TABLE IF EXISTS {table}")
                conn.execute(f"CREATE TABLE {table} (id SERIAL PRIMARY KEY, v TEXT)")
                conn.execute(
                    f"INSERT INTO {table} (v) SELECT 'row-' || g FROM generate_series(1, {rows}) g"
                )
    return plan


# ---------- test-only restore helpers ----------


def mysql_restore(ep: Endpoint, artifact: Path, scratch: str) -> None:
    sql = (
        gzip.decompress(artifact.read_bytes())
        if artifact.suffix == ".gz"
        else artifact.read_bytes()
    )
    with _mysql_root(ep) as root, root.cursor() as cur:
        cur.execute(f"DROP DATABASE IF EXISTS {scratch}")
        cur.execute(f"CREATE DATABASE {scratch}")
    env = {**os.environ, "MYSQL_PWD": MYSQL_ROOT_PW}
    command = ["mysql", "-h", ep.host, "-P", str(ep.port), "-u", "root", scratch]
    done = subprocess.run(command, input=sql, env=env, capture_output=True)
    assert done.returncode == 0, done.stderr.decode()  # noqa: S603


def mysql_counts(ep: Endpoint, database: str) -> dict[str, int]:
    with _mysql_root(ep, database) as root, root.cursor() as cur:
        cur.execute("SHOW FULL TABLES WHERE Table_type = 'BASE TABLE'")
        tables = [row[0] for row in cur.fetchall()]
        counts = {}
        for table in tables:
            cur.execute(f"SELECT COUNT(*) FROM `{table}`")
            counts[table] = int(cur.fetchone()[0])
        return counts


def pg_restore(ep: Endpoint, artifact: Path, scratch: str) -> None:
    with _pg_admin(ep, "postgres") as conn:
        conn.execute(f"DROP DATABASE IF EXISTS {scratch}")
        conn.execute(f"CREATE DATABASE {scratch}")
    env = {**os.environ, "PGPASSWORD": PG_ADMIN[1]}
    command = ["pg_restore", "--no-owner", "-h", ep.host, "-p", str(ep.port), "-U", PG_ADMIN[0]]
    done = subprocess.run([*command, "-d", scratch, str(artifact)], env=env, capture_output=True)
    # A dump from a newer pg_dump emits `SET transaction_timeout`, which a PostgreSQL 16 server
    # rejects. pg_restore carries on and exits 1; that one error is expected here.
    errors = [
        line
        for line in done.stderr.decode().splitlines()
        if "error" in line.lower() and "transaction_timeout" not in line
    ]
    assert done.returncode in (0, 1) and not [e for e in errors if "errors ignored" not in e], (
        done.stderr.decode()
    )  # noqa: S603


def pg_counts(ep: Endpoint, database: str) -> dict[str, int]:
    with _pg_admin(ep, database) as conn:
        names = [
            r[0] for r in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'")
        ]
        return {n: int(conn.execute(f'SELECT COUNT(*) FROM "{n}"').fetchone()[0]) for n in names}


# ---------- helpers ----------


def create_source(
    client: TestClient, kind: str, ep: Endpoint, password: str, **config: Any
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "host": ep.host,
        "port": ep.port,
        "username": ep.username,
        "databases": ["shop", "blog"],
    }
    body = {
        "type": kind,
        "name": f"{kind}-src",
        "config": {**base, **config},
        "secrets": {"password": password},
    }
    response = api_post(client, "/api/sources", body)
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


def artifact_file(run: dict[str, Any], database: str) -> Path:
    (found,) = [a for a in run["artifacts"] if a["source_database"] == database]
    return Path(get_settings().backup_dir / "offsite" / str(found["path"]))


def all_text(client: TestClient, run: dict[str, Any]) -> str:
    log: str = client.get(f"/api/runs/{run['id']}/logs?format=text").text
    manifests = "".join(p.read_text() for p in (get_settings().backup_dir).rglob("*.manifest.json"))
    return log + json.dumps(run) + manifests


# ---------- round trips ----------


def test_mysql_backup_restores_with_matching_row_counts(
    authed: TestClient, mysql_server: Endpoint, mysql_seeded: dict[str, dict[str, int]]
) -> None:
    source = create_source(authed, "mysql", mysql_server, mysql_server.password)
    job = make_job(authed, source, [make_destination(authed, "offsite", "offsite")], "mysql-job")
    run = run_now(authed, job)
    assert run["status"] == "success", (run["error"], run["step_results"])
    assert sorted(a["source_database"] for a in run["artifacts"]) == ["blog", "shop"]
    assert all(a["path"].endswith(".sql.gz") for a in run["artifacts"])

    for database, expected in mysql_seeded.items():
        mysql_restore(mysql_server, artifact_file(run, database), f"scratch_{database}")
        assert mysql_counts(mysql_server, f"scratch_{database}") == expected
    with _mysql_root(mysql_server, "scratch_shop") as root, root.cursor() as cur:
        cur.execute("SHOW PROCEDURE STATUS WHERE Db = 'scratch_shop'")
        assert [row[1] for row in cur.fetchall()] == ["hello"]  # --routines worked
    assert mysql_server.password not in all_text(authed, run)


def test_postgres_backup_restores_with_matching_row_counts(
    authed: TestClient, postgres_server: Endpoint, postgres_seeded: dict[str, dict[str, int]]
) -> None:
    source = create_source(
        authed, "postgres", postgres_server, postgres_server.password, include_globals=True
    )
    job = make_job(authed, source, [make_destination(authed, "offsite", "offsite")], "pg-job")
    run = run_now(authed, job)
    assert run["status"] == "success", (run["error"], run["step_results"])
    databases = sorted(a["source_database"] for a in run["artifacts"])
    assert {"blog", "shop"} <= set(databases) and "postgres" not in databases
    # `reader` is not a superuser, so the globals dump may fail; that is a warning, never a failure.
    assert ("globals" in databases) or any(
        "Globals dump failed" in w for w in run["step_results"]["warnings"]
    )

    for database, expected in postgres_seeded.items():
        pg_restore(postgres_server, artifact_file(run, database), f"scratch_{database}")
        assert pg_counts(postgres_server, f"scratch_{database}") == expected
    assert postgres_server.password not in all_text(authed, run)


# ---------- wrong password ----------


def test_wrong_password_fails_the_run_without_artifacts(
    authed: TestClient, mysql_server: Endpoint, postgres_server: Endpoint
) -> None:
    for kind, ep in (("mysql", mysql_server), ("postgres", postgres_server)):
        source = create_source(authed, kind, ep, WRONG_PW)
        dest = make_destination(authed, f"d-{kind}", f"d-{kind}")
        run = run_now(authed, make_job(authed, source, [dest], f"bad-{kind}"))
        assert run["status"] == "failed" and run["artifacts"] == []
        assert run["error"] and WRONG_PW not in all_text(authed, run)
    assert not list(get_settings().backup_dir.rglob("*.*"))
    assert not get_settings().work_dir.exists() or not list(get_settings().work_dir.iterdir())


@pytest.mark.parametrize("kind", ["mysql", "postgres"])
def test_wrong_password_in_the_dump_tool_is_sanitized(
    authed: TestClient,
    mysql_server: Endpoint,
    postgres_server: Endpoint,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    """Skip the connection probe so the dump tool itself meets the wrong password."""
    driver = MySQLDriver if kind == "mysql" else PostgresDriver
    version = "8.0.36" if kind == "mysql" else "16.4"
    monkeypatch.setattr(
        driver,
        "_probe",
        lambda self: ConnectionTestResult(
            ok=True, message="ok", server_version=version, databases=["shop"]
        ),
    )
    ep = mysql_server if kind == "mysql" else postgres_server
    source = create_source(authed, kind, ep, WRONG_PW, databases=["shop"])
    dest = make_destination(authed, "offsite", "offsite")
    run = run_now(authed, make_job(authed, source, [dest], "bad-dump"))
    assert run["status"] == "failed" and run["artifacts"] == []
    assert "exited with code" in run["error"]
    assert WRONG_PW not in all_text(authed, run)
    assert not list(get_settings().backup_dir.rglob("*.*"))
    assert not get_settings().work_dir.exists() or not list(get_settings().work_dir.iterdir())

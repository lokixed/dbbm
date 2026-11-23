"""Back up and restore through the application's own engines, against real servers.

Each test seeds a database, backs it up with a job, restores that backup into a new database
through the restore API, and compares the two (row and document counts, a checksum of the data,
and the schema objects). Then it damages the copy and restores again with "replace existing".

They need mysqldump, mysql, pg_dump, pg_restore, mongodump and mongorestore on PATH and are
skipped without them; `make test-integration-docker` runs them inside the application image.
"""

import shutil
import time
from collections.abc import Iterator
from typing import Any

import psycopg
import pymysql
import pytest
from fastapi.testclient import TestClient
from pymongo import MongoClient

from app.api import backups as backups_api
from tests.conftest import api_post
from tests.integration.conftest import Endpoint
from tests.runs_helpers import make_destination, make_job, run_now, wait_final

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not all(
            shutil.which(t)
            for t in ("mysqldump", "mysql", "pg_dump", "pg_restore", "mongodump", "mongorestore")
        ),
        reason="the database client tools must be installed",
    ),
]

MYSQL_ROOT = ("root", "root-pw")
PG_ADMIN = ("admin", "admin-pw")
MONGO_ROOT = ("root", "root-pw")
SRC, DST = "rt_src", "rt_dst"


@pytest.fixture(autouse=True)
def reset_rate_limit() -> None:
    backups_api._restore_starts.clear()


# ---------- helpers ----------


def create_source(client: TestClient, body: dict[str, Any]) -> dict[str, Any]:
    response = api_post(client, "/api/sources", body)
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


def back_up(client: TestClient, source: dict[str, Any], database: str) -> dict[str, Any]:
    job = make_job(client, source, [make_destination(client)], f"{source['name']}-job")
    run = run_now(client, job)
    assert run["status"] == "success", (run["error"], run["step_results"])
    items = client.get(f"/api/backups?run_id={run['id']}").json()["items"]
    (backup,) = [i for i in items if i["source_database"] == database]
    found: dict[str, Any] = backup
    return found


def restore(
    client: TestClient, backup: dict[str, Any], target: dict[str, Any], **over: Any
) -> dict[str, Any]:
    """Restore into DST on `target` and wait for the run; retries while the target is busy."""
    body = {
        "target_source_id": target["id"],
        "target_database": DST,
        "confirm_database": DST,
        **over,
    }
    deadline = time.monotonic() + 15
    while True:
        response = api_post(client, f"/api/backups/{backup['id']}/restore", body)
        if response.status_code != 409 or time.monotonic() > deadline:
            break
        time.sleep(0.1)
    assert response.status_code == 201, response.text
    run = wait_final(client, response.json()["id"], timeout=120)
    assert run["status"] == "success", (run["error"], run["step_results"])
    return run


# ---------- MySQL ----------


def mysql_root(ep: Endpoint, database: str | None = None) -> Any:
    return pymysql.connect(
        host=ep.host, port=ep.port, user=MYSQL_ROOT[0], password=MYSQL_ROOT[1],
        database=database, autocommit=True,
    )  # fmt: skip


def mysql_fingerprint(ep: Endpoint, database: str) -> dict[str, Any]:
    with mysql_root(ep, database) as conn, conn.cursor() as cur:
        cur.execute("SHOW FULL TABLES WHERE Table_type = 'BASE TABLE'")
        tables = sorted(str(r[0]) for r in cur.fetchall())
        counts, checksums = {}, {}
        for table in tables:
            cur.execute(f"SELECT COUNT(*) FROM `{table}`")
            counts[table] = int(cur.fetchone()[0])
            cur.execute(f"CHECKSUM TABLE `{table}`")
            checksums[table] = int(cur.fetchone()[1])
        cur.execute(
            "SELECT COUNT(*) FROM information_schema.views WHERE table_schema = %s", (database,)
        )
        views = int(cur.fetchone()[0])
        cur.execute(
            "SELECT routine_name FROM information_schema.routines WHERE routine_schema = %s",
            (database,),
        )
        routines = sorted(str(r[0]) for r in cur.fetchall())
    return {"counts": counts, "checksums": checksums, "views": views, "routines": routines}


@pytest.fixture
def mysql_source(mysql_server: Endpoint) -> Iterator[dict[str, Any]]:
    with mysql_root(mysql_server) as conn, conn.cursor() as cur:
        cur.execute(f"DROP DATABASE IF EXISTS {SRC}")
        cur.execute(f"DROP DATABASE IF EXISTS {DST}")
        cur.execute(f"CREATE DATABASE {SRC}")
        cur.execute(f"USE {SRC}")
        cur.execute("CREATE TABLE customers (id INT PRIMARY KEY AUTO_INCREMENT, name VARCHAR(40))")
        cur.execute(
            "CREATE TABLE orders (id INT PRIMARY KEY AUTO_INCREMENT, customer_id INT, "
            "total DECIMAL(10,2), FOREIGN KEY (customer_id) REFERENCES customers(id))"
        )
        cur.executemany("INSERT INTO customers (name) VALUES (%s)", [(f"c{i}",) for i in range(40)])
        cur.executemany(
            "INSERT INTO orders (customer_id, total) VALUES (%s, %s)",
            [((i % 40) + 1, i * 1.25) for i in range(80)],
        )
        cur.execute("CREATE VIEW big_orders AS SELECT * FROM orders WHERE total > 50")
        cur.execute("CREATE PROCEDURE hello() SELECT 1")
    yield {
        "type": "mysql",
        "name": "rt-mysql",
        "config": {
            "host": mysql_server.host,
            "port": mysql_server.port,
            "username": MYSQL_ROOT[0],
            "databases": [SRC],
        },
        "secrets": {"password": MYSQL_ROOT[1]},
    }
    with mysql_root(mysql_server) as conn, conn.cursor() as cur:
        cur.execute(f"DROP DATABASE IF EXISTS {SRC}")
        cur.execute(f"DROP DATABASE IF EXISTS {DST}")


def test_mysql_round_trip_through_the_restore_engine(
    authed: TestClient, mysql_server: Endpoint, mysql_source: dict[str, Any]
) -> None:
    source = create_source(authed, mysql_source)
    backup = back_up(authed, source, SRC)
    expected = mysql_fingerprint(mysql_server, SRC)
    assert expected["counts"] == {"customers": 40, "orders": 80} and expected["routines"] == [
        "hello"
    ]

    restore(authed, backup, source)
    assert mysql_fingerprint(mysql_server, DST) == expected

    with mysql_root(mysql_server, DST) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM orders WHERE id <= 60")
        cur.execute("CREATE TABLE junk (x INT)")
    restore(authed, backup, source, drop=True)
    assert mysql_fingerprint(mysql_server, DST) == expected  # the junk table is gone too


# ---------- PostgreSQL ----------


def pg_connect(ep: Endpoint, database: str) -> Any:
    return psycopg.connect(
        host=ep.host, port=ep.port, user=PG_ADMIN[0], password=PG_ADMIN[1], dbname=database,
        autocommit=True,
    )  # fmt: skip


def pg_fingerprint(ep: Endpoint, database: str) -> dict[str, Any]:
    with pg_connect(ep, database) as conn:
        tables = sorted(
            r[0]
            for r in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        )
        counts = {t: conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}
        sums = {
            t: conn.execute(
                f"SELECT md5(string_agg(x::text, ',' ORDER BY x::text)) FROM \"{t}\" x"
            ).fetchone()[0]
            for t in tables
        }
        views = conn.execute(
            "SELECT COUNT(*) FROM pg_views WHERE schemaname = 'public'"
        ).fetchone()[0]
        indexes = sorted(
            r[0]
            for r in conn.execute("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'")
        )
    return {"counts": counts, "sums": sums, "views": views, "indexes": indexes}


@pytest.fixture
def pg_source(postgres_server: Endpoint) -> Iterator[dict[str, Any]]:
    with pg_connect(postgres_server, "shop") as conn:
        for name in (SRC, DST):
            conn.execute(f"DROP DATABASE IF EXISTS {name}")
        conn.execute(f"CREATE DATABASE {SRC}")
    with pg_connect(postgres_server, SRC) as conn:
        conn.execute("CREATE TABLE customers (id serial PRIMARY KEY, name text)")
        conn.execute(
            "CREATE TABLE orders (id serial PRIMARY KEY, "
            "customer_id int REFERENCES customers(id), total numeric(10,2))"
        )
        conn.execute("CREATE INDEX orders_total ON orders (total)")
        conn.execute("INSERT INTO customers (name) SELECT 'c' || i FROM generate_series(1, 40) i")
        conn.execute(
            "INSERT INTO orders (customer_id, total) "
            "SELECT (i % 40) + 1, i * 1.25 FROM generate_series(1, 80) i"
        )
        conn.execute("CREATE VIEW big_orders AS SELECT * FROM orders WHERE total > 50")
    yield {
        "type": "postgres",
        "name": "rt-postgres",
        "config": {
            "host": postgres_server.host,
            "port": postgres_server.port,
            "username": PG_ADMIN[0],
            "connection_database": "shop",
            "databases": [SRC],
        },
        "secrets": {"password": PG_ADMIN[1]},
    }
    with pg_connect(postgres_server, "shop") as conn:
        for name in (SRC, DST):
            conn.execute(f"DROP DATABASE IF EXISTS {name}")


def test_postgres_round_trip_through_the_restore_engine(
    authed: TestClient, postgres_server: Endpoint, pg_source: dict[str, Any]
) -> None:
    source = create_source(authed, pg_source)
    backup = back_up(authed, source, SRC)
    expected = pg_fingerprint(postgres_server, SRC)
    assert expected["counts"] == {"customers": 40, "orders": 80} and expected["views"] == 1

    restore(authed, backup, source)
    assert pg_fingerprint(postgres_server, DST) == expected

    with pg_connect(postgres_server, DST) as conn:
        conn.execute("DELETE FROM orders WHERE id <= 60")
        conn.execute("UPDATE customers SET name = 'changed'")
    restore(authed, backup, source, drop=True)
    assert pg_fingerprint(postgres_server, DST) == expected


# ---------- MongoDB ----------

Client = MongoClient[dict[str, Any]]


def mongo_client(ep: Endpoint) -> Client:
    return MongoClient(ep.host, ep.port, username=MONGO_ROOT[0], password=MONGO_ROOT[1])


def mongo_fingerprint(client: Client, database: str) -> dict[str, Any]:
    db = client[database]
    names = sorted(db.list_collection_names())
    return {
        "docs": {n: list(db[n].find().sort("_id")) for n in names},
        "indexes": {n: sorted(i["name"] for i in db[n].list_indexes()) for n in names},
    }


@pytest.fixture
def mongo_source(mongo_server: Endpoint) -> Iterator[dict[str, Any]]:
    client = mongo_client(mongo_server)
    for name in (SRC, DST):
        client.drop_database(name)
    client[SRC]["customers"].insert_many([{"_id": i, "name": f"c{i}"} for i in range(40)])
    client[SRC]["orders"].insert_many(
        [{"_id": i, "customer": i % 40, "total": i * 1.25} for i in range(80)]
    )
    client[SRC]["orders"].create_index("customer")
    client.close()
    yield {
        "type": "mongo",
        "name": "rt-mongo",
        "config": {
            "mode": "fields",
            "host": mongo_server.host,
            "port": mongo_server.port,
            "username": MONGO_ROOT[0],
            "auth_database": "admin",
            "databases": [SRC],
        },
        "secrets": {"password": MONGO_ROOT[1]},
    }
    client = mongo_client(mongo_server)
    for name in (SRC, DST):
        client.drop_database(name)
    client.close()


def test_mongo_round_trip_through_the_restore_engine(
    authed: TestClient, mongo_server: Endpoint, mongo_source: dict[str, Any]
) -> None:
    source = create_source(authed, mongo_source)
    backup = back_up(authed, source, SRC)
    client = mongo_client(mongo_server)
    try:
        expected = mongo_fingerprint(client, SRC)
        assert (
            len(expected["docs"]["orders"]) == 80 and "customer_1" in expected["indexes"]["orders"]
        )

        restore(authed, backup, source)
        assert mongo_fingerprint(client, DST) == expected

        client[DST]["orders"].delete_many({"_id": {"$lt": 60}})
        client[DST]["customers"].update_many({}, {"$set": {"name": "changed"}})
        restore(authed, backup, source, drop=True)
        assert mongo_fingerprint(client, DST) == expected
    finally:
        client.close()

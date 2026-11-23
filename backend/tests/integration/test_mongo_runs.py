"""End-to-end MongoDB backups against real servers (Testcontainers) and the real mongodump.

They need mongodump and mongorestore on PATH and are skipped without them; run them in the
application image with `make test-integration-docker`. mongorestore is used by these tests only:
the application has no restore feature yet.
"""

import json
import shutil
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pymongo import MongoClient
from testcontainers.community.mongodb import MongoDbContainer
from testcontainers.core.container import DockerContainer
from testcontainers.core.waiting_utils import wait_for_logs

from app.core.config import get_settings
from app.drivers.base import ConnectionTestResult
from app.drivers.mongo import MongoDriver
from tests.conftest import api_post
from tests.integration.conftest import Endpoint, free_port
from tests.runs_helpers import make_destination, make_job, run_now

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not all(shutil.which(t) for t in ("mongodump", "mongorestore")),
        reason="mongodump and mongorestore must be installed",
    ),
]

WRONG_PW = "definitely-the-wrong-pw"
EXPECTED = {"shop": {"items": 25, "orders": 40}, "blog": {"posts": 10}}

Client = MongoClient[dict[str, Any]]


def seed(client: Client) -> None:
    for database, collections in EXPECTED.items():
        for name, count in collections.items():
            client[database][name].insert_many([{"n": i, "v": f"row-{i}"} for i in range(count)])


def counts(client: Client, databases: list[str]) -> dict[str, dict[str, int]]:
    return {
        db: {
            n: client[db][n].count_documents({}) for n in sorted(client[db].list_collection_names())
        }
        for db in databases
    }


# ---------- servers ----------


@pytest.fixture(scope="module")
def mongo_auth() -> Iterator[Endpoint]:
    """A standalone server with authentication and a `backup` user."""
    with MongoDbContainer("mongo:7", username="root", password="root-pw") as container:
        host, port = container.get_container_host_ip(), int(container.get_exposed_port(27017))
        root: Client = MongoClient(host, port, username="root", password="root-pw")
        seed(root)
        root["admin"].command(
            "createUser", "backup", pwd="backup-pw", roles=[{"role": "backup", "db": "admin"}]
        )
        root.close()
        yield Endpoint(host, port, "backup", "backup-pw")


@pytest.fixture(scope="module")
def mongo_scratch() -> Iterator[Client]:
    """An empty server for test-only mongorestore runs."""
    with MongoDbContainer("mongo:7", username="root", password="scratch-pw") as container:
        host, port = container.get_container_host_ip(), int(container.get_exposed_port(27017))
        client: Client = MongoClient(host, port, username="root", password="scratch-pw")
        yield client
        client.close()


@pytest.fixture(scope="module")
def mongo_replica_set() -> Iterator[Endpoint]:
    """A single-node replica set. It listens on the same port inside and outside the container
    so the member address stored in its configuration also works for the client."""
    port = free_port()
    container = (
        DockerContainer("mongo:7")
        .with_bind_ports(port, port)
        .with_command(f"mongod --replSet rs0 --bind_ip_all --port {port}")
    )
    with container:
        wait_for_logs(container, "Waiting for connections", timeout=60)
        admin: Client = MongoClient("localhost", port, directConnection=True)
        admin.admin.command(
            "replSetInitiate", {"_id": "rs0", "members": [{"_id": 0, "host": f"localhost:{port}"}]}
        )
        for _ in range(60):
            if admin.admin.command("hello").get("isWritablePrimary"):
                break
            time.sleep(0.5)
        seed(admin)
        admin.close()
        yield Endpoint("localhost", port, "", "")


def restore(scratch: Client, artifact: Path, *extra: str) -> None:
    address = scratch.address
    assert address is not None
    host, port = address
    for database in EXPECTED:
        scratch.drop_database(database)
    uri = f"mongodb://root:scratch-pw@{host}:{port}/?authSource=admin"
    command = ["mongorestore", f"--uri={uri}", f"--archive={artifact}", "--gzip", *extra]
    done = subprocess.run(command, capture_output=True)  # noqa: S603
    assert done.returncode == 0, done.stderr.decode()


# ---------- helpers ----------


def create_source(client: TestClient, ep: Endpoint, password: str, **config: Any) -> dict[str, Any]:
    cfg: dict[str, Any] = {"mode": "fields", "host": ep.host, "port": ep.port, **config}
    secrets: dict[str, str] = {}
    if ep.username:
        cfg.update(username=ep.username, auth_database="admin")
        secrets["password"] = password
    body = {"type": "mongo", "name": "mongo-src", "config": cfg, "secrets": secrets}
    response = api_post(client, "/api/sources", body)
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


def artifact_file(run: dict[str, Any], database: str) -> Path:
    (found,) = [a for a in run["artifacts"] if a["source_database"] == database]
    return Path(get_settings().backup_dir / "offsite" / str(found["path"]))


def all_text(client: TestClient, run: dict[str, Any]) -> str:
    log: str = client.get(f"/api/runs/{run['id']}/logs?format=text").text
    manifests = "".join(p.read_text() for p in get_settings().backup_dir.rglob("*.manifest.json"))
    return log + json.dumps(run) + manifests


def make_job_for(client: TestClient, source: dict[str, Any], name: str) -> dict[str, Any]:
    return make_job(client, source, [make_destination(client, "offsite", "offsite")], name)


# ---------- tests ----------


def test_per_database_run_restores_with_matching_document_counts(
    authed: TestClient, mongo_auth: Endpoint, mongo_scratch: Client
) -> None:
    source = create_source(authed, mongo_auth, mongo_auth.password)
    run = run_now(authed, make_job_for(authed, source, "mongo-job"))
    assert run["status"] == "success", (run["error"], run["step_results"])
    assert sorted(a["source_database"] for a in run["artifacts"]) == ["blog", "shop"]
    assert all(a["path"].endswith(".archive.gz") for a in run["artifacts"])

    for database, expected in EXPECTED.items():
        restore(mongo_scratch, artifact_file(run, database), f"--nsInclude={database}.*")
        assert counts(mongo_scratch, [database]) == {database: expected}

    manifest = json.loads(next(get_settings().backup_dir.rglob("*.manifest.json")).read_text())
    assert manifest["source"] == {"dump_mode": "per_database"}
    assert manifest["tool_versions"]["mongodump"].startswith("mongodump version")
    assert mongo_auth.password not in all_text(authed, run)


def test_wrong_password_fails_the_run_without_artifacts(
    authed: TestClient, mongo_auth: Endpoint
) -> None:
    source = create_source(authed, mongo_auth, WRONG_PW)
    run = run_now(authed, make_job_for(authed, source, "bad-mongo"))
    assert run["status"] == "failed" and run["artifacts"] == [] and run["error"]
    assert WRONG_PW not in all_text(authed, run)
    assert not list(get_settings().backup_dir.rglob("*.*"))


def test_wrong_password_in_mongodump_is_sanitized(
    authed: TestClient, mongo_auth: Endpoint, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Skip the probe so mongodump itself meets the wrong password."""

    def probe(self: MongoDriver) -> ConnectionTestResult:
        self._is_replica_set = False
        return ConnectionTestResult(ok=True, message="ok", server_version="7", databases=["shop"])

    monkeypatch.setattr(MongoDriver, "_probe", probe)
    source = create_source(authed, mongo_auth, WRONG_PW, databases=["shop"])
    run = run_now(authed, make_job_for(authed, source, "bad-dump"))
    assert run["status"] == "failed" and run["artifacts"] == []
    assert "mongodump exited with code" in run["error"]
    assert WRONG_PW not in all_text(authed, run) and "mongodb://" not in all_text(authed, run)
    assert not list(get_settings().backup_dir.rglob("*.*"))
    assert not get_settings().work_dir.exists() or not list(get_settings().work_dir.iterdir())


def test_full_oplog_on_a_standalone_server_fails_early(
    authed: TestClient, mongo_auth: Endpoint
) -> None:
    source = create_source(authed, mongo_auth, mongo_auth.password, dump_mode="full_oplog")
    run = run_now(authed, make_job_for(authed, source, "oplog-standalone"))
    assert run["status"] == "failed" and "needs a replica set" in run["error"]
    assert run["artifacts"] == [] and not list(get_settings().backup_dir.rglob("*.*"))


def test_full_oplog_on_a_replica_set_makes_one_restorable_archive(
    authed: TestClient, mongo_replica_set: Endpoint, mongo_scratch: Client
) -> None:
    source = create_source(
        authed, mongo_replica_set, "", dump_mode="full_oplog", replica_set="rs0", databases=["shop"]
    )
    run = run_now(authed, make_job_for(authed, source, "oplog-rs"))
    assert run["status"] == "success", (run["error"], run["step_results"])
    assert [a["source_database"] for a in run["artifacts"]] == ["full-instance"]  # one archive
    archive = artifact_file(run, "full-instance")
    restore(mongo_scratch, archive, "--oplogReplay")
    assert counts(mongo_scratch, list(EXPECTED)) == EXPECTED


def test_per_database_on_a_replica_set_warns_in_the_log(
    authed: TestClient, mongo_replica_set: Endpoint
) -> None:
    source = create_source(authed, mongo_replica_set, "", replica_set="rs0", databases=["shop"])
    run = run_now(authed, make_job_for(authed, source, "pd-rs"))
    assert run["status"] == "success", (run["error"], run["step_results"])
    log = authed.get(f"/api/runs/{run['id']}/logs?format=text").text
    assert "not point-in-time" in log and "oplog" in log

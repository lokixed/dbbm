"""Restore runs: mongorestore through the whole lifecycle (fake tools, no servers)."""

import asyncio
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.api import backups as backups_api
from app.core.config import get_settings
from app.core.db import get_engine
from app.drivers.base import (
    BaseDriver,
    ConnectionTestResult,
    RestoreContext,
    RestoreError,
    RestoreOptions,
)
from app.models import AuditLog, Run
from app.services.restore_run import JOB_NAME_MAX, restore_run_name
from tests.conftest import api_post
from tests.fakes import FakeBins, FakeMySQL, FakePostgres
from tests.runs_helpers import (
    PW,
    get_run,
    make_destination,
    make_job,
    make_source,
    run_now,
    wait_final,
    wait_until,
    work_entries,
)
from tests.test_runs_m4 import make_mongo

pytestmark = pytest.mark.usefixtures("fake_bins", "fake_drivers")


@pytest.fixture(autouse=True)
def reset_state() -> None:
    backups_api._restore_starts.clear()
    FakeMySQL.prepared.clear()
    FakePostgres.prepared.clear()


def backed_up_mongo(client: TestClient) -> tuple[dict[str, Any], dict[str, Any]]:
    """A mongo source, a job over it, one run; returns (source, a backup row for 'shop')."""
    source = make_mongo(client, databases=["shop"])
    job = make_job(client, source, [make_destination(client)])
    assert run_now(client, job)["status"] == "success"
    items = client.get("/api/backups").json()["items"]
    return source, next(i for i in items if i["source_database"] == "shop")


def restore_body(
    target: dict[str, Any], database: str = "shop_copy", **over: Any
) -> dict[str, Any]:
    return {
        "target_source_id": target["id"],
        "target_database": database,
        "confirm_database": database,
        **over,
    }


def restore(client: TestClient, backup: dict[str, Any], body: dict[str, Any]) -> Any:
    return api_post(client, f"/api/backups/{backup['id']}/restore", body)


def audit_actions() -> list[str]:
    with Session(get_engine()) as db:
        return [a.action for a in db.exec(select(AuditLog).order_by(AuditLog.timestamp)).all()]  # type: ignore[arg-type]


def test_restore_into_another_database(authed: TestClient, fake_bins: FakeBins) -> None:
    _, backup = backed_up_mongo(authed)
    target = make_mongo(authed, "mongo-scratch")
    response = restore(authed, backup, restore_body(target, drop=True))
    assert response.status_code == 201, response.text
    run = wait_final(authed, response.json()["id"])

    assert run["status"] == "success", run["error"]
    assert run["kind"] == "restore" and run["job_id"] is None
    assert [s["name"] for s in run["step_results"]["steps"]] == [
        "preflight",
        "fetch backup",
        "restore",
        "cleanup",
    ]
    assert run["step_results"]["restore"]["target_database"] == "shop_copy"
    assert run["job_name"] == "Restore shop → shop_copy on mongo-scratch"
    assert run["source_type"] == "mongo"
    args = fake_bins.read("mongorestore.args")
    assert "--gzip" in args and "--drop" in args and "--nsInclude=shop.*" in args
    assert "--nsFrom=shop.*" in args and "--nsTo=shop_copy.*" in args
    assert "--oplogReplay" not in args and PW not in args
    assert fake_bins.read("mongorestore.archive") == "fake-mongo-archive"
    assert fake_bins.read("mongorestore.yml.perm").strip() == "600"
    # Credentials (percent-encoded in the URI) live only in the 0600 config file.
    assert "Sup3r-Secret-Pw%21" in fake_bins.read("mongorestore.yml")
    assert work_entries() == []
    log = authed.get(f"/api/runs/{run['id']}/logs?format=text").text
    assert "Checksum verified" in log and PW not in log and PW not in json.dumps(run)
    assert audit_actions().count("backup.restore_started") == 1
    wait_until(lambda: "backup.restore_finished" in audit_actions())


def test_same_database_name_skips_namespace_mapping(
    authed: TestClient, fake_bins: FakeBins
) -> None:
    _, backup = backed_up_mongo(authed)
    target = make_mongo(authed, "mongo-scratch")
    run = wait_final(authed, restore(authed, backup, restore_body(target, "shop")).json()["id"])
    assert run["status"] == "success"
    assert run["job_name"] == "Restore shop on mongo-scratch"
    assert "--nsFrom" not in fake_bins.read("mongorestore.args")


def test_typed_confirmation_is_required(authed: TestClient) -> None:
    _, backup = backed_up_mongo(authed)
    target = make_mongo(authed, "mongo-scratch")
    body = restore_body(target) | {"confirm_database": "something_else"}
    response = restore(authed, backup, body)
    assert response.status_code == 422 and "Type the target database name" in response.text


def test_overwriting_the_original_needs_extra_confirmation(
    authed: TestClient, fake_bins: FakeBins
) -> None:
    source, backup = backed_up_mongo(authed)
    body = restore_body(source, "shop")
    refused = restore(authed, backup, body)
    assert refused.status_code == 409 and "overwrite the original" in refused.json()["detail"]
    assert not fake_bins.exists("mongorestore.args")

    accepted = restore(authed, backup, body | {"confirm_overwrite_original": True})
    assert accepted.status_code == 201
    assert wait_final(authed, accepted.json()["id"])["status"] == "success"
    # The same source under a different database name is not an overwrite.
    other = restore(authed, backup, restore_body(source, "shop_copy"))
    assert other.status_code == 201
    wait_final(authed, other.json()["id"])


def test_target_must_match_the_backup_type(authed: TestClient) -> None:
    _, backup = backed_up_mongo(authed)
    mysql = make_source(authed)
    response = restore(authed, backup, restore_body(mysql))
    assert response.status_code == 422 and "mongo source" in response.json()["detail"]
    missing = restore(authed, backup, restore_body({"id": "0" * 32}))
    assert missing.status_code == 404


def test_a_driver_without_restore_support_refuses_clearly(tmp_path: Path) -> None:
    class NewEngine(BaseDriver):
        source_type = "newengine"

        def _probe(self) -> ConnectionTestResult:
            return ConnectionTestResult(ok=True, message="ok")

    ctx = RestoreContext(tmp_path, 10, logging.getLogger("test"))
    with pytest.raises(RestoreError, match="newengine backups is not supported yet"):
        asyncio.run(
            NewEngine({}, {}).restore_database(tmp_path / "x", RestoreOptions("a", "b"), ctx)
        )


def test_corrupt_backup_is_refused_before_the_target_is_touched(
    authed: TestClient, fake_bins: FakeBins
) -> None:
    _, backup = backed_up_mongo(authed)
    (get_settings().backup_dir / "daily" / backup["path"]).write_bytes(b"tampered")
    target = make_mongo(authed, "mongo-scratch")
    run = wait_final(authed, restore(authed, backup, restore_body(target)).json()["id"])
    assert run["status"] == "failed" and "corrupt" in run["error"]
    assert not fake_bins.exists("mongorestore.args")
    assert work_entries() == []


def test_tool_failure_is_reported_without_secrets(authed: TestClient, fake_bins: FakeBins) -> None:
    _, backup = backed_up_mongo(authed)
    target = make_mongo(authed, "mongo-scratch")
    fake_bins.set_mode("mongorestore", "fail")
    run = wait_final(authed, restore(authed, backup, restore_body(target)).json()["id"])
    assert run["status"] == "failed"
    assert "mongorestore exited with code 1" in run["error"] and "not authorized" in run["error"]
    assert PW not in json.dumps(run) and work_entries() == []
    wait_until(lambda: "backup.restore_finished" in audit_actions())


def test_one_restore_at_a_time_per_target_and_cancel(
    authed: TestClient, fake_bins: FakeBins
) -> None:
    _, backup = backed_up_mongo(authed)
    target = make_mongo(authed, "mongo-scratch")
    fake_bins.set_mode("mongorestore", "hang")
    first = restore(authed, backup, restore_body(target)).json()
    wait_until(lambda: fake_bins.exists("restore.pid"))

    busy = restore(authed, backup, restore_body(target, "other_copy"))
    assert busy.status_code == 409 and "already running" in busy.json()["detail"]

    assert api_post(authed, f"/api/runs/{first['id']}/cancel").status_code == 200
    run = wait_final(authed, first["id"])
    assert run["status"] == "cancelled" and get_run(authed, first["id"])["kind"] == "restore"
    assert work_entries() == []
    fake_bins.set_mode("mongorestore", "ok")
    again = restore(authed, backup, restore_body(target, "other_copy"))
    assert again.status_code == 201
    wait_final(authed, again.json()["id"])


def test_backups_can_be_listed_by_run(authed: TestClient) -> None:
    _, backup = backed_up_mongo(authed)
    page = authed.get(f"/api/backups?run_id={backup['run_id']}").json()
    assert page["total"] >= 1 and all(i["run_id"] == backup["run_id"] for i in page["items"])
    assert authed.get(f"/api/backups?run_id={'0' * 32}").json()["total"] == 0


def test_a_backup_can_be_read_by_id(authed: TestClient) -> None:
    source, backup = backed_up_mongo(authed)
    one = authed.get(f"/api/backups/{backup['id']}")
    assert one.status_code == 200 and one.json() == backup
    assert one.json()["source_id"] == source["id"]
    missing = authed.get("/api/backups/00000000-0000-0000-0000-000000000000")
    assert missing.status_code == 404


def test_restores_are_rate_limited(authed: TestClient) -> None:
    for _ in range(backups_api.RESTORES_PER_MINUTE):
        assert not backups_api._rate_limited("alice")
    assert backups_api._rate_limited("alice")
    assert not backups_api._rate_limited("bob")


# ---------- MySQL ----------


def backed_up_sql(
    client: TestClient, kind: str, name: str, **job: Any
) -> tuple[dict[str, Any], dict[str, Any]]:
    source = make_source(client, name, kind, databases=["shop"])
    job_row = make_job(client, source, [make_destination(client)], **job)
    assert run_now(client, job_row)["status"] == "success"
    items = client.get("/api/backups").json()["items"]
    return source, next(i for i in items if i["source_database"] == "shop")


def test_mysql_restore_streams_the_gunzipped_dump(authed: TestClient, fake_bins: FakeBins) -> None:
    _, backup = backed_up_sql(authed, "mysql", "mysql-main")
    assert backup["source_type"] == "mysql" and backup["path"].endswith(".sql.gz")
    target = make_source(authed, "mysql-scratch")
    run = wait_final(authed, restore(authed, backup, restore_body(target, drop=True)).json()["id"])

    assert run["status"] == "success", run["error"]
    assert FakeMySQL.prepared == [("shop_copy", True)]
    args = fake_bins.read("mysql.args").split()
    assert args[0].startswith("--defaults-extra-file=") and args[-1] == "shop_copy"
    assert PW not in " ".join(args)
    assert fake_bins.read("mysql.stdin") == "-- fake dump of shop\nCREATE TABLE t (id int);\n"
    assert fake_bins.read("mysql.cnf.mode").strip() == "600" and PW in fake_bins.read("mysql.cnf")
    log = authed.get(f"/api/runs/{run['id']}/logs?format=text").text
    assert PW not in log and PW not in json.dumps(run) and work_entries() == []


def test_mysql_restore_of_an_uncompressed_dump(authed: TestClient, fake_bins: FakeBins) -> None:
    _, backup = backed_up_sql(authed, "mysql", "mysql-main", compress=False)
    assert backup["path"].endswith(".sql") and not backup["path"].endswith(".gz")
    target = make_source(authed, "mysql-scratch")
    run = wait_final(authed, restore(authed, backup, restore_body(target)).json()["id"])
    assert run["status"] == "success", run["error"]
    assert FakeMySQL.prepared == [("shop_copy", False)]
    assert fake_bins.read("mysql.stdin").startswith("-- fake dump of shop")


def test_mysql_failure_is_reported_without_secrets(authed: TestClient, fake_bins: FakeBins) -> None:
    _, backup = backed_up_sql(authed, "mysql", "mysql-main")
    target = make_source(authed, "mysql-scratch")
    fake_bins.set_mode("mysql", "fail")
    run = wait_final(authed, restore(authed, backup, restore_body(target)).json()["id"])
    assert run["status"] == "failed"
    assert "mysql exited with code 1" in run["error"] and "Access denied" in run["error"]
    assert PW not in json.dumps(run) and work_entries() == []


def test_mysql_backup_cannot_go_into_a_mongo_source(authed: TestClient) -> None:
    _, backup = backed_up_sql(authed, "mysql", "mysql-main")
    response = restore(authed, backup, restore_body(make_mongo(authed, "mongo-x")))
    assert response.status_code == 422 and "mysql source" in response.json()["detail"]


# ---------- PostgreSQL ----------


def test_postgres_restore_runs_one_transaction(authed: TestClient, fake_bins: FakeBins) -> None:
    _, backup = backed_up_sql(authed, "postgres", "pg-main")
    assert backup["source_type"] == "postgres" and backup["path"].endswith(".dump")
    target = make_source(authed, "pg-scratch", "postgres")
    run = wait_final(authed, restore(authed, backup, restore_body(target)).json()["id"])

    assert run["status"] == "success", run["error"]
    assert FakePostgres.prepared == ["shop_copy"]
    args = fake_bins.read("pg_restore.args").split()
    assert args[args.index("-d") : args.index("-d") + 2] == ["-d", "shop_copy"]
    assert {"--single-transaction", "--no-owner", "--no-privileges"} <= set(args)
    assert "--clean" not in args and args[-1].endswith(".dump")
    assert PW not in " ".join(args) and fake_bins.read("pg_restore.pgpassword").strip() == PW
    assert fake_bins.read("pg_restore.input") == "PGDMP-fake-custom-format"
    assert work_entries() == []


def test_postgres_drop_cleans_existing_objects(authed: TestClient, fake_bins: FakeBins) -> None:
    _, backup = backed_up_sql(authed, "postgres", "pg-main")
    target = make_source(authed, "pg-scratch", "postgres")
    run = wait_final(authed, restore(authed, backup, restore_body(target, drop=True)).json()["id"])
    assert run["status"] == "success"
    args = fake_bins.read("pg_restore.args").split()
    assert "--clean" in args and "--if-exists" in args


def test_postgres_failure_is_reported_without_secrets(
    authed: TestClient, fake_bins: FakeBins
) -> None:
    _, backup = backed_up_sql(authed, "postgres", "pg-main")
    target = make_source(authed, "pg-scratch", "postgres")
    fake_bins.set_mode("pg_restore", "fail")
    run = wait_final(authed, restore(authed, backup, restore_body(target)).json()["id"])
    assert run["status"] == "failed" and "pg_restore exited with code 1" in run["error"]
    assert "permission denied" in run["error"] and PW not in json.dumps(run)


def test_postgres_roles_file_is_refused(authed: TestClient) -> None:
    source = make_source(authed, "pg-main", "postgres", include_globals=True, databases=["shop"])
    job = make_job(authed, source, [make_destination(authed)])
    assert run_now(authed, job)["status"] == "success"
    items = authed.get("/api/backups").json()["items"]
    globals_backup = next(i for i in items if i["source_database"] == "globals")
    assert globals_backup["source_type"] == "postgres"  # not mistaken for MySQL by its .sql.gz
    response = restore(authed, globals_backup, restore_body(source, "roles_copy"))
    assert response.status_code == 422 and "roles file" in response.json()["detail"]


def test_database_names_cannot_start_with_a_dash(authed: TestClient) -> None:
    _, backup = backed_up_sql(authed, "mysql", "mysql-main")
    target = make_source(authed, "mysql-scratch")
    response = restore(authed, backup, restore_body(target, "-evil"))
    assert response.status_code == 422


def test_newer_pg_restore_than_server_skips_the_single_transaction(
    authed: TestClient, fake_bins: FakeBins
) -> None:
    _, backup = backed_up_sql(authed, "postgres", "pg-main")
    target = make_source(authed, "pg-scratch", "postgres")
    fake_bins.set_text("pg_restore_version", "18.1")  # the fake server is version 16
    fake_bins.set_mode("pg_restore", "harmless")
    run = wait_final(authed, restore(authed, backup, restore_body(target)).json()["id"])

    assert run["status"] == "success", run["error"]
    assert "--single-transaction" not in fake_bins.read("pg_restore.args").split()
    log = authed.get(f"/api/runs/{run['id']}/logs?format=text").text
    assert "newer than the server" in log and "known to be harmless" in log


def test_newer_pg_restore_still_fails_on_real_errors(
    authed: TestClient, fake_bins: FakeBins
) -> None:
    _, backup = backed_up_sql(authed, "postgres", "pg-main")
    target = make_source(authed, "pg-scratch", "postgres")
    fake_bins.set_text("pg_restore_version", "18.1")
    fake_bins.set_mode("pg_restore", "fail")
    run = wait_final(authed, restore(authed, backup, restore_body(target)).json()["id"])
    assert run["status"] == "failed" and "permission denied" in run["error"]


def test_long_restore_names_fit_the_run_name_column() -> None:
    plan = SimpleNamespace(
        database="d" * 30,
        target_source_name="s" * 30,
        options=SimpleNamespace(target_database="t" * 30),
    )
    name = restore_run_name(plan)  # type: ignore[arg-type]
    assert len(name) == JOB_NAME_MAX and name.endswith("…")


def test_older_restore_runs_find_their_engine_by_source_name(authed: TestClient) -> None:
    target = make_mongo(authed, "mongo-scratch")
    with Session(get_engine()) as db:
        run = Run(
            job_name="Restore shop",
            kind="restore",
            trigger="manual",
            status="failed",
            step_results={"restore": {"target_source": target["name"]}},  # no target_type yet
        )
        db.add(run)
        db.commit()
        run_id = str(run.id)
    listed = authed.get("/api/runs").json()["items"]
    assert next(r for r in listed if r["id"] == run_id)["source_type"] == "mongo"


def test_restores_are_listed_with_their_target_source(authed: TestClient) -> None:
    source, backup = backed_up_mongo(authed)
    target = make_mongo(authed, "mongo-scratch")
    response = restore(authed, backup, restore_body(target))
    assert response.status_code == 201, response.text
    restored = wait_final(authed, response.json()["id"])
    with Session(get_engine()) as db:  # a restore from before the target's id was recorded
        older = Run(
            job_name="Restore shop",
            kind="restore",
            trigger="manual",
            status="failed",
            step_results={"restore": {"target_source": target["name"]}},
        )
        db.add(older)
        db.commit()
        older_id = str(older.id)

    def ids(src: dict[str, Any]) -> set[str]:
        page = authed.get("/api/runs", params={"source_id": src["id"]}).json()
        return {r["id"] for r in page["items"]}

    assert ids(target) == {restored["id"], older_id}
    # The new restore also shows on the source it was backed up from; the older one names no backup.
    assert restored["id"] in ids(source) and older_id not in ids(source)


def test_restores_are_listed_with_the_source_they_were_backed_up_from(authed: TestClient) -> None:
    source, backup = backed_up_mongo(authed)
    target = make_mongo(authed, "mongo-scratch")
    response = restore(authed, backup, restore_body(target))
    assert response.status_code == 201, response.text
    restored = wait_final(authed, response.json()["id"])
    assert restored["step_results"]["restore"]["backup_source_id"] == source["id"]
    with Session(get_engine()) as db:  # a restore from before the backup's source was recorded
        older = Run(
            job_name="Restore shop",
            kind="restore",
            trigger="manual",
            status="failed",
            step_results={"restore": {"artifact_id": backup["id"], "target_source": "gone"}},
        )
        db.add(older)
        db.commit()
        older_id = str(older.id)

    page = authed.get("/api/runs", params={"source_id": source["id"]}).json()
    listed = {r["id"] for r in page["items"]}
    assert {restored["id"], older_id} <= listed and page["total"] == 3  # plus its backup run

"""Restore runs: mongorestore through the whole lifecycle (fake tools, no servers)."""

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.api import backups as backups_api
from app.core.config import get_settings
from app.core.db import get_engine
from app.models import AuditLog
from tests.conftest import api_post
from tests.fakes import FakeBins
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
def reset_rate_limit() -> None:
    backups_api._restore_starts.clear()


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


def test_unsupported_engine_is_refused_up_front(authed: TestClient) -> None:
    mysql = make_source(authed)
    run = run_now(authed, make_job(authed, mysql, [make_destination(authed)]))
    assert run["status"] == "success"
    backup = authed.get("/api/backups").json()["items"][0]
    response = restore(authed, backup, restore_body(make_source(authed, "mysql-2")))
    assert response.status_code == 422 and "not supported yet" in response.json()["detail"]


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


def test_restores_are_rate_limited(authed: TestClient) -> None:
    for _ in range(backups_api.RESTORES_PER_MINUTE):
        assert not backups_api._rate_limited("alice")
    assert backups_api._rate_limited("alice")
    assert not backups_api._rate_limited("bob")

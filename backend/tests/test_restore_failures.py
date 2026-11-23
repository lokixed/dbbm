"""Restore from a Google Drive copy and the ways a restore can fail (fake tools, fake rclone)."""

import json
import shutil
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api import backups as backups_api
from app.core.config import get_settings
from tests.conftest import api_post
from tests.fake_rclone import FakeRclone
from tests.fakes import FakeBins, FakeMySQL, alive
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
from tests.test_restore_api import restore, restore_body
from tests.test_runs_m4 import make_mongo
from tests.test_runs_m5 import make_drive

pytestmark = pytest.mark.usefixtures("fake_bins", "fake_drivers")

NETWORK = "ERROR : Failed to copy: dial tcp: lookup www.googleapis.com: no such host"


@pytest.fixture(autouse=True)
def reset_state() -> None:
    backups_api._restore_starts.clear()
    FakeMySQL.prepared.clear()
    FakeMySQL.ok = True


def mongo_on_both(client: TestClient) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """A mongo backup stored locally and on Drive; returns (target source, copies by type)."""
    source = make_mongo(client, databases=["shop"])
    job = make_job(client, source, [make_destination(client), make_drive(client)])
    assert run_now(client, job)["status"] == "success"
    items = client.get("/api/backups").json()["items"]
    copies = {i["destination_type"]: i for i in items if i["source_database"] == "shop"}
    assert set(copies) == {"local", "gdrive"}
    return make_mongo(client, "mongo-scratch"), copies


def restore_when_free(client: TestClient, backup: dict[str, Any], body: dict[str, Any]) -> str:
    """Start a restore, waiting while the target still counts the previous run as active (its
    final status is visible a moment before the lock is released)."""
    deadline = time.monotonic() + 10
    while True:
        response = restore(client, backup, body)
        if response.status_code != 409 or time.monotonic() > deadline:
            assert response.status_code == 201, response.text
            return str(response.json()["id"])
        time.sleep(0.05)


def local_file(path: str) -> Any:
    return get_settings().backup_dir / "daily" / path


# ---------- restoring from Drive ----------


def test_restore_from_drive_copy_after_the_local_file_is_deleted(
    authed: TestClient, fake_rclone: FakeRclone, fake_bins: FakeBins
) -> None:
    target, copies = mongo_on_both(authed)
    local_file(copies["local"]["path"]).unlink()

    run = wait_final(authed, restore_when_free(authed, copies["gdrive"], restore_body(target)))

    assert run["status"] == "success", run["error"]
    assert fake_bins.read("mongorestore.archive") == "fake-mongo-archive"
    assert run["step_results"]["restore"]["copy"] == "gdrive1"
    log = authed.get(f"/api/runs/{run['id']}/logs?format=text").text
    assert "Downloading the backup from 'gdrive1'" in log and "Checksum verified" in log
    assert work_entries() == []  # the downloaded copy is removed with the work directory


def test_drive_download_failure_suggests_another_copy(
    authed: TestClient, fake_rclone: FakeRclone, fake_bins: FakeBins
) -> None:
    target, copies = mongo_on_both(authed)
    fake_rclone.fail("copyto", 1, NETWORK)

    run = wait_final(authed, restore_when_free(authed, copies["gdrive"], restore_body(target)))

    assert run["status"] == "failed"
    assert "Cannot download the backup from 'gdrive1'" in run["error"]
    assert "restore from that one" in run["error"]
    assert not fake_bins.exists("mongorestore.args") and work_entries() == []
    # The other copy still restores.
    fake_rclone.clear("copyto")
    again = wait_final(authed, restore_when_free(authed, copies["local"], restore_body(target)))
    assert again["status"] == "success"


def test_corrupt_drive_download_is_refused(
    authed: TestClient, fake_rclone: FakeRclone, fake_bins: FakeBins
) -> None:
    target, copies = mongo_on_both(authed)
    drive_file = next(p for p in fake_rclone.remote.rglob("*") if p.name.endswith(".archive.gz"))
    drive_file.write_bytes(b"tampered")

    run = wait_final(authed, restore_when_free(authed, copies["gdrive"], restore_body(target)))

    assert run["status"] == "failed" and not fake_bins.exists("mongorestore.args")
    assert work_entries() == []


def test_full_disk_stops_a_drive_restore_before_downloading(
    authed: TestClient,
    fake_rclone: FakeRclone,
    fake_bins: FakeBins,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target, copies = mongo_on_both(authed)
    full = shutil._ntuple_diskusage(total=100, used=100, free=0)  # type: ignore[attr-defined]
    monkeypatch.setattr("app.services.restore_run.shutil.disk_usage", lambda _p: full)
    calls_before = fake_rclone.commands().count("copyto")

    run = wait_final(authed, restore_when_free(authed, copies["gdrive"], restore_body(target)))

    assert run["status"] == "failed" and "Not enough free disk space" in run["error"]
    assert fake_rclone.commands().count("copyto") == calls_before  # nothing was downloaded
    assert not fake_bins.exists("mongorestore.args")


def test_drive_copy_that_needs_reauthorization_is_not_read(
    authed: TestClient, fake_rclone: FakeRclone, fake_bins: FakeBins
) -> None:
    target, copies = mongo_on_both(authed)
    fake_rclone.fail("copyto", 1, 'ERROR : couldn\'t fetch token: invalid_grant: "Token expired"')
    first = wait_final(authed, restore_when_free(authed, copies["gdrive"], restore_body(target)))
    assert first["status"] == "failed"
    second = wait_final(authed, restore_when_free(authed, copies["gdrive"], restore_body(target)))
    assert second["status"] == "failed" and "Google Drive" in second["error"]
    assert not fake_bins.exists("mongorestore.args")


# ---------- wrong credentials, cancel ----------


def test_wrong_target_credentials_stop_the_restore_in_preflight(
    authed: TestClient, fake_bins: FakeBins
) -> None:
    source = make_source(authed, "mysql-main", databases=["shop"])
    assert (
        run_now(authed, make_job(authed, source, [make_destination(authed)]))["status"] == "success"
    )
    backup = next(
        i for i in authed.get("/api/backups").json()["items"] if i["source_database"] == "shop"
    )
    target = make_source(authed, "mysql-scratch")
    FakeMySQL.ok = False

    run = wait_final(authed, restore_when_free(authed, backup, restore_body(target)))

    assert run["status"] == "failed" and "Cannot connect to the target" in run["error"]
    assert [s["name"] for s in run["step_results"]["steps"]][:1] == ["preflight"]
    assert not fake_bins.exists("mysql.args") and FakeMySQL.prepared == []
    assert PW not in json.dumps(run)


def test_cancelling_a_mysql_restore_kills_the_client(
    authed: TestClient, fake_bins: FakeBins
) -> None:
    source = make_source(authed, "mysql-main", databases=["shop"])
    assert (
        run_now(authed, make_job(authed, source, [make_destination(authed)]))["status"] == "success"
    )
    backup = next(
        i for i in authed.get("/api/backups").json()["items"] if i["source_database"] == "shop"
    )
    target = make_source(authed, "mysql-scratch")
    fake_bins.set_mode("mysql", "hang")

    started = {"id": restore_when_free(authed, backup, restore_body(target))}
    wait_until(lambda: fake_bins.exists("restore.childpid"))
    pid, child = int(fake_bins.read("restore.pid")), int(fake_bins.read("restore.childpid"))
    assert alive(pid) and alive(child)

    assert api_post(authed, f"/api/runs/{started['id']}/cancel").status_code == 200
    assert wait_final(authed, started["id"])["status"] == "cancelled"
    wait_until(lambda: not alive(pid) and not alive(child))
    assert work_entries() == [] and get_run(authed, started["id"])["kind"] == "restore"

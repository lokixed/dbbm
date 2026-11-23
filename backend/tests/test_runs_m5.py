"""Local + Google Drive destinations through the run lifecycle, with a fake rclone."""

import json
import time
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, col, select

from app.core.config import get_settings
from app.core.db import get_engine
from app.models import Artifact, AuditLog, Destination
from app.services.secrets import load_secrets
from tests.conftest import api_post, api_put
from tests.fake_rclone import ACCESS, REFRESH, TOKEN, FakeRclone, token_json
from tests.runs_helpers import (
    backup_files,
    get_run,
    make_destination,
    make_job,
    make_source,
    run_now,
    start,
    wait_final,
)

pytestmark = pytest.mark.usefixtures("fake_bins", "fake_drivers")

INVALID_GRANT = (
    "2026/11/22 10:00:00 ERROR : Failed to copy: couldn't fetch token: invalid_grant: "
    '"Token has been expired or revoked."'
)
NETWORK = "ERROR : Failed to copy: dial tcp: lookup www.googleapis.com: no such host"


def make_drive(client: TestClient, name: str = "gdrive1", **config: Any) -> dict[str, Any]:
    body = {
        "type": "gdrive",
        "name": name,
        "config": {"remote_folder": "db-backups", **config},
        "secrets": {"token": token_json()},
    }
    response = api_post(client, "/api/destinations", body)
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


def setup_job(client: TestClient, **over: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    local = make_destination(client)
    drive = make_drive(client)
    job = make_job(client, make_source(client), [local, drive], **over)
    return job, drive


def entries(run: dict[str, Any], dest_type: str) -> list[dict[str, Any]]:
    return [
        d
        for u in run["step_results"]["units"]
        for d in u["destinations"]
        if d.get("type") == dest_type
    ]


def stored_secrets(dest_id: str) -> dict[str, str]:
    with Session(get_engine()) as db:
        row = db.get(Destination, uuid.UUID(dest_id))
        assert row is not None
        return load_secrets(row.secrets_enc)


def auth_status(client: TestClient, dest_id: str) -> str:
    status: str = client.get(f"/api/destinations/{dest_id}").json()["auth_status"]
    return status


def assert_no_secrets(*texts: str) -> None:
    for text in texts:
        for secret in (REFRESH, ACCESS, "csecret"):
            assert secret not in text


def test_local_and_drive_both_verified(authed: TestClient, fake_rclone: FakeRclone) -> None:
    job, drive = setup_job(authed)
    run = run_now(authed, job)
    assert run["status"] == "success", run["error"]

    local_files = [f.removeprefix("daily/") for f in backup_files()]
    drive_files = [f.removeprefix("db-backups/") for f in fake_rclone.files()]
    assert sorted(local_files) == sorted(drive_files) and len(drive_files) == 4

    by_dest: dict[str, list[dict[str, Any]]] = {}
    for art in run["artifacts"]:
        by_dest.setdefault(art["destination_id"], []).append(art)
    assert len(by_dest) == 2 and all(len(v) == 2 for v in by_dest.values())
    assert all(a["md5"] for a in by_dest[drive["id"]])
    for entry in entries(run, "gdrive"):
        assert entry["status"] == "ok" and entry["md5"]
        assert [s["name"] for s in entry["steps"]] == ["put", "verify"]
        assert entry["bytes"] > 0 and entry["store_ms"] >= 0
    assert auth_status(authed, drive["id"]) == "ok"

    log = authed.get(f"/api/runs/{run['id']}/logs?format=text").text
    assert "[gdrive1]" in log
    assert_no_secrets(log, json.dumps(run))


def test_running_twice_never_duplicates(authed: TestClient, fake_rclone: FakeRclone) -> None:
    job, _ = setup_job(authed)
    assert run_now(authed, job)["status"] == "success"
    time.sleep(1.1)  # keys are timestamped to the second
    assert run_now(authed, job)["status"] == "success"
    files = fake_rclone.files()
    assert len(files) == len(set(files)) == 8
    uploads = [c["argv"][2] for c in fake_rclone.calls() if c["argv"][0] == "copyto"]
    assert len(uploads) == len(set(uploads))


def test_drive_failure_is_partial_then_retry_succeeds(
    authed: TestClient, fake_rclone: FakeRclone
) -> None:
    job, drive = setup_job(authed)
    fake_rclone.fail("copyto", 1, NETWORK)
    run = run_now(authed, job)
    assert run["status"] == "partial"
    assert len(backup_files()) == 4 and fake_rclone.files() == []
    failed = entries(run, "gdrive")
    assert {e["error_class"] for e in failed} == {"network"}
    assert all(e["status"] == "failed" for e in failed)
    assert auth_status(authed, drive["id"]) != "needs_reauth"

    fake_rclone.clear("copyto")
    response = api_post(authed, f"/api/runs/{run['id']}/retry")
    assert response.status_code == 201, response.text
    child = wait_final(authed, response.json()["id"])
    assert child["status"] == "success", child["error"]
    assert child["trigger"] == "retry" and child["parent_run_id"] == run["id"]
    assert child["artifacts"] == []  # the copies belong to the original run

    parent = get_run(authed, run["id"])
    assert parent["status"] == "success" and parent["error"] is None
    assert parent["retry_run_ids"] == [child["id"]]
    assert len(parent["artifacts"]) == 4
    for entry in entries(parent, "gdrive"):
        assert entry["status"] == "ok" and entry["retried_by"] == child["id"]
        assert entry["previous_error"]
    assert len(fake_rclone.files()) == 4

    with Session(get_engine()) as db:
        audit = db.exec(select(AuditLog).where(col(AuditLog.action) == "run.retry")).all()
    assert [a.object_id for a in audit] == [run["id"]]

    again = api_post(authed, f"/api/runs/{run['id']}/retry")
    assert again.status_code == 409


def test_revoked_token_sets_needs_reauth_and_preflight_skips_drive(
    authed: TestClient, fake_rclone: FakeRclone
) -> None:
    job, drive = setup_job(authed)
    fake_rclone.fail("lsjson", 1, INVALID_GRANT)
    run = run_now(authed, job)
    assert run["status"] == "partial"
    assert {e["error_class"] for e in entries(run, "gdrive")} == {"auth_expired"}
    assert auth_status(authed, drive["id"]) == "needs_reauth"

    calls_before = len(fake_rclone.calls())
    time.sleep(1.1)
    second = run_now(authed, job)
    assert second["status"] == "partial"
    assert len(fake_rclone.calls()) == calls_before  # Drive was not even attempted
    for entry in entries(second, "gdrive"):
        assert entry["error_class"] == "auth_expired"
        assert "reconnect in Destinations" in entry["error"]
        assert entry["steps"] == []

    dashboard = authed.get("/api/dashboard").json()
    health = {d["id"]: d for d in dashboard["destinations"]}
    assert health[drive["id"]]["auth_status"] == "needs_reauth"
    assert health[drive["id"]]["last_error_class"] == "auth_expired"

    # Reconnecting (a new token) clears the verdict, and a retry then succeeds.
    body = {"type": "gdrive", "name": "gdrive1", "config": drive["config"],
            "secrets": {"token": token_json(access_token="ya29.New")}}  # fmt: skip
    assert api_put(authed, f"/api/destinations/{drive['id']}", body).status_code == 200
    assert auth_status(authed, drive["id"]) == "unknown"
    fake_rclone.clear("lsjson")
    child = wait_final(authed, api_post(authed, f"/api/runs/{second['id']}/retry").json()["id"])
    assert child["status"] == "success"
    assert get_run(authed, second["id"])["status"] == "success"
    assert auth_status(authed, drive["id"]) == "ok"


def test_only_blocked_destinations_fail_in_preflight(
    authed: TestClient, fake_rclone: FakeRclone, fake_bins: Any
) -> None:
    drive = make_drive(authed)
    with Session(get_engine()) as db:
        row = db.get(Destination, uuid.UUID(drive["id"]))
        assert row is not None
        row.auth_status = "needs_reauth"
        db.add(row)
        db.commit()
    job = make_job(authed, make_source(authed), [drive])
    run = run_now(authed, job)
    assert run["status"] == "failed"
    assert "reconnect in Destinations" in run["error"]
    assert not fake_bins.exists("mysqldump.args")
    assert fake_rclone.calls() == []


def test_retry_rules(authed: TestClient, fake_rclone: FakeRclone) -> None:
    job, _ = setup_job(authed)
    ok_run = run_now(authed, job)
    assert api_post(authed, f"/api/runs/{ok_run['id']}/retry").status_code == 409
    assert api_post(authed, f"/api/runs/{uuid.uuid4()}/retry").status_code == 404

    time.sleep(1.1)
    fake_rclone.fail("copyto", 1, NETWORK)
    run = run_now(authed, job)
    assert run["status"] == "partial"
    fake_rclone.clear("copyto")

    # The local copy was tampered with: its SHA-256 no longer matches, so it is not used.
    local = get_settings().backup_dir / "daily"
    first = next(a for a in run["artifacts"])
    (local / first["path"]).write_bytes(b"tampered")
    child = wait_final(authed, api_post(authed, f"/api/runs/{run['id']}/retry").json()["id"])
    assert child["status"] == "partial"
    assert get_run(authed, run["id"])["status"] == "partial"
    log = authed.get(f"/api/runs/{child['id']}/logs?format=text").text
    assert "SHA-256" in log

    # No live copy left at all: 422.
    with Session(get_engine()) as db:
        for art in db.exec(select(Artifact).where(col(Artifact.run_id) == uuid.UUID(run["id"]))):
            art.deleted_at = art.created_at
            db.add(art)
        db.commit()
    response = api_post(authed, f"/api/runs/{run['id']}/retry")
    assert response.status_code == 422
    assert "Run the whole job again" in response.json()["detail"]


def test_retry_conflicts_while_running(authed: TestClient, fake_rclone: FakeRclone) -> None:
    job, _ = setup_job(authed)
    fake_rclone.fail("copyto", 1, NETWORK)
    run = run_now(authed, job)
    fake_rclone.clear("copyto")
    fake_rclone.set("delay", "0.5")
    first = api_post(authed, f"/api/runs/{run['id']}/retry")
    assert first.status_code == 201
    assert api_post(authed, f"/api/runs/{run['id']}/retry").status_code == 409
    assert api_post(authed, f"/api/jobs/{job['id']}/run").status_code == 409
    wait_final(authed, first.json()["id"])


def test_refreshed_token_persists_across_concurrent_jobs(
    authed: TestClient, fake_rclone: FakeRclone
) -> None:
    drive = make_drive(authed)
    local = make_destination(authed)
    source = make_source(authed)
    jobs = [make_job(authed, source, [local, drive], name=f"job{i}") for i in range(2)]
    new = {**TOKEN, "access_token": "ya29.Refreshed", "expiry": "2026-11-23T10:00:00Z"}
    fake_rclone.refresh_to(new)
    fake_rclone.set("delay", "0.1")
    runs = [start(authed, j) for j in jobs]
    finals = [wait_final(authed, r["id"]) for r in runs]
    assert [r["status"] for r in finals] == ["success", "success"]
    assert fake_rclone.peak() == 1
    assert json.loads(stored_secrets(drive["id"])["token"]) == new


def test_gdrive_destination_api(authed: TestClient, fake_rclone: FakeRclone) -> None:
    body: dict[str, Any] = {
        "type": "gdrive",
        "name": "drive",
        "config": {
            "remote_folder": "//Backups//db/",
            "client_id": "cid.apps.googleusercontent.com",
        },
        "secrets": {"token": token_json(), "client_secret": "csecret"},
    }
    response = api_post(authed, "/api/destinations", body)
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["config"]["remote_folder"] == "Backups/db"
    assert created["secrets_set"] == {"token": True, "client_secret": True}
    assert created["auth_status"] == "unknown"
    assert_no_secrets(response.text, authed.get("/api/destinations").text)

    bad = {**body, "name": "bad", "secrets": {"token": '{"access_token": "ya29.Leak"}'}}
    response = api_post(authed, "/api/destinations", bad)
    assert response.status_code == 422 and "ya29.Leak" not in response.text
    lone = {**body, "name": "lone", "secrets": {"token": token_json()}}
    assert api_post(authed, "/api/destinations", lone).status_code == 422
    escape = {**body, "name": "esc", "config": {"remote_folder": "a/../b"}}
    assert api_post(authed, "/api/destinations", escape).status_code == 422

    test = api_post(authed, f"/api/destinations/{created['id']}/test")
    assert test.status_code == 200 and test.json()["ok"], test.text
    assert [c["name"] for c in test.json()["checks"]] == ["folder", "space", "write"]
    assert test.json()["free_bytes"] == 14 * 2**30
    assert auth_status(authed, created["id"]) == "ok"

    # Omit keeps, null clears (both client fields together), a value replaces.
    config = {"remote_folder": "Backups/db", "client_id": "cid.apps.googleusercontent.com"}
    keep = {**body, "config": config, "secrets": {}}
    assert api_put(authed, f"/api/destinations/{created['id']}", keep).status_code == 200
    assert stored_secrets(created["id"])["client_secret"] == "csecret"
    assert auth_status(authed, created["id"]) == "ok"
    cleared = {
        **body,
        "config": {"remote_folder": "Backups/db"},
        "secrets": {"client_secret": None},
    }
    response = api_put(authed, f"/api/destinations/{created['id']}", cleared)
    assert response.status_code == 200, response.text
    assert response.json()["secrets_set"] == {"token": True, "client_secret": False}
    assert "client_secret" not in stored_secrets(created["id"])

    with Session(get_engine()) as db:
        details = [json.dumps(a.details) for a in db.exec(select(AuditLog)).all()]
    assert_no_secrets(*details)


def test_dashboard_shape(authed: TestClient, fake_rclone: FakeRclone) -> None:
    job, drive = setup_job(authed)
    run_now(authed, job)
    data = authed.get("/api/dashboard").json()
    assert [j["job_name"] for j in data["jobs"]] == ["nightly"]
    health = {d["name"]: d for d in data["destinations"]}
    assert health["gdrive1"]["last_success_at"] and health["local1"]["last_success_at"]
    assert health["gdrive1"]["auth_status"] == "ok"
    assert health["gdrive1"]["last_error_class"] is None

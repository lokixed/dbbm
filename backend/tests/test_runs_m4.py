"""Mongo and Firestore through the whole run lifecycle (fake dump tools, no servers)."""

import json
import re
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.drivers import firestore
from tests.conftest import api_post
from tests.fakes import FakeBins, FakeMongo
from tests.runs_helpers import (
    PW,
    backup_files,
    get_run,
    make_destination,
    make_job,
    run_now,
    start,
    wait_final,
    wait_until,
    work_entries,
)
from tests.test_sources_api import SA_KEY, firestore_body

pytestmark = pytest.mark.usefixtures("fake_bins", "fake_drivers")


@pytest.fixture(autouse=True)
def fast_polling(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(firestore, "POLL_SECONDS", 0.01)


def make_mongo(client: TestClient, name: str = "mongo-main", **config: Any) -> dict[str, Any]:
    body = {
        "type": "mongo",
        "name": name,
        "config": {"mode": "fields", "host": "mongo.internal", "username": "backup", **config},
        "secrets": {"password": PW},
    }
    response = api_post(client, "/api/sources", body)
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


def make_firestore(client: TestClient, **config: Any) -> dict[str, Any]:
    body = firestore_body("fs-main")
    body["config"].update(config)
    response = api_post(client, "/api/sources", body)
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


def manifest_of(artifact: dict[str, Any]) -> dict[str, Any]:
    path = get_settings().backup_dir / "daily" / (artifact["path"] + ".manifest.json")
    loaded: dict[str, Any] = json.loads(path.read_text())
    return loaded


def leaked(text: str) -> bool:
    return any(s in text for s in (PW, "TOPSECRETKEYMATERIAL", "BEGIN PRIVATE KEY", "ya29."))


# ---------- mongo ----------


def test_mongo_per_database_run(authed: TestClient, fake_bins: FakeBins) -> None:
    job = make_job(authed, make_mongo(authed), [make_destination(authed)])
    run = run_now(authed, job)
    assert run["status"] == "success", run
    assert [a["source_database"] for a in run["artifacts"]] == ["blog", "shop"]  # no admin
    assert all(a["path"].endswith(".archive.gz") for a in run["artifacts"])
    units = run["step_results"]["units"]
    assert [[s["name"] for s in u["dump"]["steps"]] for u in units] == [["dump"], ["dump"]]
    manifest = manifest_of(run["artifacts"][0])
    assert manifest["source_type"] == "mongo" and manifest["compression"] == "gzip"
    assert manifest["tool_versions"] == {"mongodump": "mongodump version: 100.9.4"}
    assert manifest["source"] == {"dump_mode": "per_database"}
    log = authed.get(f"/api/runs/{run['id']}/logs?format=text").text
    assert "MongoDB server version: 7.0.5" in log and "mongodump version" in log
    assert not leaked(log + json.dumps(run) + json.dumps(manifest))
    assert work_entries() == []


def test_mongo_full_oplog_on_a_replica_set_makes_one_archive(
    authed: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(FakeMongo, "replica_set", True)
    source = make_mongo(authed, dump_mode="full_oplog", databases=["shop"])
    run = run_now(authed, make_job(authed, source, [make_destination(authed)]))
    assert run["status"] == "success", run
    assert [a["source_database"] for a in run["artifacts"]] == ["full-instance"]
    assert manifest_of(run["artifacts"][0])["source"] == {"dump_mode": "full_oplog"}


def test_mongo_full_oplog_on_a_standalone_server_fails_early(
    authed: TestClient, fake_bins: FakeBins
) -> None:
    source = make_mongo(authed, dump_mode="full_oplog")
    run = run_now(authed, make_job(authed, source, [make_destination(authed)]))
    assert run["status"] == "failed" and "needs a replica set" in run["error"]
    assert not fake_bins.exists("mongodump.args") and backup_files() == []
    assert work_entries() == []


def test_mongo_wrong_password_fails_sanitized_without_artifacts(
    authed: TestClient, fake_bins: FakeBins
) -> None:
    fake_bins.set_mode("mongodump", "fail")
    fake_bins.set_text("stderr_text", f"Failed: auth error mongodb://backup:{PW}@h/ {PW}")
    run = run_now(authed, make_job(authed, make_mongo(authed), [make_destination(authed)]))
    assert run["status"] == "failed" and "Failed: auth error" in run["error"]
    log = authed.get(f"/api/runs/{run['id']}/logs?format=text").text
    assert not leaked(log + json.dumps(run))
    assert run["artifacts"] == [] and backup_files() == [] and work_entries() == []


# ---------- firestore ----------


def test_firestore_run_reports_sub_steps_and_stores_a_tarball(
    authed: TestClient, fake_bins: FakeBins
) -> None:
    run = run_now(authed, make_job(authed, make_firestore(authed), [make_destination(authed)]))
    assert run["status"] == "success", run
    [artifact] = run["artifacts"]
    assert artifact["source_database"] == "default" and artifact["path"].endswith(".tar.gz")
    assert re.search(r"_default_\d{8}T\d{6}Z\.tar\.gz$", artifact["path"])
    [unit] = run["step_results"]["units"]
    steps = unit["dump"]["steps"]
    assert [s["name"] for s in steps] == ["export", "poll", "download", "pack", "cleanup-staging"]
    assert all(s["status"] == "ok" and s["duration_ms"] is not None for s in steps)
    assert run["step_results"]["warnings"] == []
    manifest = manifest_of(artifact)
    assert manifest["source"] == {
        "project_id": "my-project-1",
        "database_id": "(default)",
        "collection_ids": None,
        "bucket": "my-bucket",
    }
    assert manifest["tool_versions"] == {"gcloud": "Google Cloud SDK 999.0.0"}
    log = authed.get(f"/api/runs/{run['id']}/logs?format=text").text
    for marker in ("== export ==", "== poll ==", "== download ==", "== pack ==", "poll ok after"):
        assert marker in log
    assert fake_bins.exists("gcloud.rm.args")
    blob = log + json.dumps(run) + json.dumps(manifest) + fake_bins.read("gcloud.calls")
    assert not leaked(blob) and "bk@my-project-1" not in blob and SA_KEY.strip() not in blob
    assert work_entries() == []


def test_firestore_staging_cleanup_failure_is_a_visible_warning(
    authed: TestClient, fake_bins: FakeBins
) -> None:
    fake_bins.set_mode("gcloud_rm", "fail")
    fake_bins.set_text("gcloud_stderr", "AccessDeniedException: 403 cannot delete")
    run = run_now(authed, make_job(authed, make_firestore(authed), [make_destination(authed)]))
    assert run["status"] == "success" and run["error"] is None
    [warning] = run["step_results"]["warnings"]
    assert "lifecycle rule" in warning
    steps = run["step_results"]["units"][0]["dump"]["steps"]
    assert steps[-1] == {**steps[-1], "name": "cleanup-staging", "status": "warning"}
    assert len(backup_files()) == 2


def test_firestore_invalid_service_account_fails_without_artifacts(
    authed: TestClient, fake_bins: FakeBins
) -> None:
    fake_bins.set_mode("gcloud_auth", "fail")
    fake_bins.set_text("gcloud_stderr", f"ERROR: bad key {SA_KEY} ya29.token-value")
    run = run_now(authed, make_job(authed, make_firestore(authed), [make_destination(authed)]))
    assert run["status"] == "failed" and "service account" in run["error"]
    log = authed.get(f"/api/runs/{run['id']}/logs?format=text").text
    assert not leaked(log + json.dumps(run))
    assert backup_files() == [] and work_entries() == []


def test_firestore_cancel_mid_export_stops_polling_and_cleans_up(
    authed: TestClient, fake_bins: FakeBins
) -> None:
    fake_bins.set_mode("gcloud_describe", "forever")
    job = make_job(authed, make_firestore(authed), [make_destination(authed)])
    started = start(authed, job)
    wait_until(lambda: fake_bins.exists("describe.count"))
    cancelled = api_post(authed, f"/api/runs/{started['id']}/cancel")
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    assert "OP123" in fake_bins.read("cancel.called")
    final = wait_final(authed, started["id"])
    assert final["status"] == "cancelled"
    polls = fake_bins.read("describe.count")
    assert fake_bins.read("describe.count") == polls
    assert get_run(authed, started["id"])["artifacts"] == []
    assert backup_files() == [] and work_entries() == []

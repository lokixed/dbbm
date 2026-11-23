import hashlib
import json
import shutil
import stat
import time
from collections import namedtuple

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.config import get_settings
from app.core.db import get_engine
from app.models import AuditLog
from tests.conftest import api_delete, api_post
from tests.fakes import FakeBins, FakeMySQL, alive
from tests.runs_helpers import (
    PW,
    backup_files,
    get_run,
    make_destination,
    make_job,
    make_source,
    run_now,
    start,
    wait_final,
    wait_until,
    work_entries,
)

pytestmark = pytest.mark.usefixtures("fake_bins", "fake_drivers")


@pytest.fixture
def one_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_CONCURRENT_RUNS", "1")


def secret_free(*texts: str) -> None:
    for text in texts:
        assert PW not in text


# ---------- success path ----------


def test_mysql_run_end_to_end(authed: TestClient, fake_bins: FakeBins) -> None:
    source, dest = make_source(authed), make_destination(authed)
    job = make_job(authed, source, [dest])
    started = start(authed, job)
    assert started["status"] in ("queued", "running") and started["trigger"] == "manual"

    run = wait_final(authed, started["id"])
    assert run["status"] == "success" and run["error"] is None
    assert [a["source_database"] for a in run["artifacts"]] == ["blog", "shop"]
    assert run["artifact_count"] == 2
    assert run["total_size"] == sum(a["size_bytes"] for a in run["artifacts"])
    names = [s["name"] for s in run["step_results"]["steps"]]
    assert names == ["preflight", "backup", "cleanup"]
    assert [u["database"] for u in run["step_results"]["units"]] == ["shop", "blog"]

    base = get_settings().backup_dir / "daily"
    for artifact in run["artifacts"]:
        path = base / artifact["path"]
        year_month = artifact["path"].split("/")[1:3]
        assert artifact["path"].startswith("nightly/") and len(year_month[0]) == 4
        assert (
            artifact["path"].endswith(".sql.gz") and f"_{artifact['source_database']}_" in path.name
        )
        data = path.read_bytes()
        assert len(data) == artifact["size_bytes"]
        assert hashlib.sha256(data).hexdigest() == artifact["sha256"]
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        manifest_path = path.with_name(path.name + ".manifest.json")
        manifest = json.loads(manifest_path.read_text())
        assert stat.S_IMODE(manifest_path.stat().st_mode) == 0o600
        assert manifest["job"] == "nightly" and manifest["source_type"] == "mysql"
        assert manifest["database"] == artifact["source_database"]
        assert manifest["sha256"] == artifact["sha256"] and manifest["size_bytes"] == len(data)
        assert manifest["compression"] == "gzip" and "mysqldump" in manifest["tool_versions"]
        assert manifest["created_at"].endswith("Z")
        text = manifest_path.read_text()
        assert "db.internal" not in text and "backup" not in manifest and PW not in text

    assert len(backup_files()) == 4  # two artifacts and two manifests
    assert work_entries() == []  # the work directory is gone
    log = authed.get(f"/api/runs/{run['id']}/logs?format=text").text
    assert "Dump of shop finished" in log and "Work directory removed" in log
    secret_free(log, json.dumps(run), authed.get("/api/jobs").text)
    log_file = get_settings().logs_dir / f"{run['id']}.log"
    assert stat.S_IMODE(log_file.stat().st_mode) == 0o600


def test_second_run_never_overwrites(authed: TestClient) -> None:
    job = make_job(authed, make_source(authed), [make_destination(authed)])
    first = run_now(authed, job)
    before = {f: (get_settings().backup_dir / f).read_bytes() for f in backup_files()}
    time.sleep(1.1)  # artifact names carry a timestamp with one-second resolution
    second = run_now(authed, job)
    assert first["status"] == second["status"] == "success"
    after = backup_files()
    assert len(after) == 8 and set(before) < set(after)
    assert all((get_settings().backup_dir / f).read_bytes() == data for f, data in before.items())


def test_postgres_run_and_globals_failure_is_only_a_warning(
    authed: TestClient, fake_bins: FakeBins
) -> None:
    fake_bins.set_mode("pg_dumpall", "fail")
    source = make_source(authed, "pg-main", "postgres", include_globals=True, databases="all")
    job = make_job(authed, source, [make_destination(authed)], "pgjob", compress=False)
    run = run_now(authed, job)
    assert run["status"] == "success", run
    assert [a["source_database"] for a in run["artifacts"]] == ["blog", "shop"]  # not "postgres"
    assert all(a["path"].endswith(".dump") for a in run["artifacts"])
    assert any("Globals dump failed" in w for w in run["step_results"]["warnings"])
    assert not any("globals" in f for f in backup_files())


def test_postgres_globals_are_stored_when_they_succeed(authed: TestClient) -> None:
    source = make_source(authed, "pg-main", "postgres", include_globals=True)
    run = run_now(authed, make_job(authed, source, [make_destination(authed)], "pgjob"))
    assert run["status"] == "success"
    assert [a["source_database"] for a in run["artifacts"]] == ["blog", "globals", "shop"]
    assert sum("_globals_" in f for f in backup_files()) == 2  # artifact and manifest


# ---------- failures ----------


def test_dump_failure_fails_the_run_and_leaves_no_artifact(
    authed: TestClient, fake_bins: FakeBins
) -> None:
    fake_bins.set_mode("mysqldump", "fail")
    fake_bins.set_text("stderr_text", f"mysqldump: Got error: 1045 using password={PW}\n")
    job = make_job(authed, make_source(authed), [make_destination(authed)])
    run = run_now(authed, job)
    assert run["status"] == "failed" and run["artifacts"] == []
    assert "exited with code 2" in run["error"]
    secret_free(
        run["error"], json.dumps(run), authed.get(f"/api/runs/{run['id']}/logs?format=text").text
    )
    assert backup_files() == [] and work_entries() == []
    units = run["step_results"]["units"]
    assert all(u["dump"]["status"] == "failed" for u in units)
    assert all(d["status"] == "skipped" for u in units for d in u["destinations"])


def test_one_failed_database_does_not_stop_the_others(
    authed: TestClient, fake_bins: FakeBins
) -> None:
    # Fail only while dumping "shop": the script switches mode when it sees that name.
    script = fake_bins.dir / "mysqldump"
    text = script.read_text().replace(
        'mode=$(cat "$dir/mysqldump.mode" 2>/dev/null || echo ok)',
        'mode=ok; [ "$last" = "shop" ] && mode=fail',
    )
    script.write_text(text)
    fake_bins.set_text("stderr_text", "mysqldump: Got error: 1044 access denied\n")
    job = make_job(authed, make_source(authed), [make_destination(authed)])
    run = run_now(authed, job)
    assert run["status"] == "partial"
    assert [a["source_database"] for a in run["artifacts"]] == ["blog"]
    assert "shop:" in run["error"]


def test_partial_when_one_destination_fails(authed: TestClient) -> None:
    blocked = get_settings().backup_dir / "blocked"
    blocked.parent.mkdir(parents=True, exist_ok=True)
    blocked.write_text("a file where a directory is needed")
    good, bad = (
        make_destination(authed, "good", "daily"),
        make_destination(authed, "bad", "blocked"),
    )
    run = run_now(authed, make_job(authed, make_source(authed), [good, bad]))
    assert run["status"] == "partial"
    assert {a["destination_name"] for a in run["artifacts"]} == {"good"}
    results = [d for u in run["step_results"]["units"] for d in u["destinations"]]
    assert sorted({(d["destination_name"], d["status"]) for d in results}) == [
        ("bad", "failed"),
        ("good", "ok"),
    ]
    assert "→ bad:" in run["error"] and str(get_settings().backup_dir) not in run["error"]
    assert work_entries() == []


def test_failed_when_every_destination_fails(authed: TestClient) -> None:
    blocked = get_settings().backup_dir / "blocked"
    blocked.parent.mkdir(parents=True, exist_ok=True)
    blocked.write_text("x")
    dest = make_destination(authed, "bad", "blocked")
    run = run_now(authed, make_job(authed, make_source(authed), [dest]))
    assert run["status"] == "failed" and run["artifacts"] == []
    assert work_entries() == []


def test_preflight_connection_failure(authed: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(FakeMySQL, "ok", False)
    run = run_now(authed, make_job(authed, make_source(authed), [make_destination(authed)]))
    assert run["status"] == "failed" and "Cannot connect to the source" in run["error"]
    assert run["step_results"]["steps"][0]["status"] == "failed"
    assert backup_files() == [] and work_entries() == []


def test_preflight_disk_space(authed: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    usage = namedtuple("usage", "total used free")  # noqa: PYI024
    monkeypatch.setattr(shutil, "disk_usage", lambda _p: usage(10**9, 10**9 - 10**6, 10**6))
    run = run_now(authed, make_job(authed, make_source(authed), [make_destination(authed)]))
    assert run["status"] == "failed"
    assert "Not enough free disk space" in run["error"] and "256.0 MiB" in run["error"]
    assert backup_files() == [] and work_entries() == []


def test_preflight_requires_one_and_a_half_times_the_last_artifact(
    authed: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = make_job(authed, make_source(authed), [make_destination(authed)])
    first = run_now(authed, job)
    biggest = max(a["size_bytes"] for a in first["artifacts"])
    usage = namedtuple("usage", "total used free")  # noqa: PYI024
    free = int(biggest * 1.5) - 1
    monkeypatch.setattr(shutil, "disk_usage", lambda _p: usage(10**9, 0, free))
    time.sleep(1.1)
    second = run_now(authed, job)
    assert second["status"] == "failed" and "Not enough free disk space" in second["error"]


def test_old_pg_client_fails_before_dumping(authed: TestClient, fake_bins: FakeBins) -> None:
    fake_bins.set_text("pg_version", "15.6")
    source = make_source(authed, "pg-main", "postgres")
    run = run_now(authed, make_job(authed, source, [make_destination(authed)], "pgjob"))
    assert run["status"] == "failed" and "older than the PostgreSQL server (16)" in run["error"]
    assert not fake_bins.exists("pg_dump.args") and backup_files() == []


def test_no_databases_to_back_up(authed: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(FakeMySQL, "listed", ["mysql", "sys"])
    source = make_source(authed, databases="all")
    run = run_now(authed, make_job(authed, source, [make_destination(authed)]))
    assert run["status"] == "failed" and "no databases to back up" in run["error"]


# ---------- locking, concurrency, cancel ----------


def test_second_run_of_the_same_job_is_409_and_cancel_cleans_up(
    authed: TestClient, fake_bins: FakeBins
) -> None:
    fake_bins.set_mode("mysqldump", "hang")
    job = make_job(authed, make_source(authed), [make_destination(authed)])
    first = start(authed, job)
    wait_until(lambda: fake_bins.exists("childpid") and fake_bins.read("childpid").strip() != "")
    assert get_run(authed, first["id"])["status"] == "running"
    assert work_entries() == [first["id"]]

    second = api_post(authed, f"/api/jobs/{job['id']}/run")
    assert second.status_code == 409 and "already has a run in progress" in second.json()["detail"]
    deleted = api_delete(authed, f"/api/jobs/{job['id']}")
    assert deleted.status_code == 409

    pid, child = int(fake_bins.read("pid")), int(fake_bins.read("childpid"))
    cancelled = api_post(authed, f"/api/runs/{first['id']}/cancel")
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    assert not alive(pid) and not alive(child)
    assert work_entries() == [] and backup_files() == []

    again = api_post(authed, f"/api/runs/{first['id']}/cancel")
    assert again.status_code == 409
    fake_bins.set_mode("mysqldump", "ok")
    assert run_now(authed, job)["status"] == "success"  # the job is free again


def test_global_limit_keeps_extra_runs_queued_and_queued_runs_can_be_cancelled(
    one_slot: None, authed: TestClient, fake_bins: FakeBins
) -> None:
    fake_bins.set_mode("mysqldump", "hang")
    source, dest = make_source(authed), make_destination(authed)
    job_a = make_job(authed, source, [dest], "job-a")
    job_b = make_job(authed, source, [dest], "job-b")
    run_a = start(authed, job_a)
    wait_until(lambda: fake_bins.exists("childpid") and fake_bins.read("childpid").strip() != "")
    run_b = start(authed, job_b)
    time.sleep(0.3)
    assert get_run(authed, run_b["id"])["status"] == "queued"
    assert work_entries() == [run_a["id"]]  # the queued run has not touched the disk

    cancelled = api_post(authed, f"/api/runs/{run_b['id']}/cancel").json()
    assert cancelled["status"] == "cancelled" and cancelled["finished_at"] is not None
    api_post(authed, f"/api/runs/{run_a['id']}/cancel")
    assert wait_final(authed, run_a["id"])["status"] == "cancelled"
    assert work_entries() == []


def test_queued_run_starts_when_a_slot_frees_up(
    one_slot: None, authed: TestClient, fake_bins: FakeBins
) -> None:
    fake_bins.set_mode("mysqldump", "hang")
    source, dest = make_source(authed), make_destination(authed)
    run_a = start(authed, make_job(authed, source, [dest], "job-a"))
    wait_until(lambda: fake_bins.exists("childpid") and fake_bins.read("childpid").strip() != "")
    run_b = start(authed, make_job(authed, source, [dest], "job-b"))
    fake_bins.set_mode("mysqldump", "ok")
    api_post(authed, f"/api/runs/{run_a['id']}/cancel")
    assert wait_final(authed, run_b["id"])["status"] == "success"


# ---------- runs API ----------


def test_runs_list_filters_and_pagination(authed: TestClient, fake_bins: FakeBins) -> None:
    source, dest = make_source(authed), make_destination(authed)
    job_a, job_b = (
        make_job(authed, source, [dest], "job-a"),
        make_job(authed, source, [dest], "job-b"),
    )
    ok = run_now(authed, job_a)
    fake_bins.set_mode("mysqldump", "fail")
    fake_bins.set_text("stderr_text", "boom\n")
    bad = run_now(authed, job_b)

    everything = authed.get("/api/runs").json()
    assert everything["total"] == 2 and [r["id"] for r in everything["items"]] == [
        bad["id"],
        ok["id"],
    ]
    assert "step_results" not in everything["items"][0]
    by_job = authed.get("/api/runs", params={"job_id": job_a["id"]}).json()
    assert [r["id"] for r in by_job["items"]] == [ok["id"]]
    by_status = authed.get("/api/runs", params={"status": "failed"}).json()
    assert [r["id"] for r in by_status["items"]] == [bad["id"]]
    assert authed.get("/api/runs", params={"status": "bogus"}).status_code == 422
    paged = authed.get("/api/runs", params={"limit": 1, "offset": 1}).json()
    assert paged["total"] == 2 and [r["id"] for r in paged["items"]] == [ok["id"]]
    future = authed.get("/api/runs", params={"from": "2999-01-01T00:00:00Z"}).json()
    assert future["total"] == 0
    past = authed.get("/api/runs", params={"to": "2000-01-01T00:00:00Z"}).json()
    assert past["total"] == 0
    assert authed.get("/api/runs/00000000-0000-0000-0000-000000000000").status_code == 404


def test_log_endpoints(authed: TestClient) -> None:
    run = run_now(authed, make_job(authed, make_source(authed), [make_destination(authed)]))
    path = f"/api/runs/{run['id']}/logs"

    plain = authed.get(path, params={"format": "text"})
    assert plain.headers["content-type"].startswith("text/plain") and "Run started" in plain.text
    by_accept = authed.get(path, headers={"Accept": "text/plain"})
    assert by_accept.text == plain.text

    sse = authed.get(path, headers={"Accept": "text/event-stream"})
    assert sse.headers["content-type"].startswith("text/event-stream")
    frames = [f for f in sse.text.split("\n\n") if f]
    assert frames[-1] == 'event: done\ndata: {"status": "success"}'
    ids = [int(f.split("\n")[0].removeprefix("id: ")) for f in frames[:-1]]
    assert ids == sorted(ids) and ids[-1] == len(plain.text.encode())

    # Resume after the third line, by query parameter and by Last-Event-ID.
    resumed = authed.get(path, params={"offset": ids[2]})
    resumed_header = authed.get(path, headers={"Last-Event-ID": str(ids[2])})
    assert resumed.text == resumed_header.text
    resumed_frames = [f for f in resumed.text.split("\n\n") if f]
    assert len(resumed_frames) == len(frames) - 3
    assert resumed_frames[0].split("\n")[1] == frames[3].split("\n")[1]
    assert authed.get(path, params={"format": "other"}).status_code == 422


def test_dashboard(authed: TestClient, fake_bins: FakeBins) -> None:
    source, dest = make_source(authed), make_destination(authed)
    idle = make_job(authed, source, [dest], "idle")
    job = make_job(authed, source, [dest], "worker")
    entry = {e["job_name"]: e for e in authed.get("/api/dashboard").json()["jobs"]}
    assert entry["idle"]["latest_status"] is None and entry["idle"]["last_artifact_size"] is None

    ok = run_now(authed, job)
    fake_bins.set_mode("mysqldump", "fail")
    fake_bins.set_text("stderr_text", "boom\n")
    time.sleep(1.1)
    bad = run_now(authed, job)
    entry = {e["job_name"]: e for e in authed.get("/api/dashboard").json()["jobs"]}["worker"]
    assert entry["latest_status"] == "failed" and entry["latest_run_id"] == bad["id"]
    assert entry["last_success_at"] is not None
    assert entry["last_artifact_size"] == ok["total_size"]
    assert idle["id"] != job["id"]


def test_deleting_a_job_keeps_its_runs_and_artifacts(authed: TestClient) -> None:
    job = make_job(authed, make_source(authed), [make_destination(authed)])
    run = run_now(authed, job)
    assert api_delete(authed, f"/api/jobs/{job['id']}").status_code == 204
    kept = get_run(authed, run["id"])
    assert kept["job_id"] is None and kept["job_name"] == "nightly" and len(kept["artifacts"]) == 2
    assert authed.get("/api/jobs").json() == []


def test_audit_entries_for_job_actions_contain_no_secrets(authed: TestClient) -> None:
    job = make_job(authed, make_source(authed), [make_destination(authed)])
    api_post(authed, f"/api/jobs/{job['id']}/disable")
    api_post(authed, f"/api/jobs/{job['id']}/enable")
    run = run_now(authed, job)
    api_delete(authed, f"/api/jobs/{job['id']}")
    with Session(get_engine()) as db:
        rows = db.exec(select(AuditLog)).all()
    actions = [r.action for r in rows]
    for expected in ("job.create", "job.disable", "job.enable", "job.run", "job.delete"):
        assert expected in actions
    assert any(r.action == "job.run" and r.details["run_id"] == run["id"] for r in rows)
    secret_free(json.dumps([r.details for r in rows]))


def test_run_cancel_is_audited(authed: TestClient, fake_bins: FakeBins) -> None:
    fake_bins.set_mode("mysqldump", "hang")
    job = make_job(authed, make_source(authed), [make_destination(authed)])
    run = start(authed, job)
    wait_until(lambda: fake_bins.exists("childpid") and fake_bins.read("childpid").strip() != "")
    api_post(authed, f"/api/runs/{run['id']}/cancel")
    with Session(get_engine()) as db:
        assert "run.cancel" in [r.action for r in db.exec(select(AuditLog)).all()]


def test_run_routes_need_auth_and_csrf(client: TestClient) -> None:
    assert client.get("/api/runs").status_code == 401
    assert client.get("/api/dashboard").status_code == 401
    assert client.get("/api/runs/00000000-0000-0000-0000-000000000000/logs").status_code == 401
    forbidden = client.post("/api/jobs/00000000-0000-0000-0000-000000000000/run")
    assert forbidden.status_code in (401, 403)

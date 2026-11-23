import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.config import get_settings
from app.core.db import get_engine
from app.main import create_app
from app.models import Job, Run
from app.services.runner import JobNotFoundError, Runner, compute_final_status
from tests.conftest import api_delete, api_post, api_put, login, setup_admin
from tests.fakes import FakeBins
from tests.runs_helpers import (
    make_destination,
    make_job,
    make_source,
    run_now,
    wait_until,
    work_entries,
)
from tests.test_sources_api import firestore_body, mongo_uri_body


def job_body(
    source: dict[str, Any], destinations: list[dict[str, Any]], **over: Any
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "nightly",
        "source_id": source["id"],
        "destination_ids": [d["id"] for d in destinations],
    }
    body.update(over)
    return body


# ---------- final status ----------


@pytest.mark.parametrize(
    ("outcomes", "expected"),
    [
        ([True, True], "success"),
        ([True], "success"),
        ([True, False], "partial"),
        ([False, True, False], "partial"),
        ([False, False], "failed"),
        ([False], "failed"),
        ([], "failed"),
    ],
)
def test_compute_final_status(outcomes: list[bool], expected: str) -> None:
    assert compute_final_status(outcomes) == expected


# ---------- validation ----------


def test_create_read_update_delete(authed: TestClient) -> None:
    source, d1, d2 = (
        make_source(authed),
        make_destination(authed, "d1", "a"),
        make_destination(authed, "d2", "b"),
    )
    created = api_post(authed, "/api/jobs", job_body(source, [d1], schedule="  0 3 * * *  ")).json()
    assert (
        created["name"] == "nightly" and created["enabled"] is True and created["compress"] is True
    )
    assert created["timeout_seconds"] == 3600 and created["schedule"] == "0 3 * * *"
    assert created["source"] == {"id": source["id"], "name": "mysql-main", "type": "mysql"}
    assert (
        created["destinations"] == [{"id": d1["id"], "name": "d1"}] and created["last_run"] is None
    )
    assert authed.get(f"/api/jobs/{created['id']}").json() == created

    updated = api_put(
        authed,
        f"/api/jobs/{created['id']}",
        job_body(
            source, [d2, d1], name="renamed", compress=False, timeout_seconds=120, schedule=" "
        ),
    ).json()
    assert (
        updated["name"] == "renamed"
        and updated["compress"] is False
        and updated["schedule"] is None
    )
    assert updated["timeout_seconds"] == 120
    assert [d["name"] for d in updated["destinations"]] == ["d1", "d2"]
    assert api_delete(authed, f"/api/jobs/{created['id']}").status_code == 204
    assert authed.get(f"/api/jobs/{created['id']}").status_code == 404


@pytest.mark.parametrize("body", [mongo_uri_body("mongo1"), firestore_body("fire1")])
def test_mongo_and_firestore_sources_are_accepted(authed: TestClient, body: dict[str, Any]) -> None:
    source = api_post(authed, "/api/sources", body).json()
    dest = make_destination(authed)
    created = api_post(authed, "/api/jobs", job_body(source, [dest]))
    assert created.status_code == 201
    assert created.json()["source"]["type"] == body["type"]


def test_reference_and_field_validation(authed: TestClient) -> None:
    source, dest = make_source(authed), make_destination(authed)
    missing = str(uuid.uuid4())
    assert api_post(authed, "/api/jobs", job_body({"id": missing}, [dest])).status_code == 422
    assert api_post(authed, "/api/jobs", job_body(source, [{"id": missing}])).status_code == 422
    assert api_post(authed, "/api/jobs", job_body(source, [])).status_code == 422
    assert (
        api_post(authed, "/api/jobs", job_body(source, [dest], schedule="x" * 101)).status_code
        == 422
    )
    for timeout in (9, 10**7, "100"):
        response = api_post(authed, "/api/jobs", job_body(source, [dest], timeout_seconds=timeout))
        assert response.status_code == 422
    assert api_post(authed, "/api/jobs", job_body(source, [dest], unknown=1)).status_code == 422
    assert api_post(authed, "/api/jobs", job_body(source, [dest], name=" ")).status_code == 422


def test_duplicate_name_conflicts(authed: TestClient) -> None:
    source, dest = make_source(authed), make_destination(authed)
    make_job(authed, source, [dest], "one")
    other = make_job(authed, source, [dest], "two")
    assert api_post(authed, "/api/jobs", job_body(source, [dest], name="one")).status_code == 409
    renamed = api_put(authed, f"/api/jobs/{other['id']}", job_body(source, [dest], name="one"))
    assert renamed.status_code == 409


def test_enable_and_disable(authed: TestClient) -> None:
    job = make_job(authed, make_source(authed), [make_destination(authed)])
    assert api_post(authed, f"/api/jobs/{job['id']}/disable").json()["enabled"] is False
    assert api_post(authed, f"/api/jobs/{job['id']}/enable").json()["enabled"] is True
    assert api_post(authed, f"/api/jobs/{uuid.uuid4()}/enable").status_code == 404


def test_jobs_need_auth(client: TestClient) -> None:
    assert client.get("/api/jobs").status_code == 401


# ---------- deletion guards ----------


def test_sources_and_destinations_in_use_cannot_be_deleted(authed: TestClient) -> None:
    source, dest = make_source(authed), make_destination(authed)
    job = make_job(authed, source, [dest], "uses-both")

    blocked_source = api_delete(authed, f"/api/sources/{source['id']}")
    assert blocked_source.status_code == 409 and "uses-both" in blocked_source.json()["detail"]
    blocked_dest = api_delete(authed, f"/api/destinations/{dest['id']}")
    assert blocked_dest.status_code == 409 and "uses-both" in blocked_dest.json()["detail"]
    assert authed.get(f"/api/sources/{source['id']}").status_code == 200

    assert api_delete(authed, f"/api/jobs/{job['id']}").status_code == 204
    assert api_delete(authed, f"/api/destinations/{dest['id']}").status_code == 204
    assert api_delete(authed, f"/api/sources/{source['id']}").status_code == 204


@pytest.mark.usefixtures("fake_bins", "fake_drivers")
def test_destination_with_recorded_backups_cannot_be_deleted(authed: TestClient) -> None:
    source, dest = make_source(authed), make_destination(authed)
    job = make_job(authed, source, [dest])
    assert run_now(authed, job)["status"] == "success"
    api_delete(authed, f"/api/jobs/{job['id']}")
    response = api_delete(authed, f"/api/destinations/{dest['id']}")
    assert response.status_code == 409 and "recorded backup" in response.json()["detail"]
    assert api_delete(authed, f"/api/sources/{source['id']}").status_code == 204


# ---------- runner housekeeping ----------


def test_submit_unknown_job() -> None:
    async def scenario() -> None:
        runner = Runner(get_settings())
        with pytest.raises(JobNotFoundError):
            await runner.submit(uuid.uuid4())
        assert not runner.is_active(uuid.uuid4())

    with TestClient(create_app()):  # applies the migrations
        asyncio.run(scenario())


def test_startup_recovery_marks_active_runs_interrupted_and_removes_work_dirs() -> None:
    settings = get_settings()
    with TestClient(create_app()):
        pass
    with Session(get_engine()) as db:
        ids = {}
        for status in ("queued", "running", "success", "failed"):
            run = Run(
                job_name="gone", trigger="manual", status=status, created_at=datetime.now(UTC)
            )
            db.add(run)
            ids[status] = run.id
        db.commit()
    leftover = settings.work_dir / str(ids["running"])
    (leftover / "nested").mkdir(parents=True)
    (leftover / "nested" / "dump.sql.gz.partial").write_bytes(b"half a dump")
    (settings.work_dir / "stray-file").write_text("x")

    with TestClient(create_app()):  # a restart
        pass

    with Session(get_engine()) as db:
        statuses = {name: db.get(Run, rid) for name, rid in ids.items()}
    assert statuses["queued"] is not None and statuses["queued"].status == "interrupted"
    assert statuses["running"] is not None and statuses["running"].status == "interrupted"
    assert statuses["running"].finished_at is not None and "restarted" in (
        statuses["running"].error or ""
    )
    assert statuses["success"] is not None and statuses["success"].status == "success"
    assert statuses["failed"] is not None and statuses["failed"].status == "failed"
    assert work_entries() == []


@pytest.mark.usefixtures("fake_drivers")
def test_stopping_the_service_interrupts_the_active_run(
    fake_bins: FakeBins, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_bins.set_mode("mysqldump", "hang")
    with TestClient(create_app()) as client:
        setup_admin(client)
        login(client)
        job = make_job(client, make_source(client), [make_destination(client)])
        run = api_post(client, f"/api/jobs/{job['id']}/run").json()
        wait_until(lambda: fake_bins.exists("childpid") and fake_bins.read("childpid").strip())
    with Session(get_engine()) as db:
        stored = db.get(Run, uuid.UUID(run["id"]))
        assert stored is not None and stored.status == "interrupted"
        assert db.exec(select(Job)).all()
    assert work_entries() == []

"""Scheduler: registration sync, firing, overlap, catch-up, single-owner lock and health."""

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, col, select

from app.core.config import get_settings
from app.core.db import get_engine
from app.main import create_app
from app.models import AuditLog, Job
from app.services.scheduler import (
    JobScheduler,
    JobSpec,
    SchedulerLock,
    SchedulerState,
    check_single_worker,
    configured_workers,
)
from tests.conftest import api_delete, api_post, api_put, login
from tests.m6 import (
    FakeClock,
    db_ready,
    drain,
    get_job_row,
    on_loop,
    seed_job,
    services_of,
)
from tests.runs_helpers import make_destination, make_job, make_source, wait_final


class FakeRunner:
    def __init__(self) -> None:
        self.active: set[uuid.UUID] = set()
        self.submitted: list[tuple[uuid.UUID, str, datetime | None]] = []

    def is_active(self, job_id: uuid.UUID) -> bool:
        return job_id in self.active

    async def submit(
        self, job_id: uuid.UUID, trigger: str = "manual", scheduled_for: datetime | None = None
    ) -> uuid.UUID:
        self.submitted.append((job_id, trigger, scheduled_for))
        return uuid.uuid4()


def make_scheduler(clock: FakeClock, runner: FakeRunner) -> JobScheduler:
    fake: Any = runner
    return JobScheduler(get_settings(), fake, clock=clock)


def spec(job_id: uuid.UUID) -> JobSpec:
    with Session(get_engine()) as db:
        job = db.get(Job, job_id)
        assert job is not None
        return JobSpec.of(job)


# ---------- API registration sync ----------


def _job(client: TestClient, **over: Any) -> dict[str, Any]:
    return make_job(client, make_source(client), [make_destination(client)], **over)


def test_create_edit_disable_delete_keep_scheduler_in_sync(m6_client: TestClient) -> None:
    scheduler = services_of(m6_client).scheduler
    job = _job(m6_client, schedule="0 3 * * *", timezone="Europe/Berlin")
    assert job["timezone"] == "Europe/Berlin"
    assert job["schedule_error"] is None
    assert scheduler.registered_jobs() == [job["id"]]
    first = datetime.fromisoformat(job["next_run_at"])
    assert first.astimezone(UTC).hour in (2, 1)  # 03:00 Berlin

    body = {
        "name": job["name"],
        "source_id": job["source"]["id"],
        "destination_ids": [d["id"] for d in job["destinations"]],
        "schedule": "30 4 * * *",
        "timezone": "UTC",
    }
    edited = api_put(m6_client, f"/api/jobs/{job['id']}", body).json()
    assert (
        datetime.fromisoformat(edited["next_run_at"]).astimezone(UTC).strftime("%H:%M") == "04:30"
    )

    disabled = api_post(m6_client, f"/api/jobs/{job['id']}/disable").json()
    assert disabled["next_run_at"] is None
    assert scheduler.registered_jobs() == []
    enabled = api_post(m6_client, f"/api/jobs/{job['id']}/enable").json()
    assert enabled["next_run_at"] is not None

    manual = api_put(m6_client, f"/api/jobs/{job['id']}", {**body, "schedule": None}).json()
    assert manual["next_run_at"] is None and scheduler.registered_jobs() == []

    api_put(m6_client, f"/api/jobs/{job['id']}", body)
    assert scheduler.registered_jobs() == [job["id"]]
    assert api_delete(m6_client, f"/api/jobs/{job['id']}").status_code == 204
    assert scheduler.registered_jobs() == []


def test_job_timezone_defaults_to_setting(m6_client: TestClient) -> None:
    settings = m6_client.get("/api/settings").json()
    settings.pop("updated_at")
    saved = api_put(m6_client, "/api/settings", {**settings, "default_timezone": "Asia/Kolkata"})
    assert saved.status_code == 200, saved.text
    job = _job(m6_client, schedule="0 3 * * *")
    assert job["timezone"] == "Asia/Kolkata"


@pytest.mark.parametrize(
    ("over", "loc"),
    [
        ({"schedule": "* * * * *"}, "schedule"),
        ({"schedule": "0 0 13 * 5"}, "schedule"),
        ({"schedule": "nonsense"}, "schedule"),
        ({"schedule": "0 3 * * *", "timezone": "Mars/Base"}, "timezone"),
    ],
)
def test_invalid_schedule_rejected(authed: TestClient, over: dict[str, Any], loc: str) -> None:
    body = {
        "name": "j",
        "source_id": make_source(authed)["id"],
        "destination_ids": [make_destination(authed)["id"]],
        **over,
    }
    response = api_post(authed, "/api/jobs", body)
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"][-1] == loc


def test_legacy_free_text_schedule_is_reported_not_registered(m6_client: TestClient) -> None:
    job = _job(m6_client)
    with Session(get_engine()) as db:
        row = db.get(Job, uuid.UUID(job["id"]))
        assert row is not None
        row.schedule = "every night at 3"
        db.add(row)
        db.commit()
    on_loop(m6_client, lambda: services_of(m6_client).scheduler.sync_job(uuid.UUID(job["id"])))
    out = m6_client.get(f"/api/jobs/{job['id']}").json()
    assert "5 fields" in out["schedule_error"]
    assert out["next_run_at"] is None
    assert services_of(m6_client).scheduler.registered_jobs() == []


# ---------- firing ----------


@pytest.mark.usefixtures("fake_bins", "fake_drivers")
def test_scheduled_trigger_creates_schedule_run(m6_client: TestClient) -> None:
    job = _job(m6_client, schedule="0 3 * * *")
    job_id = uuid.UUID(job["id"])
    fire = datetime(2026, 11, 23, 3, 0, tzinfo=UTC)
    run_id = on_loop(m6_client, lambda: services_of(m6_client).scheduler.trigger(job_id, fire))
    run = wait_final(m6_client, str(run_id))
    assert run["trigger"] == "schedule"
    assert run["status"] == "success"
    assert datetime.fromisoformat(run["scheduled_for"]) == fire
    assert get_job_row(job_id).last_scheduled_fire_at == fire
    drain(m6_client)


@pytest.mark.usefixtures("fake_bins", "fake_drivers")
def test_active_run_is_skipped_and_recorded(m6_client: TestClient, fake_bins: Any) -> None:
    fake_bins.set_mode("mysqldump", "hang")
    job = _job(m6_client, schedule="0 3 * * *")
    job_id = uuid.UUID(job["id"])
    running = api_post(m6_client, f"/api/jobs/{job['id']}/run").json()
    fire = datetime(2026, 11, 23, 3, 0, tzinfo=UTC)
    assert (
        on_loop(m6_client, lambda: services_of(m6_client).scheduler.trigger(job_id, fire)) is None
    )
    with Session(get_engine()) as db:
        skipped = db.exec(select(AuditLog).where(col(AuditLog.action) == "schedule.skipped")).all()
    assert [s.object_id for s in skipped] == [job["id"]]
    assert get_job_row(job_id).last_scheduled_fire_at == fire
    dashboard = m6_client.get("/api/dashboard").json()
    assert dashboard["jobs"][0]["skipped_schedules_24h"] == 1
    api_post(m6_client, f"/api/runs/{running['id']}/cancel")
    wait_final(m6_client, running["id"])


def test_fire_uses_the_scheduled_time(fake_clock: FakeClock) -> None:
    db_ready()
    fake_clock.current = datetime(2026, 11, 23, 12, 15, 4, tzinfo=UTC)
    job_id = seed_job(schedule="*/15 * * * *", created_at=fake_clock.current)
    runner = FakeRunner()

    async def go() -> None:
        scheduler = make_scheduler(fake_clock, runner)
        await scheduler.start()
        try:
            await scheduler._fire(job_id)
        finally:
            await scheduler.stop()

    asyncio.run(go())
    assert runner.submitted == [(job_id, "schedule", datetime(2026, 11, 23, 12, 15, tzinfo=UTC))]


# ---------- catch-up ----------


def _catch_up(clock: FakeClock, runner: FakeRunner, job_ids: list[uuid.UUID]) -> list[uuid.UUID]:
    async def go() -> list[uuid.UUID]:
        scheduler = make_scheduler(clock, runner)
        return await scheduler.catch_up([spec(j) for j in job_ids])

    return asyncio.run(go())


def test_catch_up_within_grace_runs_once(fake_clock: FakeClock) -> None:
    db_ready()
    created = datetime(2026, 11, 1, tzinfo=UTC)
    job_id = seed_job(
        schedule="0 3 * * *",
        created_at=created,
        last_scheduled_fire_at=datetime(2026, 11, 22, 3, 0, tzinfo=UTC),
    )
    fake_clock.current = datetime(2026, 11, 23, 3, 40, tzinfo=UTC)
    runner = FakeRunner()
    assert len(_catch_up(fake_clock, runner, [job_id])) == 1
    assert runner.submitted == [(job_id, "schedule", datetime(2026, 11, 23, 3, 0, tzinfo=UTC))]
    assert get_job_row(job_id).last_scheduled_fire_at == datetime(2026, 11, 23, 3, 0, tzinfo=UTC)


def test_several_missed_fires_coalesce(fake_clock: FakeClock) -> None:
    db_ready()
    job_id = seed_job(
        schedule="*/5 * * * *",
        last_scheduled_fire_at=datetime(2026, 11, 23, 11, 0, tzinfo=UTC),
    )
    fake_clock.current = datetime(2026, 11, 23, 11, 32, tzinfo=UTC)
    runner = FakeRunner()
    _catch_up(fake_clock, runner, [job_id])
    assert runner.submitted == [(job_id, "schedule", datetime(2026, 11, 23, 11, 30, tzinfo=UTC))]


def test_missed_outside_grace_is_skipped_and_logged(
    fake_clock: FakeClock, caplog: pytest.LogCaptureFixture
) -> None:
    db_ready()
    job_id = seed_job(
        schedule="0 3 * * *",
        last_scheduled_fire_at=datetime(2026, 11, 22, 3, 0, tzinfo=UTC),
    )
    fake_clock.current = datetime(2026, 11, 23, 5, 0, tzinfo=UTC)  # 2 h late, grace 1 h
    runner = FakeRunner()
    with caplog.at_level(logging.WARNING):
        _catch_up(fake_clock, runner, [job_id])
    assert runner.submitted == []
    assert "outside the grace window" in caplog.text


def test_nothing_missed_means_no_catch_up(fake_clock: FakeClock) -> None:
    db_ready()
    job_id = seed_job(
        schedule="0 3 * * *", last_scheduled_fire_at=datetime(2026, 11, 23, 3, tzinfo=UTC)
    )
    fake_clock.current = datetime(2026, 11, 23, 3, 30, tzinfo=UTC)
    runner = FakeRunner()
    _catch_up(fake_clock, runner, [job_id])
    assert runner.submitted == []


def test_catch_up_ignores_disabled_and_manual_jobs(fake_clock: FakeClock) -> None:
    db_ready()
    anchor = datetime(2026, 11, 22, 3, 0, tzinfo=UTC)
    disabled = seed_job("off", schedule="0 3 * * *", enabled=False, last_scheduled_fire_at=anchor)
    manual = seed_job("manual", created_at=anchor)
    fake_clock.current = datetime(2026, 11, 23, 3, 10, tzinfo=UTC)
    runner = FakeRunner()
    _catch_up(fake_clock, runner, [disabled, manual])
    assert runner.submitted == []


def test_catch_up_anchor_is_created_at_for_new_jobs(fake_clock: FakeClock) -> None:
    db_ready()
    job_id = seed_job(schedule="0 3 * * *", created_at=datetime(2026, 11, 23, 3, 5, tzinfo=UTC))
    fake_clock.current = datetime(2026, 11, 23, 3, 30, tzinfo=UTC)
    runner = FakeRunner()
    _catch_up(fake_clock, runner, [job_id])
    assert runner.submitted == []


def test_catch_up_skips_busy_job(fake_clock: FakeClock) -> None:
    db_ready()
    job_id = seed_job(
        schedule="0 3 * * *", last_scheduled_fire_at=datetime(2026, 11, 22, 3, tzinfo=UTC)
    )
    fake_clock.current = datetime(2026, 11, 23, 3, 10, tzinfo=UTC)
    runner = FakeRunner()
    runner.active.add(job_id)
    _catch_up(fake_clock, runner, [job_id])
    assert runner.submitted == []
    assert get_job_row(job_id).last_scheduled_fire_at == datetime(2026, 11, 23, 3, tzinfo=UTC)


# ---------- single owner ----------


def test_lock_allows_one_owner(tmp_path: Any) -> None:
    first, second = SchedulerLock(tmp_path / "s.lock"), SchedulerLock(tmp_path / "s.lock")
    assert first.acquire() is True
    assert second.acquire() is False
    first.release()
    assert second.acquire() is True
    second.release()


def test_second_instance_is_not_owner_and_unhealthy(m6_client: TestClient) -> None:
    with TestClient(create_app()) as other:
        services = services_of(other)
        assert services.scheduler.state == SchedulerState.NOT_OWNER
        health = other.get("/healthz")
        assert health.status_code == 503
        assert health.json()["scheduler"] == {
            "state": "not_owner",
            "owner": False,
            "last_tick_age_seconds": None,
        }
        assert services.scheduler.registered_jobs() == []
    assert m6_client.get("/healthz").status_code == 200


def test_not_owner_does_not_register_jobs(m6_client: TestClient) -> None:
    with TestClient(create_app()) as other:
        assert login(other).status_code == 200
        job = _job(other, schedule="0 3 * * *")
        assert job["next_run_at"] is None
        assert services_of(other).scheduler.registered_jobs() == []


def test_stale_tick_makes_healthz_fail(m6_client: TestClient) -> None:
    scheduler = services_of(m6_client).scheduler
    assert m6_client.get("/healthz").status_code == 200
    scheduler.last_tick = datetime.now(UTC) - timedelta(minutes=3)
    response = m6_client.get("/healthz")
    assert response.status_code == 503
    assert response.json()["scheduler"]["last_tick_age_seconds"] >= 180


@pytest.mark.parametrize(
    ("argv", "env", "workers"),
    [
        (["uvicorn", "app.main:create_app"], {}, 1),
        (["uvicorn", "--workers", "2"], {}, 2),
        (["uvicorn", "--workers=3"], {}, 3),
        (["uvicorn"], {"WEB_CONCURRENCY": "4"}, 4),
    ],
)
def test_configured_workers(argv: list[str], env: dict[str, str], workers: int) -> None:
    assert configured_workers(argv, env) == workers


def test_more_than_one_worker_fails_fast() -> None:
    check_single_worker(["uvicorn"], {})
    with pytest.raises(RuntimeError, match="exactly one uvicorn worker"):
        check_single_worker(["uvicorn", "--workers", "2"], {})

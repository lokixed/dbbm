"""After a run: notify_on matrix, recovery, heartbeat pings, restart and Drive auth events."""

import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, col, select

from app.core.db import get_engine
from app.main import create_app
from app.models import AuditLog, Destination
from app.models.notification import EventType
from app.services.destination_tokens import DestinationTokenStore, set_auth_expired_handler
from app.services.heartbeat import ping_url
from app.services.messages import Message
from app.services.post_run import PostRun, decide_event
from app.services.secrets import store_secrets
from tests.conftest import api_post, api_put, login
from tests.m6 import InstantClock, Outbound, db_ready, drain, seed_job, seed_run
from tests.runs_helpers import make_destination, make_job, make_source, run_now

PING = "https://hc-ping.com/5f0c2a1e-8a2b-4f6e-9d1c-3b7a9e0f1234"
T0 = datetime(2026, 11, 20, 3, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("status", "notify_on", "recovery", "previous", "expected"),
    [
        ("failed", "failure", True, None, EventType.RUN_FINISHED),
        ("partial", "failure", True, "success", EventType.RUN_FINISHED),
        ("interrupted", "failure", True, None, EventType.RUN_FINISHED),
        ("cancelled", "failure", True, None, None),
        ("success", "failure", True, "success", None),
        ("success", "always", True, "success", EventType.RUN_FINISHED),
        ("success", "failure", True, "failed", EventType.RECOVERY),
        ("success", "always", True, "partial", EventType.RECOVERY),
        ("success", "failure", False, "failed", None),
        ("success", "always", False, "interrupted", EventType.RUN_FINISHED),
        ("failed", "never", True, None, None),
        ("success", "never", True, "failed", None),
    ],
)
def test_notify_on_matrix(
    status: str, notify_on: str, recovery: bool, previous: str | None, expected: EventType | None
) -> None:
    assert decide_event(status, notify_on, recovery, previous) == expected


class Recorder:
    def __init__(self) -> None:
        self.pings: list[tuple[uuid.UUID, str]] = []
        self.messages: list[tuple[uuid.UUID, Message]] = []
        self.retention: list[uuid.UUID] = []

    def ping(self, job_id: uuid.UUID, kind: str) -> None:
        self.pings.append((job_id, kind))

    async def notify_job(self, job_id: uuid.UUID, message: Message) -> int:
        self.messages.append((job_id, message))
        return 1

    def schedule_for_job(self, job_id: uuid.UUID) -> None:
        self.retention.append(job_id)


def _handle(recorder: Recorder, run_id: uuid.UUID) -> None:
    fake: Any = recorder
    asyncio.run(PostRun(fake, fake, fake).handle(run_id))


def test_recovery_is_sent_once() -> None:
    db_ready()
    job = seed_job(notify_on="failure")
    rec = Recorder()
    _handle(rec, seed_run(job, "failed", T0))
    _handle(rec, seed_run(job, "success", T0 + timedelta(hours=1)))
    _handle(rec, seed_run(job, "success", T0 + timedelta(hours=2)))
    assert [m.event for _j, m in rec.messages] == ["run_finished", "recovery"]
    assert rec.messages[1][1].title.startswith("Recovered")


def test_retry_success_recovers_partial_parent() -> None:
    db_ready()
    job = seed_job()
    rec = Recorder()
    parent = seed_run(job, "partial", T0)
    _handle(rec, parent)
    with Session(get_engine()) as db:
        from app.models import Run

        child = Run(
            job_id=job,
            job_name="nightly",
            trigger="retry",
            status="success",
            parent_run_id=parent,
            created_at=T0 + timedelta(minutes=5),
            started_at=T0 + timedelta(minutes=5),
            finished_at=T0 + timedelta(minutes=6),
        )
        db.add(child)
        db.commit()
        child_id = child.id
    _handle(rec, child_id)
    assert [m.event for _j, m in rec.messages] == ["run_finished", "recovery"]
    assert rec.pings == [(job, "fail")]  # no pings for the retry run


@pytest.mark.parametrize(
    ("status", "ping", "retention"),
    [
        ("success", "success", True),
        ("partial", "fail", True),
        ("failed", "fail", False),
        ("interrupted", "fail", False),
        ("cancelled", None, False),
    ],
)
def test_ping_and_retention_mapping(status: str, ping: str | None, retention: bool) -> None:
    db_ready()
    job = seed_job()
    rec = Recorder()
    _handle(rec, seed_run(job, status, T0, trigger="schedule"))
    assert rec.pings == ([(job, ping)] if ping else [])
    assert rec.retention == ([job] if retention else [])


def test_success_clears_overdue_and_records_fire_time() -> None:
    db_ready()
    job = seed_job(overdue_notified_at=T0)
    run = seed_run(job, "success", T0, trigger="schedule")
    with Session(get_engine()) as db:
        from app.models import Job, Run

        row = db.get(Run, run)
        assert row is not None
        row.scheduled_for = T0
        db.add(row)
        db.commit()
    _handle(Recorder(), run)
    with Session(get_engine()) as db:
        job_row = db.get(Job, job)
        assert job_row is not None
        assert job_row.overdue_notified_at is None
        assert job_row.last_scheduled_fire_at == T0


def test_ping_url_mapping() -> None:
    assert ping_url(PING, "start") == PING + "/start"
    assert ping_url(PING + "/", "fail") == PING + "/fail"
    assert ping_url(PING, "success") == PING
    assert ping_url("https://hc.example.com/ping/abc?rid=1", "start") == (
        "https://hc.example.com/ping/abc/start?rid=1"
    )


# ---------- end to end through the app ----------


def _webhook(client: TestClient, url: str = "https://hooks.example.com/run-events") -> str:
    body = {"type": "webhook", "name": "hook", "secrets": {"url": url}}
    response = api_post(client, "/api/notifications", body)
    assert response.status_code == 201, response.text
    channel_id: str = response.json()["id"]
    return channel_id


def _payloads(outbound: Outbound) -> list[dict[str, Any]]:
    return [
        json.loads(r.content)
        for r in outbound.requests
        if r.headers["host"] == "hooks.example.com" and r.content
    ]


@pytest.mark.usefixtures("fake_bins", "fake_drivers")
def test_failure_then_recovery_notifies_selected_channels(
    m6_client: TestClient, outbound: Outbound, fake_bins: Any
) -> None:
    channel = _webhook(m6_client)
    _webhook_other = api_post(
        m6_client,
        "/api/notifications",
        {"type": "webhook", "name": "unused", "secrets": {"url": "https://other.example.com/x"}},
    )
    assert _webhook_other.status_code == 201
    job = make_job(
        m6_client,
        make_source(m6_client),
        [make_destination(m6_client)],
        notification_channel_ids=[channel],
    )
    fake_bins.set_mode("mysqldump", "fail")
    fake_bins.set_text("stderr_text", "mysqldump: Got error: 1045: Access denied for user 'backup'")
    failed = run_now(m6_client, job)
    assert failed["status"] == "failed"
    drain(m6_client)
    fake_bins.set_mode("mysqldump", "ok")
    ok = run_now(m6_client, job)
    drain(m6_client)
    events = [(p["event"], p["status"]) for p in _payloads(outbound)]
    assert events == [("run_finished", "failed"), ("recovery", "success")]
    first = _payloads(outbound)[0]
    assert first["job"]["name"] == job["name"]
    assert first["run"]["id"] == failed["id"]
    assert first["run"]["trigger"] == "manual"
    assert "Sup3r-Secret-Pw!" not in json.dumps(first)
    assert all(r.headers["host"] != "other.example.com" for r in outbound.requests)
    detail = m6_client.get(f"/api/runs/{ok['id']}").json()
    assert [(n["event_type"], n["status"]) for n in detail["notifications"]] == [
        ("recovery", "sent")
    ]


@pytest.mark.usefixtures("fake_bins", "fake_drivers")
def test_heartbeat_pings_and_secrecy(
    m6_client: TestClient, outbound: Outbound, fake_bins: Any, caplog: pytest.LogCaptureFixture
) -> None:
    job = make_job(
        m6_client, make_source(m6_client), [make_destination(m6_client)], heartbeat_url=PING
    )
    assert job["heartbeat_set"] is True
    assert "5f0c2a1e" not in json.dumps(job)
    run_now(m6_client, job)
    drain(m6_client)
    fake_bins.set_mode("mysqldump", "fail")
    fake_bins.set_text("stderr_text", "boom")
    run_now(m6_client, job)
    drain(m6_client)
    paths = [r.url.path for r in outbound.requests if r.headers["host"] == "hc-ping.com"]
    uuid_path = "/5f0c2a1e-8a2b-4f6e-9d1c-3b7a9e0f1234"
    assert paths == [f"{uuid_path}/start", uuid_path, f"{uuid_path}/start", f"{uuid_path}/fail"]

    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    outbound.responder = unreachable
    fake_bins.set_mode("mysqldump", "ok")
    # Another job: artifact names carry the job name and second, so they cannot collide.
    other = make_job(
        m6_client,
        make_source(m6_client, name="other-src"),
        [make_destination(m6_client, name="other-dest", path="other")],
        name="other",
        heartbeat_url=PING,
    )
    with caplog.at_level(logging.WARNING):
        run = run_now(m6_client, other)
        drain(m6_client)
    assert run["status"] == "success"
    assert "Heartbeat ping (start)" in caplog.text
    assert "5f0c2a1e" not in caplog.text
    with Session(get_engine()) as db:
        audits = db.exec(select(AuditLog).where(col(AuditLog.object_id) == job["id"])).all()
    assert audits and all("5f0c2a1e" not in json.dumps(a.details) for a in audits)
    assert "5f0c2a1e" not in m6_client.get("/api/jobs").text


def test_heartbeat_url_rules(authed: TestClient) -> None:
    source, dest = make_source(authed), make_destination(authed)
    base = {"name": "j", "source_id": source["id"], "destination_ids": [dest["id"]]}
    bad = api_post(authed, "/api/jobs", {**base, "heartbeat_url": "http://169.254.169.254/x"})
    assert bad.status_code == 422
    job = api_post(authed, "/api/jobs", {**base, "heartbeat_url": PING}).json()
    kept = api_put(authed, f"/api/jobs/{job['id']}", base).json()
    assert kept["heartbeat_set"] is True
    cleared = api_put(authed, f"/api/jobs/{job['id']}", {**base, "heartbeat_url": None}).json()
    assert cleared["heartbeat_set"] is False


@pytest.mark.usefixtures("fake_bins", "fake_drivers")
def test_interrupted_runs_notify_after_restart(outbound: Outbound) -> None:
    with TestClient(create_app(outbound_policy=outbound.policy(), clock=InstantClock())) as app1:
        from tests.conftest import setup_admin

        setup_admin(app1)
        login(app1)
        channel = _webhook(app1)
        job = make_job(
            app1,
            make_source(app1),
            [make_destination(app1)],
            notification_channel_ids=[channel],
            heartbeat_url=PING,
        )
    seed_run(uuid.UUID(job["id"]), "running", datetime.now(UTC) - timedelta(minutes=5))
    with TestClient(create_app(outbound_policy=outbound.policy(), clock=InstantClock())) as app2:
        drain(app2)
    assert [(p["event"], p["status"]) for p in _payloads(outbound)] == [
        ("run_finished", "interrupted")
    ]
    assert [
        r.url.path.rsplit("/", 1)[-1]
        for r in outbound.requests
        if r.headers["host"] == "hc-ping.com"
    ] == ["fail"]


def test_auth_expired_fires_once_per_transition() -> None:
    db_ready()
    token = json.dumps(
        {"access_token": "ya29.x", "refresh_token": "1//0abc", "expiry": "2026-01-01T00:00:00Z"}
    )
    with Session(get_engine()) as db:
        row = Destination(
            name="drive",
            type="gdrive",
            config={"remote_folder": "db"},
            secrets_enc=store_secrets({"token": token}),
        )
        db.add(row)
        db.commit()
        dest_id = row.id
    calls: list[tuple[uuid.UUID, str]] = []
    set_auth_expired_handler(lambda d, n: calls.append((d, n)))
    try:
        store = DestinationTokenStore(dest_id)
        store.set_auth("needs_reauth", token)
        store.set_auth("needs_reauth", token)
        store.set_auth("ok", token)
        store.set_auth("needs_reauth", token)
    finally:
        set_auth_expired_handler(None)
    assert calls == [(dest_id, "drive"), (dest_id, "drive")]

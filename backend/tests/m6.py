"""Fakes for milestone 6: an injectable clock, outbound HTTP/DNS/SMTP and an app wired to them."""

import asyncio
import smtplib
import uuid
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, col, select

from app.core.db import get_engine
from app.main import create_app
from app.models import Artifact, Destination, Job, JobDestination, Run, Source
from app.services.container import Services
from app.services.outbound import OutboundPolicy
from tests.conftest import login, setup_admin

PUBLIC_IP = "93.184.216.34"


class FakeClock:
    def __init__(self, now: datetime | None = None) -> None:
        self.current = now or datetime(2026, 11, 23, 12, 0, tzinfo=UTC)
        self.sleeps: list[float] = []

    def now(self) -> datetime:
        return self.current

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        await asyncio.sleep(0)

    def advance(self, **delta: float) -> None:
        self.current += timedelta(**delta)


class InstantClock(FakeClock):
    """Real time, but sleeps return at once (retry backoff in app-level tests)."""

    def now(self) -> datetime:
        return datetime.now(UTC)


@dataclass
class SentMail:
    security: str
    address: str
    host: str
    port: int
    calls: list[str] = field(default_factory=list)
    messages: list[EmailMessage] = field(default_factory=list)
    login: tuple[str, str] | None = None


class FakeSMTP:
    """Stands in for smtplib.SMTP / SMTP_SSL; records the conversation."""

    def __init__(self, record: SentMail, fail_login: bool = False) -> None:
        self.record = record
        self.fail_login = fail_login

    def ehlo(self) -> None:
        self.record.calls.append("ehlo")

    def has_extn(self, name: str) -> bool:
        return name == "starttls"

    def starttls(self, context: Any = None) -> None:
        self.record.calls.append("starttls")

    def login(self, user: str, password: str) -> None:
        if self.fail_login:
            raise smtplib.SMTPAuthenticationError(535, b"bad credentials")
        self.record.login = (user, password)
        self.record.calls.append("login")

    def send_message(self, message: EmailMessage) -> None:
        self.record.messages.append(message)
        self.record.calls.append("send")

    def quit(self) -> None:
        self.record.calls.append("quit")

    def close(self) -> None:
        self.record.calls.append("close")


class Outbound:
    """Fake DNS, HTTP and SMTP for every outbound call of the app."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.addresses: dict[str, list[str]] = {}
        self.resolutions: list[str] = []
        self.responder: Callable[[httpx.Request], httpx.Response] = lambda _r: httpx.Response(200)
        self.mail: list[SentMail] = []
        self.fail_login = False

    def resolve(self, host: str, port: int) -> list[str]:
        self.resolutions.append(host)
        return list(self.addresses.get(host, [PUBLIC_IP]))

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.responder(request)

    def smtp(self, security: str, address: str, host: str, port: int, timeout: float) -> Any:
        record = SentMail(security, address, host, port)
        self.mail.append(record)
        return FakeSMTP(record, self.fail_login)

    def policy(self, allow_private: bool = False) -> OutboundPolicy:
        return OutboundPolicy(
            allow_private=allow_private,
            resolver=self.resolve,
            transport=httpx.MockTransport(self._handle),
            smtp_factory=self.smtp,
        )

    def hosts(self) -> list[str]:
        return [r.headers["host"] for r in self.requests]


@pytest.fixture
def outbound() -> Outbound:
    return Outbound()


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def m6_client(outbound: Outbound) -> Iterator[TestClient]:
    app = create_app(outbound_policy=outbound.policy(), clock=InstantClock())
    with TestClient(app) as client:
        setup_admin(client)
        assert login(client).status_code == 200
        yield client


def services_of(client: TestClient) -> Services:
    services: Services = client.app.state.services  # type: ignore[attr-defined]
    return services


def on_loop[T](client: TestClient, work: Callable[[], Awaitable[T]]) -> T:
    """Run a coroutine factory on the app's event loop (where its services live)."""
    portal = client.portal
    assert portal is not None
    return portal.call(work)


def drain(client: TestClient) -> None:
    services = services_of(client)

    async def wait() -> None:
        await services.runner.wait_idle()
        await services.post_run.wait_idle()
        await services.retention.wait_idle()
        await services.heartbeat.wait_idle()
        await services.notifier.wait_idle()

    on_loop(client, wait)


# ---------- direct database seeding (unit tests without the HTTP layer) ----------


def db_ready() -> None:
    from app.core.migrations import run_migrations

    run_migrations()


def seed_job(name: str = "nightly", **fields: Any) -> uuid.UUID:
    with Session(get_engine()) as db:
        source = db.exec(select(Source).where(col(Source.name) == "src")).first()
        if source is None:
            source = Source(name="src", type="mysql", config={})
            db.add(source)
            db.flush()
        job = Job(name=name, source_id=source.id, **fields)
        db.add(job)
        db.commit()
        return job.id


def seed_destination(name: str = "local1", path: str = "daily", **fields: Any) -> uuid.UUID:
    with Session(get_engine()) as db:
        row = Destination(name=name, type="local", config={"path": path}, **fields)
        db.add(row)
        db.commit()
        return row.id


def link(job_id: uuid.UUID, destination_id: uuid.UUID) -> None:
    with Session(get_engine()) as db:
        db.add(JobDestination(job_id=job_id, destination_id=destination_id))
        db.commit()


def seed_run(
    job_id: uuid.UUID | None,
    status: str,
    at: datetime,
    *,
    job_name: str = "nightly",
    trigger: str = "manual",
    destinations: dict[uuid.UUID, str] | None = None,
    artifacts: dict[uuid.UUID, list[tuple[str, int]]] | None = None,
    finished: datetime | None = None,
) -> uuid.UUID:
    """A run with per-destination outcomes and artifact rows (paths relative to the root)."""
    units = [
        {
            "database": "shop",
            "kind": "database",
            "dump": {"status": "ok"},
            "destinations": [
                {"destination_id": str(d), "destination_name": "d", "status": s}
                for d, s in (destinations or {}).items()
            ],
        }
    ]
    with Session(get_engine()) as db:
        run = Run(
            job_id=job_id,
            job_name=job_name,
            trigger=trigger,
            status=status,
            started_at=at,
            finished_at=finished
            or (at + timedelta(minutes=1) if status not in ("queued", "running") else None),
            created_at=at,
            step_results={"units": units},
        )
        db.add(run)
        db.flush()
        for dest_id, items in (artifacts or {}).items():
            for path, size in items:
                db.add(
                    Artifact(
                        run_id=run.id,
                        destination_id=dest_id,
                        source_database="shop",
                        path=path,
                        size_bytes=size,
                        sha256="0" * 64,
                        compressed=True,
                        created_at=at,
                    )
                )
        db.commit()
        return run.id


def get_job_row(job_id: uuid.UUID) -> Job:
    with Session(get_engine()) as db:
        job = db.get(Job, job_id)
        assert job is not None
        db.expunge(job)
        return job

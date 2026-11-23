"""Overdue detection and daily housekeeping."""

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlmodel import Session

from app.core.config import get_settings
from app.core.db import get_engine
from app.models import Run
from app.services.housekeeping import DiskUsage, Housekeeping
from app.services.messages import Message
from app.services.overdue import OverdueChecker
from app.services.retention import RetentionService
from tests.m6 import FakeClock, db_ready, get_job_row, seed_destination, seed_job, seed_run

CREATED = datetime(2026, 11, 1, tzinfo=UTC)


class RecordingNotifier:
    def __init__(self) -> None:
        self.job_messages: list[tuple[uuid.UUID, Message]] = []
        self.system: list[Message] = []

    async def notify_job(self, job_id: uuid.UUID, message: Message) -> int:
        self.job_messages.append((job_id, message))
        return 1

    async def notify(self, message: Message, channel_ids: Any = None) -> int:
        self.system.append(message)
        return 1


def _checker(clock: FakeClock, notifier: RecordingNotifier, active: bool = False) -> OverdueChecker:
    fake: Any = notifier
    return OverdueChecker(fake, lambda _job: active, clock)


def _check(checker: OverdueChecker) -> list[Any]:
    return asyncio.run(checker.check())


# ---------- overdue ----------


def test_overdue_fires_once_per_episode_and_clears_after_success(fake_clock: FakeClock) -> None:
    db_ready()
    job = seed_job(schedule="0 3 * * *", created_at=CREATED, updated_at=CREATED)
    notifier = RecordingNotifier()
    checker = _checker(fake_clock, notifier)
    # Daily: slack = max(1 h grace, 24 h * 50 %) = 12 h after 03:00.
    fake_clock.current = datetime(2026, 11, 23, 14, 50, tzinfo=UTC)
    seed_run(job, "success", datetime(2026, 11, 22, 3, 0, tzinfo=UTC))
    assert _check(checker) == []
    fake_clock.current = datetime(2026, 11, 23, 15, 10, tzinfo=UTC)
    [found] = _check(checker)
    assert found.expected_at == datetime(2026, 11, 23, 3, 0, tzinfo=UTC)
    assert [m.event for _j, m in notifier.job_messages] == ["overdue"]
    fake_clock.advance(hours=30)
    assert _check(checker) == []  # same episode
    assert len(notifier.job_messages) == 1

    with Session(get_engine()) as db:
        from app.models import Job

        row = db.get(Job, job)
        assert row is not None
        row.overdue_notified_at = None  # what PostRun does after a success
        db.add(row)
        db.commit()
    seed_run(job, "success", fake_clock.current - timedelta(hours=1))
    assert _check(checker) == []
    fake_clock.advance(days=2)
    assert len(_check(checker)) == 1  # a new episode
    assert get_job_row(job).overdue_notified_at == fake_clock.current


def test_overdue_ignores_manual_disabled_new_and_active_jobs(fake_clock: FakeClock) -> None:
    db_ready()
    seed_job("manual", created_at=CREATED, updated_at=CREATED)
    seed_job("off", schedule="0 3 * * *", enabled=False, created_at=CREATED, updated_at=CREATED)
    fake_clock.current = datetime(2026, 11, 23, 20, 0, tzinfo=UTC)
    seed_job(
        "fresh", schedule="0 3 * * *", created_at=fake_clock.current, updated_at=fake_clock.current
    )
    notifier = RecordingNotifier()
    assert _check(_checker(fake_clock, notifier)) == []
    busy = seed_job("busy", schedule="0 3 * * *", created_at=CREATED, updated_at=CREATED)
    assert _check(_checker(fake_clock, notifier, active=True)) == []
    seed_run(busy, "running", fake_clock.current - timedelta(minutes=5))
    assert _check(_checker(fake_clock, notifier)) == []
    assert notifier.job_messages == []


def test_overdue_short_interval_uses_grace(fake_clock: FakeClock) -> None:
    db_ready()
    job = seed_job(schedule="*/15 * * * *", created_at=CREATED, updated_at=CREATED)
    fake_clock.current = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)
    seed_run(job, "success", datetime(2026, 11, 23, 11, 1, tzinfo=UTC))
    assert _check(_checker(fake_clock, RecordingNotifier())) == []
    seed_run(job, "failed", datetime(2026, 11, 23, 12, 0, tzinfo=UTC))
    fake_clock.current = datetime(2026, 11, 23, 12, 20, tzinfo=UTC)
    [found] = _check(_checker(fake_clock, RecordingNotifier()))
    assert found.expected_at == datetime(2026, 11, 23, 11, 15, tzinfo=UTC)


# ---------- housekeeping ----------


class StubRetention:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    async def run_all(self) -> list[Any]:
        self.calls += 1
        if self.fail:
            raise RuntimeError("retention exploded")
        return []


def _housekeeping(
    clock: FakeClock,
    notifier: RecordingNotifier | None = None,
    retention: Any = None,
    active: list[uuid.UUID] | None = None,
) -> Housekeeping:
    fake: Any = notifier
    stub: Any = retention or StubRetention()
    return Housekeeping(get_settings(), stub, fake, lambda: active or [], clock)


def test_steps_are_independent(fake_clock: FakeClock) -> None:
    db_ready()
    retention = StubRetention(fail=True)
    work = get_settings().work_dir / "orphan"
    work.mkdir(parents=True)
    summary = asyncio.run(_housekeeping(fake_clock, retention=retention).run())
    assert summary["retention"] == {"error": "RuntimeError"}
    assert summary["work_dirs"] == {"removed": 1}
    assert "logs" in summary and "history" in summary and "low_disk" in summary
    assert not work.exists()


def test_logs_and_history_pruning(fake_clock: FakeClock) -> None:
    db_ready()
    fake_clock.current = datetime.now(UTC)
    logs = get_settings().logs_dir
    logs.mkdir(parents=True, exist_ok=True)
    active_id = uuid.uuid4()
    old, new, active = (
        logs / f"{uuid.uuid4()}.log",
        logs / f"{uuid.uuid4()}.log",
        logs / f"{active_id}.log",
    )
    for path in (old, new, active):
        path.write_text("log")
    stale = (fake_clock.current - timedelta(days=100)).timestamp()
    os.utime(old, (stale, stale))
    os.utime(active, (stale, stale))

    dest = seed_destination()
    job = seed_job()
    ancient = fake_clock.current - timedelta(days=400)
    gone = seed_run(job, "failed", ancient)
    holds_backup = seed_run(job, "success", ancient, artifacts={dest: [("k.sql.gz", 1)]})
    deleted_backup = seed_run(job, "success", ancient, artifacts={dest: [("d.sql.gz", 1)]})
    with Session(get_engine()) as db:
        from sqlmodel import select

        from app.models import Artifact

        art = db.exec(select(Artifact).where(Artifact.run_id == deleted_backup)).one()
        art.deleted_at = fake_clock.current
        db.add(art)
        db.commit()
    recent = seed_run(job, "failed", fake_clock.current - timedelta(days=3))

    summary = asyncio.run(_housekeeping(fake_clock, active=[active_id]).run())
    assert summary["logs"] == {"removed": 1}
    assert not old.exists() and new.exists() and active.exists()
    assert summary["history"] == {"removed": 2}
    with Session(get_engine()) as db:
        remaining = {r for r in (gone, holds_backup, deleted_backup, recent) if db.get(Run, r)}
    assert remaining == {holds_backup, recent}


def test_low_disk_notifies_once_per_day(
    fake_clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_ready()
    usage = DiskUsage("data", "Data volume", "data", None, total=100, used=95, free=5)
    monkeypatch.setattr("app.services.housekeeping.disk_usages", lambda _s: [usage])
    notifier = RecordingNotifier()
    housekeeping = _housekeeping(fake_clock, notifier)
    asyncio.run(housekeeping.check_disks())
    fake_clock.advance(hours=12)
    asyncio.run(housekeeping.check_disks())
    assert [m.event for m in notifier.system] == ["low_disk"]
    fake_clock.advance(hours=13)
    asyncio.run(housekeeping.check_disks())
    assert len(notifier.system) == 2
    assert "5.0%" in notifier.system[0].text


def test_housekeeping_runs_retention_for_all_destinations(fake_clock: FakeClock) -> None:
    db_ready()
    seed_destination("a", "a")
    seed_destination("b", "b")
    retention = RetentionService(clock=fake_clock)
    summary = asyncio.run(_housekeeping(fake_clock, retention=retention).run())
    assert summary["retention"]["destinations"] == 2

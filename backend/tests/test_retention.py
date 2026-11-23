"""Retention: planning rules, deletion at a destination, safety checks, triggers and API."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, col, select

from app.core.config import get_settings
from app.core.db import get_engine
from app.models import Artifact, AuditLog
from app.services.messages import Message
from app.services.retention import Plan, Policy, RetentionService, RunCopy, plan_retention
from app.storage.local import LocalStorage
from tests.conftest import api_post, api_put
from tests.m6 import FakeClock, db_ready, drain, link, seed_destination, seed_job, seed_run
from tests.runs_helpers import backup_files, make_destination, make_job, make_source, run_now

NOW = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)


# ---------- planner (pure) ----------


def copies(
    *items: tuple[str, float] | tuple[str, float, bool], job: str = "j", offset: int = 0
) -> list[RunCopy]:
    result = []
    for index, item in enumerate(items):
        status, days = item[0], item[1]
        counted = item[2] if len(item) > 2 else status == "success"
        result.append(
            RunCopy(
                run_id=uuid.UUID(int=index + 1 + offset),
                job_key=job,
                job_name=job,
                status=status,
                moment=NOW - timedelta(days=days),
                counted=bool(counted),
            )
        )
    return result


def deleted(plan: Plan) -> list[int]:
    return sorted(c.run_id.int for c in plan.delete)


SIX_DAILY = [("success", float(d)) for d in range(6)]  # ids 1 (newest) .. 6 (oldest)


@pytest.mark.parametrize(
    ("policy", "expected"),
    [
        (Policy(keep_last=3), [4, 5, 6]),
        (Policy(keep_days=3), [5, 6]),  # exactly 3 days old is not older than 3 days
        (Policy(keep_last=2, keep_days=3), [5, 6]),
        (Policy(keep_last=5, keep_days=1), [6]),
        (Policy(), []),
        (Policy(keep_last=10), []),
    ],
)
def test_policy_table(policy: Policy, expected: list[int]) -> None:
    assert deleted(plan_retention(copies(*SIX_DAILY), policy, NOW)) == expected


def test_newest_success_is_always_kept() -> None:
    plan = plan_retention(copies(("success", 30), ("success", 40)), Policy(keep_days=1), NOW)
    assert deleted(plan) == [2]
    assert [c.run_id.int for c in plan.keep if c.protected] == [1]


def test_newest_success_kept_even_behind_newer_partials() -> None:
    items = copies(("partial", 1, True), ("partial", 2, True), ("success", 3))
    assert deleted(plan_retention(items, Policy(keep_last=1), NOW)) == [2]


def test_failed_and_partial_do_not_count_toward_keep_last() -> None:
    items = copies(
        ("partial", 0, False),  # this destination failed in that run
        ("success", 1),
        ("cancelled", 2, False),
        ("success", 3),
        ("success", 4),
        ("interrupted", 5, False),
    )
    # Two counted runs kept (ids 2 and 4); uncounted copies go once older than the last kept.
    assert deleted(plan_retention(items, Policy(keep_last=2), NOW)) == [5, 6]


def test_partial_where_destination_succeeded_counts() -> None:
    items = copies(("partial", 0, True), ("success", 1), ("success", 2))
    assert deleted(plan_retention(items, Policy(keep_last=2), NOW)) == [3]


def test_jobs_are_planned_separately() -> None:
    items = copies(("success", 0), ("success", 1), job="a") + copies(
        ("success", 0), ("success", 1), job="b", offset=1000
    )
    assert deleted(plan_retention(items, Policy(keep_last=1), NOW)) == [2, 1002]


# ---------- execution at a local destination ----------


def _write(dest_path: str, key: str, data: bytes = b"x" * 10) -> Path:
    path = get_settings().backup_dir / dest_path / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    (path.parent / (path.name + ".manifest.json")).write_text("{}")
    return path


def _setup(
    count: int, keep_last: int | None = None, keep_days: int | None = None
) -> tuple[uuid.UUID, uuid.UUID, list[uuid.UUID]]:
    db_ready()
    dest = seed_destination(retention={"keep_last": keep_last, "keep_days": keep_days})
    job = seed_job()
    link(job, dest)
    runs = []
    for index in range(count):
        key = f"nightly/2026/11/nightly_shop_{index:02d}.sql.gz"
        _write("daily", key)
        runs.append(
            seed_run(
                job,
                "success",
                NOW - timedelta(days=count - index),
                destinations={dest: "ok"},
                artifacts={dest: [(key, 10)]},
            )
        )
    return dest, job, runs  # runs oldest first


def _live(dest: uuid.UUID) -> list[str]:
    with Session(get_engine()) as db:
        return sorted(
            a.path
            for a in db.exec(
                select(Artifact).where(
                    col(Artifact.destination_id) == dest, col(Artifact.deleted_at).is_(None)
                )
            ).all()
        )


def _run(service: RetentionService, dest: uuid.UUID, **kw: Any) -> Any:
    return asyncio.run(service.run(dest, **kw))


def test_keep_last_three_leaves_exactly_three_and_preview_matches() -> None:
    dest, _job, _runs = _setup(6, keep_last=3)
    untracked = _write("daily", "nightly/2026/11/not-ours.sql.gz")
    service = RetentionService(clock=FakeClock(NOW))
    preview = asyncio.run(service.preview(dest))
    predicted = sorted(a.path for c in preview.delete for a in c.artifacts)
    result = _run(service, dest)
    assert result.deleted_runs == 3 and result.deleted_artifacts == 3 and not result.failures
    assert _live(dest) == [f"nightly/2026/11/nightly_shop_{i:02d}.sql.gz" for i in (3, 4, 5)]
    remaining = [f for f in backup_files() if f.endswith(".sql.gz")]
    assert sorted(set(_live(dest)) | {"nightly/2026/11/not-ours.sql.gz"}) == sorted(
        f.removeprefix("daily/") for f in remaining
    )
    assert predicted == [f"nightly/2026/11/nightly_shop_{i:02d}.sql.gz" for i in (0, 1, 2)]
    assert untracked.exists()
    assert not any(f.endswith("_00.sql.gz.manifest.json") for f in backup_files())


def test_delete_failure_leaves_deleted_at_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    dest, _job, _runs = _setup(3, keep_last=1)
    original = LocalStorage.delete_sync

    def flaky(self: LocalStorage, key: str) -> None:
        if key.endswith("_00.sql.gz"):
            raise PermissionError(13, "Permission denied")
        original(self, key)

    monkeypatch.setattr(LocalStorage, "delete_sync", flaky)
    result = _run(RetentionService(clock=FakeClock(NOW)), dest)
    assert len(result.failures) == 1
    assert "Permission denied" in result.failures[0]["error"]
    assert "nightly/2026/11/nightly_shop_00.sql.gz" in _live(dest)
    assert "nightly/2026/11/nightly_shop_01.sql.gz" not in _live(dest)


def test_already_missing_file_counts_as_deleted() -> None:
    dest, _job, _runs = _setup(2, keep_last=1)
    (get_settings().backup_dir / "daily/nightly/2026/11/nightly_shop_00.sql.gz").unlink()
    result = _run(RetentionService(clock=FakeClock(NOW)), dest)
    assert result.deleted_artifacts == 1 and not result.failures
    assert _live(dest) == ["nightly/2026/11/nightly_shop_01.sql.gz"]


@pytest.mark.parametrize("bad_path", ["../outside.sql.gz", "/etc/passwd", "link/escaped.sql.gz"])
def test_paths_outside_the_destination_are_never_deleted(bad_path: str, tmp_path: Path) -> None:
    dest, job, _runs = _setup(1, keep_last=1)
    outside = get_settings().backup_dir / "outside.sql.gz"
    outside.write_text("keep me")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "escaped.sql.gz").write_text("keep me too")
    (get_settings().backup_dir / "daily" / "link").symlink_to(elsewhere)
    seed_run(
        job,
        "success",
        NOW - timedelta(days=30),
        destinations={dest: "ok"},
        artifacts={dest: [(bad_path, 1)]},
    )
    result = _run(RetentionService(clock=FakeClock(NOW)), dest)
    assert len(result.failures) == 1
    assert outside.read_text() == "keep me"
    assert (elsewhere / "escaped.sql.gz").read_text() == "keep me too"
    assert bad_path in _live(dest)


def test_active_runs_are_excluded() -> None:
    dest, job, _runs = _setup(1, keep_last=1)
    key = "nightly/2026/11/active.sql.gz"
    _write("daily", key)
    seed_run(
        job, "running", NOW - timedelta(days=60), destinations={}, artifacts={dest: [(key, 1)]}
    )
    _run(RetentionService(clock=FakeClock(NOW)), dest)
    assert key in _live(dest)


def test_destinations_are_independent() -> None:
    db_ready()
    keep_one = seed_destination("local-a", "a", retention={"keep_last": 1, "keep_days": None})
    keep_all = seed_destination("local-b", "b", retention={"keep_last": None, "keep_days": None})
    job = seed_job()
    for index in range(3):
        key = f"nightly/k{index}.sql.gz"
        _write("a", key)
        _write("b", key)
        seed_run(
            job,
            "success",
            NOW - timedelta(days=3 - index),
            destinations={keep_one: "ok", keep_all: "ok"},
            artifacts={keep_one: [(key, 1)], keep_all: [(key, 1)]},
        )
    service = RetentionService(clock=FakeClock(NOW))
    _run(service, keep_one)
    _run(service, keep_all)
    assert _live(keep_one) == ["nightly/k2.sql.gz"]
    assert len(_live(keep_all)) == 3


def test_retention_for_one_destination_does_not_run_concurrently() -> None:
    dest, _job, _runs = _setup(3, keep_last=1)
    service = RetentionService(clock=FakeClock(NOW))

    async def go() -> tuple[bool, int]:
        lock = service.lock(dest)
        await lock.acquire()
        task = asyncio.create_task(service.run(dest))
        for _ in range(20):
            await asyncio.sleep(0)
        waiting = not task.done()
        lock.release()
        result = await task
        return waiting, result.deleted_runs

    assert asyncio.run(go()) == (True, 2)


class RecordingNotifier:
    def __init__(self) -> None:
        self.sent: list[Message] = []

    async def notify(self, message: Message, channel_ids: Any = None) -> int:
        self.sent.append(message)
        return 1


def test_repeated_failures_send_one_retention_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    dest, _job, _runs = _setup(2, keep_last=1)

    def broken(self: LocalStorage, key: str) -> None:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(LocalStorage, "delete_sync", broken)
    notifier = RecordingNotifier()
    service = RetentionService(notifier, FakeClock(NOW))  # type: ignore[arg-type]
    for _ in range(5):
        _run(service, dest)
    assert [m.event for m in notifier.sent] == ["retention_failed"]
    assert "Permission denied" in notifier.sent[0].text


# ---------- API and triggers ----------


def test_preview_and_run_endpoints(authed: TestClient) -> None:
    dest = make_destination(authed)
    dest_id = uuid.UUID(dest["id"])
    job = seed_job()
    for index in range(4):
        key = f"nightly/r{index}.sql.gz"
        _write("daily", key)
        seed_run(
            job,
            "success",
            datetime.now(UTC) - timedelta(days=4 - index),
            destinations={dest_id: "ok"},
            artifacts={dest_id: [(key, 100)]},
        )
    saved = authed.get(f"/api/destinations/{dest['id']}/retention/preview").json()
    assert saved["delete_runs"] == 0  # no policy saved yet
    preview = authed.get(
        f"/api/destinations/{dest['id']}/retention/preview", params={"keep_last": 2}
    ).json()
    assert preview["delete_runs"] == 2 and preview["delete_bytes"] == 200
    assert sorted(a for c in preview["delete"] for a in c["artifacts"]) == [
        "nightly/r0.sql.gz",
        "nightly/r1.sql.gz",
    ]
    body = {**dest, "retention": {"keep_last": 2, "keep_days": None}}
    body = {k: body[k] for k in ("type", "name", "config", "retention")}
    assert api_put(authed, f"/api/destinations/{dest['id']}", body).status_code == 200
    result = api_post(authed, f"/api/destinations/{dest['id']}/retention/run").json()
    assert result["deleted_runs"] == 2 and result["failures"] == []
    with Session(get_engine()) as db:
        audit = db.exec(select(AuditLog).where(col(AuditLog.action) == "retention.run")).one()
    assert sorted(audit.details["deleted_paths"]) == ["nightly/r0.sql.gz", "nightly/r1.sql.gz"]
    assert audit.username == "admin"
    missing = api_post(authed, f"/api/destinations/{uuid.uuid4()}/retention/run")
    assert missing.status_code == 404


@pytest.mark.usefixtures("fake_bins", "fake_drivers")
def test_retention_runs_after_each_finished_run(m6_client: TestClient) -> None:
    dest = make_destination(m6_client)
    body = {k: dest[k] for k in ("type", "name", "config")}
    api_put(
        m6_client,
        f"/api/destinations/{dest['id']}",
        {**body, "retention": {"keep_last": 1, "keep_days": None}},
    )
    job = make_job(m6_client, make_source(m6_client), [dest])
    dest_id = uuid.UUID(dest["id"])
    old_key = "nightly/2026/01/nightly_shop_old.sql.gz"
    old_file = _write("daily", old_key)
    seed_run(
        uuid.UUID(job["id"]),
        "success",
        datetime.now(UTC) - timedelta(days=10),
        destinations={dest_id: "ok"},
        artifacts={dest_id: [(old_key, 10)]},
    )
    run = run_now(m6_client, job)
    assert run["status"] == "success"
    drain(m6_client)
    with Session(get_engine()) as db:
        live = db.exec(select(Artifact).where(col(Artifact.deleted_at).is_(None))).all()
    assert {str(a.run_id) for a in live} == {run["id"]}
    assert not old_file.exists()

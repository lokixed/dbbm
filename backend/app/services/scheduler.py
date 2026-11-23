"""In-process scheduler: APScheduler cron triggers rebuilt from the database at startup.

Only one process may schedule: an exclusive flock on DATA_DIR/scheduler.lock decides the owner.
A second instance on the same data directory serves the UI but never fires jobs.
"""

import asyncio
import errno
import fcntl
import logging
import os
import sys
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlmodel import Session, col, select

from app.core.clock import SYSTEM_CLOCK, Clock
from app.core.config import Settings
from app.core.db import get_engine
from app.core.schedule import (
    build_trigger,
    fire_after,
    latest_fire_between,
    schedule_problem,
    validate_timezone,
)
from app.models import Job, RunTrigger
from app.services.app_settings import load_settings
from app.services.audit import write_audit
from app.services.runner import JobBusyError, JobNotFoundError, Runner

logger = logging.getLogger(__name__)

TICK_SECONDS = 30
STALE_TICK = timedelta(minutes=2)
OVERDUE_MINUTES = 10
TICK_ID = "__tick__"
HOUSEKEEPING_ID = "__housekeeping__"
OVERDUE_ID = "__overdue__"


class SchedulerState(StrEnum):
    RUNNING = "running"
    STOPPED = "stopped"
    NOT_OWNER = "not_owner"


def configured_workers(argv: Sequence[str], env: Mapping[str, str]) -> int:
    workers = 1
    raw = env.get("WEB_CONCURRENCY")
    if raw and raw.strip().isdigit():
        workers = int(raw)
    for index, arg in enumerate(argv):
        value = None
        if arg == "--workers" and index + 1 < len(argv):
            value = argv[index + 1]
        elif arg.startswith("--workers="):
            value = arg.split("=", 1)[1]
        if value is not None and value.strip().isdigit():
            workers = int(value)
    return workers


def check_single_worker(
    argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None
) -> None:
    """Several workers would each run a scheduler (the lock would leave all but one idle)."""
    workers = configured_workers(
        sys.argv if argv is None else argv, os.environ if env is None else env
    )
    if workers > 1:
        raise RuntimeError(
            f"DBBackup must run with exactly one uvicorn worker (configured: {workers}). "
            "Remove --workers / WEB_CONCURRENCY."
        )


class SchedulerLock:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._fd: int | None = None

    def acquire(self) -> bool:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            return False
        except OSError as exc:
            if exc.errno in (errno.EAGAIN, errno.EACCES):
                os.close(fd)
                return False
            # e.g. ENOLCK/EOPNOTSUPP on some network or host-mounted filesystems.
            logger.warning(
                "The filesystem of %s does not support file locks (%s); assuming this is the "
                "only DBBackup instance using this data directory.",
                self._path, errno.errorcode.get(exc.errno or 0, "error"),
            )  # fmt: skip
        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()}\n".encode())
        self._fd = fd
        return True

    def release(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None


@dataclass(frozen=True)
class JobSpec:
    id: uuid.UUID
    name: str
    enabled: bool
    schedule: str | None
    timezone: str
    created_at: datetime
    last_scheduled_fire_at: datetime | None

    @classmethod
    def of(cls, job: Job) -> "JobSpec":
        return cls(
            job.id,
            job.name,
            job.enabled,
            job.schedule,
            job.timezone,
            job.created_at,
            job.last_scheduled_fire_at,
        )

    def trigger(self) -> CronTrigger | None:
        if not self.enabled or not self.schedule:
            return None
        if schedule_problem(self.schedule, self.timezone) is not None:
            return None
        return build_trigger(self.schedule, validate_timezone(self.timezone))


Task = Callable[[], Awaitable[Any]]


class JobScheduler:
    def __init__(
        self,
        settings: Settings,
        runner: Runner,
        *,
        clock: Clock = SYSTEM_CLOCK,
        housekeeping: Task | None = None,
        overdue: Task | None = None,
    ) -> None:
        self._settings = settings
        self._runner = runner
        self._clock = clock
        self._housekeeping = housekeeping
        self._overdue = overdue
        self._lock = SchedulerLock(settings.scheduler_lock_path)
        self._aps: AsyncIOScheduler | None = None
        self._grace = 3600
        self.state = SchedulerState.STOPPED
        self.last_tick: datetime | None = None

    # ---------- lifecycle ----------

    async def start(self) -> None:
        if not await asyncio.to_thread(self._lock.acquire):
            self.state = SchedulerState.NOT_OWNER
            logger.error(
                "SCHEDULER NOT STARTED: another DBBackup process holds %s. Only one instance "
                "may use a data directory; this one will not run scheduled jobs.",
                self._settings.scheduler_lock_path,
            )
            return
        app_settings = await asyncio.to_thread(load_settings)
        self._grace = app_settings.misfire_grace_seconds
        self._aps = AsyncIOScheduler(
            timezone=UTC,
            job_defaults={
                "coalesce": True,
                "max_instances": 1,
                "misfire_grace_time": self._grace,
            },
        )
        self._aps.start()
        self.state = SchedulerState.RUNNING
        self._tick()
        self._aps.add_job(
            self._tick, IntervalTrigger(seconds=TICK_SECONDS), id=TICK_ID, name="heartbeat tick"
        )
        if self._overdue is not None:
            self._aps.add_job(
                self._guard("overdue check", self._overdue),
                IntervalTrigger(minutes=OVERDUE_MINUTES),
                id=OVERDUE_ID,
                name="overdue check",
            )
        self._schedule_housekeeping(app_settings.housekeeping_time, app_settings.default_timezone)
        specs = await asyncio.to_thread(self._load_specs)
        for spec in specs:
            self._register(spec)
        await self.catch_up(specs)

    async def stop(self) -> None:
        if self._aps is not None:
            self._aps.shutdown(wait=False)
            self._aps = None
        if self.state != SchedulerState.NOT_OWNER:
            self.state = SchedulerState.STOPPED
        await asyncio.to_thread(self._lock.release)

    @property
    def running(self) -> bool:
        return self.state == SchedulerState.RUNNING and self._aps is not None

    def status(self) -> dict[str, Any]:
        age = None
        if self.last_tick is not None and self.running:
            age = max(0.0, (self._clock.now() - self.last_tick).total_seconds())
        return {
            "state": self.state.value,
            "owner": self.state != SchedulerState.NOT_OWNER,
            "last_tick_age_seconds": round(age, 1) if age is not None else None,
        }

    def healthy(self) -> bool:
        if not self.running or self.last_tick is None:
            return False
        return self._clock.now() - self.last_tick <= STALE_TICK

    def _tick(self) -> None:
        self.last_tick = self._clock.now()

    @staticmethod
    def _guard(name: str, task: Task) -> Callable[[], Awaitable[None]]:
        async def run() -> None:
            try:
                await task()
            except Exception as exc:
                logger.error("Scheduled %s failed: %s", name, type(exc).__name__)

        return run

    def _schedule_housekeeping(self, at: str, timezone: str) -> None:
        if self._aps is None or self._housekeeping is None:
            return
        hour, minute = (int(part) for part in at.split(":"))
        trigger = CronTrigger(hour=hour, minute=minute, timezone=ZoneInfo(timezone))
        self._aps.add_job(
            self._guard("housekeeping", self._housekeeping),
            trigger,
            id=HOUSEKEEPING_ID,
            name="housekeeping",
            replace_existing=True,
        )

    async def apply_settings(self) -> None:
        """Settings changed: reschedule housekeeping and update the misfire grace time."""
        if not self.running or self._aps is None:
            return
        app_settings = await asyncio.to_thread(load_settings)
        self._grace = app_settings.misfire_grace_seconds
        self._schedule_housekeeping(app_settings.housekeeping_time, app_settings.default_timezone)
        for job in self._aps.get_jobs():
            if not job.id.startswith("__"):
                job.modify(misfire_grace_time=self._grace)

    # ---------- job registration ----------

    @staticmethod
    def _load_specs() -> list[JobSpec]:
        with Session(get_engine()) as db:
            return [JobSpec.of(j) for j in db.exec(select(Job).order_by(col(Job.name))).all()]

    @staticmethod
    def _load_spec(job_id: uuid.UUID) -> JobSpec | None:
        with Session(get_engine()) as db:
            job = db.get(Job, job_id)
            return JobSpec.of(job) if job else None

    def _register(self, spec: JobSpec) -> None:
        if self._aps is None:
            return
        trigger = spec.trigger()
        if trigger is None:
            self.remove_job(spec.id)
            if spec.enabled and spec.schedule:
                logger.warning(
                    "Job %s has an invalid schedule and is not scheduled: %s",
                    spec.name, schedule_problem(spec.schedule, spec.timezone),
                )  # fmt: skip
            return
        self._aps.add_job(
            self._fire,
            trigger,
            args=[spec.id],
            id=str(spec.id),
            name=spec.name,
            replace_existing=True,
            misfire_grace_time=self._grace,
        )

    async def sync_job(self, job_id: uuid.UUID) -> None:
        """Call after a job was created, edited, enabled or disabled."""
        if not self.running:
            return
        spec = await asyncio.to_thread(self._load_spec, job_id)
        if spec is None:
            self.remove_job(job_id)
        else:
            self._register(spec)

    def remove_job(self, job_id: uuid.UUID) -> None:
        if self._aps is not None and self._aps.get_job(str(job_id)) is not None:
            self._aps.remove_job(str(job_id))

    def next_run_at(self, job_id: uuid.UUID) -> datetime | None:
        if self._aps is None:
            return None
        job = self._aps.get_job(str(job_id))
        next_time: datetime | None = getattr(job, "next_run_time", None) if job else None
        return next_time.astimezone(UTC) if next_time else None

    def registered_jobs(self) -> list[str]:
        if self._aps is None:
            return []
        return sorted(j.id for j in self._aps.get_jobs() if not j.id.startswith("__"))

    # ---------- firing ----------

    async def _fire(self, job_id: uuid.UUID) -> None:
        now = self._clock.now()
        aps_job = self._aps.get_job(str(job_id)) if self._aps else None
        fire_time = now
        if aps_job is not None:
            window_start = now - timedelta(seconds=self._grace + 60)
            fire_time = latest_fire_between(aps_job.trigger, window_start, now) or now
        await self.trigger(job_id, fire_time)

    async def trigger(self, job_id: uuid.UUID, fire_time: datetime) -> uuid.UUID | None:
        """Enqueue a scheduled run, or record a skipped schedule if the job is busy."""
        if self._runner.is_active(job_id):
            await self._skip(job_id, fire_time)
            return None
        try:
            run_id = await self._runner.submit(job_id, RunTrigger.SCHEDULE, scheduled_for=fire_time)
        except JobBusyError:
            await self._skip(job_id, fire_time)
            return None
        except JobNotFoundError:
            self.remove_job(job_id)
            return None
        await asyncio.to_thread(self._mark_fired, job_id, fire_time, None)
        return run_id

    async def _skip(self, job_id: uuid.UUID, fire_time: datetime) -> None:
        logger.warning(
            "Scheduled run of job %s at %s skipped: the previous run is still active",
            job_id, fire_time.isoformat(),
        )  # fmt: skip
        await asyncio.to_thread(self._mark_fired, job_id, fire_time, "schedule.skipped")

    @staticmethod
    def _mark_fired(job_id: uuid.UUID, fire_time: datetime, audit: str | None) -> None:
        with Session(get_engine()) as db:
            job = db.get(Job, job_id)
            if job is None:
                return
            if job.last_scheduled_fire_at is None or job.last_scheduled_fire_at < fire_time:
                job.last_scheduled_fire_at = fire_time
                db.add(job)
            if audit:
                write_audit(
                    db,
                    audit,
                    username="scheduler",
                    object_type="job",
                    object_id=str(job.id),
                    details={"name": job.name, "scheduled_for": fire_time.isoformat()},
                )
            db.commit()

    async def catch_up(self, specs: Sequence[JobSpec]) -> list[uuid.UUID]:
        """Run once for a fire time missed while stopped, if it is within the grace window."""
        now = self._clock.now()
        grace = timedelta(seconds=self._grace)
        started: list[uuid.UUID] = []
        for spec in specs:
            trigger = spec.trigger()
            if trigger is None:
                continue
            anchor = spec.last_scheduled_fire_at or spec.created_at
            fire = latest_fire_between(trigger, max(anchor, now - grace), now)
            if fire is not None:
                logger.info(
                    "Job %s missed its run at %s while stopped; starting one catch-up run",
                    spec.name, fire.isoformat(),
                )  # fmt: skip
                run_id = await self.trigger(spec.id, fire)
                if run_id is not None:
                    started.append(run_id)
                continue
            first = fire_after(trigger, anchor)
            if first is not None and first <= now:
                logger.warning(
                    "Job %s missed its run at %s while stopped, outside the grace window "
                    "of %ss; not catching up",
                    spec.name, first.isoformat(), self._grace,
                )  # fmt: skip
        return started

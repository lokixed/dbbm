"""What happens after a run: notifications, heartbeat pings, retention and schedule bookkeeping."""

import asyncio
import logging
import uuid
from collections.abc import Coroutine
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func
from sqlmodel import Session, col, select

from app.core.db import get_engine
from app.models import Artifact, Job, Run, RunStatus, RunTrigger
from app.models.job import NotifyOn
from app.models.notification import EventType
from app.services.app_settings import read_settings
from app.services.heartbeat import Heartbeat
from app.services.messages import RunFacts, run_message
from app.services.notifier import Notifier
from app.services.retention import RetentionService

logger = logging.getLogger(__name__)

FAILURE_STATUSES = frozenset({RunStatus.FAILED, RunStatus.PARTIAL, RunStatus.INTERRUPTED})


def decide_event(
    status: str, notify_on: str, notify_on_recovery: bool, previous: str | None
) -> EventType | None:
    """Which job event a finished run produces (a recovery replaces the success message)."""
    if notify_on == NotifyOn.NEVER:
        return None
    if status == RunStatus.SUCCESS:
        if notify_on_recovery and previous in FAILURE_STATUSES:
            return EventType.RECOVERY
        return EventType.RUN_FINISHED if notify_on == NotifyOn.ALWAYS else None
    if status in FAILURE_STATUSES:
        return EventType.RUN_FINISHED
    return None


@dataclass
class Outcome:
    facts: RunFacts
    job_id: uuid.UUID | None
    notify_on: str
    notify_on_recovery: bool
    previous: str | None
    base_url: str


def _previous_status(db: Session, run: Run) -> str | None:
    """Status of the job's run before this one (a retry follows its partial parent)."""
    if run.job_id is None:
        return None
    if run.trigger == RunTrigger.RETRY:
        newer = db.exec(
            select(Run.id).where(
                col(Run.job_id) == run.job_id,
                col(Run.trigger) != RunTrigger.RETRY.value,
                col(Run.status) != RunStatus.CANCELLED.value,
                col(Run.created_at)
                > select(Run.created_at).where(col(Run.id) == run.parent_run_id).scalar_subquery(),
            )
        ).first()
        return None if newer else RunStatus.PARTIAL.value
    previous = db.exec(
        select(Run.status)
        .where(
            col(Run.job_id) == run.job_id,
            col(Run.id) != run.id,
            col(Run.trigger) != RunTrigger.RETRY.value,
            col(Run.status).in_([s.value for s in (RunStatus.SUCCESS, *FAILURE_STATUSES)]),
            col(Run.created_at) < run.created_at,
        )
        .order_by(col(Run.created_at).desc())
        .limit(1)
    ).first()
    return previous


def load_outcome(run_id: uuid.UUID) -> Outcome | None:
    """Gather what the side effects need and update the job's bookkeeping (blocking)."""
    with Session(get_engine()) as db:
        run = db.get(Run, run_id)
        if run is None:
            return None
        # A retry stores its artifacts under the parent run.
        owner = (
            run.parent_run_id if run.trigger == RunTrigger.RETRY and run.parent_run_id else run.id
        )
        count, total = db.exec(
            select(func.count(), func.coalesce(func.sum(Artifact.size_bytes), 0)).where(
                col(Artifact.run_id) == owner, col(Artifact.deleted_at).is_(None)
            )
        ).one()
        job = db.get(Job, run.job_id) if run.job_id else None
        if job is not None:
            changed = False
            if run.status == RunStatus.SUCCESS and job.overdue_notified_at is not None:
                job.overdue_notified_at = None
                changed = True
            fire = run.scheduled_for
            if fire and (job.last_scheduled_fire_at is None or job.last_scheduled_fire_at < fire):
                job.last_scheduled_fire_at = fire
                changed = True
            if changed:
                db.add(job)
                db.commit()
        outcome = Outcome(
            facts=RunFacts(run, run.job_name, int(count), int(total)),
            job_id=job.id if job else None,
            notify_on=job.notify_on if job else NotifyOn.NEVER.value,
            notify_on_recovery=job.notify_on_recovery if job else False,
            previous=_previous_status(db, run),
            base_url=read_settings(db).public_base_url,
        )
        db.expunge(run)
        return outcome


class PostRun:
    def __init__(
        self, notifier: Notifier, heartbeat: Heartbeat, retention: RetentionService
    ) -> None:
        self._notifier = notifier
        self._heartbeat = heartbeat
        self._retention = retention
        self._tasks: set[asyncio.Task[None]] = set()

    def on_started(self, run_id: uuid.UUID, job_id: uuid.UUID, trigger: str) -> None:
        if trigger != RunTrigger.RETRY:
            self._heartbeat.ping(job_id, "start")

    def on_finished(self, run_id: uuid.UUID) -> None:
        self._spawn(self.handle(run_id))

    def _spawn(self, work: Coroutine[Any, Any, None]) -> None:
        task = asyncio.create_task(work)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def wait_idle(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def handle(self, run_id: uuid.UUID) -> None:
        try:
            outcome = await asyncio.to_thread(load_outcome, run_id)
        except Exception as exc:
            logger.warning("Post-run handling of %s failed: %s", run_id, type(exc).__name__)
            return
        if outcome is None or outcome.job_id is None:
            return
        run = outcome.facts.run
        if run.trigger != RunTrigger.RETRY:
            if run.status == RunStatus.SUCCESS:
                self._heartbeat.ping(outcome.job_id, "success")
            elif run.status in FAILURE_STATUSES:
                self._heartbeat.ping(outcome.job_id, "fail")
        if run.status in (RunStatus.SUCCESS, RunStatus.PARTIAL):
            self._retention.schedule_for_job(outcome.job_id)
        event = decide_event(
            run.status, outcome.notify_on, outcome.notify_on_recovery, outcome.previous
        )
        if event is None:
            return
        message = run_message(outcome.facts, outcome.base_url, recovery=event == EventType.RECOVERY)
        try:
            await self._notifier.notify_job(outcome.job_id, message)
        except Exception as exc:
            logger.warning(
                "Queueing the notification for %s failed: %s", run_id, type(exc).__name__
            )

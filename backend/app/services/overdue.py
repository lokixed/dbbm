"""Overdue detection: a scheduled job without a successful run in its expected window."""

import asyncio
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from apscheduler.triggers.cron import CronTrigger
from sqlmodel import Session, col, select

from app.core.clock import SYSTEM_CLOCK, Clock
from app.core.db import get_engine
from app.core.schedule import (
    ScheduleError,
    build_trigger,
    fire_after,
    latest_fire_between,
    validate_timezone,
)
from app.models import Job, Run, RunStatus
from app.models.run import ACTIVE_STATUSES
from app.schemas.settings import AppSettingsOut
from app.services.app_settings import load_settings
from app.services.messages import overdue_message
from app.services.notifier import Notifier

logger = logging.getLogger(__name__)

MIN_LOOKBACK = timedelta(days=2)


@dataclass(frozen=True)
class OverdueCheck:
    job_id: uuid.UUID
    job_name: str
    expected_at: datetime


def slack_for(trigger: CronTrigger, fire: datetime, grace: int, percent: int) -> timedelta:
    following = fire_after(trigger, fire)
    interval = (following - fire) if following else timedelta(0)
    return max(timedelta(seconds=grace), interval * percent / 100)


def expected_fire(
    trigger: CronTrigger, anchor: datetime, now: datetime, grace: int, percent: int
) -> datetime | None:
    """The latest fire time whose success window has already closed, after `anchor`."""
    upcoming = fire_after(trigger, now)
    after = fire_after(trigger, upcoming) if upcoming else None
    interval = (after - upcoming) if upcoming and after else timedelta(0)
    slack = max(timedelta(seconds=grace), interval * percent / 100)
    lookback = max(3 * interval, MIN_LOOKBACK) + slack
    fire = latest_fire_between(trigger, max(anchor, now - lookback), now - slack)
    if fire is None or now < fire + slack_for(trigger, fire, grace, percent):
        return None
    return fire


def job_overdue_since(
    job: Job, now: datetime, settings: AppSettingsOut, succeeded_since: Callable[[datetime], bool]
) -> datetime | None:
    if not job.enabled or not job.schedule:
        return None
    try:
        trigger = build_trigger(job.schedule, validate_timezone(job.timezone))
    except ScheduleError:
        return None
    anchor = max(job.created_at, job.updated_at)
    fire = expected_fire(
        trigger, anchor, now, settings.misfire_grace_seconds, settings.overdue_grace_percent
    )
    if fire is None or succeeded_since(fire):
        return None
    return fire


class OverdueChecker:
    def __init__(
        self,
        notifier: Notifier | None,
        is_active: Callable[[uuid.UUID], bool],
        clock: Clock = SYSTEM_CLOCK,
    ) -> None:
        self._notifier = notifier
        self._is_active = is_active
        self._clock = clock

    async def check(self) -> list[OverdueCheck]:
        found = await asyncio.to_thread(self._find)
        settings = await asyncio.to_thread(load_settings)
        for item in found:
            logger.warning(
                "Job %s is overdue: no successful run since %s",
                item.job_name, item.expected_at.isoformat(),
            )  # fmt: skip
            if self._notifier is not None:
                message = overdue_message(
                    str(item.job_id), item.job_name, item.expected_at, settings.public_base_url
                )
                await self._notifier.notify_job(item.job_id, message)
        return found

    def _find(self) -> list[OverdueCheck]:
        now = self._clock.now()
        found: list[OverdueCheck] = []
        with Session(get_engine()) as db:
            settings = load_settings()
            jobs = db.exec(
                select(Job).where(col(Job.enabled), col(Job.schedule).is_not(None))
            ).all()
            for job in jobs:
                if job.overdue_notified_at is not None or self._is_active(job.id):
                    continue
                if self._has_active(db, job.id):
                    continue

                def succeeded(since: datetime, job_id: uuid.UUID = job.id) -> bool:
                    return self._success_since(db, job_id, since)

                fire = job_overdue_since(job, now, settings, succeeded)
                if fire is None:
                    continue
                job.overdue_notified_at = now
                db.add(job)
                found.append(OverdueCheck(job.id, job.name, fire))
            db.commit()
        return found

    @staticmethod
    def _has_active(db: Session, job_id: uuid.UUID) -> bool:
        return (
            db.exec(
                select(Run.id).where(
                    col(Run.job_id) == job_id, col(Run.status).in_(ACTIVE_STATUSES)
                )
            ).first()
            is not None
        )

    @staticmethod
    def _success_since(db: Session, job_id: uuid.UUID, since: datetime) -> bool:
        return (
            db.exec(
                select(Run.id).where(
                    col(Run.job_id) == job_id,
                    col(Run.status) == RunStatus.SUCCESS.value,
                    col(Run.finished_at) >= since,
                )
            ).first()
            is not None
        )

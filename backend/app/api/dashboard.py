from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter
from sqlalchemy import func
from sqlmodel import Session, col, select

from app.api.deps import CurrentUser, ServicesDep
from app.api.run_views import restore_target_types
from app.core.db import SessionDep
from app.models import Artifact, AuditLog, Destination, Job, Run, RunStatus
from app.models.run import ACTIVE_STATUSES
from app.schemas.job import (
    DashboardDestinationOut,
    DashboardDiskOut,
    DashboardFailureOut,
    DashboardJobOut,
    DashboardNextRunOut,
    DashboardOut,
    DashboardSchedulerOut,
)
from app.services.app_settings import read_settings
from app.services.container import Services
from app.services.housekeeping import disk_usages

RECENT_RUNS = 50
DAY = timedelta(hours=24)
MAX_FAILURES = 20
MAX_NEXT_RUNS = 10
FAILURE_STATUSES = (RunStatus.FAILED.value, RunStatus.PARTIAL.value, RunStatus.INTERRUPTED.value)

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


def _last_success_size(db: Session, run_id: object) -> int:
    # One file may sit at several destinations; count each database once.
    rows = db.exec(
        select(func.max(Artifact.size_bytes))
        .where(col(Artifact.run_id) == run_id, col(Artifact.deleted_at).is_(None))
        .group_by(col(Artifact.source_database))
    ).all()
    return sum(int(size) for size in rows if size is not None)


@router.get("")
def dashboard(db: SessionDep, _user: CurrentUser, services: ServicesDep) -> DashboardOut:
    now = services.clock.now()
    jobs = _jobs(db, services, now)
    timezones = _timezones(db)
    next_runs = sorted(
        (
            DashboardNextRunOut(
                job_id=j.job_id,
                job_name=j.job_name,
                next_run_at=j.next_run_at,
                schedule=j.schedule or "",
                timezone=timezones.get(j.job_id, "UTC"),
            )
            for j in jobs
            if j.next_run_at is not None
        ),
        key=lambda n: n.next_run_at,
    )[:MAX_NEXT_RUNS]
    threshold = read_settings(db).low_disk_threshold_percent
    status = services.scheduler.status()
    return DashboardOut(
        jobs=jobs,
        destinations=_destinations(db),
        next_runs=next_runs,
        failures_24h=_failures(db, now),
        disks=_disks(services, threshold),
        low_disk_threshold_percent=threshold,
        scheduler=DashboardSchedulerOut(state=status["state"], owner=status["owner"]),
    )


def _timezones(db: Session) -> dict[str, str]:
    return {str(i): tz for i, tz in db.exec(select(Job.id, Job.timezone)).all()}


def _failures(db: Session, now: datetime) -> list[DashboardFailureOut]:
    rows = db.exec(
        select(Run)
        .where(col(Run.status).in_(FAILURE_STATUSES), col(Run.finished_at) >= now - DAY)
        .order_by(col(Run.finished_at).desc())
        .limit(MAX_FAILURES)
    ).all()
    target_types = restore_target_types(db, rows)
    return [
        DashboardFailureOut(
            run_id=str(r.id),
            job_id=str(r.job_id) if r.job_id else None,
            job_name=r.job_name,
            status=r.status,
            finished_at=r.finished_at,
            error=r.error,
            source_type=target_types.get(r.id),
        )
        for r in rows
    ]


def _disks(services: Services, threshold: int) -> list[DashboardDiskOut]:
    return [
        DashboardDiskOut(
            key=u.key,
            label=u.label,
            kind=u.kind,
            destination_id=u.destination_id,
            total_bytes=u.total,
            used_bytes=u.used,
            free_bytes=u.free,
            free_percent=round(u.free_percent, 1),
            below_threshold=u.free_percent < threshold,
        )
        for u in disk_usages(services.settings)
    ]


def _skipped(db: Session, now: datetime) -> dict[str, int]:
    rows = db.exec(
        select(AuditLog.object_id, func.count())
        .where(col(AuditLog.action) == "schedule.skipped", col(AuditLog.timestamp) >= now - DAY)
        .group_by(col(AuditLog.object_id))
    ).all()
    return {str(object_id): int(count) for object_id, count in rows}


def _latest_outcomes(db: Session) -> dict[str, dict[str, Any]]:
    """Each destination's most recent per-destination result among recent finished runs."""
    latest: dict[str, dict[str, Any]] = {}
    runs = db.exec(
        select(Run)
        .where(col(Run.status).not_in(ACTIVE_STATUSES))
        .order_by(col(Run.created_at).desc())
        .limit(RECENT_RUNS)
    ).all()
    for run in runs:
        for unit in run.step_results.get("units", []):
            for entry in unit.get("destinations", []):
                dest_id = entry.get("destination_id")
                if dest_id and dest_id not in latest and entry.get("status") != "skipped":
                    latest[dest_id] = entry
    return latest


def _destinations(db: Session) -> list[DashboardDestinationOut]:
    last_success = dict(
        db.exec(
            select(Artifact.destination_id, func.max(Artifact.created_at))
            .where(col(Artifact.deleted_at).is_(None))
            .group_by(col(Artifact.destination_id))
        ).all()
    )
    outcomes = _latest_outcomes(db)
    result: list[DashboardDestinationOut] = []
    for row in db.exec(select(Destination).order_by(col(Destination.name))).all():
        outcome = outcomes.get(str(row.id), {})
        success = last_success.get(row.id)
        result.append(
            DashboardDestinationOut(
                id=str(row.id),
                name=row.name,
                type=row.type,
                auth_status=row.auth_status,
                auth_checked_at=row.auth_checked_at,
                last_success_at=success.replace(tzinfo=UTC) if success else None,
                last_error_class=(
                    outcome.get("error_class") if outcome.get("status") == "failed" else None
                ),
            )
        )
    return result


def _jobs(db: Session, services: Services, now: datetime) -> list[DashboardJobOut]:
    skipped = _skipped(db, now)
    result: list[DashboardJobOut] = []
    for job in db.exec(select(Job).order_by(col(Job.name))).all():
        latest = db.exec(
            select(Run)
            .where(col(Run.job_id) == job.id)
            .order_by(col(Run.created_at).desc())
            .limit(1)
        ).first()
        success = db.exec(
            select(Run)
            .where(col(Run.job_id) == job.id, col(Run.status) == RunStatus.SUCCESS.value)
            .order_by(col(Run.finished_at).desc())
            .limit(1)
        ).first()
        result.append(
            DashboardJobOut(
                job_id=str(job.id),
                job_name=job.name,
                enabled=job.enabled,
                schedule=job.schedule,
                next_run_at=services.scheduler.next_run_at(job.id),
                overdue=job.overdue_notified_at is not None,
                overdue_since=job.overdue_notified_at,
                skipped_schedules_24h=skipped.get(str(job.id), 0),
                latest_run_id=str(latest.id) if latest else None,
                latest_status=latest.status if latest else None,
                latest_finished_at=latest.finished_at if latest else None,
                last_success_at=success.finished_at if success else None,
                last_artifact_size=_last_success_size(db, success.id) if success else None,
            )
        )
    return result

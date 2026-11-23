import uuid
from typing import Annotated

from anyio import from_thread
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import delete
from sqlmodel import Session, col, select

from app.api.common import commit_or_conflict, invalid_field, name_conflict
from app.api.deps import CurrentUser, ServicesDep, client_ip
from app.api.run_views import summarize_runs
from app.core.db import SessionDep
from app.core.schedule import ScheduleError, schedule_problem, validate_schedule
from app.models import (
    Destination,
    Job,
    JobDestination,
    JobNotificationChannel,
    NotificationChannel,
    Run,
    Source,
)
from app.models.base import utcnow
from app.models.run import ACTIVE_STATUSES
from app.schemas.job import JobIn, JobOut, RefOut, RunSummaryOut, SourceRefOut
from app.services.app_settings import read_settings
from app.services.audit import write_audit
from app.services.container import Services
from app.services.heartbeat import register_heartbeat
from app.services.runner import JobBusyError, JobNotFoundError, Runner
from app.services.secrets import store_secrets
from app.storage import STORAGES

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


def get_runner(request: Request) -> Runner:
    runner: Runner = request.app.state.runner
    return runner


RunnerDep = Annotated[Runner, Depends(get_runner)]


def _get_or_404(db: Session, job_id: uuid.UUID) -> Job:
    row = db.get(Job, job_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return row


def _destinations(db: Session, job_id: uuid.UUID) -> list[Destination]:
    return list(
        db.exec(
            select(Destination)
            .join(JobDestination, col(JobDestination.destination_id) == col(Destination.id))
            .where(col(JobDestination.job_id) == job_id)
            .order_by(col(Destination.name))
        ).all()
    )


def _channels(db: Session, job_id: uuid.UUID) -> list[NotificationChannel]:
    return list(
        db.exec(
            select(NotificationChannel)
            .join(
                JobNotificationChannel,
                col(JobNotificationChannel.notification_channel_id) == col(NotificationChannel.id),
            )
            .where(col(JobNotificationChannel.job_id) == job_id)
            .order_by(col(NotificationChannel.name))
        ).all()
    )


def _to_out(db: Session, job: Job, services: Services) -> JobOut:
    source = db.get(Source, job.source_id)
    if source is None:
        raise RuntimeError("Job without a source")  # the foreign key is RESTRICT
    last = db.exec(
        select(Run).where(col(Run.job_id) == job.id).order_by(col(Run.created_at).desc()).limit(1)
    ).first()
    return JobOut(
        id=str(job.id),
        name=job.name,
        source=SourceRefOut(id=str(source.id), name=source.name, type=source.type),
        destinations=[RefOut(id=str(d.id), name=d.name) for d in _destinations(db, job.id)],
        enabled=job.enabled,
        schedule=job.schedule,
        timezone=job.timezone,
        schedule_error=schedule_problem(job.schedule, job.timezone),
        next_run_at=services.scheduler.next_run_at(job.id),
        compress=job.compress,
        timeout_seconds=job.timeout_seconds,
        notify_on=job.notify_on,
        notify_on_recovery=job.notify_on_recovery,
        notification_channels=[RefOut(id=str(c.id), name=c.name) for c in _channels(db, job.id)],
        heartbeat_set=job.heartbeat_secret_enc is not None,
        last_run=summarize_runs(db, [last])[0] if last else None,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


def _check_refs(db: Session, body: JobIn) -> list[Destination]:
    source = db.get(Source, body.source_id)
    if source is None:
        raise invalid_field(["source_id"], "Source not found.")
    destinations: list[Destination] = []
    for destination_id in body.destination_ids:
        destination = db.get(Destination, destination_id)
        if destination is None:
            raise invalid_field(["destination_ids"], "Destination not found.")
        if destination.type not in STORAGES:
            raise invalid_field(["destination_ids"], "Unsupported destination type.")
        destinations.append(destination)
    for channel_id in body.notification_channel_ids:
        if db.get(NotificationChannel, channel_id) is None:
            raise invalid_field(["notification_channel_ids"], "Notification channel not found.")
    return destinations


def _schedule(db: Session, body: JobIn, current_timezone: str | None) -> tuple[str | None, str]:
    timezone = body.timezone or current_timezone or read_settings(db).default_timezone
    if body.schedule is None:
        return None, timezone
    try:
        normalized, _zone = validate_schedule(body.schedule, timezone)
    except ScheduleError as exc:
        raise invalid_field(["schedule"], str(exc)) from None
    return normalized, timezone


def _apply(
    db: Session,
    job: Job,
    body: JobIn,
    destinations: list[Destination],
    current_timezone: str | None,
) -> bool:
    """Copy the request onto the job and its links; True if the heartbeat URL changed."""
    job.schedule, job.timezone = _schedule(db, body, current_timezone)
    job.name = body.name
    job.source_id = body.source_id
    job.enabled = body.enabled
    job.compress = body.compress
    job.timeout_seconds = body.timeout_seconds
    job.notify_on = body.notify_on.value
    job.notify_on_recovery = body.notify_on_recovery
    heartbeat_changed = "heartbeat_url" in body.model_fields_set
    if heartbeat_changed:
        url = body.heartbeat_url.get_secret_value() if body.heartbeat_url else None
        job.heartbeat_secret_enc = store_secrets({"url": url}) if url else None
    db.add(job)
    db.flush()
    db.exec(delete(JobDestination).where(col(JobDestination.job_id) == job.id))
    db.exec(delete(JobNotificationChannel).where(col(JobNotificationChannel.job_id) == job.id))
    db.add_all(JobDestination(job_id=job.id, destination_id=d.id) for d in destinations)
    db.add_all(
        JobNotificationChannel(job_id=job.id, notification_channel_id=c)
        for c in body.notification_channel_ids
    )
    return heartbeat_changed


def _after_save(services: Services, job: Job, body: JobIn, heartbeat_changed: bool) -> None:
    if heartbeat_changed:
        url = body.heartbeat_url.get_secret_value() if body.heartbeat_url else None
        register_heartbeat(job.id, url)
    from_thread.run(services.scheduler.sync_job, job.id)


def _name_taken(db: Session, name: str, exclude: uuid.UUID | None = None) -> bool:
    found = db.exec(select(Job.id).where(col(Job.name) == name)).first()
    return found is not None and found != exclude


def _audit_details(job: Job, body: JobIn, heartbeat_changed: bool) -> dict[str, object]:
    return {
        "name": job.name,
        "source_id": str(job.source_id),
        "destination_ids": [str(d) for d in body.destination_ids],
        "enabled": job.enabled,
        "schedule": job.schedule,
        "timezone": job.timezone,
        "compress": job.compress,
        "timeout_seconds": job.timeout_seconds,
        "notify_on": job.notify_on,
        "notify_on_recovery": job.notify_on_recovery,
        "notification_channel_ids": [str(c) for c in body.notification_channel_ids],
        "heartbeat_set": job.heartbeat_secret_enc is not None,
        "heartbeat_changed": heartbeat_changed,
    }


@router.get("")
def list_jobs(db: SessionDep, _user: CurrentUser, services: ServicesDep) -> list[JobOut]:
    return [_to_out(db, j, services) for j in db.exec(select(Job).order_by(col(Job.name))).all()]


@router.post("", status_code=201)
def create_job(
    body: JobIn, request: Request, db: SessionDep, user: CurrentUser, services: ServicesDep
) -> JobOut:
    destinations = _check_refs(db, body)
    if _name_taken(db, body.name):
        raise name_conflict()
    job = Job(name=body.name, source_id=body.source_id)
    heartbeat_changed = _apply(db, job, body, destinations, None)
    write_audit(
        db,
        "job.create",
        user=user,
        object_type="job",
        object_id=str(job.id),
        details=_audit_details(job, body, heartbeat_changed),
        ip=client_ip(request),
    )
    commit_or_conflict(db)
    _after_save(services, job, body, heartbeat_changed)
    return _to_out(db, job, services)


@router.get("/{job_id}")
def get_job(job_id: uuid.UUID, db: SessionDep, _user: CurrentUser, services: ServicesDep) -> JobOut:
    return _to_out(db, _get_or_404(db, job_id), services)


@router.put("/{job_id}")
def update_job(
    job_id: uuid.UUID,
    body: JobIn,
    request: Request,
    db: SessionDep,
    user: CurrentUser,
    services: ServicesDep,
) -> JobOut:
    job = _get_or_404(db, job_id)
    destinations = _check_refs(db, body)
    if _name_taken(db, body.name, exclude=job.id):
        raise name_conflict()
    heartbeat_changed = _apply(db, job, body, destinations, job.timezone)
    job.updated_at = utcnow()
    db.add(job)
    write_audit(
        db,
        "job.update",
        user=user,
        object_type="job",
        object_id=str(job.id),
        details=_audit_details(job, body, heartbeat_changed),
        ip=client_ip(request),
    )
    commit_or_conflict(db)
    _after_save(services, job, body, heartbeat_changed)
    return _to_out(db, job, services)


@router.delete("/{job_id}", status_code=204)
def delete_job(
    job_id: uuid.UUID,
    request: Request,
    db: SessionDep,
    user: CurrentUser,
    runner: RunnerDep,
    services: ServicesDep,
) -> Response:
    job = _get_or_404(db, job_id)
    active = db.exec(
        select(Run.id).where(col(Run.job_id) == job.id, col(Run.status).in_(ACTIVE_STATUSES))
    ).first()
    if active is not None or runner.is_active(job.id):
        raise HTTPException(
            status_code=409,
            detail="This job has a run in progress. Cancel it or wait for it to finish.",
        )
    write_audit(
        db,
        "job.delete",
        user=user,
        object_type="job",
        object_id=str(job.id),
        details={"name": job.name},
        ip=client_ip(request),
    )
    db.delete(job)  # runs keep their rows: the foreign key sets job_id to NULL
    db.commit()
    register_heartbeat(job_id, None)
    from_thread.run_sync(services.scheduler.remove_job, job_id)
    return Response(status_code=204)


def _set_enabled(
    job_id: uuid.UUID,
    enabled: bool,
    request: Request,
    db: Session,
    user: CurrentUser,
    services: Services,
) -> JobOut:
    job = _get_or_404(db, job_id)
    job.enabled = enabled
    job.updated_at = utcnow()
    db.add(job)
    write_audit(
        db,
        "job.enable" if enabled else "job.disable",
        user=user,
        object_type="job",
        object_id=str(job.id),
        details={"name": job.name},
        ip=client_ip(request),
    )
    db.commit()
    from_thread.run(services.scheduler.sync_job, job.id)
    return _to_out(db, job, services)


@router.post("/{job_id}/enable")
def enable_job(
    job_id: uuid.UUID, request: Request, db: SessionDep, user: CurrentUser, services: ServicesDep
) -> JobOut:
    return _set_enabled(job_id, True, request, db, user, services)


@router.post("/{job_id}/disable")
def disable_job(
    job_id: uuid.UUID, request: Request, db: SessionDep, user: CurrentUser, services: ServicesDep
) -> JobOut:
    return _set_enabled(job_id, False, request, db, user, services)


@router.post("/{job_id}/run", status_code=201)
async def run_job(
    job_id: uuid.UUID, request: Request, db: SessionDep, user: CurrentUser, runner: RunnerDep
) -> RunSummaryOut:
    job = _get_or_404(db, job_id)
    try:
        run_id = await runner.submit(job.id, "manual")
    except JobBusyError:
        raise HTTPException(
            status_code=409, detail="This job already has a run in progress."
        ) from None
    except JobNotFoundError:
        raise HTTPException(status_code=404, detail="Job not found") from None
    write_audit(
        db,
        "job.run",
        user=user,
        object_type="job",
        object_id=str(job.id),
        details={"name": job.name, "run_id": str(run_id), "trigger": "manual"},
        ip=client_ip(request),
    )
    db.commit()
    run = db.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return summarize_runs(db, [run])[0]

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import PlainTextResponse, StreamingResponse
from sqlalchemy import ColumnElement, and_, func, or_
from sqlmodel import Session, col, select

from app.api.deps import CurrentUser, SettingsDep, client_ip
from app.api.jobs import RunnerDep
from app.api.run_views import run_detail, summarize_runs
from app.core.db import SessionDep, get_engine
from app.models import Artifact, Job, JobDestination, Run, RunStatus, Source
from app.models.run import ACTIVE_STATUSES
from app.schemas.job import RunOut, RunPage, RunSummaryOut
from app.services.audit import write_audit
from app.services.logstream import follow_log, log_path_for, parse_offset, read_all_text
from app.services.runner import (
    JobBusyError,
    JobNotFoundError,
    NoSourceArtifactError,
    RetryNotAllowedError,
)

router = APIRouter(prefix="/api/runs", tags=["runs"])


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _get_or_404(db: Session, run_id: uuid.UUID) -> Run:
    run = db.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


def _runs_of_source(db: Session, source_id: uuid.UUID) -> ColumnElement[bool]:
    """Backup runs of the source's current jobs (runs of deleted jobs have no job_id), restores
    into it, and restores of backups taken from it."""
    source_jobs = select(Job.id).where(col(Job.source_id) == source_id)
    return or_(
        col(Run.job_id).in_(source_jobs),
        and_(
            col(Run.kind) == "restore",
            or_(_restored_into(db, source_id), _restored_from(db, source_id)),
        ),
    )


def _restored_into(db: Session, source_id: uuid.UUID) -> ColumnElement[bool]:
    """Restores into the source. Older ones recorded only its name, so they match by name."""
    restore = col(Run.step_results)["restore"]
    target_id = restore["target_source_id"].as_string()
    into: ColumnElement[bool] = target_id == str(source_id)
    source = db.get(Source, source_id)
    if source is None:
        return into
    by_name = and_(target_id.is_(None), restore["target_source"].as_string() == source.name)
    return or_(into, by_name)


def _restored_from(db: Session, source_id: uuid.UUID) -> ColumnElement[bool]:
    """Restores of backups taken from the source. Older ones did not record it, so they are traced
    through the backup file to the job that made it, while both still exist."""
    restore = col(Run.step_results)["restore"]
    backup_source = restore["backup_source_id"].as_string()
    artifact_ids = db.exec(
        select(Artifact.id)
        .join(Run, col(Run.id) == col(Artifact.run_id))
        .join(Job, col(Job.id) == col(Run.job_id))
        .where(col(Job.source_id) == source_id)
    ).all()
    traced = and_(
        backup_source.is_(None),
        restore["artifact_id"].as_string().in_([str(a) for a in artifact_ids]),
    )
    return or_(backup_source == str(source_id), traced)


@router.get("")
def list_runs(
    db: SessionDep,
    _user: CurrentUser,
    job_id: uuid.UUID | None = None,
    source_id: uuid.UUID | None = None,
    destination_id: uuid.UUID | None = None,
    status: RunStatus | None = None,
    date_from: Annotated[datetime | None, Query(alias="from")] = None,
    date_to: Annotated[datetime | None, Query(alias="to")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> RunPage:
    conditions = []
    if job_id:
        conditions.append(col(Run.job_id) == job_id)
    if source_id:
        conditions.append(_runs_of_source(db, source_id))
    if destination_id:
        # Runs of jobs that write there now, plus any run that stored a file there (this keeps
        # runs of deleted jobs and of jobs since moved to another destination).
        dest_jobs = select(JobDestination.job_id).where(
            col(JobDestination.destination_id) == destination_id
        )
        dest_runs = select(Artifact.run_id).where(col(Artifact.destination_id) == destination_id)
        conditions.append(or_(col(Run.job_id).in_(dest_jobs), col(Run.id).in_(dest_runs)))
    if status:
        conditions.append(col(Run.status) == status.value)
    if start := _as_utc(date_from):
        conditions.append(col(Run.created_at) >= start)
    if end := _as_utc(date_to):
        conditions.append(col(Run.created_at) <= end)
    total = db.exec(select(func.count()).select_from(Run).where(*conditions)).one()
    rows = db.exec(
        select(Run)
        .where(*conditions)
        .order_by(col(Run.created_at).desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return RunPage(items=summarize_runs(db, rows), total=int(total))


@router.get("/{run_id}")
def get_run(run_id: uuid.UUID, db: SessionDep, _user: CurrentUser) -> RunOut:
    return run_detail(db, _get_or_404(db, run_id))


@router.post("/{run_id}/cancel")
async def cancel_run(
    run_id: uuid.UUID, request: Request, db: SessionDep, user: CurrentUser, runner: RunnerDep
) -> RunOut:
    run = _get_or_404(db, run_id)
    if run.status not in ACTIVE_STATUSES or not await runner.cancel(run.id):
        raise HTTPException(
            status_code=409, detail="This run is not active, so it cannot be cancelled."
        )
    write_audit(
        db,
        "run.cancel",
        user=user,
        object_type="run",
        object_id=str(run.id),
        details={"job_name": run.job_name},
        ip=client_ip(request),
    )
    db.commit()
    db.refresh(run)
    return run_detail(db, run)


@router.post("/{run_id}/retry", status_code=201)
async def retry_run(
    run_id: uuid.UUID, request: Request, db: SessionDep, user: CurrentUser, runner: RunnerDep
) -> RunSummaryOut:
    run = _get_or_404(db, run_id)
    try:
        child_id = await runner.submit_retry(run.id)
    except JobBusyError:
        raise HTTPException(
            status_code=409,
            detail="A run of this job is already in progress (possibly a retry of this run).",
        ) from None
    except RetryNotAllowedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except NoSourceArtifactError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except JobNotFoundError:
        raise HTTPException(status_code=404, detail="Run not found") from None
    write_audit(
        db,
        "run.retry",
        user=user,
        object_type="run",
        object_id=str(run.id),
        details={"job_name": run.job_name, "retry_run_id": str(child_id)},
        ip=client_ip(request),
    )
    db.commit()
    return summarize_runs(db, [_get_or_404(db, child_id)])[0]


def _final_status(run_id: uuid.UUID) -> str | None:
    """The run's status once it has finished, else None. Uses its own short-lived session."""
    with Session(get_engine()) as db:
        status = db.exec(select(Run.status).where(col(Run.id) == run_id)).first()
    if status is None:
        return "deleted"
    return None if status in ACTIVE_STATUSES else status


def _wants_text(request: Request, fmt: str | None) -> bool:
    if fmt:
        return fmt == "text"
    accept = request.headers.get("accept", "")
    return "text/plain" in accept and "text/event-stream" not in accept


@router.get("/{run_id}/logs", response_model=None)
async def run_logs(
    run_id: uuid.UUID,
    request: Request,
    db: SessionDep,
    _user: CurrentUser,
    settings: SettingsDep,
    fmt: Annotated[str | None, Query(alias="format", pattern="^(text|sse)$")] = None,
    offset: Annotated[int | None, Query(ge=0)] = None,
) -> Response:
    _get_or_404(db, run_id)
    path = log_path_for(settings.logs_dir, run_id)
    if _wants_text(request, fmt):
        return PlainTextResponse(await asyncio.to_thread(read_all_text, path))
    start = parse_offset(offset, request.headers.get("last-event-id"))
    return StreamingResponse(
        follow_log(path, start, lambda: _final_status(run_id)),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

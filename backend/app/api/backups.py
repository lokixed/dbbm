import time
import uuid
from collections import defaultdict, deque
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy import ColumnElement, func
from sqlmodel import Session, col, select
from starlette.background import BackgroundTask

from app.api.deps import CurrentUser, ServicesDep, client_ip
from app.api.run_views import summarize_runs
from app.core.db import SessionDep
from app.drivers import DRIVERS
from app.drivers.base import BaseDriver, RestoreOptions
from app.drivers.mongo import FULL_INSTANCE
from app.models import Artifact, Destination, Job, Run, Source
from app.models.base import utcnow
from app.schemas.backup import BackupOut, BackupPage, RescanOut, RestoreIn, VerifyOut
from app.schemas.job import RunSummaryOut
from app.schemas.source import FirestoreConfig
from app.services import backups as svc
from app.services.audit import write_audit
from app.services.naming import database_label
from app.services.restore_run import DEFAULT_TIMEOUT, load_restore_plan, source_type_of
from app.services.runner import JobBusyError, RunFailedError

router = APIRouter(prefix="/api/backups", tags=["backups"])

Row = tuple[Artifact, Run, Source | None, Destination]


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _artifact_or_404(db: Session, backup_id: uuid.UUID) -> tuple[Artifact, Destination]:
    artifact = db.get(Artifact, backup_id)
    if artifact is None or artifact.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Backup not found")
    row = db.get(Destination, artifact.destination_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Backup not found")
    return artifact, row


def _out(row: Row) -> BackupOut:
    artifact, run, source, dest = row
    return BackupOut(
        id=str(artifact.id),
        run_id=str(run.id),
        job_id=str(run.job_id) if run.job_id else None,
        job_name=run.job_name,
        source_id=str(source.id) if source else None,
        source_name=source.name if source else None,
        source_type=source_type_of(artifact.path, source.type if source else None),
        source_database=artifact.source_database,
        destination_id=str(dest.id),
        destination_name=dest.name,
        destination_type=dest.type,
        path=artifact.path,
        size_bytes=artifact.size_bytes,
        sha256=artifact.sha256,
        md5=artifact.md5,
        compressed=artifact.compressed,
        created_at=artifact.created_at,
        verified_at=artifact.verified_at,
        missing=artifact.missing_at is not None,
    )


def _audit(
    db: Session,
    action: str,
    user: CurrentUser,
    request: Request,
    artifact: Artifact,
    **extra: object,
) -> None:
    write_audit(
        db,
        action,
        user=user,
        object_type="artifact",
        object_id=str(artifact.id),
        details={"path": artifact.path, **extra},
        ip=client_ip(request),
    )


@router.get("")
def list_backups(
    db: SessionDep,
    _user: CurrentUser,
    run_id: uuid.UUID | None = None,
    job_id: uuid.UUID | None = None,
    source_id: uuid.UUID | None = None,
    database: Annotated[str | None, Query(max_length=255)] = None,
    destination_id: uuid.UUID | None = None,
    status: Annotated[str | None, Query(pattern="^(ok|missing)$")] = None,
    date_from: Annotated[datetime | None, Query(alias="from")] = None,
    date_to: Annotated[datetime | None, Query(alias="to")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> BackupPage:
    conditions: list[ColumnElement[bool]] = [col(Artifact.deleted_at).is_(None)]
    if run_id:
        conditions.append(col(Artifact.run_id) == run_id)
    if job_id:
        conditions.append(col(Run.job_id) == job_id)
    if source_id:
        conditions.append(col(Job.source_id) == source_id)
    if database:
        conditions.append(col(Artifact.source_database) == database)
    if destination_id:
        conditions.append(col(Artifact.destination_id) == destination_id)
    if status == "missing":
        conditions.append(col(Artifact.missing_at).is_not(None))
    elif status == "ok":
        conditions.append(col(Artifact.missing_at).is_(None))
    if start := _as_utc(date_from):
        conditions.append(col(Artifact.created_at) >= start)
    if end := _as_utc(date_to):
        conditions.append(col(Artifact.created_at) <= end)
    joined = (
        select(Artifact)
        .join(Run, col(Run.id) == col(Artifact.run_id))
        .join(Job, col(Job.id) == col(Run.job_id), isouter=True)
    )
    total = db.exec(select(func.count()).select_from(joined.where(*conditions).subquery())).one()
    rows = db.exec(
        select(Artifact, Run, Source, Destination)
        .join(Run, col(Run.id) == col(Artifact.run_id))
        .join(Job, col(Job.id) == col(Run.job_id), isouter=True)
        .join(Source, col(Source.id) == col(Job.source_id), isouter=True)
        .join(Destination, col(Destination.id) == col(Artifact.destination_id))
        .where(*conditions)
        .order_by(col(Artifact.created_at).desc(), col(Artifact.path))
        .offset(offset)
        .limit(limit)
    ).all()
    return BackupPage(items=[_out(r) for r in rows], total=int(total))


@router.get("/{backup_id}/download", response_class=FileResponse)
async def download_backup(
    backup_id: uuid.UUID, request: Request, db: SessionDep, user: CurrentUser
) -> FileResponse:
    artifact, dest = _artifact_or_404(db, backup_id)
    try:
        download = await svc.prepare_download(db, artifact, dest)
    except svc.BackupError as exc:
        _audit(db, "backup.download_failed", user, request, artifact, error=str(exc))
        db.commit()
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from None
    except Exception as exc:
        message = svc.describe(exc)
        _audit(db, "backup.download_failed", user, request, artifact, error=message)
        db.commit()
        raise HTTPException(
            status_code=502, detail=f"Could not read the backup: {message}"
        ) from None
    _audit(db, "backup.download", user, request, artifact, destination=dest.name)
    db.commit()
    background = (
        BackgroundTask(svc.remove_dir, download.cleanup_dir) if download.cleanup_dir else None
    )
    return FileResponse(
        download.path,
        media_type="application/octet-stream",
        filename=download.filename,
        background=background,
    )


@router.post("/{backup_id}/verify")
async def verify_backup(
    backup_id: uuid.UUID, request: Request, db: SessionDep, user: CurrentUser
) -> VerifyOut:
    artifact, dest = _artifact_or_404(db, backup_id)
    try:
        result = await svc.check_integrity(artifact, dest)
    except svc.BackupError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from None
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"Could not read the backup: {svc.describe(exc)}"
        ) from None
    artifact.missing_at = utcnow() if result.status == svc.MISSING else None
    if result.status == svc.OK:
        artifact.verified_at = utcnow()
    db.add(artifact)
    _audit(db, "backup.verify", user, request, artifact, result=result.status)
    db.commit()
    return VerifyOut(status=result.status, detail=result.detail, verified_at=artifact.verified_at)


@router.delete("/{backup_id}", status_code=204)
async def delete_backup(
    backup_id: uuid.UUID,
    request: Request,
    db: SessionDep,
    user: CurrentUser,
    services: ServicesDep,
) -> None:
    artifact, dest = _artifact_or_404(db, backup_id)
    async with services.retention.lock(dest.id):
        try:
            await svc.delete_stored(artifact, dest)
        except svc.BackupError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from None
        except Exception as exc:
            raise HTTPException(
                status_code=502, detail=f"Could not delete the backup: {svc.describe(exc)}"
            ) from None
    artifact.deleted_at = utcnow()
    db.add(artifact)
    _audit(db, "backup.delete", user, request, artifact, destination=dest.name)
    db.commit()


@router.post("/rescan")
async def rescan_destination(
    destination_id: uuid.UUID, request: Request, db: SessionDep, user: CurrentUser
) -> RescanOut:
    dest = db.get(Destination, destination_id)
    if dest is None:
        raise HTTPException(status_code=404, detail="Destination not found")
    try:
        checked, missing = await svc.scan_destination(db, dest)
    except svc.BackupError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from None
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"Could not scan the destination: {svc.describe(exc)}"
        ) from None
    write_audit(
        db,
        "backup.rescan",
        user=user,
        object_type="destination",
        object_id=str(dest.id),
        details={"name": dest.name, "checked": checked, "missing": missing},
        ip=client_ip(request),
    )
    db.commit()
    return RescanOut(destination_id=str(dest.id), checked=checked, missing=missing)


__all__ = ["router"]


def _check_firestore_target(target: Source, body: RestoreIn) -> None:
    """A Firestore import goes into the target source's own database and never deletes."""
    label = database_label(FirestoreConfig.model_validate(target.config).database_id)
    if body.target_database != label:
        raise HTTPException(
            status_code=422,
            detail=f"A Firestore import goes into the target source's database '{label}'.",
        )
    if body.drop:
        raise HTTPException(
            status_code=422,
            detail="Replace existing is not available for Firestore: an import adds to and "
            "overwrites documents, and never deletes them.",
        )


RESTORES_PER_MINUTE = 5
_restore_starts: dict[str, deque[float]] = defaultdict(deque)


def _rate_limited(username: str) -> bool:
    """At most RESTORES_PER_MINUTE restore starts per user in any minute."""
    now = time.monotonic()
    recent = _restore_starts[username]
    while recent and now - recent[0] > 60:
        recent.popleft()
    if len(recent) >= RESTORES_PER_MINUTE:
        return True
    recent.append(now)
    return False


def _original_source(db: Session, artifact: Artifact) -> Source | None:
    run = db.get(Run, artifact.run_id)
    job = db.get(Job, run.job_id) if run and run.job_id else None
    return db.get(Source, job.source_id) if job else None


@router.post("/{backup_id}/restore", status_code=201)
async def restore_backup(
    backup_id: uuid.UUID,
    body: RestoreIn,
    request: Request,
    db: SessionDep,
    user: CurrentUser,
    services: ServicesDep,
) -> RunSummaryOut:
    artifact, copy = _artifact_or_404(db, backup_id)
    if artifact.missing_at is not None:
        raise HTTPException(
            status_code=409, detail="This copy is marked missing; rescan or pick another copy."
        )
    original = _original_source(db, artifact)
    kind = source_type_of(artifact.path, original.type if original else None)
    target = db.get(Source, body.target_source_id)
    if kind is None:
        raise HTTPException(status_code=422, detail="This backup's file type cannot be restored.")
    if target is None:
        raise HTTPException(status_code=404, detail="Target source not found")
    if target.type != kind:
        raise HTTPException(
            status_code=422,
            detail=f"A {kind} backup can only be restored into a {kind} source.",
        )
    driver_class: type[BaseDriver] = DRIVERS[kind]
    if driver_class.restore_database is BaseDriver.restore_database:
        raise HTTPException(
            status_code=422, detail=f"Restoring {kind} backups is not supported yet."
        )
    if kind == "postgres" and artifact.source_database == "globals":
        raise HTTPException(
            status_code=422,
            detail="The roles file cannot be restored here; download it and apply it with psql.",
        )
    if kind == "firestore":
        _check_firestore_target(target, body)
    full = kind == "mongo" and artifact.source_database == FULL_INSTANCE
    if full and body.target_database != FULL_INSTANCE:
        raise HTTPException(
            status_code=422,
            detail=(
                "A full-instance backup restores the whole server; "
                f"use '{FULL_INSTANCE}' as the target name."
            ),
        )
    if body.confirm_database != body.target_database:
        raise HTTPException(status_code=422, detail="Type the target database name to confirm.")
    overwrites_original = (original.id if original else None) == target.id and (
        full or body.target_database == artifact.source_database
    )
    if overwrites_original and not body.confirm_overwrite_original:
        raise HTTPException(
            status_code=409,
            detail=(
                "This would overwrite the original database. "
                "Confirm overwriting the original to continue."
            ),
        )
    if _rate_limited(user.username):
        raise HTTPException(status_code=429, detail="Too many restores started. Wait a minute.")
    options = RestoreOptions(artifact.source_database, body.target_database, body.drop, full)
    try:
        plan = load_restore_plan(artifact, copy, target, options, DEFAULT_TIMEOUT, user.username)
        run_id = await services.runner.submit_restore(plan)
    except RunFailedError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except JobBusyError:
        raise HTTPException(
            status_code=409,
            detail="A backup or restore is already running on the target source.",
        ) from None
    write_audit(
        db,
        "backup.restore_started",
        user=user,
        object_type="artifact",
        object_id=str(artifact.id),
        details={
            **plan.summary(),
            "run_id": str(run_id),
            "overwrites_original": overwrites_original,
        },
        ip=client_ip(request),
    )
    db.commit()
    run = db.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=500, detail="The restore run was not created.")
    return summarize_runs(db, [run])[0]

import uuid
from collections.abc import Sequence

from sqlalchemy import func
from sqlmodel import Session, col, select

from app.models import Artifact, Destination, NotificationChannel, NotificationEvent, Run, Source
from app.schemas.job import ArtifactOut, RunNotificationOut, RunOut, RunSummaryOut


def restore_target_types(db: Session, runs: Sequence[Run]) -> dict[uuid.UUID, str | None]:
    """The engine of each restore run's target, for its icon. Runs from before it was recorded
    look their target source up by name (None once that source is renamed or deleted)."""
    infos = {r.id: (r.step_results or {}).get("restore") or {} for r in runs if r.kind == "restore"}
    names = {str(i["target_source"]) for i in infos.values() if i.get("target_source")}
    by_name: dict[str, str] = {}
    if names:
        rows = db.exec(select(Source.name, Source.type).where(col(Source.name).in_(names))).all()
        by_name = {name: kind for name, kind in rows}
    return {
        run_id: info.get("target_type") or by_name.get(str(info.get("target_source")))
        for run_id, info in infos.items()
    }


def summarize_runs(db: Session, runs: Sequence[Run]) -> list[RunSummaryOut]:
    stats: dict[uuid.UUID, tuple[int, int]] = {}
    if runs:
        ids = [r.id for r in runs]
        rows = db.exec(
            select(Artifact.run_id, func.count(), func.coalesce(func.sum(Artifact.size_bytes), 0))
            .where(col(Artifact.run_id).in_(ids), col(Artifact.deleted_at).is_(None))
            .group_by(col(Artifact.run_id))
        ).all()
        stats = {run_id: (int(count), int(total)) for run_id, count, total in rows}
    target_types = restore_target_types(db, runs)
    return [
        RunSummaryOut(
            id=str(r.id),
            job_id=str(r.job_id) if r.job_id else None,
            job_name=r.job_name,
            trigger=r.trigger,
            status=r.status,
            started_at=r.started_at,
            finished_at=r.finished_at,
            error=r.error,
            created_at=r.created_at,
            artifact_count=stats.get(r.id, (0, 0))[0],
            total_size=stats.get(r.id, (0, 0))[1],
            parent_run_id=str(r.parent_run_id) if r.parent_run_id else None,
            scheduled_for=r.scheduled_for,
            kind=r.kind,
            source_type=target_types.get(r.id),
        )
        for r in runs
    ]


def run_detail(db: Session, run: Run) -> RunOut:
    summary = summarize_runs(db, [run])[0]
    rows = db.exec(
        select(Artifact, Destination.name)
        .join(Destination, col(Destination.id) == col(Artifact.destination_id), isouter=True)
        .where(col(Artifact.run_id) == run.id, col(Artifact.deleted_at).is_(None))
        .order_by(col(Artifact.source_database), col(Artifact.created_at))
    ).all()
    artifacts = [
        ArtifactOut(
            id=str(a.id),
            destination_id=str(a.destination_id),
            destination_name=name,
            source_database=a.source_database,
            path=a.path,
            size_bytes=a.size_bytes,
            sha256=a.sha256,
            md5=a.md5,
            compressed=a.compressed,
            encrypted=a.encrypted,
            recipients=list(a.recipients or []),
            created_at=a.created_at,
        )
        for a, name in rows
    ]
    retries = db.exec(
        select(Run.id).where(col(Run.parent_run_id) == run.id).order_by(col(Run.created_at))
    ).all()
    events = db.exec(
        select(NotificationEvent, NotificationChannel.name)
        .join(
            NotificationChannel,
            col(NotificationChannel.id) == col(NotificationEvent.channel_id),
            isouter=True,
        )
        .where(col(NotificationEvent.run_id) == run.id)
        .order_by(col(NotificationEvent.created_at))
    ).all()
    return RunOut(
        **summary.model_dump(),
        step_results=run.step_results,
        artifacts=artifacts,
        retry_run_ids=[str(r) for r in retries],
        notifications=[
            RunNotificationOut(
                id=str(e.id),
                channel_id=str(e.channel_id),
                channel_name=name,
                event_type=e.event_type,
                status=e.status,
                attempts=e.attempts,
                error=e.error,
                created_at=e.created_at,
            )
            for e, name in events
        ],
    )

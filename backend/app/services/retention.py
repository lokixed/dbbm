"""Retention: delete expired backups per destination.

The unit is one run's backup at one destination. Per (job, destination) the counted runs are
`success` runs and `partial` runs whose copies at this destination all succeeded. A run is
deleted only when it is outside the newest `keep_last` counted runs AND older than `keep_days`
(when only one is set, that one decides; with neither, nothing is deleted). The newest
successful run per (job, destination) is always kept. Only tracked artifacts are touched.
"""

import asyncio
import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlmodel import Session, col, select

from app.core.clock import SYSTEM_CLOCK, Clock
from app.core.db import get_engine
from app.models import Artifact, Destination, JobDestination, Run, RunStatus, User
from app.models.run import ACTIVE_STATUSES
from app.services.audit import write_audit
from app.services.destination_tokens import DestinationTokenStore
from app.services.messages import retention_failed_message
from app.services.naming import manifest_key
from app.services.notifier import Notifier
from app.services.paths import PathNotAllowed, check_lexical
from app.services.redact import describe_error, sanitize
from app.services.runner import destination_info
from app.storage import StorageContext, get_storage
from app.storage.rclone_utils import RcloneError

logger = logging.getLogger(__name__)

DELETE_TIMEOUT = 600
FAILED_PASSES_ALERT = 3
MAX_AUDIT_PATHS = 50


@dataclass(frozen=True)
class Policy:
    keep_last: int | None = None
    keep_days: int | None = None

    @property
    def active(self) -> bool:
        return self.keep_last is not None or self.keep_days is not None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "Policy":
        return cls(keep_last=data.get("keep_last"), keep_days=data.get("keep_days"))


@dataclass(frozen=True)
class ArtifactRef:
    id: uuid.UUID
    path: str
    size: int


@dataclass
class RunCopy:
    run_id: uuid.UUID
    job_key: str
    job_name: str
    status: str
    moment: datetime
    counted: bool
    artifacts: list[ArtifactRef] = field(default_factory=list)
    protected: bool = False

    @property
    def size(self) -> int:
        return sum(a.size for a in self.artifacts)


@dataclass
class Plan:
    policy: Policy
    delete: list[RunCopy] = field(default_factory=list)
    keep: list[RunCopy] = field(default_factory=list)


@dataclass
class Result:
    destination_id: uuid.UUID
    destination_name: str
    deleted_runs: int = 0
    deleted_artifacts: int = 0
    deleted_bytes: int = 0
    failures: list[dict[str, str]] = field(default_factory=list)
    skipped: str | None = None


def plan_retention(copies: list[RunCopy], policy: Policy, now: datetime) -> Plan:
    plan = Plan(policy)
    groups: dict[str, list[RunCopy]] = defaultdict(list)
    for copy in copies:
        groups[copy.job_key].append(copy)
    cutoff = now - timedelta(days=policy.keep_days) if policy.keep_days is not None else None
    for group in groups.values():
        group.sort(key=lambda c: (c.moment, str(c.run_id)), reverse=True)
        newest = next((c for c in group if c.status == RunStatus.SUCCESS), None)
        newest = newest or next((c for c in group if c.counted), None)
        if newest is not None:
            newest.protected = True
        newer_counted = 0
        for copy in group:
            outside = policy.keep_last is None or newer_counted >= policy.keep_last
            older = cutoff is None or copy.moment < cutoff
            if copy.counted:
                newer_counted += 1
            if policy.active and outside and older and not copy.protected:
                plan.delete.append(copy)
            else:
                plan.keep.append(copy)
    for items in (plan.delete, plan.keep):
        items.sort(key=lambda c: (c.moment, str(c.run_id)), reverse=True)
    return plan


def _stored_ok(run: Run, destination_id: uuid.UUID) -> bool:
    entries = [
        entry
        for unit in run.step_results.get("units", [])
        if unit.get("kind") == "database"
        for entry in unit.get("destinations", [])
        if entry.get("destination_id") == str(destination_id)
    ]
    return bool(entries) and all(e.get("status") == "ok" for e in entries)


def collect_copies(db: Session, destination_id: uuid.UUID) -> list[RunCopy]:
    rows = db.exec(
        select(Artifact, Run)
        .join(Run, col(Run.id) == col(Artifact.run_id))
        .where(
            col(Artifact.destination_id) == destination_id,
            col(Artifact.deleted_at).is_(None),
            col(Run.status).not_in(ACTIVE_STATUSES),
        )
        .order_by(col(Artifact.path))
    ).all()
    copies: dict[uuid.UUID, RunCopy] = {}
    for artifact, run in rows:
        copy = copies.get(run.id)
        if copy is None:
            counted = run.status == RunStatus.SUCCESS or (
                run.status == RunStatus.PARTIAL and _stored_ok(run, destination_id)
            )
            copy = RunCopy(
                run_id=run.id,
                job_key=str(run.job_id) if run.job_id else f"name:{run.job_name}",
                job_name=run.job_name,
                status=run.status,
                moment=run.started_at or run.created_at,
                counted=counted,
            )
            copies[run.id] = copy
        copy.artifacts.append(ArtifactRef(artifact.id, artifact.path, artifact.size_bytes))
    return list(copies.values())


def _describe(exc: BaseException) -> str:
    if isinstance(exc, PathNotAllowed | RcloneError | FileExistsError):
        return sanitize(str(exc))
    if isinstance(exc, OSError):
        return f"{type(exc).__name__}: {exc.strerror or 'I/O error'}"
    return describe_error(exc)


class RetentionService:
    def __init__(self, notifier: Notifier | None = None, clock: Clock = SYSTEM_CLOCK) -> None:
        self._notifier = notifier
        self._clock = clock
        self._locks: dict[uuid.UUID, asyncio.Lock] = {}
        self._failed_passes: dict[uuid.UUID, int] = defaultdict(int)
        self._tasks: set[asyncio.Task[None]] = set()

    def lock(self, destination_id: uuid.UUID) -> asyncio.Lock:
        return self._locks.setdefault(destination_id, asyncio.Lock())

    # ---------- planning ----------

    def _plan(
        self, destination_id: uuid.UUID, policy: Policy | None
    ) -> tuple[Destination, Plan] | None:
        with Session(get_engine()) as db:
            row = db.get(Destination, destination_id)
            if row is None:
                return None
            effective = policy or Policy.from_json(row.retention)
            plan = plan_retention(collect_copies(db, row.id), effective, self._clock.now())
            db.expunge(row)
            return row, plan

    async def preview(self, destination_id: uuid.UUID, policy: Policy | None = None) -> Plan:
        found = await asyncio.to_thread(self._plan, destination_id, policy)
        if found is None:
            raise LookupError(destination_id)
        return found[1]

    # ---------- execution ----------

    async def run(
        self,
        destination_id: uuid.UUID,
        *,
        trigger: str = "auto",
        user: User | None = None,
        ip: str | None = None,
    ) -> Result:
        async with self.lock(destination_id):
            found = await asyncio.to_thread(self._plan, destination_id, None)
            if found is None:
                raise LookupError(destination_id)
            row, plan = found
            result = Result(row.id, row.name)
            if plan.delete:
                await self._execute(row, plan, result)
            if plan.delete or trigger == "manual":
                await asyncio.to_thread(self._audit, row, plan, result, trigger, user, ip)
        await self._track_failures(row, result)
        return result

    async def _execute(self, row: Destination, plan: Plan, result: Result) -> None:
        info = destination_info(row)
        if info.blocked:
            result.skipped = info.blocked
            logger.warning("Retention for destination %s skipped: %s", row.name, info.blocked)
            return
        context = StorageContext(
            destination_id=row.id,
            timeout_seconds=DELETE_TIMEOUT,
            token_store=DestinationTokenStore(row.id) if row.type == "gdrive" else None,
        )
        storage = get_storage(row.type, dict(row.config), {}, context)
        for copy in plan.delete:
            removed = 0
            for artifact in copy.artifacts:
                try:
                    check_lexical(artifact.path)
                    await storage.delete(artifact.path)
                    await storage.delete(manifest_key(artifact.path))
                    await asyncio.to_thread(self._mark_deleted, artifact.id, row.id)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    message = _describe(exc)
                    result.failures.append({"path": artifact.path, "error": message})
                    logger.warning(
                        "Retention could not delete %s at %s (retried next time): %s",
                        artifact.path, row.name, message,
                    )  # fmt: skip
                    continue
                removed += 1
                result.deleted_artifacts += 1
                result.deleted_bytes += artifact.size
            if removed == len(copy.artifacts):
                result.deleted_runs += 1

    def _mark_deleted(self, artifact_id: uuid.UUID, destination_id: uuid.UUID) -> None:
        with Session(get_engine()) as db:
            artifact = db.get(Artifact, artifact_id)
            if artifact is None or artifact.destination_id != destination_id:
                return
            if artifact.deleted_at is None:
                artifact.deleted_at = self._clock.now()
                db.add(artifact)
                db.commit()

    @staticmethod
    def _audit(
        row: Destination,
        plan: Plan,
        result: Result,
        trigger: str,
        user: User | None,
        ip: str | None,
    ) -> None:
        failed = {f["path"] for f in result.failures}
        deleted = [a.path for c in plan.delete for a in c.artifacts if a.path not in failed]
        with Session(get_engine()) as db:
            write_audit(
                db,
                "retention.run",
                user=db.get(User, user.id) if user else None,
                object_type="destination",
                object_id=str(row.id),
                details={
                    "name": row.name,
                    "trigger": trigger,
                    "keep_last": plan.policy.keep_last,
                    "keep_days": plan.policy.keep_days,
                    "deleted_runs": result.deleted_runs,
                    "deleted_artifacts": result.deleted_artifacts,
                    "deleted_bytes": result.deleted_bytes,
                    "deleted_paths": deleted[:MAX_AUDIT_PATHS],
                    "failures": len(result.failures),
                    "skipped": result.skipped,
                },
                ip=ip,
            )
            db.commit()

    async def _track_failures(self, row: Destination, result: Result) -> None:
        if not result.failures:
            self._failed_passes.pop(row.id, None)
            return
        self._failed_passes[row.id] += 1
        passes = self._failed_passes[row.id]
        if passes == FAILED_PASSES_ALERT and self._notifier is not None:
            message = retention_failed_message(
                str(row.id), row.name, passes, result.failures[-1]["error"]
            )
            await self._notifier.notify(message)

    # ---------- triggers ----------

    async def run_all(self) -> list[Result]:
        ids = await asyncio.to_thread(self._all_destinations)
        return await self._run_many(ids)

    async def run_for_job(self, job_id: uuid.UUID) -> list[Result]:
        ids = await asyncio.to_thread(self._job_destinations, job_id)
        return await self._run_many(ids)

    def schedule_for_job(self, job_id: uuid.UUID) -> None:
        """Background retention after a run; never raises into the caller."""
        task = asyncio.create_task(self._quiet(self.run_for_job(job_id)))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def wait_idle(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def stop(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*list(self._tasks), return_exceptions=True)

    @staticmethod
    async def _quiet(work: Any) -> None:
        try:
            await work
        except Exception as exc:
            logger.warning("Retention after run failed: %s", type(exc).__name__)

    async def _run_many(self, ids: list[uuid.UUID]) -> list[Result]:
        results: list[Result] = []
        for destination_id in ids:
            try:
                results.append(await self.run(destination_id))
            except LookupError:
                continue
            except Exception as exc:
                logger.warning(
                    "Retention for destination %s failed: %s", destination_id, _describe(exc)
                )
        return results

    @staticmethod
    def _all_destinations() -> list[uuid.UUID]:
        with Session(get_engine()) as db:
            return list(db.exec(select(Destination.id).order_by(col(Destination.name))).all())

    @staticmethod
    def _job_destinations(job_id: uuid.UUID) -> list[uuid.UUID]:
        with Session(get_engine()) as db:
            return list(
                db.exec(
                    select(JobDestination.destination_id).where(
                        col(JobDestination.job_id) == job_id
                    )
                ).all()
            )

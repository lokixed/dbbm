"""Run lifecycle: queued -> running (preflight, dump, store, verify, cleanup) -> final status.

The runner is a plain in-process asyncio component with no FastAPI dependency, so the
scheduler of a later milestone can call `submit()` directly.
"""

import asyncio
import copy
import hmac
import json
import logging
import shutil
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager, suppress
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from functools import partial
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Protocol

from sqlalchemy import func
from sqlmodel import Session, col, select

from app.core.config import Settings, get_settings
from app.core.db import get_engine
from app.drivers import get_driver
from app.drivers.base import (
    BaseDriver,
    DumpContext,
    DumpedArtifact,
    DumpError,
    DumpReporter,
    StepHandle,
)
from app.models import (
    AgeRecipient,
    Artifact,
    AuthStatus,
    Destination,
    Job,
    JobDestination,
    JobRecipient,
    Run,
    Source,
)
from app.models.base import new_uuid, utcnow
from app.models.run import ACTIVE_STATUSES, RunStatus, RunTrigger
from app.services import age
from app.services.destination_tokens import DestinationTokenStore
from app.services.hashing import OperationCancelledError, sha256_file
from app.services.logstream import RunLogger, log_path_for
from app.services.naming import artifact_key, manifest_key
from app.services.paths import PathNotAllowed
from app.services.redact import describe_error, sanitize
from app.services.secrets import UNDECRYPTABLE_MESSAGE, SecretsUnavailable, load_secrets
from app.storage import BaseStorage, StorageContext, get_storage
from app.storage.base import run_blocking
from app.storage.local import format_bytes
from app.storage.rclone_utils import NEEDS_REAUTH_MESSAGE, ErrorClass, RcloneError

if TYPE_CHECKING:
    from app.services.restore_run import RestorePlan

logger = logging.getLogger(__name__)

MIN_FREE_BYTES = 256 * 1024 * 1024
FREE_SPACE_FACTOR = 1.5
CANCEL_WAIT_SECONDS = 60
MAX_ERROR_CHARS = 1900
DEFAULT_TIMEOUT = 3600
VERIFY_MISMATCH = "verify_mismatch"
DISCARD_TIMEOUT = 120
NO_TOKEN_MESSAGE = "No Google Drive token is saved; reconnect in Destinations."  # noqa: S105


class JobBusyError(Exception):
    """The job already has a queued or running run."""


class JobNotFoundError(Exception):
    pass


class RunFailedError(Exception):
    """The run cannot continue. The message is sanitized and safe to store and show."""


class VerificationError(Exception):
    """The stored copy differs from the local file. The message is safe to show."""


class RetryNotAllowedError(Exception):
    """The run is not in a state that can be retried (HTTP 409)."""


class NoSourceArtifactError(Exception):
    """No verified copy remains to retry from (HTTP 422)."""


def no_databases_message(listed: Sequence[str]) -> str:
    """Why a run has nothing to dump. A source set to "All databases" skips system databases
    (e.g. postgres, mysql, admin); on hosted services such as Supabase the data lives in one of
    them, so name what was skipped and how to include it."""
    message = "There are no databases to back up on this source."
    if not listed:
        return message
    return (
        f'{message} "All databases" skips system databases, and the source has only these: '
        f"{', '.join(listed)}. To back one up, edit the source, choose Selected databases "
        "and tick it."
    )


def compute_final_status(outcomes: Sequence[bool]) -> str:
    """One outcome per (database, destination) pair: all ok, some ok, or none ok."""
    if not outcomes:
        return RunStatus.FAILED
    if all(outcomes):
        return RunStatus.SUCCESS
    return RunStatus.PARTIAL if any(outcomes) else RunStatus.FAILED


def database_outcomes(results: dict[str, Any]) -> list[bool]:
    return [
        d.get("status") == "ok"
        for unit in results.get("units", [])
        if unit.get("kind") == "database"
        for d in unit.get("destinations", [])
    ]


def _iso(moment: datetime | None = None) -> str:
    return (moment or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class DestinationInfo:
    id: uuid.UUID
    name: str
    type: str
    config: dict[str, Any]
    # Why the destination must not be attempted (e.g. Drive authorization expired).
    blocked: str | None = None


def destination_info(row: Destination) -> DestinationInfo:
    info = DestinationInfo(row.id, row.name, row.type, dict(row.config))
    if row.type != "gdrive":
        return info
    try:
        secrets = load_secrets(row.secrets_enc)
    except SecretsUnavailable:
        info.blocked = UNDECRYPTABLE_MESSAGE
        return info
    if not secrets.get("token"):
        info.blocked = NO_TOKEN_MESSAGE
    elif row.auth_status == AuthStatus.NEEDS_REAUTH:
        info.blocked = NEEDS_REAUTH_MESSAGE
    return info


@dataclass
class RunPlan:
    job_name: str
    compress: bool
    timeout_seconds: int
    source_type: str
    source_config: dict[str, Any]
    source_secrets: dict[str, str]
    destinations: list[DestinationInfo]
    last_artifact_size: int | None
    # (fingerprint, public key) of every key to encrypt to; empty when encryption is off.
    recipients: list[tuple[str, str]] = field(default_factory=list)
    encrypt: bool = False


@dataclass
class RunState:
    steps: list[dict[str, Any]] = field(default_factory=list)
    units: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    secrets: list[str] = field(default_factory=list)
    # Non-secret description of a restore run (what was restored where); None for backups.
    restore: dict[str, Any] | None = None

    def outcomes(self) -> list[bool]:
        return database_outcomes({"units": self.units})

    def as_json(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "steps": self.steps,
            "units": self.units,
            "warnings": self.warnings,
        }
        if self.restore is not None:
            data["restore"] = self.restore
        return data

    def summary_error(self) -> str | None:
        if not self.errors:
            return None
        return "; ".join(self.errors)[:MAX_ERROR_CHARS]


@dataclass(frozen=True)
class RetrySource:
    destination: DestinationInfo
    size: int
    sha256: str
    compressed: bool
    encrypted: bool = False
    recipients: tuple[str, ...] = ()


@dataclass
class RetryItem:
    """One failed (database, destination) pair of the parent run."""

    unit_index: int
    entry_index: int
    database: str
    kind: str
    key: str
    target: DestinationInfo | None
    sources: list[RetrySource]
    result: dict[str, Any] | None = None


@dataclass
class RetryPlan:
    parent_id: uuid.UUID
    job_id: uuid.UUID | None
    job_name: str
    timeout_seconds: int
    items: list[RetryItem]

    @property
    def lock_key(self) -> uuid.UUID:
        return self.job_id or self.parent_id


def _log_abort(log: RunLogger, error: str) -> str:
    """Record why a run stopped early as an ERROR line (the log's Errors filter shows it) and
    return the error. Steps that fail without aborting log their own errors."""
    with suppress(OSError):
        log.error(error)
    return error


def _describe(exc: BaseException, secrets: Sequence[str]) -> str:
    if isinstance(
        exc,
        FileExistsError
        | PathNotAllowed
        | DumpError
        | RunFailedError
        | RcloneError
        | VerificationError,
    ):
        return sanitize(str(exc), secrets)
    if isinstance(exc, OSError):
        return f"{type(exc).__name__}: {exc.strerror or 'I/O error'}"  # str(exc) embeds paths
    return describe_error(exc, secrets)


def _error_class(exc: BaseException) -> str:
    if isinstance(exc, RcloneError):
        return exc.error_class.value
    if isinstance(exc, VerificationError):
        return VERIFY_MISMATCH
    return ErrorClass.OTHER.value


@asynccontextmanager
async def _timed(steps: list[dict[str, Any]], name: str) -> AsyncIterator[dict[str, Any]]:
    """A per-destination sub-step (put, verify) with its status and duration."""
    entry: dict[str, Any] = {"name": name, "status": "running", "duration_ms": None}
    steps.append(entry)
    began = time.monotonic()
    try:
        yield entry
        entry["status"] = "ok"
    except asyncio.CancelledError:
        entry["status"] = "cancelled"
        raise
    except BaseException:
        entry["status"] = "failed"
        raise
    finally:
        entry["duration_ms"] = int((time.monotonic() - began) * 1000)


class _UnitReporter(DumpReporter):
    """Records a driver's named sub-steps (with timings) in one unit's step results and the log."""

    def __init__(
        self,
        save: Callable[[], Awaitable[None]],
        steps: list[dict[str, Any]],
        state: RunState,
        log: RunLogger,
        secrets: Sequence[str],
    ) -> None:
        self._save = save
        self._steps = steps
        self._state = state
        self._log = log
        self._secrets = secrets

    @asynccontextmanager
    async def step(self, name: str) -> AsyncIterator[StepHandle]:
        entry: dict[str, Any] = {
            "name": name,
            "status": "running",
            "started_at": _iso(),
            "duration_ms": None,
            "detail": None,
        }
        self._steps.append(entry)
        self._log.info(f"== {name} ==")
        await self._save()
        handle = StepHandle(name)
        began = time.monotonic()
        try:
            yield handle
            entry["status"] = "warning" if handle.warned else "ok"
            entry["detail"] = handle.detail
        except asyncio.CancelledError:
            entry["status"] = "cancelled"
            raise
        except BaseException as exc:
            entry["status"] = "failed"
            entry["detail"] = sanitize(str(exc), self._secrets)
            raise
        finally:
            entry["duration_ms"] = int((time.monotonic() - began) * 1000)
            self._log.info(f"{name} {entry['status']} after {entry['duration_ms'] / 1000:.1f}s")
            with suppress(Exception):
                await self._save()

    def warning(self, message: str) -> None:
        self._state.warnings.append(sanitize(message, self._secrets))


class RunHooks(Protocol):
    """Side effects outside the run (pings, notifications, retention). Must not block or raise."""

    def on_started(self, run_id: uuid.UUID, job_id: uuid.UUID, trigger: str) -> None: ...
    def on_finished(self, run_id: uuid.UUID) -> None: ...


class Runner:
    def __init__(self, settings: Settings | None = None, hooks: RunHooks | None = None) -> None:
        self._settings = settings or get_settings()
        self._semaphore = asyncio.Semaphore(self._settings.max_concurrent_runs)
        self._active: dict[uuid.UUID, uuid.UUID] = {}  # job id -> run id
        self._tasks: dict[uuid.UUID, asyncio.Task[None]] = {}
        self._stopping = False
        self.hooks = hooks

    # ---------- lifecycle ----------

    async def start(self) -> list[uuid.UUID]:
        """Recover from the previous process; returns the runs marked interrupted."""
        return await asyncio.to_thread(self.recover)

    async def stop(self) -> None:
        self._stopping = True
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def recover(self) -> list[uuid.UUID]:
        """Mark runs left over from a previous process as interrupted; remove their files."""
        with Session(get_engine()) as db:
            rows = db.exec(select(Run).where(col(Run.status).in_(ACTIVE_STATUSES))).all()
            interrupted = [run.id for run in rows]
            for run in rows:
                run.status = RunStatus.INTERRUPTED
                run.finished_at = utcnow()
                run.error = "Interrupted: the service restarted while this run was active."
                db.add(run)
            db.commit()
        work = self._settings.work_dir
        if work.is_dir():
            for child in work.iterdir():
                shutil.rmtree(child, ignore_errors=True)
                child.unlink(missing_ok=True)
        return interrupted

    def is_active(self, job_id: uuid.UUID) -> bool:
        return job_id in self._active

    def active_run_ids(self) -> list[uuid.UUID]:
        return [*self._active.values(), *self._tasks.keys()]

    # ---------- public API ----------

    async def submit(
        self,
        job_id: uuid.UUID,
        trigger: str = "manual",
        scheduled_for: datetime | None = None,
    ) -> uuid.UUID:
        if job_id in self._active:
            raise JobBusyError
        run_id = new_uuid()
        # No await between the check above and this line, so two requests cannot both pass.
        self._active[job_id] = run_id
        try:
            await asyncio.to_thread(self._create_run, run_id, job_id, trigger, scheduled_for)
        except BaseException:
            self._active.pop(job_id, None)
            raise
        self._spawn(run_id, job_id, partial(self._backup, run_id, job_id, trigger))
        return run_id

    async def submit_retry(self, parent_id: uuid.UUID) -> uuid.UUID:
        """Start a child run that re-uploads only the parent's failed destinations."""
        plan = await asyncio.to_thread(self._load_retry_plan, parent_id)
        if plan.lock_key in self._active:
            raise JobBusyError
        run_id = new_uuid()
        self._active[plan.lock_key] = run_id
        try:
            await asyncio.to_thread(self._create_retry_run, run_id, plan)
        except BaseException:
            self._active.pop(plan.lock_key, None)
            raise
        self._spawn(
            run_id,
            plan.lock_key,
            partial(self._retry, run_id, plan),
            after=partial(self._after_retry, run_id, plan),
        )
        return run_id

    async def submit_restore(self, plan: "RestorePlan") -> uuid.UUID:
        """Start a restore run. A target source is never restored while it is being backed up
        or restored, and is not backed up while it is being restored."""
        from app.services import restore_run

        jobs = await asyncio.to_thread(self._job_ids_of_source, plan.target_source_id)
        if plan.target_source_id in self._active or any(j in self._active for j in jobs):
            raise JobBusyError
        run_id = new_uuid()
        # No await between the check above and this line, so two requests cannot both pass.
        self._active[plan.target_source_id] = run_id
        try:
            await asyncio.to_thread(restore_run.create_restore_run, self, run_id, plan)
        except BaseException:
            self._active.pop(plan.target_source_id, None)
            raise
        self._spawn(
            run_id,
            plan.target_source_id,
            partial(restore_run.restore_body, self, run_id, plan),
            after=partial(asyncio.to_thread, restore_run.audit_restore_finished, plan, run_id),
            notify=False,
        )
        return run_id

    @staticmethod
    def _job_ids_of_source(source_id: uuid.UUID) -> list[uuid.UUID]:
        with Session(get_engine()) as db:
            return list(db.exec(select(Job.id).where(col(Job.source_id) == source_id)).all())

    # Used by restore runs, which share the run machinery.
    @property
    def settings(self) -> Settings:
        return self._settings

    def mark_running(self, run_id: uuid.UUID) -> datetime:
        return self._mark_running(run_id)

    def step(
        self, run_id: uuid.UUID, state: RunState, log: RunLogger, name: str
    ) -> AbstractAsyncContextManager[dict[str, Any]]:
        return self._step(run_id, state, log, name)

    async def cleanup(
        self, run_id: uuid.UUID, work_dir: Path, state: RunState, log: RunLogger
    ) -> None:
        await self._cleanup(run_id, work_dir, state, log)

    def _spawn(
        self,
        run_id: uuid.UUID,
        lock_key: uuid.UUID,
        body: Callable[[RunState, RunLogger], Awaitable[str]],
        after: Callable[[], Awaitable[None]] | None = None,
        notify: bool = True,
    ) -> None:
        task = asyncio.create_task(self._execute(run_id, lock_key, body, after, notify))
        self._tasks[run_id] = task
        task.add_done_callback(lambda _t: self._tasks.pop(run_id, None))

    async def cancel(self, run_id: uuid.UUID) -> bool:
        """Cancel a queued or running run. False if it is not active in this process."""
        task = self._tasks.get(run_id)
        if task is None or task.done():
            return False
        task.cancel()
        await asyncio.wait({task}, timeout=CANCEL_WAIT_SECONDS)
        return True

    async def wait_idle(self) -> None:
        """Wait for all runs to finish (tests)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks.values()), return_exceptions=True)

    # ---------- database access (blocking; always called through to_thread) ----------

    def _create_run(
        self,
        run_id: uuid.UUID,
        job_id: uuid.UUID,
        trigger: str,
        scheduled_for: datetime | None = None,
    ) -> None:
        with Session(get_engine()) as db:
            job = db.get(Job, job_id)
            if job is None:
                raise JobNotFoundError
            if job.source_id in self._active:
                raise JobBusyError  # the source is being restored
            db.add(
                Run(
                    id=run_id,
                    job_id=job_id,
                    job_name=job.name,
                    trigger=trigger,
                    scheduled_for=scheduled_for,
                    status=RunStatus.QUEUED,
                    log_path=str(log_path_for(self._settings.logs_dir, run_id)),
                )
            )
            db.commit()

    def _create_retry_run(self, run_id: uuid.UUID, plan: RetryPlan) -> None:
        with Session(get_engine()) as db:
            db.add(
                Run(
                    id=run_id,
                    job_id=plan.job_id,
                    job_name=plan.job_name,
                    parent_run_id=plan.parent_id,
                    trigger=RunTrigger.RETRY,
                    status=RunStatus.QUEUED,
                    log_path=str(log_path_for(self._settings.logs_dir, run_id)),
                )
            )
            db.commit()

    def _load_retry_plan(self, parent_id: uuid.UUID) -> RetryPlan:
        with Session(get_engine()) as db:
            parent = db.get(Run, parent_id)
            if parent is None:
                raise JobNotFoundError
            if parent.status != RunStatus.PARTIAL:
                raise RetryNotAllowedError("Only runs with status 'partial' can be retried.")
            items = self._failed_items(db, parent)
            if not items:
                raise RetryNotAllowedError("This run has no failed destinations to retry.")
            if not any(i.sources and i.target for i in items):
                raise NoSourceArtifactError(
                    "No stored copy of this run's backups remains, so the failed destinations "
                    "cannot be retried. Run the whole job again instead."
                )
            job = db.get(Job, parent.job_id) if parent.job_id else None
            return RetryPlan(
                parent_id=parent.id,
                job_id=parent.job_id,
                job_name=parent.job_name,
                timeout_seconds=job.timeout_seconds if job else DEFAULT_TIMEOUT,
                items=items,
            )

    @staticmethod
    def _failed_items(db: Session, parent: Run) -> list[RetryItem]:
        stored = db.exec(
            select(Artifact, Destination)
            .join(Destination, col(Destination.id) == col(Artifact.destination_id))
            .where(col(Artifact.run_id) == parent.id, col(Artifact.deleted_at).is_(None))
        ).all()
        items: list[RetryItem] = []
        for ui, unit in enumerate(parent.step_results.get("units", [])):
            if unit.get("dump", {}).get("status") != "ok":
                continue
            for ei, entry in enumerate(unit.get("destinations", [])):
                key = entry.get("path")
                if entry.get("status") != "failed" or not key:
                    continue
                target_row = db.get(Destination, uuid.UUID(entry["destination_id"]))
                sources = [
                    RetrySource(
                        destination_info(d),
                        a.size_bytes,
                        a.sha256,
                        a.compressed,
                        a.encrypted,
                        tuple(a.recipients or ()),
                    )
                    for a, d in stored
                    if a.source_database == unit["database"] and a.path == key
                ]
                sources.sort(key=lambda s: s.destination.type != "local")  # local copies first
                items.append(
                    RetryItem(
                        unit_index=ui,
                        entry_index=ei,
                        database=unit["database"],
                        kind=unit.get("kind", "database"),
                        key=key,
                        target=destination_info(target_row) if target_row else None,
                        sources=sources,
                    )
                )
        return items

    def _load_plan(self, job_id: uuid.UUID) -> RunPlan:
        with Session(get_engine()) as db:
            job = db.get(Job, job_id)
            if job is None:
                raise RunFailedError("The job was deleted before the run started.")
            source = db.get(Source, job.source_id)
            if source is None:
                raise RunFailedError("The job's source no longer exists.")
            try:
                secrets = load_secrets(source.secrets_enc)
            except SecretsUnavailable:
                raise RunFailedError(UNDECRYPTABLE_MESSAGE) from None
            rows = db.exec(
                select(Destination)
                .join(JobDestination, col(JobDestination.destination_id) == col(Destination.id))
                .where(col(JobDestination.job_id) == job_id)
                .order_by(col(Destination.name))
            ).all()
            if not rows:
                raise RunFailedError("The job has no destinations.")
            recipients = [
                (r.fingerprint, r.public_key)
                for r in db.exec(
                    select(AgeRecipient)
                    .join(JobRecipient, col(JobRecipient.recipient_id) == col(AgeRecipient.id))
                    .where(col(JobRecipient.job_id) == job_id)
                    .order_by(col(AgeRecipient.name))
                ).all()
            ]
            if job.encrypt and not recipients:
                # Fail closed: never fall back to storing an unencrypted backup.
                raise RunFailedError(
                    "Encryption is turned on for this job but it has no encryption key. "
                    "Add a key to the job, or turn encryption off."
                )
            return RunPlan(
                job_name=job.name,
                compress=job.compress,
                timeout_seconds=job.timeout_seconds,
                source_type=source.type,
                source_config=dict(source.config),
                source_secrets=secrets,
                destinations=[destination_info(d) for d in rows],
                last_artifact_size=self._last_artifact_size(db, job_id),
                recipients=recipients,
                encrypt=job.encrypt,
            )

    @staticmethod
    def _last_artifact_size(db: Session, job_id: uuid.UUID) -> int | None:
        last = db.exec(
            select(Run.id)
            .where(col(Run.job_id) == job_id, col(Run.status) == RunStatus.SUCCESS)
            .order_by(col(Run.finished_at).desc())
            .limit(1)
        ).first()
        if last is None:
            return None
        return db.exec(
            select(func.max(Artifact.size_bytes)).where(col(Artifact.run_id) == last)
        ).first()

    def _mark_running(self, run_id: uuid.UUID) -> datetime:
        with Session(get_engine()) as db:
            run = db.get(Run, run_id)
            if run is None:
                raise JobNotFoundError
            run.status = RunStatus.RUNNING
            run.started_at = utcnow()
            db.add(run)
            db.commit()
            return run.started_at

    def _save_progress(self, run_id: uuid.UUID, results: dict[str, Any]) -> None:
        with Session(get_engine()) as db:
            run = db.get(Run, run_id)
            if run is not None:
                run.step_results = results
                db.add(run)
                db.commit()

    def _finalize(
        self, run_id: uuid.UUID, status: str, error: str | None, results: dict[str, Any]
    ) -> None:
        with Session(get_engine()) as db:
            run = db.get(Run, run_id)
            if run is None:
                return
            run.status = status
            run.finished_at = utcnow()
            run.error = error
            run.step_results = results
            db.add(run)
            db.commit()

    def _add_artifact(
        self,
        run_id: uuid.UUID,
        destination_id: uuid.UUID,
        art: DumpedArtifact,
        key: str,
        md5: str | None,
    ) -> None:
        with Session(get_engine()) as db:
            db.add(
                Artifact(
                    run_id=run_id,
                    destination_id=destination_id,
                    source_database=art.database,
                    path=key,
                    size_bytes=art.size,
                    sha256=art.sha256,
                    md5=md5,
                    compressed=art.compressed,
                    encrypted=art.encrypted,
                    recipients=list(art.recipients) if art.encrypted else None,
                )
            )
            db.commit()

    def _apply_retry(self, child_id: uuid.UUID, plan: RetryPlan) -> None:
        """Fold the retry's outcomes into the parent run and recompute its status."""
        with Session(get_engine()) as db:
            parent = db.get(Run, plan.parent_id)
            if parent is None:
                return
            results = copy.deepcopy(parent.step_results)
            for item in plan.items:
                if item.result is None:
                    continue
                entry = results["units"][item.unit_index]["destinations"][item.entry_index]
                entry["last_retry_run_id"] = str(child_id)
                if item.result.get("status") != "ok":
                    continue
                previous = entry.pop("error", None)
                entry.pop("error_class", None)
                entry.update(
                    {k: v for k, v in item.result.items() if k not in ("error", "error_class")}
                )
                entry["previous_error"] = previous
                entry["retried_by"] = str(child_id)
            parent.step_results = results
            if compute_final_status(database_outcomes(results)) == RunStatus.SUCCESS:
                parent.status = RunStatus.SUCCESS
                parent.error = None
            db.add(parent)
            db.commit()

    # ---------- execution ----------

    async def _save(self, run_id: uuid.UUID, state: RunState) -> None:
        await asyncio.to_thread(self._save_progress, run_id, state.as_json())

    async def _execute(
        self,
        run_id: uuid.UUID,
        lock_key: uuid.UUID,
        body: Callable[[RunState, RunLogger], Awaitable[str]],
        after: Callable[[], Awaitable[None]] | None = None,
        notify: bool = True,
    ) -> None:
        state = RunState()
        log = RunLogger(log_path_for(self._settings.logs_dir, run_id))
        try:
            status, error = await self._guarded(run_id, state, log, body)
            if status == RunStatus.FAILED and error and not state.errors:
                state.errors.append(error)
            with suppress(OSError):
                log.info(f"Run finished with status: {status}." + (f" {error}" if error else ""))
            await asyncio.to_thread(self._finalize, run_id, status, error, state.as_json())
            if after is not None:
                await after()
            if notify and self.hooks is not None:
                try:
                    self.hooks.on_finished(run_id)
                except Exception as exc:
                    logger.warning("Post-run hook failed for %s: %s", run_id, type(exc).__name__)
        finally:
            # Released only after the final status is stored, so a job never has two active runs.
            self._active.pop(lock_key, None)

    async def _guarded(
        self,
        run_id: uuid.UUID,
        state: RunState,
        log: RunLogger,
        body: Callable[[RunState, RunLogger], Awaitable[str]],
    ) -> tuple[str, str | None]:
        """Run the body and map every way it can end to (status, error)."""
        try:
            log.info("Run queued.")
            async with self._semaphore:
                status = await body(state, log)
            return status, state.summary_error()
        except asyncio.CancelledError:
            if self._stopping:
                return RunStatus.INTERRUPTED, "Interrupted: the service was stopping."
            return RunStatus.CANCELLED, "Cancelled by user."
        except RunFailedError as exc:
            return RunStatus.FAILED, _log_abort(log, str(exc))
        except Exception as exc:
            logger.error("Run %s crashed: %s", run_id, type(exc).__name__)
            return RunStatus.FAILED, _log_abort(
                log, f"Unexpected error: {_describe(exc, state.secrets)}"
            )

    def _started(self, run_id: uuid.UUID, job_id: uuid.UUID, trigger: str) -> None:
        if self.hooks is None:
            return
        try:
            self.hooks.on_started(run_id, job_id, trigger)
        except Exception as exc:
            logger.warning("Run start hook failed for %s: %s", run_id, type(exc).__name__)

    async def _backup(
        self,
        run_id: uuid.UUID,
        job_id: uuid.UUID,
        trigger: str,
        state: RunState,
        log: RunLogger,
    ) -> str:
        plan = await asyncio.to_thread(self._load_plan, job_id)
        started_at = await asyncio.to_thread(self._mark_running, run_id)
        self._started(run_id, job_id, trigger)
        log.info(f"Run started for job '{plan.job_name}' ({plan.source_type} source).")
        await self._pipeline(run_id, plan, started_at, state, log)
        return compute_final_status(state.outcomes())

    def _storage(
        self, dest: DestinationInfo, work_dir: Path, timeout: int, log: RunLogger
    ) -> BaseStorage:
        context = StorageContext(
            destination_id=dest.id,
            cache_dir=work_dir,
            timeout_seconds=timeout,
            log=lambda line: log.info(f"[{dest.name}] {line}"),
            token_store=DestinationTokenStore(dest.id) if dest.type == "gdrive" else None,
        )
        return get_storage(dest.type, dest.config, {}, context)

    @staticmethod
    def _check_destinations(destinations: Sequence[DestinationInfo], log: RunLogger) -> None:
        blocked = [d for d in destinations if d.blocked]
        for dest in blocked:
            log.error(f"Destination '{dest.name}' will be skipped: {dest.blocked}")
        if blocked and len(blocked) == len(destinations):
            reasons = "; ".join(f"{d.name}: {d.blocked}" for d in blocked)
            raise RunFailedError(f"No destination can be used. {reasons}")

    @asynccontextmanager
    async def _step(
        self, run_id: uuid.UUID, state: RunState, log: RunLogger, name: str
    ) -> AsyncIterator[dict[str, Any]]:
        entry: dict[str, Any] = {
            "name": name,
            "status": "running",
            "started_at": _iso(),
            "duration_ms": None,
            "detail": None,
        }
        state.steps.append(entry)
        log.info(f"== {name} ==")
        await self._save(run_id, state)
        began = time.monotonic()
        try:
            yield entry
            entry["status"] = "ok"
        except asyncio.CancelledError:
            entry["status"] = "cancelled"
            raise
        except BaseException as exc:
            entry["status"] = "failed"
            entry["detail"] = sanitize(str(exc), state.secrets)[:500]
            raise
        finally:
            entry["duration_ms"] = int((time.monotonic() - began) * 1000)

    async def _pipeline(
        self,
        run_id: uuid.UUID,
        plan: RunPlan,
        started_at: datetime,
        state: RunState,
        log: RunLogger,
    ) -> None:
        work_dir = self._settings.work_dir / str(run_id)
        try:
            await asyncio.to_thread(work_dir.mkdir, 0o700, True, True)
            driver = get_driver(plan.source_type, plan.source_config, plan.source_secrets)
            log.add_secrets(driver.sensitive_values())
            state.secrets = driver.sensitive_values()
            ctx = DumpContext(
                work_dir,
                plan.timeout_seconds,
                plan.compress,
                log,
                job_name=plan.job_name,
                run_id=str(run_id),
            )

            async with self._step(run_id, state, log, "preflight"):
                self._check_destinations(plan.destinations, log)
                databases, tools = await self._preflight(driver, plan, ctx, log)

            storages = [
                (dest, self._storage(dest, work_dir, plan.timeout_seconds, log))
                for dest in plan.destinations
            ]
            async with self._step(run_id, state, log, "backup") as step:
                used: set[str] = set()
                for database in databases:
                    await self._unit(
                        run_id, plan, driver, storages, database, "database", ctx,
                        started_at, tools, state, log, used,
                    )  # fmt: skip
                try:
                    extra = await driver.dump_globals(ctx)
                except DumpError as exc:
                    extra = None
                    state.warnings.append(f"Globals dump failed: {exc}")
                    log.warning(f"Globals dump failed (not a run failure): {exc}")
                if extra is not None:
                    await self._unit(
                        run_id, plan, driver, storages, extra.database, "globals", ctx,
                        started_at, tools, state, log, used, dumped=extra,
                    )  # fmt: skip
                failed = [o for o in state.outcomes() if not o]
                step["detail"] = f"{len(databases)} database(s), {len(failed)} failed."
        finally:
            await self._cleanup(run_id, work_dir, state, log)

    async def _preflight(
        self, driver: BaseDriver, plan: RunPlan, ctx: DumpContext, log: RunLogger
    ) -> tuple[list[str], dict[str, str]]:
        work_dir = ctx.work_dir
        result = await asyncio.to_thread(driver.test_connection)
        if not result.ok:
            raise RunFailedError(f"Cannot connect to the source: {result.message}")
        log.info(result.message)
        try:
            tools = await driver.tool_versions()
            for name, version in tools.items():
                log.info(f"{name}: {version}")
            driver.check_compatibility(result.server_version, tools)
            await driver.preflight(ctx)
        except DumpError as exc:
            raise RunFailedError(str(exc)) from None

        required = (
            int(plan.last_artifact_size * FREE_SPACE_FACTOR)
            if plan.last_artifact_size
            else MIN_FREE_BYTES
        )
        free = (await asyncio.to_thread(shutil.disk_usage, work_dir)).free
        log.info(f"Work directory: {format_bytes(free)} free, {format_bytes(required)} required.")
        if free < required:
            raise RunFailedError(
                f"Not enough free disk space in the work directory: "
                f"{format_bytes(free)} free, {format_bytes(required)} needed."
            )

        if plan.encrypt:
            try:
                log.info(f"Encryption: {await age.version()}, {len(plan.recipients)} key(s).")
            except age.AgeError as exc:
                raise RunFailedError(str(exc)) from None

        listed = result.databases or []
        databases = driver.select_databases(listed)
        if not databases:
            raise RunFailedError(no_databases_message(listed))
        log.info(f"Databases to back up: {', '.join(databases)}")
        return databases, tools

    async def _unit(
        self,
        run_id: uuid.UUID,
        plan: RunPlan,
        driver: BaseDriver,
        storages: list[tuple[DestinationInfo, BaseStorage]],
        database: str,
        kind: str,
        ctx: DumpContext,
        started_at: datetime,
        tools: dict[str, str],
        state: RunState,
        log: RunLogger,
        used: set[str],
        dumped: DumpedArtifact | None = None,
    ) -> None:
        """Dump one database, then store and verify it at every destination."""
        secrets = driver.sensitive_values()
        steps: list[dict[str, Any]] = []
        unit: dict[str, Any] = {
            "database": database,
            "kind": kind,
            "dump": {"steps": steps},
            "destinations": [],
        }
        state.units.append(unit)
        log.info(f"-- {database} --")
        began = time.monotonic()
        art = dumped
        if art is None or plan.encrypt:
            reporter = _UnitReporter(partial(self._save, run_id, state), steps, state, log, secrets)
            unit_ctx = replace(ctx, report=reporter)
            try:
                if art is None:
                    if driver.reports_substeps:
                        art = await driver.dump_database(database, unit_ctx)
                    else:
                        async with reporter.step("dump"):
                            art = await driver.dump_database(database, unit_ctx)
                if plan.encrypt:
                    art = await self._encrypt(art, plan, reporter, log)
            except DumpError as exc:
                what = "Encryption" if isinstance(exc, age.AgeError) else "Dump"
                if art is not None:
                    art.path.unlink(missing_ok=True)  # never leave plaintext behind
                unit["dump"] = {"status": "failed", "error": str(exc), "steps": steps}
                unit["destinations"] = [
                    {"destination_id": str(d.id), "destination_name": d.name, "status": "skipped"}
                    for d, _s in storages
                ]
                state.errors.append(f"{database}: {exc}")
                log.error(f"{what} of {database} failed: {exc}")
                await self._save(run_id, state)
                return
        unit["dump"] = {
            "status": "ok",
            "duration_ms": int((time.monotonic() - began) * 1000),
            "size_bytes": art.size,
            "sha256": art.sha256,
            "encrypted": art.encrypted,
            "steps": steps,
        }
        log.info(f"Dump of {database} finished: {format_bytes(art.size)}, sha256 {art.sha256}")
        await self._save(run_id, state)

        key = self._unique_key(plan.job_name, art, started_at, used)
        manifest = art.path.with_name(art.path.name + ".manifest.json")
        try:
            await asyncio.to_thread(
                self._write_manifest,
                manifest,
                plan,
                art,
                kind,
                tools,
                started_at,
                driver.manifest_extra(),
            )
            for dest, storage in storages:
                entry = await self._store(
                    run_id, dest, storage, art, manifest, key, state, log, secrets, kind
                )
                unit["destinations"].append(entry)
                await self._save(run_id, state)
        finally:
            art.path.unlink(missing_ok=True)
            manifest.unlink(missing_ok=True)

    @staticmethod
    async def _encrypt(
        art: DumpedArtifact, plan: RunPlan, reporter: _UnitReporter, log: RunLogger
    ) -> DumpedArtifact:
        """Encrypt the dump to the job's keys. The plaintext file is deleted as soon as the
        ciphertext exists, and size and checksum from here on describe the ciphertext."""
        target = art.path.with_name(art.path.name + ".age")
        async with reporter.step("encrypt"):
            size, digest = await age.encrypt_file(
                art.path, target, [key for _fp, key in plan.recipients], plan.timeout_seconds
            )
        art.path.unlink(missing_ok=True)
        log.info(f"Encrypted to {len(plan.recipients)} key(s): {format_bytes(size)}.")
        return replace(
            art,
            path=target,
            extension=art.extension + ".age",
            size=size,
            sha256=digest,
            encrypted=True,
            recipients=tuple(fp for fp, _key in plan.recipients),
        )

    @staticmethod
    def _unique_key(job: str, art: DumpedArtifact, moment: datetime, used: set[str]) -> str:
        suffix = 1
        while True:
            key = artifact_key(job, art.database, moment, art.extension, suffix=suffix)
            if key not in used:
                break
            suffix += 1
        used.add(key)
        return key

    @staticmethod
    def _write_manifest(
        path: Path,
        plan: RunPlan,
        art: DumpedArtifact,
        kind: str,
        tools: dict[str, str],
        moment: datetime,
        extra: dict[str, Any],
    ) -> None:
        body: dict[str, Any] = {
            "format_version": 1,
            "job": plan.job_name,
            "source_type": plan.source_type,
            "database": art.database,
            "kind": kind,
            "tool_versions": tools,
            "created_at": _iso(moment),
            "size_bytes": art.size,
            "sha256": art.sha256,
            "compression": art.compression,
            "encrypted": art.encrypted,
        }
        if art.encrypted:
            # Public information only: which keys can decrypt this file.
            body["recipients"] = list(art.recipients)
        if extra:
            body["source"] = extra
        path.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")

    async def _store(
        self,
        run_id: uuid.UUID,
        dest: DestinationInfo,
        storage: BaseStorage,
        art: DumpedArtifact,
        manifest: Path,
        key: str,
        state: RunState,
        log: RunLogger,
        secrets: Sequence[str],
        kind: str,
        artifact_run_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        """Upload, then verify, at one destination. Never raises except on cancel."""
        steps: list[dict[str, Any]] = []
        entry: dict[str, Any] = {
            "destination_id": str(dest.id),
            "destination_name": dest.name,
            "type": dest.type,
            "status": "failed",
            "path": key,
            "bytes": art.size,
            "steps": steps,
        }
        written: list[str] = []
        try:
            if dest.blocked:
                raise RcloneError(ErrorClass.AUTH_EXPIRED, dest.blocked)
            log.info(f"Storing {key} at '{dest.name}'")
            async with _timed(steps, "put") as put:
                await storage.put(art.path, key)
                written.append(key)
                await storage.put(manifest, manifest_key(key))
                written.append(manifest_key(key))
            entry["store_ms"] = put["duration_ms"]
            log.info(f"Verifying {key} at '{dest.name}'")
            async with _timed(steps, "verify") as check:
                result = await storage.verify(art.path, key)
                if not result.ok:
                    raise VerificationError(f"Verification failed: {result.reason}")
            entry["verify_ms"] = check["duration_ms"]
            if result.md5:
                entry["md5"] = result.md5
            await asyncio.to_thread(
                self._add_artifact, artifact_run_id or run_id, dest.id, art, key, result.md5
            )
            entry["status"] = "ok"
            log.info(f"Stored and verified at '{dest.name}' ({format_bytes(art.size)}).")
        except (asyncio.CancelledError, OperationCancelledError):
            await self._discard(storage, written)
            raise
        except Exception as exc:
            await self._discard(storage, written)
            message = _describe(exc, secrets)
            entry["error"] = message
            entry["error_class"] = _error_class(exc)
            log.error(f"Storing at '{dest.name}' failed: {message}")
            if kind == "database":
                state.errors.append(f"{art.database} → {dest.name}: {message}")
            else:
                state.warnings.append(f"Globals could not be stored at '{dest.name}': {message}")
        return entry

    @staticmethod
    async def _discard(storage: BaseStorage, keys: Sequence[str]) -> None:
        """Best effort: never leave an unverified or unrecorded artifact behind."""
        for key in keys:
            with suppress(Exception):
                async with asyncio.timeout(DISCARD_TIMEOUT):
                    await storage.delete(key)

    # ---------- retry ----------

    async def _retry(
        self, run_id: uuid.UUID, plan: RetryPlan, state: RunState, log: RunLogger
    ) -> str:
        await asyncio.to_thread(self._mark_running, run_id)
        log.info(f"Retrying the failed destinations of run {plan.parent_id} ({plan.job_name}).")
        work_dir = self._settings.work_dir / str(run_id)
        try:
            await asyncio.to_thread(work_dir.mkdir, 0o700, True, True)
            async with self._step(run_id, state, log, "retry") as step:
                for index, item in enumerate(plan.items):
                    await self._retry_item(run_id, plan, item, work_dir / str(index), state, log)
                    await self._save(run_id, state)
                done = sum(1 for i in plan.items if i.result and i.result["status"] == "ok")
                step["detail"] = f"{done} of {len(plan.items)} destination(s) stored."
        finally:
            await self._cleanup(run_id, work_dir, state, log)
        outcomes = [d["status"] == "ok" for u in state.units for d in u["destinations"]]
        return compute_final_status(outcomes)

    async def _after_retry(self, run_id: uuid.UUID, plan: RetryPlan) -> None:
        await asyncio.to_thread(self._apply_retry, run_id, plan)

    async def _retry_item(
        self,
        run_id: uuid.UUID,
        plan: RetryPlan,
        item: RetryItem,
        item_dir: Path,
        state: RunState,
        log: RunLogger,
    ) -> None:
        unit: dict[str, Any] = {
            "database": item.database,
            "kind": item.kind,
            "dump": {"status": "skipped"},
            "destinations": [],
        }
        state.units.append(unit)
        target = item.target
        if target is None or not item.sources:
            reason = (
                "The destination no longer exists."
                if target is None
                else "No stored copy remains to retry from."
            )
            unit["destinations"].append({"status": "skipped", "path": item.key, "error": reason})
            log.warning(f"{item.database}: skipped. {reason}")
            return
        log.info(f"-- {item.database} → {target.name} --")
        await asyncio.to_thread(item_dir.mkdir, 0o700, True, True)
        fetched = await self._fetch_source(item, item_dir, plan.timeout_seconds, state, log)
        if fetched is None:
            message = "No stored copy could be read and verified."
            unit["destinations"].append(
                {
                    "destination_id": str(target.id),
                    "destination_name": target.name,
                    "type": target.type,
                    "status": "failed",
                    "path": item.key,
                    "error": message,
                    "error_class": ErrorClass.OTHER.value,
                }
            )
            state.errors.append(f"{item.database} → {target.name}: {message}")
            item.result = unit["destinations"][0]
            return
        art, manifest, source_name = fetched
        unit["dump"] = {
            "status": "ok",
            "source": source_name,
            "size_bytes": art.size,
            "sha256": art.sha256,
        }
        storage = self._storage(target, item_dir, plan.timeout_seconds, log)
        item.result = await self._store(
            run_id, target, storage, art, manifest, item.key, state, log, [], item.kind,
            artifact_run_id=plan.parent_id,
        )  # fmt: skip
        unit["destinations"].append(item.result)

    async def _fetch_source(
        self, item: RetryItem, item_dir: Path, timeout: int, state: RunState, log: RunLogger
    ) -> tuple[DumpedArtifact, Path, str] | None:
        """Copy the artifact and its manifest from a stored copy, checking the SHA-256."""
        local = item_dir / PurePosixPath(item.key).name
        manifest = local.with_name(local.name + ".manifest.json")
        for source in item.sources:
            dest = source.destination
            if dest.blocked:
                log.warning(f"Cannot read the copy at '{dest.name}': {dest.blocked}")
                continue
            storage = self._storage(dest, item_dir, timeout, log)
            try:
                log.info(f"Reading {item.key} from '{dest.name}'")
                await storage.get(item.key, local)
                sha, size = await run_blocking(sha256_file, local)
                if size != source.size or not hmac.compare_digest(sha, source.sha256):
                    raise VerificationError(
                        "the stored copy does not match the recorded SHA-256 checksum"
                    )
                await storage.get(manifest_key(item.key), manifest)
            except (asyncio.CancelledError, OperationCancelledError):
                raise
            except Exception as exc:
                local.unlink(missing_ok=True)
                manifest.unlink(missing_ok=True)
                log.warning(f"Cannot use the copy at '{dest.name}': {_describe(exc, [])}")
                continue
            log.info(f"Copy at '{dest.name}' verified (sha256 {sha}).")
            art = DumpedArtifact(
                database=item.database,
                path=local,
                extension="",
                size=size,
                sha256=sha,
                compressed=source.compressed,
                compression="",
                encrypted=source.encrypted,
                recipients=source.recipients,
            )
            return art, manifest, dest.name
        return None

    async def _cleanup(
        self, run_id: uuid.UUID, work_dir: Path, state: RunState, log: RunLogger
    ) -> None:
        began = time.monotonic()
        await asyncio.to_thread(shutil.rmtree, work_dir, True)
        log.info("Work directory removed.")
        state.steps.append(
            {
                "name": "cleanup",
                "status": "ok",
                "started_at": _iso(),
                "duration_ms": int((time.monotonic() - began) * 1000),
                "detail": None,
            }
        )

"""Restore runs: take a stored backup, check it, and load it into a registered source.

A restore is a tracked run of kind "restore" (live log, cancel, work directory cleanup) that
uses the runner's machinery. Nothing is written to the target until the stored file's SHA-256
has been verified against the one recorded at backup time.
"""

import asyncio
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from sqlmodel import Session

from app.core.db import get_engine
from app.drivers import get_driver
from app.drivers.base import DumpError, RestoreContext, RestoreError, RestoreOptions
from app.models import Artifact, Destination, Run, Source
from app.models.run import RunStatus
from app.services import age
from app.services.audit import write_audit
from app.services.destination_tokens import DestinationTokenStore
from app.services.hashing import sha256_file
from app.services.logstream import RunLogger
from app.services.paths import PathNotAllowed, check_lexical
from app.services.redact import describe_error
from app.services.runner import (
    FREE_SPACE_FACTOR,
    DestinationInfo,
    RunFailedError,
    RunState,
    destination_info,
)
from app.services.secrets import UNDECRYPTABLE_MESSAGE, SecretsUnavailable, load_secrets
from app.storage import StorageContext, get_storage
from app.storage.local import LocalStorage, format_bytes

if TYPE_CHECKING:
    from app.services.runner import Runner

# Artifact file extension -> the source type that can restore it.
EXTENSION_TYPES = {
    ".archive.gz": "mongo",
    ".sql.gz": "mysql",
    ".sql": "mysql",
    ".dump": "postgres",
    ".tar.gz": "firestore",
}
DEFAULT_TIMEOUT = 3600


def source_type_of(path: str, source_type: str | None = None) -> str | None:
    """The engine of a backup: its original source's type when known, else a guess from
    the file extension (which is ambiguous: PostgreSQL role files are .sql.gz too)."""
    if source_type:
        return source_type
    name = PurePosixPath(path).name.removesuffix(".age")
    return next((t for ext, t in EXTENSION_TYPES.items() if name.endswith(ext)), None)


@dataclass
class RestorePlan:
    artifact_id: uuid.UUID
    database: str
    key: str
    size: int
    sha256: str
    copy: DestinationInfo
    target_source_id: uuid.UUID
    target_source_name: str
    target_type: str
    target_config: dict[str, Any]
    target_secrets: dict[str, str]
    options: RestoreOptions
    timeout_seconds: int
    username: str | None
    encrypted: bool = False
    # The age private key for an encrypted backup. Memory only: never part of summary(), the
    # database, the log or an audit entry, and hidden from repr() so it cannot leak in a traceback.
    private_key: str | None = field(default=None, repr=False)

    def summary(self) -> dict[str, Any]:
        """Non-secret facts shown with the run."""
        return {
            "artifact_id": str(self.artifact_id),
            "database": self.database,
            "copy": self.copy.name,
            "path": self.key,
            "target_source": self.target_source_name,
            "target_database": self.options.target_database,
            "drop": self.options.drop,
            "full_instance": self.options.full_instance,
            "only": list(self.options.only),
            "encrypted": self.encrypted,
        }


def load_restore_plan(
    artifact: Artifact,
    copy: Destination,
    target: Source,
    options: RestoreOptions,
    timeout_seconds: int,
    username: str | None,
    private_key: str | None = None,
) -> RestorePlan:
    try:
        secrets = load_secrets(target.secrets_enc)
    except SecretsUnavailable:
        raise RunFailedError(UNDECRYPTABLE_MESSAGE) from None
    return RestorePlan(
        artifact_id=artifact.id,
        database=artifact.source_database,
        key=artifact.path,
        size=artifact.size_bytes,
        sha256=artifact.sha256,
        copy=destination_info(copy),
        target_source_id=target.id,
        target_source_name=target.name,
        target_type=target.type,
        target_config=dict(target.config),
        target_secrets=secrets,
        options=options,
        timeout_seconds=timeout_seconds,
        username=username,
        encrypted=artifact.encrypted,
        private_key=private_key,
    )


def create_restore_run(runner: "Runner", run_id: uuid.UUID, plan: RestorePlan) -> None:
    from app.services.logstream import log_path_for

    with Session(get_engine()) as db:
        db.add(
            Run(
                id=run_id,
                job_id=None,
                job_name=f"Restore {plan.database}",
                kind="restore",
                trigger="manual",
                status=RunStatus.QUEUED,
                log_path=str(log_path_for(runner.settings.logs_dir, run_id)),
                step_results={"steps": [], "units": [], "warnings": [], "restore": plan.summary()},
            )
        )
        db.commit()


async def restore_body(
    runner: "Runner", run_id: uuid.UUID, plan: RestorePlan, state: RunState, log: RunLogger
) -> str:
    await asyncio.to_thread(runner.mark_running, run_id)
    state.restore = plan.summary()
    options = plan.options
    log.info(
        f"Restore of '{plan.database}' (stored at '{plan.copy.name}') into "
        f"'{options.target_database}' on source '{plan.target_source_name}' started."
    )
    work_dir = runner.settings.work_dir / str(run_id)
    try:
        await asyncio.to_thread(work_dir.mkdir, 0o700, True, True)
        driver = get_driver(plan.target_type, plan.target_config, plan.target_secrets)
        secrets = driver.sensitive_values()
        if plan.private_key:
            secrets.append(plan.private_key)
        log.add_secrets(secrets)
        state.secrets = secrets
        async with runner.step(run_id, state, log, "preflight"):
            await _preflight(driver, log)
        async with runner.step(run_id, state, log, "fetch backup") as step:
            path = await _fetch(runner, plan, work_dir, log)
            step["detail"] = f"{format_bytes(plan.size)}, SHA-256 verified."
        if plan.encrypted:
            async with runner.step(run_id, state, log, "decrypt"):
                path = await _decrypt(plan, path, work_dir, log)
        async with runner.step(run_id, state, log, "restore"):
            ctx = RestoreContext(work_dir, plan.timeout_seconds, log)
            try:
                await driver.restore_database(path, options, ctx)
            except DumpError as exc:
                raise RunFailedError(str(exc)) from None
        log.info("Restore finished.")
        return RunStatus.SUCCESS
    finally:
        await runner.cleanup(run_id, work_dir, state, log)


async def _decrypt(plan: RestorePlan, stored: Path, work_dir: Path, log: RunLogger) -> Path:
    """Decrypt the (checksum-verified) ciphertext into the work directory. The private key goes
    to `age` on standard input; a wrong key or damaged file stops the run before the target is
    touched."""
    if not plan.private_key:
        raise RunFailedError("This backup is encrypted and no private key was provided.")
    name = stored.name.removesuffix(".age")
    target = work_dir / ("restore-" + name)
    free = (await asyncio.to_thread(shutil.disk_usage, work_dir)).free
    needed = int(plan.size * FREE_SPACE_FACTOR)
    if free < needed:
        raise RunFailedError(
            f"Not enough free disk space to decrypt the backup: "
            f"{format_bytes(free)} free, {format_bytes(needed)} needed."
        )
    try:
        await age.decrypt_file(stored, target, plan.private_key, plan.timeout_seconds)
    except age.AgeError as exc:
        raise RunFailedError(f"{exc} Nothing was changed on the target.") from None
    log.info("Decrypted the backup.")
    return target


async def _preflight(driver: Any, log: RunLogger) -> None:
    result = await asyncio.to_thread(driver.test_connection)
    if not result.ok:
        raise RunFailedError(f"Cannot connect to the target: {result.message}")
    log.info(result.message)
    try:
        tools = await driver.restore_tool_versions()
        for name, version in tools.items():
            log.info(f"{name}: {version}")
        for warning in driver.check_restore_compatibility(tools):
            log.warning(warning)
    except RestoreError as exc:
        raise RunFailedError(str(exc)) from None


async def _fetch(runner: "Runner", plan: RestorePlan, work_dir: Path, log: RunLogger) -> Path:
    """The backup file on local disk, with its checksum verified. Remote copies are downloaded."""
    if plan.copy.blocked:
        raise RunFailedError(f"Cannot read '{plan.copy.name}': {plan.copy.blocked}")
    try:
        check_lexical(plan.key)
    except PathNotAllowed as exc:
        raise RunFailedError(str(exc)) from None
    context = StorageContext(
        destination_id=plan.copy.id,
        cache_dir=work_dir,
        timeout_seconds=plan.timeout_seconds,
        log=lambda line: log.info(f"[{plan.copy.name}] {line}"),
        token_store=DestinationTokenStore(plan.copy.id) if plan.copy.type == "gdrive" else None,
    )
    storage = get_storage(plan.copy.type, plan.copy.config, {}, context)
    if isinstance(storage, LocalStorage):
        path = storage.local_path(plan.key)
        if not path.is_file():
            raise RunFailedError(f"The backup file is missing from '{plan.copy.name}'.")
        log.info(f"Using the local copy at '{plan.copy.name}'.")
    else:
        free = (await asyncio.to_thread(shutil.disk_usage, work_dir)).free
        needed = int(plan.size * FREE_SPACE_FACTOR)
        if free < needed:
            raise RunFailedError(
                f"Not enough free disk space to download the backup: "
                f"{format_bytes(free)} free, {format_bytes(needed)} needed."
            )
        path = work_dir / PurePosixPath(plan.key).name
        log.info(f"Downloading the backup from '{plan.copy.name}'.")
        try:
            await storage.get(plan.key, path)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            reason = exc.strerror if isinstance(exc, OSError) else describe_error(exc, [])
            raise RunFailedError(
                f"Cannot download the backup from '{plan.copy.name}': {reason} "
                "If another copy of this backup exists, restore from that one."
            ) from None
    digest, size = await asyncio.to_thread(sha256_file, path)
    if size != plan.size or digest != plan.sha256:
        raise RunFailedError(
            "The stored backup is corrupt: its checksum does not match the one recorded when it "
            "was backed up. Nothing was changed on the target."
        )
    log.info(f"Checksum verified (sha256 {digest}).")
    return path


def audit_restore_finished(plan: RestorePlan, run_id: uuid.UUID) -> None:
    with Session(get_engine()) as db:
        run = db.get(Run, run_id)
        write_audit(
            db,
            "backup.restore_finished",
            username=plan.username,
            object_type="artifact",
            object_id=str(plan.artifact_id),
            details={
                **plan.summary(),
                "run_id": str(run_id),
                "outcome": run.status if run else "unknown",
            },
        )
        db.commit()

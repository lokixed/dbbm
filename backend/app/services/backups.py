"""Backups browser: integrity checks, downloads, deletion and drift detection for artifacts."""

import asyncio
import hmac
import logging
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from sqlmodel import Session, col, select

from app.core.config import get_settings
from app.models import Artifact, Destination
from app.models.base import utcnow
from app.services.destination_tokens import DestinationTokenStore
from app.services.hashing import sha256_file
from app.services.naming import manifest_key
from app.services.paths import PathNotAllowed, check_lexical
from app.services.redact import describe_error, sanitize
from app.services.runner import destination_info
from app.storage import BaseStorage, StorageContext, get_storage
from app.storage.local import LocalStorage
from app.storage.rclone_utils import RcloneError

logger = logging.getLogger(__name__)

IO_TIMEOUT = 3600
OK = "ok"
MISSING = "missing"
CORRUPT = "corrupt"


class BackupError(Exception):
    """A backups-browser operation failed. The message is sanitized and safe to show."""

    def __init__(self, message: str, status_code: int = 409) -> None:
        super().__init__(message)
        self.status_code = status_code


class CorruptBackupError(BackupError):
    """A stored copy does not match its recorded checksum; it must not be served."""


@dataclass(frozen=True)
class Integrity:
    status: str  # ok | missing | corrupt
    detail: str


@dataclass
class Download:
    path: Path
    filename: str
    cleanup_dir: Path | None = None


def remove_dir(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def describe(exc: BaseException) -> str:
    if isinstance(exc, PathNotAllowed | RcloneError | FileExistsError | BackupError):
        return sanitize(str(exc))
    if isinstance(exc, OSError):
        return f"{type(exc).__name__}: {exc.strerror or 'I/O error'}"
    return describe_error(exc)


def build_storage(row: Destination, work_dir: Path | None = None) -> BaseStorage:
    """The storage adapter for a destination; raises BackupError when it cannot be used now."""
    info = destination_info(row)
    if info.blocked:
        raise BackupError(info.blocked)
    context = StorageContext(
        destination_id=row.id,
        cache_dir=work_dir,
        timeout_seconds=IO_TIMEOUT,
        token_store=DestinationTokenStore(row.id) if row.type == "gdrive" else None,
    )
    return get_storage(row.type, dict(row.config), {}, context)


def _key(artifact: Artifact) -> str:
    try:
        check_lexical(artifact.path)
    except PathNotAllowed as exc:
        raise BackupError("The stored path of this backup is not valid.", 422) from exc
    return artifact.path


def _directory(key: str) -> str:
    return str(PurePosixPath(key).parent)


def _same_file(stored_size: int, stored_sha: str, artifact: Artifact) -> Integrity:
    if stored_size != artifact.size_bytes or not hmac.compare_digest(stored_sha, artifact.sha256):
        return Integrity(CORRUPT, "The stored file does not match the recorded size and SHA-256.")
    return Integrity(OK, "Size and SHA-256 match.")


async def check_integrity(artifact: Artifact, row: Destination) -> Integrity:
    """Re-read the stored copy and compare it with what was recorded at backup time."""
    key = _key(artifact)
    storage = build_storage(row)
    if isinstance(storage, LocalStorage):
        path = storage.local_path(key)
        if not path.is_file():
            return Integrity(MISSING, "The file is not on the destination any more.")
        sha, size = await asyncio.to_thread(sha256_file, path)
        return _same_file(size, sha, artifact)
    found = {o.key: o for o in await storage.list(_directory(key))}.get(key)
    if found is None:
        return Integrity(MISSING, "The file is not on the destination any more.")
    if found.size != artifact.size_bytes:
        return Integrity(CORRUPT, "The stored size differs from the recorded size.")
    if artifact.md5 and found.md5:
        if hmac.compare_digest(artifact.md5, found.md5):
            return Integrity(OK, "Size and MD5 match what Google Drive reports.")
        return Integrity(CORRUPT, "The MD5 reported by Google Drive differs from the recorded one.")
    return await _download_and_hash(storage, key, artifact)


async def _download_and_hash(storage: BaseStorage, key: str, artifact: Artifact) -> Integrity:
    scratch = get_settings().work_dir / "verify" / uuid.uuid4().hex
    scratch.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        target = scratch / "artifact"
        await storage.get(key, target)
        sha, size = await asyncio.to_thread(sha256_file, target)
        return _same_file(size, sha, artifact)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


async def prepare_download(db: Session, artifact: Artifact, row: Destination) -> Download:
    """A local file ready to serve, with its SHA-256 checked first.

    Local copies are tried before remote ones. A copy that is missing or unreadable is skipped
    in favour of another copy of the same backup; a corrupt copy is never served and ends the
    attempt."""
    failures: list[str] = []
    for candidate, candidate_row in _copies(db, artifact, row):
        try:
            return await _prepare_copy(candidate, candidate_row, artifact)
        except CorruptBackupError:
            raise
        except BackupError as exc:
            failures.append(f"{candidate_row.name}: {exc}")
        except Exception as exc:
            failures.append(f"{candidate_row.name}: {describe(exc)}")
    raise BackupError("No copy of this backup could be read. " + " ".join(failures), 404)


async def _prepare_copy(copy: Artifact, row: Destination, expected: Artifact) -> Download:
    key = _key(copy)
    storage = build_storage(row)
    filename = PurePosixPath(key).name
    if isinstance(storage, LocalStorage):
        path = storage.local_path(key)
        if not path.is_file():
            raise BackupError("The file is missing from the destination.", 404)
        sha, size = await asyncio.to_thread(sha256_file, path)
        _require_match(size, sha, expected)
        return Download(path, filename)
    scratch = get_settings().work_dir / "downloads" / uuid.uuid4().hex
    scratch.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        target = scratch / filename
        await storage.get(key, target)
        sha, size = await asyncio.to_thread(sha256_file, target)
        _require_match(size, sha, expected)
    except BaseException:
        shutil.rmtree(scratch, ignore_errors=True)
        raise
    return Download(target, filename, cleanup_dir=scratch)


def _copies(
    db: Session, artifact: Artifact, row: Destination
) -> list[tuple[Artifact, Destination]]:
    """Every live copy of this backup (same run and database): healthy local copies first."""
    rows = db.exec(
        select(Artifact, Destination)
        .join(Destination, col(Destination.id) == col(Artifact.destination_id))
        .where(
            col(Artifact.run_id) == artifact.run_id,
            col(Artifact.source_database) == artifact.source_database,
            col(Artifact.deleted_at).is_(None),
            col(Artifact.id) != artifact.id,
        )
    ).all()
    copies = [(artifact, row), *rows]
    return sorted(copies, key=lambda c: (c[0].missing_at is not None, c[1].type != "local"))


def _require_match(size: int, sha: str, artifact: Artifact) -> None:
    if _same_file(size, sha, artifact).status != OK:
        raise CorruptBackupError(
            "The stored file is corrupt: its checksum does not match the one recorded when it "
            "was backed up. It was not served."
        )


async def delete_stored(artifact: Artifact, row: Destination) -> None:
    key = _key(artifact)
    storage = build_storage(row)
    await storage.delete(key)
    await storage.delete(manifest_key(key))


async def scan_destination(db: Session, row: Destination) -> tuple[int, int]:
    """Mark artifacts whose files are gone from the destination (and clear the mark when they
    are back). Returns (checked, missing)."""
    artifacts = list(
        db.exec(
            select(Artifact).where(
                col(Artifact.destination_id) == row.id, col(Artifact.deleted_at).is_(None)
            )
        ).all()
    )
    storage = build_storage(row)
    present: set[str] = set()
    for prefix in sorted({_first_part(_key(a)) for a in artifacts}):
        present.update(o.key for o in await storage.list(prefix))
    now = utcnow()
    missing = 0
    for artifact in artifacts:
        gone = artifact.path not in present
        missing += gone
        if gone and artifact.missing_at is None:
            artifact.missing_at = now
        elif not gone and artifact.missing_at is not None:
            artifact.missing_at = None
        db.add(artifact)
    return len(artifacts), missing


def _first_part(key: str) -> str:
    return PurePosixPath(key).parts[0]

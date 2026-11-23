import hashlib
import threading
from pathlib import Path

CHUNK = 1024 * 1024


class OperationCancelledError(Exception):
    """A blocking file operation stopped because the run was cancelled."""


def check_cancel(cancel: threading.Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise OperationCancelledError


def sha256_file(path: Path, cancel: threading.Event | None = None) -> tuple[str, int]:
    return _hash_file(path, hashlib.sha256(), cancel)


def md5_file(path: Path, cancel: threading.Event | None = None) -> tuple[str, int]:
    # Integrity check against the checksum Google Drive reports, not a security use.
    return _hash_file(path, hashlib.md5(usedforsecurity=False), cancel)


def _hash_file(
    path: Path, digest: "hashlib._Hash", cancel: threading.Event | None
) -> tuple[str, int]:
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK):
            check_cancel(cancel)
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size

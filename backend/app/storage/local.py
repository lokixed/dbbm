import asyncio
import builtins
import hmac
import os
import shutil
import tempfile
import threading
from pathlib import Path

from app.core.config import get_settings
from app.drivers.base import CheckResult
from app.services.hashing import CHUNK, check_cancel, sha256_file
from app.services.paths import PathNotAllowed, check_lexical, resolve_inside, to_relative
from app.storage.base import (
    BaseStorage,
    StorageTestResult,
    StoredObject,
    VerifyResult,
    run_blocking,
)

PARTIAL_SUFFIX = ".partial"


def format_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    raise AssertionError("unreachable")


class LocalStorage(BaseStorage):
    def _fail(self, check: str, message: str) -> StorageTestResult:
        return StorageTestResult(
            ok=False, message=message, checks=[CheckResult(check, False, message)]
        )

    def test_sync(self) -> StorageTestResult:
        base = get_settings().backup_dir
        raw = str(self._config.get("path", ""))
        try:
            relative = to_relative(raw, base)
            target = resolve_inside(base, relative)
        except PathNotAllowed as exc:
            return self._fail("path", str(exc))

        try:
            target.mkdir(parents=True, exist_ok=True)
            # Re-resolve after creating: a symlink may have appeared in the meantime.
            target = resolve_inside(base, relative)
            self._probe_write(target)
            usage = shutil.disk_usage(target)
        except PathNotAllowed as exc:
            return self._fail("path", str(exc))
        except OSError as exc:
            # strerror only: the full message embeds absolute filesystem paths.
            return self._fail("writable", f"Cannot write to the destination: {exc.strerror}.")

        message = f"Writable. {format_bytes(usage.free)} free of {format_bytes(usage.total)}."
        return StorageTestResult(
            ok=True,
            message=message,
            free_bytes=usage.free,
            total_bytes=usage.total,
            checks=[
                CheckResult("path", True, "Path is inside the backup directory."),
                CheckResult("writable", True, "Created, wrote and removed a test file."),
            ],
        )

    @staticmethod
    def _probe_write(directory: Path) -> None:
        with tempfile.NamedTemporaryFile(dir=directory, prefix=".dbbm-write-test-") as handle:
            handle.write(b"ok")
            handle.flush()

    def _root(self) -> Path:
        base = get_settings().backup_dir
        return resolve_inside(base, to_relative(str(self._config.get("path", "")), base))

    def _target(self, key: str) -> Path:
        if check_lexical(key).is_absolute():
            raise PathNotAllowed("Storage keys must be relative.")
        return resolve_inside(self._root(), key)

    def local_path(self, key: str) -> Path:
        """The file behind `key` on this machine, inside the destination root."""
        return self._target(key)

    async def test(self) -> StorageTestResult:
        return await asyncio.to_thread(self.test_sync)

    async def put(self, local: Path, key: str) -> StoredObject:
        return await run_blocking(self.put_sync, local, key)

    async def verify(self, local: Path, key: str) -> VerifyResult:
        ok = await run_blocking(self.verify_sync, local, key)
        return VerifyResult(ok, reason=None if ok else "The stored file differs from the dump.")

    async def list(self, prefix: str) -> builtins.list[StoredObject]:
        return await asyncio.to_thread(self.list_sync, prefix)

    async def get(self, key: str, dest: Path) -> None:
        await run_blocking(self.get_sync, key, dest)

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self.delete_sync, key)

    def put_sync(
        self, local: Path, key: str, *, cancel: threading.Event | None = None
    ) -> StoredObject:
        target = self._target(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        target = self._target(key)  # re-resolve: a symlink may have appeared meanwhile
        if os.path.lexists(target):
            raise FileExistsError(f"{key} already exists; existing backups are never overwritten.")
        partial = target.with_name(target.name + PARTIAL_SUFFIX)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        descriptor = os.open(partial, flags, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as out, local.open("rb") as source:
                while chunk := source.read(CHUNK):
                    check_cancel(cancel)
                    out.write(chunk)
                out.flush()
                os.fsync(out.fileno())
            partial.chmod(0o600)
            _publish(partial, target)
        except BaseException:
            partial.unlink(missing_ok=True)
            raise
        _fsync_dir(target.parent)
        return StoredObject(key=key, size=target.stat().st_size)

    def verify_sync(self, local: Path, key: str, *, cancel: threading.Event | None = None) -> bool:
        target = self._target(key)
        if not target.is_file():
            return False
        stored_sha, stored_size = sha256_file(target, cancel)
        local_sha, local_size = sha256_file(local, cancel)
        return stored_size == local_size and hmac.compare_digest(stored_sha, local_sha)

    def list_sync(self, prefix: str) -> builtins.list[StoredObject]:
        root = self._root()
        start = self._target(prefix) if prefix else root
        found: builtins.list[StoredObject] = []
        for directory, _dirs, files in os.walk(start):
            for name in files:
                if name.endswith(PARTIAL_SUFFIX):
                    continue
                path = Path(directory) / name
                found.append(
                    StoredObject(key=path.relative_to(root).as_posix(), size=path.stat().st_size)
                )
        return sorted(found, key=lambda o: o.key)

    def get_sync(self, key: str, dest: Path, *, cancel: threading.Event | None = None) -> None:
        """Copy to `dest` via a partial file; never replaces an existing file."""
        source = self._target(key)
        partial = dest.with_name(dest.name + PARTIAL_SUFFIX)
        descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as out, source.open("rb") as handle:
                while chunk := handle.read(CHUNK):
                    check_cancel(cancel)
                    out.write(chunk)
            _publish(partial, dest)
        except BaseException:
            partial.unlink(missing_ok=True)
            raise

    def delete_sync(self, key: str) -> None:
        self._target(key).unlink(missing_ok=True)


def _publish(partial: Path, target: Path) -> None:
    """Move `partial` to `target` without ever replacing an existing file."""
    try:
        os.link(partial, target)  # atomic, and fails if the target exists
    except FileExistsError:
        raise
    except OSError:
        # Some filesystems (network or Windows-backed mounts) lack hard links.
        if os.path.lexists(target):
            raise FileExistsError(f"{target.name} already exists") from None
        os.replace(partial, target)
        return
    partial.unlink()


def _fsync_dir(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    except OSError:
        pass  # not supported everywhere; the file itself is already synced
    finally:
        os.close(descriptor)

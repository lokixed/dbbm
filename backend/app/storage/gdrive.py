"""Google Drive destination, driven through rclone with a private config per invocation."""

import asyncio
import builtins
import hmac
import json
import os
import uuid
import weakref
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any

from app.drivers.base import CheckResult
from app.models.destination import AuthStatus
from app.schemas.destination import GdriveConfig
from app.services.hashing import md5_file
from app.services.paths import PathNotAllowed, check_lexical
from app.services.rclone_token import normalize_token, same_token, token_expiry, token_values
from app.services.secrets import UNDECRYPTABLE_MESSAGE, SecretsUnavailable
from app.storage.base import (
    BaseStorage,
    StorageTestResult,
    StoredObject,
    VerifyResult,
    run_blocking,
)
from app.storage.local import PARTIAL_SUFFIX, _publish, format_bytes
from app.storage.rclone_utils import (
    NEEDS_REAUTH_MESSAGE,
    REMOTE,
    ErrorClass,
    RcloneError,
    RcloneResult,
    RcloneSession,
    drive_sections,
    rclone_session,
)

TEST_TIMEOUT = 120
LIST_TIMEOUT = 300
PROBE_BYTES = 1024
PROBE_PREFIX = ".dbbm-probe-"
NOT_FOUND_CODES = (3, 4)

# One lock per destination (per event loop). Every rclone call for a destination holds it
# for its whole duration, because each call may refresh the OAuth token and write it back.
# Tradeoff: two runs sharing a Drive destination upload one after the other, never in parallel,
# so a large upload delays the other run's upload; in exchange no refreshed token is lost.
_LOCKS: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[object, asyncio.Lock]]" = (
    weakref.WeakKeyDictionary()
)


def destination_lock(key: object) -> asyncio.Lock:
    locks = _LOCKS.setdefault(asyncio.get_running_loop(), {})
    return locks.setdefault(key, asyncio.Lock())


def _check_key(key: str) -> str:
    path = check_lexical(key)
    if path.is_absolute() or any(not part for part in key.split("/")):
        raise PathNotAllowed("Storage keys must be relative paths without empty segments.")
    return key


def _md5_of(item: dict[str, Any]) -> str | None:
    hashes = {str(k).lower(): v for k, v in (item.get("Hashes") or {}).items()}
    value = hashes.get("md5")
    return str(value).lower() if value else None


class _Rclone:
    """An rclone session bound to one destination: errors update its auth status."""

    def __init__(self, storage: "GdriveStorage", session: RcloneSession) -> None:
        self._storage = storage
        self._session = session

    @property
    def scratch(self) -> Path:
        return self._session.config_path.parent

    async def run(self, args: Sequence[str], timeout: int = TEST_TIMEOUT) -> RcloneResult:
        try:
            return await self._session.run(args, timeout=timeout, on_line=self._storage._log)
        except RcloneError as exc:
            if exc.error_class is ErrorClass.AUTH_EXPIRED:
                await self._storage._set_auth(AuthStatus.NEEDS_REAUTH)
            raise


class GdriveStorage(BaseStorage):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._cfg = GdriveConfig.model_validate(self._config)
        # The token the stored secrets hold as far as this adapter knows (compare-and-set).
        self._token: str | None = self._secrets.get("token")

    # ---------- plumbing ----------

    def _sections(self, secrets: dict[str, str]) -> dict[str, str]:
        return drive_sections(
            scope=self._cfg.scope,
            token=secrets.get("token", ""),
            client_id=self._cfg.client_id,
            client_secret=secrets.get("client_secret"),
            shared_drive_id=self._cfg.shared_drive_id,
        )

    def _remote(self, key: str = "") -> str:
        folder = self._cfg.remote_folder
        return f"{REMOTE}:{folder}/{_check_key(key)}" if key else f"{REMOTE}:{folder}"

    def _log(self, line: str) -> None:
        if self._context.log is not None:
            self._context.log(line)

    def _cache_dir(self) -> Path | None:
        base = self._context.cache_dir
        return base / "rclone-cache" if base is not None else None

    def _lock_key(self) -> object:
        return self._context.destination_id or id(self)

    async def _load_secrets(self) -> dict[str, str]:
        store = self._context.token_store
        if store is None:
            return dict(self._secrets)
        try:
            return await asyncio.to_thread(store.load)
        except SecretsUnavailable:
            raise RcloneError(ErrorClass.OTHER, UNDECRYPTABLE_MESSAGE) from None

    async def _set_auth(self, status: AuthStatus) -> None:
        store = self._context.token_store
        if store is not None:
            await asyncio.to_thread(store.set_auth, status.value, self._token)

    async def _write_back(self, before: str | None, after: str | None) -> None:
        """Persist a token rclone refreshed during the call (it rewrites its config file)."""
        if not after or same_token(before, after):
            return
        try:
            token = normalize_token(after)
        except ValueError:
            return  # not a usable token; keep the stored one
        store = self._context.token_store
        saved = True
        if store is not None:
            saved = await asyncio.to_thread(store.save_token, before, token)
        if saved:
            self._secrets["token"] = token
            self._token = token
            self._log(f"Google Drive token refreshed (new expiry {token_expiry(token)}).")

    @asynccontextmanager
    async def _rclone(self) -> AsyncIterator[_Rclone]:
        async with destination_lock(self._lock_key()):
            secrets = await self._load_secrets()
            token = secrets.get("token", "")
            self._token = token or None
            if not token:
                raise RcloneError(ErrorClass.AUTH_EXPIRED, NEEDS_REAUTH_MESSAGE)
            sensitive = [*token_values(token), secrets.get("client_secret", "")]
            async with rclone_session(
                self._sections(secrets), cache_dir=self._cache_dir(), secrets=sensitive
            ) as session:
                try:
                    yield _Rclone(self, session)
                finally:
                    await self._write_back(token, session.read_token())

    async def _stat(self, rc: _Rclone, remote: str) -> StoredObject:
        result = await rc.run(
            ["lsjson", "--stat", "--hash", "--hash-type", "MD5", "--no-mimetype", remote]
        )
        try:
            item = json.loads(result.stdout)
            return StoredObject(
                key=str(item["Path"]),
                size=int(item["Size"]),
                md5=_md5_of(item),
                modtime=item.get("ModTime"),
            )
        except (ValueError, KeyError, TypeError):
            raise RcloneError(ErrorClass.OTHER, "Unexpected rclone lsjson output.") from None

    async def _names(self, rc: _Rclone, directory: str) -> set[str]:
        target = self._remote(directory) if directory else self._remote()
        args = ["lsjson", "--files-only", "--no-modtime", "--no-mimetype", target]
        try:
            result = await rc.run(args, timeout=LIST_TIMEOUT)
        except RcloneError as exc:
            if exc.error_class is ErrorClass.FOLDER_NOT_FOUND:
                return set()
            raise
        try:
            return {str(item["Name"]) for item in json.loads(result.stdout)}
        except (ValueError, KeyError, TypeError):
            raise RcloneError(ErrorClass.OTHER, "Unexpected rclone lsjson output.") from None

    # ---------- test ----------

    async def test(self) -> StorageTestResult:
        checks: list[CheckResult] = []
        free = total = None
        step = "folder"
        try:
            async with self._rclone() as rc:
                checks.append(await self._check_folder(rc))
                step = "space"
                space, free, total = await self._check_space(rc)
                checks.append(space)
                step = "write"
                checks.append(await self._probe(rc))
        except (RcloneError, PathNotAllowed) as exc:
            checks.append(CheckResult(step, False, str(exc)))
            return StorageTestResult(ok=False, message=str(exc), checks=checks)
        await self._set_auth(AuthStatus.OK)
        message = f"Google Drive folder '{self._cfg.remote_folder}' is writable."
        if free is not None:
            message += f" {format_bytes(free)} free"
            message += f" of {format_bytes(total)}." if total is not None else "."
        return StorageTestResult(
            ok=True, message=message, free_bytes=free, total_bytes=total, checks=checks
        )

    async def _check_folder(self, rc: _Rclone) -> CheckResult:
        folder = self._cfg.remote_folder
        try:
            await rc.run(["lsd", self._remote()])
            return CheckResult("folder", True, f"Folder '{folder}' is reachable.")
        except RcloneError as exc:
            if exc.error_class is not ErrorClass.FOLDER_NOT_FOUND:
                raise
        await rc.run(["mkdir", self._remote()])
        return CheckResult("folder", True, f"Created folder '{folder}'.")

    async def _check_space(self, rc: _Rclone) -> tuple[CheckResult, int | None, int | None]:
        unavailable = CheckResult("space", True, "Free space is not reported for this drive.")
        try:
            result = await rc.run(["about", f"{REMOTE}:", "--json"])
            data = json.loads(result.stdout)
        except RcloneError as exc:
            if exc.error_class in (ErrorClass.AUTH_EXPIRED, ErrorClass.NETWORK):
                raise
            return unavailable, None, None
        except ValueError:
            return unavailable, None, None
        free, total = data.get("free"), data.get("total")
        if not isinstance(free, int):
            return unavailable, None, None
        total = total if isinstance(total, int) else None
        text = f"{format_bytes(free)} free" + (f" of {format_bytes(total)}." if total else ".")
        return CheckResult("space", True, text), free, total

    async def _probe(self, rc: _Rclone) -> CheckResult:
        """Upload a tiny file, read its MD5 back, then delete it permanently."""
        name = f"{PROBE_PREFIX}{uuid.uuid4().hex}"
        local = rc.scratch / name
        local.write_bytes(os.urandom(PROBE_BYTES))
        expected, _size = md5_file(local)
        remote = self._remote(name)
        delete = ["deletefile", remote, "--drive-use-trash=false"]
        await rc.run(["copyto", str(local), remote])
        try:
            stored = await self._stat(rc, remote)
            if stored.size != PROBE_BYTES or stored.md5 != expected:
                raise RcloneError(
                    ErrorClass.OTHER, "The test file read back with a different size or MD5."
                )
        except BaseException:
            with suppress(RcloneError):
                await rc.run(delete)
            raise
        await rc.run(delete)
        return CheckResult("write", True, "Uploaded a test file, verified its MD5, deleted it.")

    # ---------- objects ----------

    async def put(self, local: Path, key: str) -> StoredObject:
        remote = self._remote(key)
        directory, _, name = key.rpartition("/")
        size = (await asyncio.to_thread(local.stat)).st_size
        async with self._rclone() as rc:
            # Drive allows several files with one name, so refuse instead of adding a twin.
            if name in await self._names(rc, directory):
                raise FileExistsError(
                    f"{key} already exists in Google Drive; existing backups are never overwritten."
                )
            await rc.run(
                [
                    "copyto", str(local), remote,
                    "--ignore-existing",
                    "--drive-chunk-size", f"{self._cfg.chunk_size_mb}M",
                ],
                timeout=self._context.timeout_seconds,
            )  # fmt: skip
        await self._set_auth(AuthStatus.OK)
        return StoredObject(key=key, size=size)

    async def verify(self, local: Path, key: str) -> VerifyResult:
        async with self._rclone() as rc:
            stored = await self._stat(rc, self._remote(key))
        local_md5, local_size = await run_blocking(md5_file, local)
        if stored.size != local_size:
            return VerifyResult(
                False,
                reason=f"Size differs: Google Drive has {stored.size} bytes, "
                f"the local file has {local_size}.",
            )
        if not stored.md5:
            return VerifyResult(False, reason="Google Drive did not report an MD5 checksum.")
        if not hmac.compare_digest(stored.md5, local_md5):
            return VerifyResult(False, reason="MD5 differs between Google Drive and the dump.")
        return VerifyResult(True, md5=local_md5)

    async def list(self, prefix: str) -> builtins.list[StoredObject]:
        base = prefix.strip("/")
        target = self._remote(base) if base else self._remote()
        args = [
            "lsjson", "-R", "--files-only", "--hash", "--hash-type", "MD5", "--no-mimetype",
            target,
        ]  # fmt: skip
        async with self._rclone() as rc:
            try:
                result = await rc.run(args, timeout=LIST_TIMEOUT)
            except RcloneError as exc:
                if exc.error_class is ErrorClass.FOLDER_NOT_FOUND:
                    return []
                raise
        try:
            items = json.loads(result.stdout)
            found = [
                StoredObject(
                    key=f"{base}/{item['Path']}" if base else str(item["Path"]),
                    size=int(item["Size"]),
                    md5=_md5_of(item),
                    modtime=item.get("ModTime"),
                )
                for item in items
            ]
        except (ValueError, KeyError, TypeError):
            raise RcloneError(ErrorClass.OTHER, "Unexpected rclone lsjson output.") from None
        return sorted(found, key=lambda o: o.key)

    async def get(self, key: str, dest: Path) -> None:
        """Download to `dest` via a .partial file, checking the MD5 before it appears."""
        remote = self._remote(key)
        partial = dest.with_name(dest.name + PARTIAL_SUFFIX)
        if dest.exists() or partial.exists():
            raise FileExistsError(f"{dest.name} already exists.")
        try:
            async with self._rclone() as rc:
                stored = await self._stat(rc, remote)
                await rc.run(
                    ["copyto", remote, str(partial)], timeout=self._context.timeout_seconds
                )
            md5, size = await run_blocking(md5_file, partial)
            if size != stored.size or not stored.md5 or not hmac.compare_digest(stored.md5, md5):
                raise RcloneError(
                    ErrorClass.OTHER, "The downloaded file does not match the MD5 in Google Drive."
                )
            await asyncio.to_thread(partial.chmod, 0o600)
            await asyncio.to_thread(_publish, partial, dest)
        except BaseException:
            partial.unlink(missing_ok=True)
            raise

    async def delete(self, key: str) -> None:
        args = ["deletefile", self._remote(key)]
        if self._cfg.delete_permanently:
            args.append("--drive-use-trash=false")
        async with self._rclone() as rc:
            try:
                await rc.run(args)
            except RcloneError as exc:
                if exc.code not in NOT_FOUND_CODES:
                    raise

import asyncio
import builtins
import threading
import uuid
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from app.drivers.base import CheckResult

DEFAULT_TIMEOUT = 600


@dataclass(frozen=True)
class StoredObject:
    key: str
    size: int
    md5: str | None = None
    modtime: str | None = None


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    md5: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class StorageTestResult:
    ok: bool
    message: str
    free_bytes: int | None = None
    total_bytes: int | None = None
    checks: list[CheckResult] = field(default_factory=list)


class TokenStore(Protocol):
    """Where a remote destination's credentials live (blocking; called from a thread).

    Writes name the token they are based on and are skipped if it has changed meanwhile."""

    def load(self) -> dict[str, str]: ...
    def save_token(self, previous: str | None, token: str) -> bool: ...
    def set_auth(self, status: str, token: str | None) -> None: ...


@dataclass
class StorageContext:
    destination_id: uuid.UUID | None = None
    # Scratch space for the tool's cache (the run's work dir); a private temp dir when None.
    cache_dir: Path | None = None
    timeout_seconds: int = DEFAULT_TIMEOUT
    log: Callable[[str], None] | None = None
    token_store: TokenStore | None = None


class Storage(Protocol):
    async def test(self) -> StorageTestResult: ...
    async def put(self, local: Path, key: str) -> StoredObject: ...
    async def list(self, prefix: str) -> builtins.list[StoredObject]: ...
    async def get(self, key: str, dest: Path) -> None: ...
    async def delete(self, key: str) -> None: ...
    async def verify(self, local: Path, key: str) -> VerifyResult: ...


async def run_blocking[T](fn: Callable[..., T], *args: Any) -> T:
    """Run blocking work in a thread; on cancel, tell it to stop (`cancel=`) and wait for it."""
    cancel = threading.Event()
    future = asyncio.ensure_future(asyncio.to_thread(fn, *args, cancel=cancel))
    try:
        return await asyncio.shield(future)
    except asyncio.CancelledError:
        cancel.set()
        await asyncio.wait({future})
        if not future.cancelled():
            future.exception()  # mark as retrieved
        raise


class BaseStorage(ABC):
    def __init__(
        self,
        config: dict[str, Any],
        secrets: Mapping[str, str],
        context: StorageContext | None = None,
    ) -> None:
        self._config = config
        self._secrets = dict(secrets)
        self._context = context or StorageContext()

    @abstractmethod
    async def test(self) -> StorageTestResult: ...

    @abstractmethod
    async def put(self, local: Path, key: str) -> StoredObject: ...

    @abstractmethod
    async def list(self, prefix: str) -> builtins.list[StoredObject]: ...

    @abstractmethod
    async def get(self, key: str, dest: Path) -> None: ...

    @abstractmethod
    async def delete(self, key: str) -> None: ...

    @abstractmethod
    async def verify(self, local: Path, key: str) -> VerifyResult: ...

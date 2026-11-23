import logging
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from app.services.redact import describe_error

CONNECT_TIMEOUT = 10  # seconds, applied to every connection attempt

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    message: str


@dataclass(frozen=True)
class ConnectionTestResult:
    ok: bool
    message: str
    server_version: str | None = None
    databases: list[str] | None = None
    checks: list[CheckResult] = field(default_factory=list)


class LogSink(Protocol):
    def info(self, message: str) -> None: ...
    def warning(self, message: str) -> None: ...
    def error(self, message: str) -> None: ...


class DumpError(Exception):
    """A dump failed. The message is sanitized and safe to store and show."""


class RestoreError(DumpError):
    """A restore failed or is not possible. The message is sanitized and safe to show."""


@dataclass(frozen=True)
class RestoreOptions:
    database: str  # the database the artifact was dumped from
    target_database: str  # where its contents are restored to
    drop: bool = False  # replace existing collections/tables of the target
    full_instance: bool = False  # the artifact is a whole-server dump


@dataclass(frozen=True)
class RestoreContext:
    work_dir: Path
    timeout_seconds: int
    log: LogSink


@dataclass
class StepHandle:
    """Lets a sub-step attach a detail line or mark itself as finished with a warning."""

    name: str
    detail: str | None = None
    warned: bool = False

    def warn(self, detail: str) -> None:
        self.warned = True
        self.detail = detail


class DumpReporter:
    """Receives named sub-steps and warnings from a driver; the runner supplies the real one."""

    @asynccontextmanager
    async def step(self, name: str) -> AsyncIterator[StepHandle]:
        yield StepHandle(name)

    def warning(self, message: str) -> None:
        pass


@dataclass(frozen=True)
class DumpContext:
    work_dir: Path
    timeout_seconds: int
    compress: bool
    log: LogSink
    job_name: str = ""
    run_id: str = ""
    report: DumpReporter = field(default_factory=DumpReporter)

    def warn(self, message: str) -> None:
        """A warning for the log and the run's step results; never fails the run."""
        self.log.warning(message)
        self.report.warning(message)


@dataclass(frozen=True)
class DumpedArtifact:
    database: str
    path: Path
    extension: str
    size: int
    sha256: str
    compressed: bool
    compression: str  # "gzip", "none" or "pg_dump_custom"


class Driver(Protocol):
    def test_connection(self) -> ConnectionTestResult: ...
    def select_databases(self, listed: list[str]) -> list[str]: ...
    async def tool_versions(self) -> dict[str, str]: ...
    def check_compatibility(self, server_version: str | None, tools: dict[str, str]) -> None: ...
    async def dump_database(self, database: str, ctx: DumpContext) -> DumpedArtifact: ...
    async def dump_globals(self, ctx: DumpContext) -> DumpedArtifact | None: ...
    async def restore_database(
        self, artifact: Path, options: RestoreOptions, ctx: RestoreContext
    ) -> None: ...


class BaseDriver(ABC):
    source_type: str
    # True when dump_database reports its own named sub-steps through ctx.report.
    reports_substeps: bool = False

    def __init__(self, config: dict[str, Any], secrets: Mapping[str, str]) -> None:
        self._config = config
        self._secrets = dict(secrets)

    def sensitive_values(self) -> list[str]:
        return list(self._secrets.values())

    @abstractmethod
    def _probe(self) -> ConnectionTestResult:
        """Connect and gather the version and visible databases. May raise."""

    def test_connection(self) -> ConnectionTestResult:
        try:
            return self._probe()
        except Exception as exc:
            # Log the type only: driver exceptions can embed credentials or URIs.
            logger.info(
                "Connection test failed for %s source: %s", self.source_type, type(exc).__name__
            )
            return ConnectionTestResult(
                ok=False, message=describe_error(exc, self.sensitive_values())
            )

    def select_databases(self, listed: list[str]) -> list[str]:
        raise NotImplementedError("Dumps for this source type arrive in milestone 4.")

    async def tool_versions(self) -> dict[str, str]:
        raise NotImplementedError("Dumps for this source type arrive in milestone 4.")

    def check_compatibility(self, server_version: str | None, tools: dict[str, str]) -> None:  # noqa: B027
        """Raise DumpError if the installed client tools cannot back up this server."""

    async def preflight(self, ctx: DumpContext) -> None:  # noqa: B027
        """Extra checks before any dump (may log); raise DumpError to stop the run."""

    def manifest_extra(self) -> dict[str, Any]:
        """Non-secret, source-specific fields for the manifest."""
        return {}

    async def dump_database(self, database: str, ctx: DumpContext) -> DumpedArtifact:
        raise NotImplementedError("Dumps for this source type arrive in milestone 4.")

    async def dump_globals(self, ctx: DumpContext) -> DumpedArtifact | None:
        """Optional extra artifact (roles etc.); None when the driver or setting has none."""
        return None

    async def restore_database(
        self, artifact: Path, options: RestoreOptions, ctx: RestoreContext
    ) -> None:
        raise RestoreError(f"Restoring {self.source_type} backups is not supported yet.")

    async def restore_tool_versions(self) -> dict[str, str]:
        """Versions of the client tools a restore needs; raises RestoreError if one is missing."""
        return {}

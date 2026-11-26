"""A run that stops early logs its reason as an ERROR line, so the log's Errors filter shows it."""

import asyncio
import uuid
from pathlib import Path

from app.core.config import get_settings
from app.models.run import RunStatus
from app.services.logstream import RunLogger
from app.services.runner import RunFailedError, Runner, RunState

DENIED = "Cannot prepare the target database: denied"


def _run_body(body_error: Exception, log_path: Path) -> tuple[str, str | None]:
    async def body(_state: RunState, _log: RunLogger) -> str:
        raise body_error

    async def scenario() -> tuple[str, str | None]:
        runner = Runner(get_settings())
        return await runner._guarded(uuid.uuid4(), RunState(), RunLogger(log_path), body)

    return asyncio.run(scenario())


def _error_lines(path: Path) -> list[str]:
    return [line for line in path.read_text().splitlines() if "[ERROR]" in line]


def test_failed_run_logs_its_reason_as_error(tmp_path: Path) -> None:
    log_path = tmp_path / "run.log"
    status, error = _run_body(RunFailedError(DENIED), log_path)
    assert status == RunStatus.FAILED and error == DENIED
    lines = _error_lines(log_path)
    assert len(lines) == 1 and lines[0].endswith(f"[ERROR] {DENIED}")


def test_crashed_run_logs_a_redacted_error(tmp_path: Path) -> None:
    log_path = tmp_path / "run.log"
    status, error = _run_body(OSError(13, "Permission denied", "/secret/path"), log_path)
    assert status == RunStatus.FAILED
    assert error == "Unexpected error: PermissionError: Permission denied"
    lines = _error_lines(log_path)
    assert len(lines) == 1 and "/secret/path" not in lines[0]

import asyncio
import os
import uuid
from collections.abc import AsyncIterator, Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path

from app.services.redact import scrub

POLL_SECONDS = 0.5
KEEPALIVE_SECONDS = 15.0
READ_BYTES = 64 * 1024


def log_path_for(logs_dir: Path, run_id: uuid.UUID) -> Path:
    return logs_dir / f"{run_id}.log"


class RunLogger:
    """Timestamped, redacted, append-only log of one run (one small write per line)."""

    def __init__(self, path: Path, secrets: Iterable[str] = ()) -> None:
        self.path = path
        self._secrets = [s for s in secrets if s]

    def add_secrets(self, secrets: Iterable[str]) -> None:
        self._secrets.extend(s for s in secrets if s)

    def _write(self, level: str, message: str) -> None:
        stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        lines = scrub(message, self._secrets).replace("\r", "").split("\n")
        payload = "".join(f"{stamp} [{level}] {line}\n" for line in lines)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(descriptor, "ab") as handle:
            handle.write(payload.encode("utf-8", errors="replace"))

    def info(self, message: str) -> None:
        self._write("INFO", message)

    def warning(self, message: str) -> None:
        self._write("WARN", message)

    def error(self, message: str) -> None:
        self._write("ERROR", message)


def read_from(path: Path, offset: int, limit: int = READ_BYTES) -> bytes:
    try:
        with path.open("rb") as handle:
            handle.seek(offset)
            return handle.read(limit)
    except FileNotFoundError:
        return b""


def read_all_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return ""


def parse_offset(query_offset: int | None, last_event_id: str | None) -> int:
    """`?offset=` wins over Last-Event-ID; garbage or negative values mean 'from the start'."""
    if query_offset is not None:
        return max(query_offset, 0)
    if last_event_id and last_event_id.isdigit():
        return int(last_event_id)
    return 0


def _text(raw: bytes) -> str:
    return raw.decode("utf-8", errors="replace").replace("\r", "")


def _frame(data: str, event: str | None = None, event_id: int | None = None) -> str:
    out = ""
    if event:
        out += f"event: {event}\n"
    if event_id is not None:
        out += f"id: {event_id}\n"
    return out + f"data: {data}\n\n"


async def follow_log(
    path: Path,
    offset: int,
    final_status: Callable[[], str | None],
    *,
    poll_seconds: float = POLL_SECONDS,
    keepalive_seconds: float = KEEPALIVE_SECONDS,
) -> AsyncIterator[str]:
    """SSE frames: replay from `offset`, follow new lines, finish with a `done` event.

    `final_status` returns the run status once the run has finished, else None. It runs
    in a worker thread together with the file reads, so the event loop is never blocked.
    """
    idle = 0.0
    pending = b""
    while True:
        # Status first, then read: lines written before the status flipped are never missed.
        status = await asyncio.to_thread(final_status)
        chunk = await asyncio.to_thread(read_from, path, offset + len(pending))
        pending += chunk
        *lines, pending = pending.split(b"\n")  # the last piece is an unfinished line
        for raw in lines:
            offset += len(raw) + 1
            yield _frame(_text(raw), event_id=offset)
        if status is not None and not chunk:
            if pending:  # the final line may lack a newline
                offset += len(pending)
                yield _frame(_text(pending), event_id=offset)
            yield _frame(f'{{"status": "{status}"}}', event="done")
            return
        if chunk:
            idle = 0.0
            continue
        await asyncio.sleep(poll_seconds)
        idle += poll_seconds
        if idle >= keepalive_seconds:
            idle = 0.0
            yield ": keepalive\n\n"

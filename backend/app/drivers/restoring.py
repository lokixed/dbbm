"""Shared plumbing for driver restores: run one restore command and report failures."""

import asyncio
import gzip
import io
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path

from app.drivers.base import DumpError, RestoreContext, RestoreError
from app.drivers.dumping import capture_output, summarize_stderr
from app.services.hashing import CHUNK
from app.services.proc import StderrTail, StepTimeoutError, clean_env, run_process
from app.services.redact import scrub


async def tool_version(tool: str, work_dir: Path) -> str:
    """The first line of `<tool> --version`; raises RestoreError if it is not installed."""
    try:
        output = await capture_output([tool, "--version"], clean_env({}, work_dir))
    except DumpError as exc:
        raise RestoreError(str(exc)) from None
    return (output.strip().splitlines() or ["unknown"])[0]


def _open_stream(path: Path, gunzip: bool) -> io.BufferedIOBase:
    return gzip.open(path, "rb") if gunzip else path.open("rb")


def file_feeder(path: Path, *, gunzip: bool) -> Callable[[asyncio.StreamWriter], Awaitable[None]]:
    """A stdin source that streams `path` (decompressing it when asked) in constant memory."""

    async def feed(writer: asyncio.StreamWriter) -> None:
        handle = await asyncio.to_thread(_open_stream, path, gunzip)
        try:
            while chunk := await asyncio.to_thread(handle.read, CHUNK):
                writer.write(chunk)
                await writer.drain()
        finally:
            await asyncio.to_thread(handle.close)

    return feed


async def run_restore(
    *,
    tool: str,
    argv: Sequence[str],
    env: Mapping[str, str],
    ctx: RestoreContext,
    secrets: Sequence[str],
    noise: re.Pattern[str] | None = None,
    stdin_path: Path | None = None,
    gunzip: bool = False,
    ignorable: re.Pattern[str] | None = None,
) -> None:
    """Run a restore command. Its stderr goes to the run log (scrubbed); a non-zero exit raises
    RestoreError with a one-line reason from the end of stderr."""
    tail = StderrTail()
    ctx.log.info("Running: " + " ".join(argv))
    try:
        code = await run_process(
            argv,
            env=env,
            timeout=ctx.timeout_seconds,
            tail=tail,
            stderr_line=lambda line: ctx.log.info(f"{tool}: {scrub(line, secrets)}"),
            stdin_source=file_feeder(stdin_path, gunzip=gunzip) if stdin_path else None,
        )
    except StepTimeoutError:
        raise RestoreError(
            f"{tool} timed out after {ctx.timeout_seconds} seconds and was stopped."
        ) from None
    except FileNotFoundError:
        raise RestoreError(f"{tool} is not installed in this environment.") from None
    except OSError as exc:
        raise RestoreError(f"Cannot run the restore: {exc.strerror}.") from None
    if code == 0:
        return
    if ignorable is not None and _only_ignorable_errors(tail.text(), ignorable):
        ctx.log.warning(f"{tool} reported only errors known to be harmless; treated as success.")
        return
    reason = summarize_stderr(tail.text(), secrets, noise)
    raise RestoreError(f"{tool} exited with code {code}: {reason}")


def _only_ignorable_errors(stderr: str, ignorable: re.Pattern[str]) -> bool:
    """True if the command reported errors and every one of them is known to be harmless."""
    errors = [line for line in stderr.splitlines() if "error:" in line.lower()]
    return bool(errors) and all(ignorable.search(line) for line in errors)

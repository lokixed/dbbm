import asyncio
import contextlib
import os
import signal
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path

STDERR_TAIL_BYTES = 64 * 1024
READ_CHUNK = 256 * 1024
_BASE_ENV_KEYS = ("PATH", "LANG", "LC_ALL", "TZ")


class StepTimeoutError(Exception):
    def __init__(self, seconds: int) -> None:
        super().__init__(f"Timed out after {seconds} seconds.")
        self.seconds = seconds


class StderrTail:
    """Keeps only the last `limit` bytes of a process's stderr."""

    def __init__(self, limit: int = STDERR_TAIL_BYTES) -> None:
        self._limit = limit
        self._buffer = bytearray()

    def feed(self, data: bytes) -> None:
        self._buffer += data
        overflow = len(self._buffer) - self._limit
        if overflow > 0:
            del self._buffer[:overflow]

    def text(self) -> str:
        return self._buffer.decode("utf-8", errors="replace")


def clean_env(extra: Mapping[str, str], home: Path) -> dict[str, str]:
    """A minimal environment: the master key and other app settings never reach a child."""
    env = {k: os.environ[k] for k in _BASE_ENV_KEYS if k in os.environ}
    env["HOME"] = str(home)  # keeps tools from reading ~/.my.cnf or ~/.pgpass
    env.update(extra)
    return env


def kill_group(pid: int) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pid, signal.SIGKILL)


async def run_process(
    argv: Sequence[str],
    *,
    env: Mapping[str, str],
    timeout: int,
    tail: StderrTail,
    stdout_sink: Callable[[bytes], Awaitable[None]] | None = None,
    stderr_line: Callable[[str], None] | None = None,
    stdin_source: Callable[[asyncio.StreamWriter], Awaitable[None]] | None = None,
) -> int:
    """Run a command in its own process group; kill the whole group on timeout or cancel.

    `stdin_source` writes the command's standard input; stdin is closed when it returns."""
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.PIPE if stdin_source else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE if stdout_sink else asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
        env=dict(env),
        start_new_session=True,
    )

    async def pump_stderr(stream: asyncio.StreamReader) -> None:
        pending = b""
        while chunk := await stream.read(READ_CHUNK):
            tail.feed(chunk)
            if stderr_line is None:
                continue
            *lines, pending = (pending + chunk).split(b"\n")
            for line in lines:
                stderr_line(line.decode("utf-8", errors="replace").rstrip("\r"))
            pending = pending[-STDERR_TAIL_BYTES:]
        if stderr_line is not None and pending:
            stderr_line(pending.decode("utf-8", errors="replace").rstrip("\r"))

    async def pump_stdout(
        stream: asyncio.StreamReader, sink: Callable[[bytes], Awaitable[None]]
    ) -> None:
        while chunk := await stream.read(READ_CHUNK):
            await sink(chunk)

    async def pump_stdin(
        writer: asyncio.StreamWriter, source: Callable[[asyncio.StreamWriter], Awaitable[None]]
    ) -> None:
        try:
            await source(writer)
        except (BrokenPipeError, ConnectionResetError):
            pass  # the command stopped reading; its exit code and stderr tell why
        finally:
            with contextlib.suppress(BrokenPipeError, ConnectionResetError, OSError):
                writer.close()
                await writer.wait_closed()

    if proc.stderr is None:
        raise RuntimeError("stderr pipe missing")
    pumps = [asyncio.create_task(pump_stderr(proc.stderr))]
    if stdin_source:
        if proc.stdin is None:
            raise RuntimeError("stdin pipe missing")
        pumps.append(asyncio.create_task(pump_stdin(proc.stdin, stdin_source)))
    if stdout_sink:
        if proc.stdout is None:
            raise RuntimeError("stdout pipe missing")
        pumps.append(asyncio.create_task(pump_stdout(proc.stdout, stdout_sink)))
    finished = False
    try:
        async with asyncio.timeout(timeout):
            await asyncio.gather(*pumps)
            code = await proc.wait()
            finished = True
            return code
    except TimeoutError:
        raise StepTimeoutError(timeout) from None
    finally:
        # Never signal a reaped pid: the number could already belong to another process.
        if not finished:
            kill_group(proc.pid)
        for pump in pumps:
            pump.cancel()
        await asyncio.gather(*pumps, return_exceptions=True)
        await proc.wait()

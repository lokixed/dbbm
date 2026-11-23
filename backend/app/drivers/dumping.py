import asyncio
import gzip
import hashlib
import os
import re
import shlex
import tarfile
import threading
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import IO, Any

from app.drivers.base import DumpContext, DumpError
from app.services.hashing import check_cancel, sha256_file
from app.services.proc import StderrTail, StepTimeoutError, run_process
from app.services.redact import sanitize, scrub

SUMMARY_CHARS = 280
VERSION_TIMEOUT = 20


def partial_of(path: Path) -> Path:
    return path.with_name(path.name + ".partial")


def work_file(ctx: DumpContext, stem: str, extension: str) -> Path:
    """A unique path in the run's work dir; the stem is already file-name-safe."""
    return ctx.work_dir / f"{stem}-{uuid.uuid4().hex[:8]}.{extension}"


def summarize_stderr(
    text: str, secrets: Sequence[str], noise: re.Pattern[str] | None = None
) -> str:
    """One sanitized line from the END of stderr, where tools put the actual error.

    Lines matching `noise` (progress output) are dropped from the summary only.
    """
    scrubbed = scrub(text, secrets)
    if noise is not None:
        scrubbed = "\n".join(line for line in scrubbed.splitlines() if not noise.search(line))
    flat = " ".join(scrubbed.split())
    return flat if len(flat) <= SUMMARY_CHARS else "…" + flat[-SUMMARY_CHARS:]


class _HashingFile:
    """Counts and hashes every byte that reaches the file (the compressed bytes)."""

    def __init__(self, raw: IO[bytes]) -> None:
        self._raw = raw
        self.digest = hashlib.sha256()
        self.size = 0

    def write(self, data: bytes) -> int:
        self.digest.update(data)
        self.size += len(data)
        return self._raw.write(data)

    def flush(self) -> None:
        self._raw.flush()


class StreamSink:
    """Writes a process's stdout to `<name>.partial`, through gzip if requested."""

    def __init__(self, partial: Path, compress: bool) -> None:
        descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        self._raw = os.fdopen(descriptor, "wb")
        self._hashing = _HashingFile(self._raw)
        self._out: Any = (
            gzip.GzipFile(fileobj=self._hashing, mode="wb", mtime=0) if compress else self._hashing
        )
        self._compress = compress

    @property
    def size(self) -> int:
        return self._hashing.size

    @property
    def sha256(self) -> str:
        return self._hashing.digest.hexdigest()

    def _write(self, chunk: bytes) -> None:
        self._out.write(chunk)

    async def write(self, chunk: bytes) -> None:
        await asyncio.to_thread(self._write, chunk)

    def close(self) -> None:
        try:
            if self._compress:
                self._out.close()  # writes the gzip trailer through the hashing wrapper
            self._raw.flush()
            os.fsync(self._raw.fileno())
        finally:
            self._raw.close()


async def run_dump(
    *,
    tool: str,
    argv: Sequence[str],
    env: Mapping[str, str],
    ctx: DumpContext,
    secrets: Sequence[str],
    final_path: Path,
    stream_stdout: bool,
    noise: re.Pattern[str] | None = None,
) -> tuple[int, str]:
    """Run one dump command; returns (size, sha256) of the finished file at `final_path`.

    stream_stdout: the tool writes to stdout and we gzip it here. Otherwise the tool writes
    to `partial_of(final_path)` itself (its argv must say so). Any failure removes the
    partial file, so a non-zero exit never leaves something that looks like a backup.
    """
    partial = partial_of(final_path)
    tail = StderrTail()
    ctx.log.info("Running: " + " ".join(shlex.quote(a) for a in argv))
    sink: StreamSink | None = None
    try:
        try:
            if stream_stdout:
                sink = StreamSink(partial, ctx.compress)
            code = await run_process(
                argv,
                env=env,
                timeout=ctx.timeout_seconds,
                tail=tail,
                stdout_sink=sink.write if sink else None,
            )
        finally:
            if sink:
                await asyncio.to_thread(sink.close)
        stderr = scrub(tail.text(), secrets).strip()
        if stderr:
            ctx.log.info(f"{tool} stderr (last 64 KB at most):\n{stderr}")
        if code != 0:
            reason = summarize_stderr(tail.text(), secrets, noise)
            raise DumpError(f"{tool} exited with code {code}: {reason}")
        if sink:
            size, digest = sink.size, sink.sha256
        else:
            digest, size = await asyncio.to_thread(sha256_file, partial)
        os.replace(partial, final_path)
        return size, digest
    except StepTimeoutError:
        raise DumpError(
            f"{tool} timed out after {ctx.timeout_seconds} seconds and was stopped."
        ) from None
    except FileNotFoundError as exc:
        if exc.filename == argv[0]:
            raise DumpError(f"{tool} is not installed in this environment.") from None
        raise DumpError(f"{tool} produced no output file: {exc.strerror}.") from None
    except OSError as exc:
        raise DumpError(f"Cannot write the dump: {exc.strerror}.") from None
    finally:
        partial.unlink(missing_ok=True)


async def capture_output(argv: Sequence[str], env: Mapping[str, str]) -> str:
    """Run a short command (like `--version`) and return its stdout; raises DumpError."""
    chunks: list[bytes] = []

    async def collect(data: bytes) -> None:
        chunks.append(data)

    tail = StderrTail()
    try:
        code = await run_process(
            argv, env=env, timeout=VERSION_TIMEOUT, tail=tail, stdout_sink=collect
        )
    except FileNotFoundError:
        raise DumpError(f"{argv[0]} is not installed in this environment.") from None
    except StepTimeoutError:
        raise DumpError(f"{argv[0]} did not answer within {VERSION_TIMEOUT} seconds.") from None
    if code != 0:
        raise DumpError(f"{argv[0]} exited with code {code}: {sanitize(tail.text())}")
    return b"".join(chunks).decode("utf-8", errors="replace")


def write_tar_gz(
    source: Path, partial: Path, cancel: threading.Event | None = None
) -> tuple[int, str]:
    """Stream `source` into a new `partial` .tar.gz; returns (size, sha256) of the written file.

    Blocking. Members are streamed, so memory use is constant. Symlinks are skipped.
    """
    descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as raw:
        hashing = _HashingFile(raw)
        with (
            gzip.GzipFile(fileobj=hashing, mode="wb", mtime=0) as compressed,
            tarfile.open(fileobj=compressed, mode="w|") as archive,
        ):
            for path in sorted(source.rglob("*")):
                check_cancel(cancel)
                if path.is_symlink():
                    continue
                archive.add(path, arcname=path.relative_to(source).as_posix(), recursive=False)
        raw.flush()
        os.fsync(raw.fileno())
    return hashing.size, hashing.digest.hexdigest()

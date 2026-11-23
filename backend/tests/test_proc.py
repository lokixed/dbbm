import asyncio
import os
import sys
from pathlib import Path

import pytest

from app.services.proc import StderrTail, StepTimeoutError, clean_env, run_process
from tests.fakes import alive

# A shell that starts a long-lived grandchild, like mysqldump piping into a helper.
HANG = "sleep 300 & echo $! > {child}; echo $$ > {parent}; wait"


def test_stderr_tail_is_bounded() -> None:
    tail = StderrTail(limit=10)
    for chunk in (b"0123456789", b"abcdef", b"XYZ"):
        tail.feed(chunk)
    assert tail.text() == "9abcdefXYZ"


def test_clean_env_hides_the_master_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("APP_SECRET_KEY", "super-secret-master-key-0123456789abcdef")
    env = clean_env({"PGPASSWORD": "x"}, tmp_path)
    assert "APP_SECRET_KEY" not in env and env["PGPASSWORD"] == "x"
    assert env["HOME"] == str(tmp_path)


def test_captures_stdout_stderr_and_exit_code() -> None:
    chunks: list[bytes] = []

    async def sink(data: bytes) -> None:
        chunks.append(data)

    tail = StderrTail()
    code = asyncio.run(
        run_process(
            [
                sys.executable,
                "-c",
                "import sys; print('out'); print('err', file=sys.stderr); sys.exit(7)",
            ],
            env=os.environ,
            timeout=20,
            tail=tail,
            stdout_sink=sink,
        )
    )
    assert code == 7
    assert b"".join(chunks).strip() == b"out" and tail.text().strip() == "err"


def test_timeout_kills_the_whole_process_group(tmp_path: Path) -> None:
    parent, child = tmp_path / "parent", tmp_path / "child"
    script = HANG.format(parent=parent, child=child)
    with pytest.raises(StepTimeoutError):
        asyncio.run(run_process(["sh", "-c", script], env=os.environ, timeout=1, tail=StderrTail()))
    assert not alive(int(parent.read_text())) and not alive(int(child.read_text()))


def test_cancel_kills_the_whole_process_group(tmp_path: Path) -> None:
    parent, child = tmp_path / "parent", tmp_path / "child"
    script = HANG.format(parent=parent, child=child)

    async def scenario() -> None:
        task = asyncio.create_task(
            run_process(["sh", "-c", script], env=os.environ, timeout=60, tail=StderrTail())
        )
        for _ in range(100):
            if child.exists() and child.read_text().strip():
                break
            await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert not alive(int(parent.read_text())) and not alive(int(child.read_text()))

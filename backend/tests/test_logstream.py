import asyncio
from pathlib import Path

from app.services.logstream import RunLogger, follow_log, parse_offset, read_from


def test_logger_timestamps_and_redacts(tmp_path: Path) -> None:
    log = RunLogger(tmp_path / "logs" / "run.log", ["hunter2-secret"])
    log.info("connecting with hunter2-secret to mysql://root:hunter2-secret@db/app")
    log.warning("password=another-secret token: abc123")
    log.error("line one\nline two")
    text = (tmp_path / "logs" / "run.log").read_text()
    assert "hunter2-secret" not in text and "another-secret" not in text
    assert "abc123" not in text and "mysql://" not in text
    lines = text.splitlines()
    assert len(lines) == 4
    assert lines[0][4] == "-" and lines[0].endswith(
        "Z [INFO] connecting with [REDACTED] to [REDACTED]"
    )
    assert (
        "[WARN]" in lines[1] and "[ERROR] line one" in lines[2] and "[ERROR] line two" in lines[3]
    )
    assert oct((tmp_path / "logs" / "run.log").stat().st_mode & 0o777) == "0o600"


def test_logger_secrets_added_later(tmp_path: Path) -> None:
    log = RunLogger(tmp_path / "run.log")
    log.add_secrets(["late-secret"])
    log.info("value late-secret here")
    assert "late-secret" not in (tmp_path / "run.log").read_text()


def test_parse_offset() -> None:
    assert parse_offset(None, None) == 0
    assert parse_offset(120, "50") == 120  # the query parameter wins
    assert parse_offset(None, "50") == 50
    assert parse_offset(None, "garbage") == 0
    assert parse_offset(None, "-5") == 0
    assert parse_offset(-3, None) == 0


async def _collect(path: Path, offset: int, status: list[str | None]) -> list[str]:
    frames: list[str] = []

    def final() -> str | None:
        return status[0]

    async for frame in follow_log(path, offset, final, poll_seconds=0.01, keepalive_seconds=0.05):
        frames.append(frame)
    return frames


def test_replay_whole_log_then_done(tmp_path: Path) -> None:
    path = tmp_path / "run.log"
    path.write_bytes(b"first\nsecond\nthird\n")
    frames = asyncio.run(_collect(path, 0, ["success"]))
    assert frames[:3] == [
        "id: 6\ndata: first\n\n",
        "id: 13\ndata: second\n\n",
        "id: 19\ndata: third\n\n",
    ]
    assert frames[-1] == 'event: done\ndata: {"status": "success"}\n\n'


def test_replay_from_offset_skips_earlier_lines(tmp_path: Path) -> None:
    path = tmp_path / "run.log"
    path.write_bytes(b"first\nsecond\nthird\n")
    frames = asyncio.run(_collect(path, 6, ["failed"]))
    assert [f.split("data: ")[1].split("\n")[0] for f in frames[:2]] == ["second", "third"]
    assert frames[0].startswith("id: 13")


def test_unterminated_last_line_is_sent_once_finished(tmp_path: Path) -> None:
    path = tmp_path / "run.log"
    path.write_bytes(b"done line\nno newline")
    frames = asyncio.run(_collect(path, 0, ["cancelled"]))
    assert "data: no newline" in frames[1]
    assert frames[-1].startswith("event: done")


def test_missing_file_finishes_cleanly(tmp_path: Path) -> None:
    frames = asyncio.run(_collect(tmp_path / "absent.log", 0, ["interrupted"]))
    assert frames == ['event: done\ndata: {"status": "interrupted"}\n\n']


def test_follows_new_lines_until_finished(tmp_path: Path) -> None:
    path = tmp_path / "run.log"
    path.write_bytes(b"one\n")

    async def scenario() -> list[str]:
        status: list[str | None] = [None]
        task = asyncio.create_task(_collect(path, 0, status))
        await asyncio.sleep(0.1)
        with path.open("ab") as handle:
            handle.write(b"two\n")
        await asyncio.sleep(0.1)
        status[0] = "success"
        return await asyncio.wait_for(task, 5)

    frames = asyncio.run(scenario())
    data = [f.split("data: ")[1].split("\n")[0] for f in frames if "id:" in f]
    assert data == ["one", "two"]
    assert any(f == ": keepalive\n\n" for f in frames)
    assert frames[-1].startswith("event: done")


def test_read_from_bounds(tmp_path: Path) -> None:
    path = tmp_path / "x.log"
    path.write_bytes(b"abcdef")
    assert read_from(path, 2, 3) == b"cde"
    assert read_from(path, 99) == b""
    assert read_from(tmp_path / "nope", 0) == b""

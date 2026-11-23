"""A fake `rclone` on PATH that simulates Google Drive behaviour against a local directory."""

import json
import os
import stat
import sys
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from app.services.rclone_token import normalize_token, same_token
from app.storage.base import StorageContext
from app.storage.gdrive import GdriveStorage

REFRESH = "1//0gRefreshTokenValue-abcdefghij"
ACCESS = "ya29.AccessTokenValue"
TOKEN = {
    "access_token": ACCESS,
    "token_type": "Bearer",
    "refresh_token": REFRESH,
    "expiry": "2026-11-22T21:36:54.123456789+05:30",
}


def token_json(**over: str) -> str:
    return json.dumps({**TOKEN, **over})


# The fake keeps its state beside itself: remote/ (the "Drive"), calls.jsonl, and control files
# fail_<cmd> ("<exit code>\n<stderr>"), refresh (new token JSON), md5_override, delay.
_SCRIPT = r"""
import hashlib, json, os, shutil, sys, time
from pathlib import Path

state = Path(__file__).resolve().parent
root = state / "remote"
args = sys.argv[1:]
cmd = args[0]
with_value = {"--retries", "--low-level-retries", "--transfers", "--stats", "--stats-log-level",
              "--drive-chunk-size", "--hash-type"}
positional, flags, skip = [], [], False
for a in args[1:]:
    if skip:
        skip = False
    elif a.startswith("-"):
        flags.append(a)
        skip = a in with_value
    else:
        positional.append(a)
config = os.environ.get("RCLONE_CONFIG", "")

with open(state / "calls.jsonl", "a") as log:
    log.write(json.dumps({"argv": sys.argv[1:], "env": sorted(os.environ),
                          "config": config, "home": os.environ.get("HOME"),
                          "cache": os.environ.get("RCLONE_CACHE_DIR"),
                          "config_mode": oct(os.stat(config).st_mode & 0o777) if config else None,
                          "dir_mode": oct(os.stat(os.path.dirname(config)).st_mode & 0o777)
                          if config else None}) + "\n")
if config:
    shutil.copy(config, state / "last.conf")

active = state / "active"
active.mkdir(exist_ok=True)
marker = active / str(os.getpid())
marker.touch()
count = len(list(active.iterdir()))
peak = int((state / "peak").read_text() or 0) if (state / "peak").exists() else 0
# Atomic replace: a concurrent fake rclone must never read a half-written file.
tmp = state / f"peak.{os.getpid()}"
tmp.write_text(str(max(peak, count)))
os.replace(tmp, state / "peak")

def finish(code, stderr=""):
    if stderr:
        sys.stderr.write(stderr + "\n")
    marker.unlink()
    sys.exit(code)

if (state / "delay").exists():
    time.sleep(float((state / "delay").read_text()))

if (state / "refresh").exists():
    lines = Path(config).read_text().splitlines()
    new = (state / "refresh").read_text().strip()
    Path(config).write_text("\n".join(
        f"token = {new}" if l.startswith("token = ") else l for l in lines) + "\n")

fail = state / f"fail_{cmd}"
if fail.exists():
    code, _, text = fail.read_text().partition("\n")
    finish(int(code), text)

def local_of(remote):
    assert remote.startswith("dbb:"), remote
    return root / remote[4:]

def md5(path):
    override = state / "md5_override"
    if override.exists():
        return override.read_text().strip()
    return hashlib.md5(path.read_bytes()).hexdigest()

def item(path, base):
    return {"Path": str(path.relative_to(base)), "Name": path.name, "Size": path.stat().st_size,
            "ModTime": "2026-01-01T00:00:00Z", "IsDir": False, "Hashes": {"md5": md5(path)}}

if cmd == "version":
    print("rclone v0.0.0-fake"); finish(0)
if cmd in ("lsd",):
    if not local_of(positional[0]).is_dir():
        finish(3, "2026/01/01 00:00:00 ERROR : error listing: directory not found")
    finish(0)
if cmd == "mkdir":
    local_of(positional[0]).mkdir(parents=True, exist_ok=True); finish(0)
if cmd == "about":
    print(json.dumps({"total": 15 * 2**30, "used": 2**30, "free": 14 * 2**30})); finish(0)
if cmd == "copyto":
    src, dst = positional
    if dst.startswith("dbb:"):
        target = local_of(dst)
        if target.exists() and "--ignore-existing" in flags:
            finish(0)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, target)
        sys.stderr.write("2026/01/01 00:00:00 NOTICE: 1 KiB / 1 KiB, 100%, 0 B/s, ETA -\n")
    else:
        source = local_of(src)
        if not source.is_file():
            finish(3, "ERROR : directory not found")
        shutil.copyfile(source, dst)
    finish(0)
if cmd == "lsjson":
    target = local_of(positional[0])
    if "--stat" in flags:
        if not target.is_file():
            finish(3, "NOTICE: Failed to lsjson: directory not found")
        print(json.dumps(item(target, target.parent))); finish(0)
    if not target.is_dir():
        finish(3, "ERROR : error listing: directory not found")
    files = target.rglob("*") if "-R" in flags else target.iterdir()
    print(json.dumps([item(p, target) for p in sorted(files) if p.is_file()])); finish(0)
if cmd == "deletefile":
    target = local_of(positional[0])
    if not target.is_file():
        finish(4, "ERROR : object not found")
    if "--drive-use-trash=false" not in flags:
        trash = state / "trash" / target.name
        trash.parent.mkdir(exist_ok=True)
        shutil.move(target, trash)
    else:
        target.unlink()
    finish(0)
finish(2, f"fake rclone: unhandled {cmd}")
"""


class FakeRclone:
    def __init__(self, directory: Path) -> None:
        self.dir = directory
        self.remote = directory / "remote"

    def fail(self, command: str, code: int, stderr: str) -> None:
        (self.dir / f"fail_{command}").write_text(f"{code}\n{stderr}")

    def clear(self, command: str) -> None:
        (self.dir / f"fail_{command}").unlink(missing_ok=True)

    def refresh_to(self, token: dict[str, Any]) -> None:
        (self.dir / "refresh").write_text(json.dumps(token))

    def set(self, name: str, value: str) -> None:
        (self.dir / name).write_text(value)

    def calls(self) -> list[dict[str, Any]]:
        path = self.dir / "calls.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines()]

    def commands(self) -> list[str]:
        return [c["argv"][0] for c in self.calls()]

    def peak(self) -> int:
        path = self.dir / "peak"
        return int(path.read_text()) if path.exists() else 0

    def last_config(self) -> str:
        return (self.dir / "last.conf").read_text()

    def files(self) -> list[str]:
        if not self.remote.exists():
            return []
        return sorted(
            str(p.relative_to(self.remote)) for p in self.remote.rglob("*") if p.is_file()
        )

    def trashed(self) -> list[str]:
        trash = self.dir / "trash"
        return sorted(p.name for p in trash.iterdir()) if trash.exists() else []


@pytest.fixture
def fake_rclone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeRclone:
    directory = tmp_path / "fake-rclone"
    (directory / "remote").mkdir(parents=True)
    script = directory / "rclone"
    script.write_text(f"#!{sys.executable}\n{_SCRIPT}")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{directory}{os.pathsep}{os.environ['PATH']}")
    return FakeRclone(directory)


class MemoryStore:
    def __init__(self, secrets: dict[str, str]) -> None:
        self.secrets = dict(secrets)
        self.auth: list[str] = []
        self.saves = 0
        self.on_load: Callable[[], None] | None = None

    def load(self) -> dict[str, str]:
        loaded = dict(self.secrets)
        if self.on_load:
            self.on_load()
        return loaded

    def save_token(self, previous: str | None, token: str) -> bool:
        if not same_token(self.secrets.get("token"), previous):
            return False
        self.secrets["token"] = token
        self.saves += 1
        return True

    def set_auth(self, status: str, token: str | None) -> None:
        if same_token(self.secrets.get("token"), token):
            self.auth.append(status)


def make(
    store: MemoryStore,
    lines: list[str] | None = None,
    dest_id: uuid.UUID | None = None,
    **cfg: object,
) -> GdriveStorage:
    context = StorageContext(
        destination_id=dest_id or uuid.uuid4(),
        timeout_seconds=30,
        log=(lines.append if lines is not None else None),
        token_store=store,
    )
    return GdriveStorage({"remote_folder": "backups", **cfg}, {}, context)


@pytest.fixture
def token_store() -> MemoryStore:
    return MemoryStore({"token": normalize_token(token_json())})


@pytest.fixture
def dump_file(tmp_path: Path) -> Path:
    path = tmp_path / "dump.sql.gz"
    path.write_bytes(b"backup-bytes" * 1000)
    return path

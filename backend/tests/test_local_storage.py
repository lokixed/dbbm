import asyncio
import hashlib
import os
import stat
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.core.config import get_settings
from app.services.hashing import OperationCancelledError
from app.services.paths import PathNotAllowed
from app.storage.local import LocalStorage

run = asyncio.run


def verify(storage: LocalStorage, local: Path, key: str) -> bool:
    return asyncio.run(storage.verify(local, key)).ok


@pytest.fixture
def storage(tmp_path: Path) -> Iterator[LocalStorage]:
    # The autouse `env` fixture already points BACKUP_DIR at tmp_path / "backups".
    get_settings().backup_dir.mkdir(parents=True, exist_ok=True)
    yield LocalStorage({"path": "dest"}, {})


@pytest.fixture
def source(tmp_path: Path) -> Path:
    path = tmp_path / "dump.bin"
    path.write_bytes(os.urandom(300_000))
    return path


def root() -> Path:
    return get_settings().backup_dir / "dest"


def test_put_writes_file_0600_and_verifies(storage: LocalStorage, source: Path) -> None:
    stored = run(storage.put(source, "job/2026/03/job_db_x.dump"))
    target = root() / "job/2026/03/job_db_x.dump"
    assert stored.size == source.stat().st_size
    assert target.read_bytes() == source.read_bytes()
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert not list(root().rglob("*.partial"))
    assert verify(storage, source, "job/2026/03/job_db_x.dump")


def test_put_never_overwrites(storage: LocalStorage, source: Path, tmp_path: Path) -> None:
    run(storage.put(source, "a/file.dump"))
    other = tmp_path / "other.bin"
    other.write_bytes(b"different")
    with pytest.raises(FileExistsError):
        run(storage.put(other, "a/file.dump"))
    assert (root() / "a/file.dump").read_bytes() == source.read_bytes()
    assert not list(root().rglob("*.partial"))


def test_verify_detects_corruption_and_absence(storage: LocalStorage, source: Path) -> None:
    assert not verify(storage, source, "missing.dump")
    run(storage.put(source, "f.dump"))
    target = root() / "f.dump"
    data = bytearray(target.read_bytes())
    data[100] ^= 0xFF
    target.write_bytes(bytes(data))
    assert not verify(storage, source, "f.dump")
    target.write_bytes(data[:-1])
    assert not verify(storage, source, "f.dump")


def test_cancelled_put_leaves_nothing(storage: LocalStorage, source: Path) -> None:
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(OperationCancelledError):
        storage.put_sync(source, "c/file.dump", cancel=cancel)
    assert not (root() / "c" / "file.dump").exists()
    assert not list(root().rglob("*.partial"))


def test_symlink_escape_is_rejected(storage: LocalStorage, source: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    root().mkdir(parents=True)
    (root() / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(PathNotAllowed):
        run(storage.put(source, "link/file.dump"))
    assert not list(outside.iterdir())


def test_list_get_delete(storage: LocalStorage, source: Path, tmp_path: Path) -> None:
    run(storage.put(source, "j/a.dump"))
    run(storage.put(source, "j/b.dump"))
    (root() / "j" / "c.dump.partial").write_bytes(b"in progress")
    listed = run(storage.list("j"))
    assert [o.key for o in listed] == ["j/a.dump", "j/b.dump"]
    assert run(storage.list("nothing-here")) == []

    copy = tmp_path / "copy.bin"
    run(storage.get("j/a.dump", copy))
    assert (
        hashlib.sha256(copy.read_bytes()).digest() == hashlib.sha256(source.read_bytes()).digest()
    )

    run(storage.delete("j/a.dump"))
    run(storage.delete("j/a.dump"))  # deleting twice is fine
    assert [o.key for o in run(storage.list(""))] == ["j/b.dump"]


# ---------- destination test (matches what an upload needs) ----------


def test_destination_test_passes_and_leaves_no_probe_file(storage: LocalStorage) -> None:
    result = storage.test_sync()
    assert result.ok and [c.name for c in result.checks] == ["path", "writable"]
    assert list(root().iterdir()) == []


def test_destination_test_fails_when_chmod_is_refused(
    storage: LocalStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    # As on a Windows-drive mount where the files belong to another user.
    def refuse(_self: Path, _mode: int) -> None:
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(Path, "chmod", refuse)
    result = storage.test_sync()
    assert not result.ok
    assert result.message == "Cannot write to the destination: Operation not permitted."


def test_destination_test_fails_when_permissions_are_ignored(
    storage: LocalStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    # chmod succeeds but every file still reads as 0777.
    real_stat = os.stat_result

    def world_writable(self: Path, **_kwargs: object) -> os.stat_result:
        values = list(os.lstat(self))
        values[0] = stat.S_IFREG | 0o777
        return real_stat(values)

    monkeypatch.setattr(Path, "chmod", lambda _self, _mode: None)
    monkeypatch.setattr(Path, "stat", world_writable)
    result = storage.test_sync()
    assert not result.ok and "ignores file permissions" in result.message

"""The Drive adapter against the REAL rclone binary, with a local `alias` remote standing in
for Google Drive. Validates command construction and output parsing without a Google account."""

import asyncio
import shutil
import tempfile
from pathlib import Path

import pytest

from app.storage.gdrive import GdriveStorage
from tests.fake_rclone import MemoryStore, make
from tests.test_gdrive_storage import KEY

pytestmark = pytest.mark.skipif(shutil.which("rclone") is None, reason="rclone not installed")


class LocalBackedDrive(GdriveStorage):
    root: Path

    def _sections(self, secrets: dict[str, str]) -> dict[str, str]:
        return {"type": "alias", "remote": str(self.root)}


@pytest.fixture
def drive(tmp_path: Path, token_store: MemoryStore) -> LocalBackedDrive:
    base = make(token_store)
    storage = LocalBackedDrive(base._config, {}, base._context)
    storage.root = tmp_path / "drive"
    storage.root.mkdir()
    return storage


def leftovers() -> list[str]:
    return [p.name for p in Path(tempfile.gettempdir()).glob("dbbm-rclone-*")]


def test_real_rclone_connection_test(drive: LocalBackedDrive, token_store: MemoryStore) -> None:
    before = leftovers()
    result = asyncio.run(drive.test())
    assert result.ok, result.message
    assert [c.ok for c in result.checks] == [True, True, True]
    assert result.free_bytes is not None
    assert (drive.root / "backups").is_dir() and not list((drive.root / "backups").iterdir())
    assert token_store.auth == ["ok"]
    assert leftovers() == before


def test_real_rclone_round_trip(drive: LocalBackedDrive, dump_file: Path, tmp_path: Path) -> None:
    async def scenario() -> None:
        await drive.put(dump_file, KEY)
        assert (drive.root / "backups" / KEY).read_bytes() == dump_file.read_bytes()
        result = await drive.verify(dump_file, KEY)
        assert result.ok and result.md5
        with pytest.raises(FileExistsError):
            await drive.put(dump_file, KEY)
        listed = await drive.list("nightly")
        assert [(o.key, o.size, o.md5) for o in listed] == [
            (KEY, dump_file.stat().st_size, result.md5)
        ]
        copy = tmp_path / "copy.gz"
        await drive.get(KEY, copy)
        assert copy.read_bytes() == dump_file.read_bytes()
        await drive.delete(KEY)
        await drive.delete(KEY)
        assert await drive.list("") == []

    asyncio.run(scenario())


def test_real_rclone_verify_mismatch(drive: LocalBackedDrive, dump_file: Path) -> None:
    asyncio.run(drive.put(dump_file, KEY))
    (drive.root / "backups" / KEY).write_bytes(b"tampered" * 1500)
    result = asyncio.run(drive.verify(dump_file, KEY))
    assert not result.ok

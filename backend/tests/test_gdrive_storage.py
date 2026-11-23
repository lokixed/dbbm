import asyncio
import json
import uuid
from pathlib import Path

import pytest

from app.services.rclone_token import normalize_token
from app.storage.rclone_utils import ErrorClass, RcloneError
from tests.fake_rclone import ACCESS, REFRESH, TOKEN, FakeRclone, MemoryStore, make, token_json

KEY = "nightly/2026/11/nightly_shop_20261122T100000Z.sql.gz"


def test_connection_test_runs_three_steps(
    fake_rclone: FakeRclone, token_store: MemoryStore
) -> None:
    result = asyncio.run(make(token_store).test())
    assert result.ok, result.message
    assert [c.name for c in result.checks] == ["folder", "space", "write"]
    assert "Created folder 'backups'" in result.checks[0].message
    assert result.free_bytes == 14 * 2**30 and result.total_bytes == 15 * 2**30
    assert fake_rclone.files() == [] and fake_rclone.trashed() == []  # probe deleted for good
    assert token_store.auth == ["ok"]
    assert fake_rclone.commands() == ["lsd", "mkdir", "about", "copyto", "lsjson", "deletefile"]


def test_free_space_is_optional(fake_rclone: FakeRclone, token_store: MemoryStore) -> None:
    fake_rclone.fail("about", 1, "ERROR : about not supported for shared drives")
    result = asyncio.run(make(token_store).test())
    assert result.ok and result.free_bytes is None
    assert "not reported" in result.checks[1].message


def test_revoked_token_marks_needs_reauth(
    fake_rclone: FakeRclone, token_store: MemoryStore
) -> None:
    fake_rclone.fail(
        "lsd",
        1,
        'ERROR : couldn\'t fetch token: invalid_grant: "Token has been expired or revoked."',
    )
    result = asyncio.run(make(token_store).test())
    assert not result.ok
    assert result.checks[-1].name == "folder" and not result.checks[-1].ok
    assert "reconnect" in result.message
    assert token_store.auth == ["needs_reauth"]


def test_put_verify_list_get_delete(
    fake_rclone: FakeRclone, token_store: MemoryStore, dump_file: Path, tmp_path: Path
) -> None:
    lines: list[str] = []
    drive = make(token_store, lines)

    async def scenario() -> None:
        stored = await drive.put(dump_file, KEY)
        assert stored.size == dump_file.stat().st_size
        result = await drive.verify(dump_file, KEY)
        assert result.ok and result.md5 and len(result.md5) == 32
        listed = await drive.list("nightly")
        assert [o.key for o in listed] == [KEY] and listed[0].md5 == result.md5
        assert await drive.list("nothing") == []
        copy = tmp_path / "copy.gz"
        await drive.get(KEY, copy)
        assert copy.read_bytes() == dump_file.read_bytes()
        assert not copy.with_name("copy.gz.partial").exists()
        await drive.delete(KEY)
        await drive.delete(KEY)  # already gone is fine

    asyncio.run(scenario())
    assert fake_rclone.files() == [] and fake_rclone.trashed() == [
        "nightly_shop_20261122T100000Z.sql.gz"
    ]
    upload = next(c["argv"] for c in fake_rclone.calls() if c["argv"][0] == "copyto")
    assert upload[2] == f"dbb:backups/{KEY}" and "--ignore-existing" in upload
    assert upload[upload.index("--drive-chunk-size") + 1] == "64M"
    assert any("100%" in line for line in lines)  # rclone progress reaches the run log
    assert "ok" in token_store.auth


def test_delete_permanently(
    fake_rclone: FakeRclone, token_store: MemoryStore, dump_file: Path
) -> None:
    drive = make(token_store, delete_permanently=True)

    async def scenario() -> None:
        await drive.put(dump_file, KEY)
        await drive.delete(KEY)

    asyncio.run(scenario())
    assert fake_rclone.files() == [] and fake_rclone.trashed() == []
    assert "--drive-use-trash=false" in fake_rclone.calls()[-1]["argv"]


def test_duplicate_name_is_refused(
    fake_rclone: FakeRclone, token_store: MemoryStore, dump_file: Path
) -> None:
    drive = make(token_store)
    asyncio.run(drive.put(dump_file, KEY))
    with pytest.raises(FileExistsError):
        asyncio.run(drive.put(dump_file, KEY))
    assert fake_rclone.commands().count("copyto") == 1


def test_verify_detects_md5_and_size_mismatch(
    fake_rclone: FakeRclone, token_store: MemoryStore, dump_file: Path, tmp_path: Path
) -> None:
    drive = make(token_store)
    asyncio.run(drive.put(dump_file, KEY))
    fake_rclone.set("md5_override", "0" * 32)
    result = asyncio.run(drive.verify(dump_file, KEY))
    assert not result.ok and "MD5" in (result.reason or "")
    other = tmp_path / "other.bin"
    other.write_bytes(b"short")
    assert "Size differs" in (asyncio.run(drive.verify(other, KEY)).reason or "")


def test_get_refuses_a_corrupt_download(
    fake_rclone: FakeRclone, token_store: MemoryStore, dump_file: Path, tmp_path: Path
) -> None:
    drive = make(token_store)
    asyncio.run(drive.put(dump_file, KEY))
    fake_rclone.set("md5_override", "0" * 32)
    target = tmp_path / "restore.gz"
    with pytest.raises(RcloneError, match="MD5"):
        asyncio.run(drive.get(KEY, target))
    assert not target.exists() and not target.with_name("restore.gz.partial").exists()


def test_refreshed_token_is_written_back(fake_rclone: FakeRclone, token_store: MemoryStore) -> None:
    new = {**TOKEN, "access_token": "ya29.Fresh", "expiry": "2026-11-23T10:00:00Z"}
    fake_rclone.refresh_to(new)
    lines: list[str] = []
    asyncio.run(make(token_store, lines).test())
    assert json.loads(token_store.secrets["token"]) == new
    assert token_store.saves == 1
    assert any("2026-11-23T10:00:00Z" in line for line in lines)
    assert not any("ya29.Fresh" in line for line in lines)


def test_unchanged_token_is_not_rewritten(
    fake_rclone: FakeRclone, token_store: MemoryStore
) -> None:
    asyncio.run(make(token_store).test())
    assert token_store.saves == 0


def test_refresh_does_not_clobber_a_token_saved_meanwhile(
    fake_rclone: FakeRclone, token_store: MemoryStore
) -> None:
    fake_rclone.refresh_to({**TOKEN, "access_token": "ya29.FromOldToken"})
    replacement = normalize_token(token_json(refresh_token="1//0gUserReconnected-xxxxxxxxxx"))

    def user_reconnects() -> None:
        token_store.secrets["token"] = replacement

    token_store.on_load = user_reconnects
    asyncio.run(make(token_store).test())
    assert token_store.secrets["token"] == replacement and token_store.saves == 0


def test_rclone_calls_per_destination_are_serialized(
    fake_rclone: FakeRclone, token_store: MemoryStore, dump_file: Path
) -> None:
    fake_rclone.set("delay", "0.3")
    shared = uuid.uuid4()

    async def scenario(ids: tuple[uuid.UUID, uuid.UUID], tag: str) -> None:
        await asyncio.gather(
            make(token_store, dest_id=ids[0]).put(dump_file, f"a/{tag}.gz"),
            make(token_store, dest_id=ids[1]).put(dump_file, f"b/{tag}.gz"),
        )

    asyncio.run(scenario((shared, shared), "same"))
    assert fake_rclone.peak() == 1
    asyncio.run(scenario((uuid.uuid4(), uuid.uuid4()), "different"))
    assert fake_rclone.peak() == 2


def test_quota_error_is_classified(
    fake_rclone: FakeRclone, token_store: MemoryStore, dump_file: Path
) -> None:
    fake_rclone.fail(
        "copyto",
        7,
        "ERROR : Failed to copy: googleapi: Error 403: The user's Drive storage quota has been "
        "exceeded., storageQuotaExceeded",
    )
    with pytest.raises(RcloneError) as info:
        asyncio.run(make(token_store).put(dump_file, KEY))
    assert info.value.error_class is ErrorClass.QUOTA_EXCEEDED
    assert token_store.auth == []


def test_no_secret_in_any_argv(fake_rclone: FakeRclone, dump_file: Path) -> None:
    token_store = MemoryStore(
        {"token": normalize_token(token_json()), "client_secret": "csecret-123"}
    )
    drive = make(token_store, client_id="cid.apps.googleusercontent.com", shared_drive_id="0ABCdef")
    asyncio.run(drive.test())
    asyncio.run(drive.put(dump_file, KEY))
    for call in fake_rclone.calls():
        joined = " ".join(call["argv"])
        assert REFRESH not in joined and ACCESS not in joined and "csecret-123" not in joined
    conf = fake_rclone.last_config()
    assert "client_secret = csecret-123" in conf and "team_drive = 0ABCdef" in conf
    assert "scope = drive.file" in conf

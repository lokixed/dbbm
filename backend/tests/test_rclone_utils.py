import asyncio
import base64
import json
import stat
from pathlib import Path

import pytest

from app.schemas.common import SecretsInvalid
from app.schemas.destination import GdriveConfig, normalize_remote_folder
from app.services.rclone_token import normalize_token, same_token, token_values
from app.storage.rclone_utils import (
    COMMON_FLAGS,
    ErrorClass,
    RcloneError,
    classify_error,
    drive_sections,
    rclone_session,
    read_token,
    render_config,
)
from tests.fake_rclone import ACCESS, REFRESH, TOKEN, FakeRclone, token_json

# ---------- tokens ----------


def test_token_json_is_validated_and_compacted() -> None:
    out = json.loads(normalize_token(f"  {token_json()}\n"))
    assert out == TOKEN


def test_token_from_full_authorize_output() -> None:
    pasted = f"Paste the following into your remote machine --->\n{token_json()}\n<---End paste"
    assert json.loads(normalize_token(pasted))["refresh_token"] == REFRESH


def test_token_from_authorize_config_blob() -> None:
    blob = base64.urlsafe_b64encode(json.dumps({"token": token_json()}).encode()).decode()
    assert json.loads(normalize_token(blob.rstrip("=")))["access_token"] == ACCESS


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ("", "empty"),
        ("{not json", "not valid JSON"),
        ('["a"]', "Paste the token JSON"),
        (json.dumps({**TOKEN, "refresh_token": ""}), "refresh_token"),
        (json.dumps({k: v for k, v in TOKEN.items() if k != "expiry"}), "expiry"),
        (json.dumps({**TOKEN, "expiry": "tomorrow"}), "valid timestamp"),
        (json.dumps({**TOKEN, "expiry": "2026-01-01T00:00:00"}), "timezone"),
        ("bm90IGEgYmxvYg", "Paste the token JSON"),
        ("eyJ4IjogMX0", "does not contain a token"),
    ],
)
def test_bad_tokens_are_rejected_without_echo(raw: str, message: str) -> None:
    with pytest.raises(ValueError, match=message) as info:
        normalize_token(raw)
    assert REFRESH not in str(info.value) and ACCESS not in str(info.value)


def test_token_helpers() -> None:
    compact = normalize_token(token_json())
    assert same_token(compact, token_json())
    assert not same_token(compact, token_json(expiry="2027-01-01T00:00:00Z"))
    assert {ACCESS, REFRESH} <= set(token_values(compact))


# ---------- config ----------


def test_drive_config_sections() -> None:
    sections = drive_sections(
        scope="drive.file",
        token="{}",
        client_id="cid",
        client_secret="csecret",
        shared_drive_id="0AB",
    )
    text = render_config(sections)
    assert text.startswith("[dbb]\ntype = drive\nscope = drive.file\n")
    assert "client_id = cid\nclient_secret = csecret\nteam_drive = 0AB\ntoken = {}\n" in text
    plain = drive_sections(scope="drive", token="{}", client_id="cid")
    assert "client_id" not in plain and "team_drive" not in plain


def test_config_rejects_newlines() -> None:
    with pytest.raises(ValueError):
        render_config({"token": "{}\n[evil]\ntype = local"})


def test_session_files_are_private_and_removed() -> None:
    async def scenario() -> Path:
        async with rclone_session(
            {"type": "drive", "token": "{}"}, cache_dir=None, secrets=[]
        ) as s:
            assert stat.S_IMODE(s.config_path.stat().st_mode) == 0o600
            assert stat.S_IMODE(s.config_path.parent.stat().st_mode) == 0o700
            assert s.env["RCLONE_CONFIG"] == str(s.config_path)
            assert s.env["XDG_CACHE_HOME"].startswith(str(s.config_path.parent))
            assert "APP_SECRET_KEY" not in s.env
            return s.config_path

    path = asyncio.run(scenario())
    assert not path.parent.exists()


def test_session_removed_on_error(tmp_path: Path) -> None:
    seen: list[Path] = []

    async def scenario() -> None:
        async with rclone_session({"type": "drive"}, cache_dir=tmp_path / "c", secrets=[]) as s:
            seen.append(s.config_path)
            assert s.env["RCLONE_CACHE_DIR"] == str(tmp_path / "c")
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        asyncio.run(scenario())
    assert not seen[0].parent.exists()


def test_read_token(tmp_path: Path) -> None:
    conf = tmp_path / "rclone.conf"
    conf.write_text(f"[dbb]\ntype = drive\ntoken = {token_json()}\n")
    assert json.loads(read_token(conf) or "") == TOKEN
    conf.write_text("[dbb]\ntype = alias\n")
    assert read_token(conf) is None
    assert read_token(tmp_path / "missing.conf") is None


def test_secrets_never_reach_argv_and_lines_are_scrubbed(fake_rclone: FakeRclone) -> None:
    lines: list[str] = []
    fake_rclone.fail("lsd", 1, f"ERROR : failed: token {REFRESH} client_secret=csecret")

    async def scenario() -> None:
        sections = drive_sections(
            scope="drive", token=token_json(), client_id="cid", client_secret="csecret"
        )
        async with rclone_session(
            sections, cache_dir=None, secrets=[REFRESH, ACCESS, "csecret"]
        ) as s:
            await s.run(["about", "dbb:", "--json"], timeout=10)
            with pytest.raises(RcloneError) as info:
                await s.run(["lsd", "dbb:x"], timeout=10, on_line=lines.append)
            assert REFRESH not in str(info.value) and "csecret" not in str(info.value)

    asyncio.run(scenario())
    for call in fake_rclone.calls():
        argv = " ".join(call["argv"])
        assert REFRESH not in argv and ACCESS not in argv and "csecret" not in argv
        assert call["config_mode"] == "0o600" and call["dir_mode"] == "0o700"
        assert all(flag in call["argv"] for flag in COMMON_FLAGS)
    assert lines and not any(REFRESH in ln or "csecret" in ln for ln in lines)


def test_timeout_is_classified(fake_rclone: FakeRclone) -> None:
    fake_rclone.set("delay", "5")

    async def scenario() -> None:
        async with rclone_session({"type": "drive"}, cache_dir=None, secrets=[]) as s:
            await s.run(["about", "dbb:"], timeout=1)

    with pytest.raises(RcloneError) as info:
        asyncio.run(scenario())
    assert info.value.error_class is ErrorClass.NETWORK


# ---------- error classes ----------


@pytest.mark.parametrize(
    ("stderr", "code", "expected"),
    [
        (
            "Failed to copy: couldn't fetch token: invalid_grant: maybe token expired? "
            '- try refreshing with "rclone config reconnect dbb:"',
            7,
            ErrorClass.AUTH_EXPIRED,
        ),
        ("googleapi: Error 401: Invalid Credentials, authError", 1, ErrorClass.AUTH_EXPIRED),
        (
            "googleapi: Error 403: The user's Drive storage quota has been exceeded., "
            "storageQuotaExceeded",
            7,
            ErrorClass.QUOTA_EXCEEDED,
        ),
        (
            "Received upload limit error: googleapi: Error 403: User rate limit exceeded., "
            "userRateLimitExceeded",
            7,
            ErrorClass.QUOTA_EXCEEDED,
        ),
        (
            "googleapi: Error 403: Rate Limit Exceeded, rateLimitExceeded",
            5,
            ErrorClass.RATE_LIMITED,
        ),
        ("ERROR : error listing: directory not found", 3, ErrorClass.FOLDER_NOT_FOUND),
        (
            "googleapi: Error 404: Shared drive not found: 0AB, notFound",
            1,
            ErrorClass.FOLDER_NOT_FOUND,
        ),
        (
            'Get "https://www.googleapis.com/drive/v3/files": dial tcp: lookup www.googleapis.com: '
            "no such host",
            1,
            ErrorClass.NETWORK,
        ),
        ("something odd happened", 2, ErrorClass.OTHER),
    ],
)
def test_error_classification(stderr: str, code: int, expected: ErrorClass) -> None:
    error = classify_error(f"2026/11/22 10:00:00 ERROR : {stderr}", code)
    assert error.error_class is expected
    assert "https://" not in str(error)
    assert "2026/11/22" not in str(error)


# ---------- folder and config validation ----------


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [
        ("db-backups", "db-backups"),
        ("/backups//daily/", "backups/daily"),
        ("  Team Backups/2026 ", "Team Backups/2026"),
    ],
)
def test_folder_normalization(raw: str, normalized: str) -> None:
    assert normalize_remote_folder(raw) == normalized


@pytest.mark.parametrize(
    "raw", ["", "/", "a/../b", "..", "a/./b", "a\\b", "a:b", "a*", "a/ b", "a\nb", "x" * 300]
)
def test_folder_rejections(raw: str) -> None:
    with pytest.raises(ValueError):
        normalize_remote_folder(raw)


def test_client_id_and_secret_go_together() -> None:
    good = token_json()
    with pytest.raises(SecretsInvalid, match="together"):
        GdriveConfig(client_id="cid").check_secrets({"token": good})
    with pytest.raises(SecretsInvalid, match="together"):
        GdriveConfig().check_secrets({"token": good, "client_secret": "s"})
    GdriveConfig(client_id="cid").check_secrets({"token": good, "client_secret": "s"})
    GdriveConfig().check_secrets({"token": good})


def test_gdrive_config_defaults_and_limits() -> None:
    config = GdriveConfig()
    assert (config.remote_folder, config.scope, config.chunk_size_mb) == (
        "db-backups",
        "drive.file",
        64,
    )
    assert config.delete_permanently is False
    with pytest.raises(ValueError):
        GdriveConfig(chunk_size_mb=10)
    with pytest.raises(ValueError):
        GdriveConfig.model_validate({"scope": "drive.readonly"})
    with pytest.raises(ValueError):
        GdriveConfig(shared_drive_id="bad id")

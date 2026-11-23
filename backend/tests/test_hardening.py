"""Milestone 9: security headers, password change, audit log, key rotation, admin CLI."""

import json
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.cli import main as cli_main
from app.core.config import get_settings
from app.core.crypto import decrypt_with
from app.core.db import get_engine
from app.models import AuditLog, Source, User
from app.models.base import utcnow
from app.services import admin_tools as tools
from app.services.housekeeping import Housekeeping
from tests.conftest import MASTER_KEY, PASSWORD, api_post, login
from tests.runs_helpers import make_source

NEW_KEY = "another-master-key-0123456789-abcdefghijk"


# --- headers and docs --------------------------------------------------------


def test_security_headers_on_api_and_health(client: TestClient) -> None:
    for path in ("/healthz", "/api/auth/status"):
        headers = client.get(path).headers
        assert headers["x-frame-options"] == "DENY"
        assert headers["x-content-type-options"] == "nosniff"
        assert headers["referrer-policy"] == "no-referrer"
        assert "frame-ancestors 'none'" in headers["content-security-policy"]
    assert client.get("/api/auth/status").headers["cache-control"] == "no-store"


def test_api_docs_are_off_by_default(client: TestClient) -> None:
    assert client.get("/api/docs").status_code == 404
    assert client.get("/api/openapi.json").status_code == 404


def test_version_is_reported(client: TestClient) -> None:
    assert client.get("/api/version").json()["version"]
    assert client.get("/healthz").json()["version"]


# --- password change ---------------------------------------------------------


def test_change_password_signs_out_other_sessions(authed: TestClient, client: TestClient) -> None:
    other = TestClient(client.app)
    with other:
        assert login(other).status_code == 200
        body = {"current_password": PASSWORD, "new_password": "a brand new passphrase"}
        assert api_post(authed, "/api/auth/change-password", body).status_code == 200
        assert authed.get("/api/auth/me").status_code == 200  # this session was renewed
        assert other.get("/api/auth/me").status_code == 401  # the other one is gone
    fresh = TestClient(client.app)
    with fresh:
        assert login(fresh, password=PASSWORD).status_code == 401
        assert login(fresh, password="a brand new passphrase").status_code == 200


def test_change_password_checks_the_current_one(authed: TestClient) -> None:
    bad = {"current_password": "wrong", "new_password": "a brand new passphrase"}
    assert api_post(authed, "/api/auth/change-password", bad).status_code == 403
    same = {"current_password": PASSWORD, "new_password": PASSWORD}
    assert api_post(authed, "/api/auth/change-password", same).status_code == 422
    short = {"current_password": PASSWORD, "new_password": "short"}
    assert api_post(authed, "/api/auth/change-password", short).status_code == 422


def test_change_password_needs_login(client: TestClient) -> None:
    client.get("/api/auth/status")
    body = {"current_password": "x", "new_password": "a brand new passphrase"}
    assert api_post(client, "/api/auth/change-password", body).status_code in (401, 403)


# --- audit log ---------------------------------------------------------------


def test_audit_log_lists_and_filters(authed: TestClient) -> None:
    page = authed.get("/api/audit").json()
    assert page["total"] >= 2 and "auth.login.success" in page["actions"]
    only = authed.get("/api/audit?action=auth.login.success").json()
    assert only["total"] == 1 and only["items"][0]["username"] == "admin"
    assert authed.get("/api/audit?search=login").json()["total"] >= 1
    assert authed.get("/api/audit?search=%25").json()["total"] == 0  # wildcards are literal
    assert authed.get("/api/audit?limit=1").json()["items"].__len__() == 1


def test_audit_log_requires_login(client: TestClient) -> None:
    assert client.get("/api/audit").status_code == 401


def test_housekeeping_prunes_old_audit_entries(authed: TestClient) -> None:
    with Session(get_engine()) as db:
        old = AuditLog(action="old.entry", username="x", timestamp=utcnow() - timedelta(days=900))
        db.add(old)
        db.commit()
    result = Housekeeping._prune_audit(utcnow() - timedelta(days=365))
    assert result["removed"] == 1
    with Session(get_engine()) as db:
        assert db.exec(select(AuditLog).where(AuditLog.action == "old.entry")).first() is None
        assert db.exec(select(AuditLog)).first() is not None


# --- key rotation ------------------------------------------------------------


def _source_blob() -> bytes:
    with Session(get_engine()) as db:
        blob = db.exec(select(Source.secrets_enc)).first()
    assert blob
    return bytes(blob)


@pytest.mark.usefixtures("fake_drivers")
def test_rotation_dry_run_changes_nothing(authed: TestClient) -> None:
    make_source(authed)
    before = _source_blob()
    report = tools.rotate_master_key(get_settings(), NEW_KEY.encode(), dry_run=True)
    assert report.dry_run and report.rotated >= 1 and report.backup_path is None
    assert _source_blob() == before


@pytest.mark.usefixtures("fake_drivers")
def test_rotation_reencrypts_and_keeps_a_rollback_copy(authed: TestClient) -> None:
    make_source(authed)
    old = _source_blob()
    report = tools.rotate_master_key(get_settings(), NEW_KEY.encode(), dry_run=False)
    assert report.rotated >= 1 and report.backup_path and report.backup_path.is_file()
    new = _source_blob()
    assert new != old
    assert json.loads(decrypt_with(NEW_KEY.encode(), new))
    with pytest.raises(Exception, match="Cannot decrypt"):
        decrypt_with(MASTER_KEY.encode(), new)


@pytest.mark.usefixtures("fake_drivers")
def test_rotation_aborts_without_changes_on_an_undecryptable_secret(authed: TestClient) -> None:
    make_source(authed)
    with Session(get_engine()) as db:
        source = db.exec(select(Source)).one()
        source.secrets_enc = b"garbage"
        db.add(source)
        db.commit()
    with pytest.raises(tools.AdminError, match="nothing was changed"):
        tools.rotate_master_key(get_settings(), NEW_KEY.encode(), dry_run=False)
    assert _source_blob() == b"garbage"


def test_rotation_refuses_the_same_key(authed: TestClient) -> None:
    with pytest.raises(tools.AdminError, match="same"):
        tools.rotate_master_key(get_settings(), MASTER_KEY.encode(), dry_run=True)


def test_key_file_must_be_long_enough(tmp_path: Path) -> None:
    short = tmp_path / "k"
    short.write_text("too-short")
    with pytest.raises(tools.AdminError, match="at least"):
        tools.read_key_file(short)


# --- own-data backup, export, reset ------------------------------------------


def test_database_copy_is_consistent_and_private(authed: TestClient, tmp_path: Path) -> None:
    target = tools.backup_database(get_settings(), tmp_path / "copy.db")
    import sqlite3

    rows = sqlite3.connect(target).execute("SELECT username FROM users").fetchall()
    assert rows == [("admin",)]
    with pytest.raises(tools.AdminError, match="already exists"):
        tools.backup_database(get_settings(), target)


@pytest.mark.usefixtures("fake_drivers")
def test_config_export_has_no_secrets(authed: TestClient, tmp_path: Path) -> None:
    make_source(authed)
    with Session(get_engine()) as db:
        path = tools.write_config_export(db, tmp_path / "config.json")
    text = path.read_text()
    data = json.loads(text)
    assert data["sources"] and "secrets_enc" not in data["sources"][0]
    assert "secrets_enc" not in text and "heartbeat_secret_enc" not in text


def test_reset_password_changes_hash_and_signs_out(authed: TestClient) -> None:
    with Session(get_engine()) as db:
        before = db.exec(select(User)).one().session_version
        tools.reset_password(db, "Admin", "a recovered passphrase")
        user = db.exec(select(User)).one()
        assert user.session_version == before + 1
    assert authed.get("/api/auth/me").status_code == 401
    with pytest.raises(tools.AdminError), Session(get_engine()) as db:
        tools.reset_password(db, "nobody", "a recovered passphrase")


def test_cli_rotate_key_dry_run(
    authed: TestClient, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    key_file = tmp_path / "new.key"
    key_file.write_text(NEW_KEY)
    assert cli_main(["rotate-key", "--new-key-file", str(key_file), "--dry-run"]) == 0
    assert "Nothing was changed" in capsys.readouterr().out
    assert cli_main(["rotate-key", "--new-key-file", str(tmp_path / "missing")]) == 1


def test_wal_checkpoint_runs_on_a_live_database(authed: TestClient) -> None:
    from app.core.db import checkpoint_wal

    checkpoint_wal()  # must not raise or lock the database
    assert authed.get("/api/auth/me").status_code == 200

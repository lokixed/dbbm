from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import text
from sqlmodel import Session

from app.core.config import Settings, get_settings
from app.core.db import get_engine
from app.main import create_app


def test_healthz_ok(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["db"] == "ok"
    assert body["scheduler"]["state"] == "running"
    assert body["scheduler"]["owner"] is True


def test_healthz_reports_db_failure(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("db down")

    monkeypatch.setattr(Session, "connection", boom)
    response = client.get("/healthz")
    assert response.status_code == 503
    assert "db down" not in response.text


def test_sqlite_pragmas() -> None:
    with get_engine().connect() as conn:
        assert conn.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1
    assert get_settings().db_path.name == "app.db"


def test_startup_fails_without_master_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APP_SECRET_KEY")
    get_settings.cache_clear()
    with pytest.raises(ValidationError, match="No master key configured"):
        create_app()


def test_short_master_key_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_SECRET_KEY", "too-short")
    with pytest.raises(ValidationError, match="at least 32"):
        Settings()


def test_master_key_from_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    key_file = tmp_path / "key.txt"
    key_file.write_text("file-based-master-key-0123456789-abcdefgh\n")
    monkeypatch.delenv("APP_SECRET_KEY")
    monkeypatch.setenv("APP_SECRET_KEY_FILE", str(key_file))
    assert Settings().master_key == b"file-based-master-key-0123456789-abcdefgh"


def test_missing_key_file_is_a_clear_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APP_SECRET_KEY")
    monkeypatch.setenv("APP_SECRET_KEY_FILE", str(tmp_path / "missing.txt"))
    with pytest.raises(ValidationError, match="Cannot read APP_SECRET_KEY_FILE"):
        Settings()


def test_both_key_sources_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    key_file = tmp_path / "key.txt"
    key_file.write_text("x" * 40)
    monkeypatch.setenv("APP_SECRET_KEY_FILE", str(key_file))
    with pytest.raises(ValidationError, match="only one"):
        Settings()


def test_spa_fallback_and_api_404(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>spa</html>")
    (dist / "assets" / "app.js").write_text("console.log(1)")
    (tmp_path / "secret.txt").write_text("outside")
    monkeypatch.setenv("FRONTEND_DIST", str(dist))
    get_settings.cache_clear()

    with TestClient(create_app()) as client:
        assert "spa" in client.get("/dashboard").text
        assert "spa" in client.get("/").text
        assert client.get("/assets/app.js").text == "console.log(1)"
        assert client.get("/api/unknown").status_code == 404
        assert "spa" not in client.get("/api/unknown").text
        assert "outside" not in client.get("/..%2fsecret.txt").text
        assert client.get("/healthz").json()["status"] == "ok"

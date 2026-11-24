from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.db import reset_engine
from app.core.security import CSRF_COOKIE, CSRF_HEADER, get_rate_limiter
from app.main import create_app

MASTER_KEY = "test-master-key-0123456789-abcdefghijklmnop"
PASSWORD = "correct horse battery staple"

# Fixtures `fake_bins`, `fake_drivers` (run lifecycle), `fake_rclone` (Google Drive),
# `fake_google` (Firestore and Cloud Storage REST) and
# `outbound`, `fake_clock`, `m6_client` (scheduler, notifications).
pytest_plugins = ("tests.fakes", "tests.fake_rclone", "tests.fake_google", "tests.m6")


def _reset_caches() -> None:
    get_settings.cache_clear()
    get_rate_limiter.cache_clear()
    reset_engine()


@pytest.fixture(autouse=True)
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setenv("APP_SECRET_KEY", MASTER_KEY)
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.setenv("FRONTEND_DIST", str(tmp_path / "no-frontend"))
    monkeypatch.delenv("APP_SECRET_KEY_FILE", raising=False)
    _reset_caches()
    yield
    _reset_caches()


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as test_client:
        yield test_client


def csrf_headers(client: TestClient) -> dict[str, str]:
    token = client.cookies.get(CSRF_COOKIE)
    return {CSRF_HEADER: token} if token else {}


def api_post(client: TestClient, path: str, json: object = None) -> httpx.Response:
    response: httpx.Response = client.post(path, json=json, headers=csrf_headers(client))
    return response


def setup_admin(client: TestClient, username: str = "admin") -> None:
    client.get("/api/auth/status")
    response = api_post(client, "/api/auth/setup", {"username": username, "password": PASSWORD})
    assert response.status_code == 201, response.text


def login(client: TestClient, username: str = "admin", password: str = PASSWORD) -> httpx.Response:
    client.get("/api/auth/status")
    return api_post(client, "/api/auth/login", {"username": username, "password": password})


def api_put(client: TestClient, path: str, json: object = None) -> httpx.Response:
    response: httpx.Response = client.put(path, json=json, headers=csrf_headers(client))
    return response


def api_delete(client: TestClient, path: str) -> httpx.Response:
    response: httpx.Response = client.delete(path, headers=csrf_headers(client))
    return response


@pytest.fixture
def authed(client: TestClient) -> TestClient:
    setup_admin(client)
    assert login(client).status_code == 200
    return client

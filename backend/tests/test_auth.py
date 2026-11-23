import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.config import get_settings
from app.core.db import get_engine
from app.models import AuditLog, User
from tests.conftest import PASSWORD, api_post, login, setup_admin


def _audit_actions() -> list[str]:
    with Session(get_engine()) as db:
        rows = db.exec(select(AuditLog).order_by(AuditLog.timestamp)).all()  # type: ignore[arg-type]
        return [r.action for r in rows]


def test_status_reports_setup_required(client: TestClient) -> None:
    assert client.get("/api/auth/status").json() == {"setup_required": True}
    setup_admin(client)
    assert client.get("/api/auth/status").json() == {"setup_required": False}


def test_setup_works_once_then_409(client: TestClient) -> None:
    setup_admin(client)
    response = api_post(client, "/api/auth/setup", {"username": "second", "password": PASSWORD})
    assert response.status_code == 409
    with Session(get_engine()) as db:
        assert len(db.exec(select(User)).all()) == 1


def test_setup_validates_input_and_does_not_echo_password(client: TestClient) -> None:
    client.get("/api/auth/status")
    response = api_post(client, "/api/auth/setup", {"username": "admin", "password": "Zq9!x"})
    assert response.status_code == 422
    assert "Zq9!x" not in response.text
    assert "input" not in response.json()["detail"][0]


def test_password_is_stored_hashed(client: TestClient) -> None:
    setup_admin(client)
    with Session(get_engine()) as db:
        user = db.exec(select(User)).one()
    assert user.password_hash.startswith("$argon2")
    assert PASSWORD not in user.password_hash


def test_login_success_and_me(client: TestClient) -> None:
    setup_admin(client)
    response = login(client)
    assert response.status_code == 200
    assert response.json()["username"] == "admin"
    set_cookie = response.headers.get_list("set-cookie")
    session_cookie = next(c for c in set_cookie if c.startswith("dbbm_session="))
    assert "HttpOnly" in session_cookie
    assert "SameSite=lax" in session_cookie
    assert client.get("/api/auth/me").json()["username"] == "admin"


def test_username_is_case_insensitive(client: TestClient) -> None:
    setup_admin(client, "Admin")
    assert login(client, "ADMIN").status_code == 200


def test_login_failure_wrong_password_and_unknown_user(client: TestClient) -> None:
    setup_admin(client)
    assert login(client, password="wrong").status_code == 401
    assert login(client, username="nobody").status_code == 401
    assert client.get("/api/auth/me").status_code == 401


def test_secure_cookie_flag_follows_setting(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup_admin(client)
    monkeypatch.setenv("COOKIE_SECURE", "true")
    get_settings.cache_clear()
    response = login(client)
    cookies = response.headers.get_list("set-cookie")
    assert all("Secure" in c for c in cookies)


def test_me_requires_auth(client: TestClient) -> None:
    assert client.get("/api/auth/me").status_code == 401


def test_logout_ends_session(client: TestClient) -> None:
    setup_admin(client)
    login(client)
    assert api_post(client, "/api/auth/logout").status_code == 204
    assert client.get("/api/auth/me").status_code == 401


def test_tampered_session_cookie_is_rejected(client: TestClient) -> None:
    setup_admin(client)
    login(client)
    client.cookies.set("dbbm_session", "garbage.value.here")
    assert client.get("/api/auth/me").status_code == 401


def test_audit_entries_written(client: TestClient) -> None:
    setup_admin(client)
    login(client, password="wrong")
    login(client)
    api_post(client, "/api/auth/logout")
    assert _audit_actions() == [
        "auth.setup",
        "auth.login.failure",
        "auth.login.success",
        "auth.logout",
    ]


def test_openapi_docs_disabled_by_default(client: TestClient) -> None:
    assert client.get("/api/docs").status_code == 404
    assert client.get("/api/openapi.json").status_code == 404

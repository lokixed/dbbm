from fastapi.testclient import TestClient

from app.core.security import CSRF_COOKIE, CSRF_HEADER
from tests.conftest import PASSWORD, api_post, login, setup_admin


def test_post_without_csrf_header_is_rejected(client: TestClient) -> None:
    client.get("/api/auth/status")
    response = client.post("/api/auth/setup", json={"username": "admin", "password": PASSWORD})
    assert response.status_code == 403
    assert response.json()["detail"] == "csrf_failed"


def test_post_without_any_csrf_cookie_is_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": PASSWORD},
        headers={CSRF_HEADER: "forged.token"},
    )
    assert response.status_code == 403


def test_header_must_match_cookie(client: TestClient) -> None:
    client.get("/api/auth/status")
    other = TestClient(client.app)
    other.get("/api/auth/status")
    response = client.post(
        "/api/auth/setup",
        json={"username": "admin", "password": PASSWORD},
        headers={CSRF_HEADER: other.cookies.get(CSRF_COOKIE) or ""},
    )
    assert response.status_code == 403


def test_forged_matching_token_is_rejected(client: TestClient) -> None:
    client.cookies.set(CSRF_COOKIE, "attacker.deadbeef")
    response = client.post(
        "/api/auth/setup",
        json={"username": "admin", "password": PASSWORD},
        headers={CSRF_HEADER: "attacker.deadbeef"},
    )
    assert response.status_code == 403


def test_authenticated_post_needs_session_bound_token(client: TestClient) -> None:
    setup_admin(client)
    login(client)
    assert client.post("/api/auth/logout").status_code == 403
    assert api_post(client, "/api/auth/logout").status_code == 204


def test_anonymous_token_not_valid_for_authenticated_session(client: TestClient) -> None:
    setup_admin(client)
    anonymous = TestClient(client.app)
    anonymous.get("/api/auth/status")
    stale_token = anonymous.cookies.get(CSRF_COOKIE) or ""

    login(client)
    client.cookies.set(CSRF_COOKIE, stale_token)
    response = client.post("/api/auth/logout", headers={CSRF_HEADER: stale_token})
    assert response.status_code == 403


def test_get_requests_do_not_need_csrf(client: TestClient) -> None:
    assert client.get("/api/auth/status").status_code == 200
    assert client.get("/healthz").status_code == 200

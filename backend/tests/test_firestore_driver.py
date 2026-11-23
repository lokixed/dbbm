import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from google.oauth2 import service_account

from app.drivers.firestore import FirestoreDriver

TOKEN = "ya29.fake-access-token-value"

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PRIVATE_KEY_PEM = _KEY.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
).decode()

CONFIG: dict[str, Any] = {
    "project_id": "my-project-1",
    "database_id": "(default)",
    "staging_bucket": "gs://my-staging/backups",
    "collection_ids": None,
}
SA_JSON = json.dumps(
    {
        "type": "service_account",
        "client_email": "bk@my-project-1.iam.gserviceaccount.com",
        "private_key": PRIVATE_KEY_PEM,
        "private_key_id": "key-id-123",
        "project_id": "my-project-1",
        "token_uri": "https://attacker.example/token",
    }
)

Handler = Callable[[httpx.Request], httpx.Response]


def make_handler(
    database: int = 200, bucket: int = 200, token: Callable[[], httpx.Response] | None = None
) -> tuple[Handler, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        host = request.url.host
        if host == "oauth2.googleapis.com":
            if token:
                return token()
            return httpx.Response(200, json={"access_token": TOKEN, "expires_in": 3600})
        assert request.headers["Authorization"] == f"Bearer {TOKEN}"
        if host == "firestore.googleapis.com":
            return httpx.Response(database, json={"name": "x"})
        if host == "storage.googleapis.com":
            return httpx.Response(bucket, json={"kind": "storage#objects"})
        raise AssertionError(f"unexpected host {host}")

    return handler, seen


def driver(handler: Handler, sa_json: str = SA_JSON) -> FirestoreDriver:
    return FirestoreDriver(
        dict(CONFIG),
        {"service_account_json": sa_json},
        transport=httpx.MockTransport(handler),
    )


def test_both_checks_pass() -> None:
    handler, seen = make_handler()
    result = driver(handler).test_connection()
    assert result.ok, result.message
    assert [(c.name, c.ok) for c in result.checks] == [
        ("authentication", True),
        ("database", True),
        ("bucket", True),
    ]
    urls = [str(r.url) for r in seen]
    assert urls[0] == "https://oauth2.googleapis.com/token"  # token_uri in the file is ignored
    assert any(
        u.startswith(
            "https://firestore.googleapis.com/v1/projects/my-project-1/databases/(default)"
        )
        for u in urls
    )
    assert any(
        u.startswith("https://storage.googleapis.com/storage/v1/b/my-staging/o") for u in urls
    )


def test_reports_which_check_failed() -> None:
    handler, _ = make_handler(database=403)
    result = driver(handler).test_connection()
    by_name = {c.name: c for c in result.checks}
    assert not result.ok
    assert by_name["database"].ok is False and "permission denied" in by_name["database"].message
    assert by_name["bucket"].ok is True

    handler, _ = make_handler(bucket=404)
    result = driver(handler).test_connection()
    by_name = {c.name: c for c in result.checks}
    assert by_name["database"].ok is True
    assert by_name["bucket"].ok is False and "not found" in by_name["bucket"].message


def test_redirects_are_not_followed() -> None:
    handler, seen = make_handler(database=302)
    result = driver(handler).test_connection()
    assert not result.ok
    assert all(r.url.host != "evil.example" for r in seen)


def test_authentication_failure_skips_api_checks_and_leaks_nothing() -> None:
    def denied() -> httpx.Response:
        return httpx.Response(
            400, json={"error": "invalid_grant", "error_description": "Invalid JWT Signature."}
        )

    handler, seen = make_handler(token=denied)
    result = driver(handler).test_connection()
    assert not result.ok and result.message.startswith("Authentication failed")
    assert [c.ok for c in result.checks] == [False, False, False]
    assert {r.url.host for r in seen} == {"oauth2.googleapis.com"}
    blob = json.dumps(result.message) + json.dumps([c.message for c in result.checks])
    assert "PRIVATE KEY" not in blob and "key-id-123" not in blob


def test_network_error_is_reported_without_details() -> None:
    def boom(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused to https://oauth2.googleapis.com/token?x=1")

    result = driver(boom).test_connection()
    assert not result.ok and "https://" not in result.message and TOKEN not in result.message


def test_google_auth_is_used_with_pinned_token_uri(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class FakeCredentials:
        token: str | None = None

        def refresh(self, request: Any) -> None:
            self.token = TOKEN

    def fake_from_info(info: dict[str, Any], scopes: list[str]) -> FakeCredentials:
        captured["info"], captured["scopes"] = info, scopes
        return FakeCredentials()

    monkeypatch.setattr(
        service_account.Credentials, "from_service_account_info", staticmethod(fake_from_info)
    )
    handler, seen = make_handler()
    result = driver(handler).test_connection()
    assert result.ok
    assert captured["info"]["token_uri"] == "https://oauth2.googleapis.com/token"
    assert captured["scopes"] == ["https://www.googleapis.com/auth/cloud-platform"]
    assert all(r.url.host != "oauth2.googleapis.com" for r in seen)


def test_google_auth_exception_is_sanitized(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_from_info(info: dict[str, Any], scopes: list[str]) -> Any:
        raise ValueError(f"Could not deserialize key data {PRIVATE_KEY_PEM} bearer {TOKEN}")

    monkeypatch.setattr(
        service_account.Credentials, "from_service_account_info", staticmethod(fake_from_info)
    )
    handler, _ = make_handler()
    result = driver(handler).test_connection()
    assert not result.ok
    assert "PRIVATE KEY" not in result.message and TOKEN not in result.message

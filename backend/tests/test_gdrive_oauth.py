"""The "Connect Google Drive" flow: redirect URI, PKCE exchange, flow lifecycle, saving."""

import base64
import hashlib
import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core.db import get_engine
from app.core.logging import redact
from app.models import Destination
from app.services.gdrive_oauth import (
    FLOW_TTL,
    MAX_FLOWS_PER_SESSION,
    TOKEN_URL,
    OAuthFlowError,
    OAuthFlows,
    check_origin,
    redirect_uri_for,
    token_from_response,
)
from app.services.secrets import load_secrets
from tests.conftest import api_post, api_put, csrf_headers, setup_admin

ORIGIN = "https://dbbm.example.com"
REDIRECT_URI = f"{ORIGIN}/api/oauth/google/callback"
CLIENT_ID = "123-abc.apps.googleusercontent.com"
CLIENT_SECRET = "GOCSPX-client-secret-value"
CODE = "4/0AeanS0Authorization-Code"
REFRESH = "1//0gConnectedRefreshToken-abcdef"
GOOD_RESPONSE = {
    "access_token": "ya29.ConnectedAccess",
    "expires_in": 3599,
    "refresh_token": REFRESH,
    "scope": "https://www.googleapis.com/auth/drive.file",
    "token_type": "Bearer",
}

# ---------- pure helpers ----------


@pytest.mark.parametrize(
    "value", ["http://192.168.1.10:8080", "https://dbbm.example.com", "https://x.test/"]
)
def test_check_origin_accepts_bare_origins(value: str) -> None:
    assert check_origin(value) == value.rstrip("/")


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "null",
        "javascript:alert(1)",
        "ftp://host",
        "https://user:pw@host",
        "https://host/path",
        "https://host?x=1",
        "https://host#frag",
        "https://host:99999",
    ],
)
def test_check_origin_rejects_everything_else(value: str | None) -> None:
    with pytest.raises(ValueError):
        check_origin(value)


@pytest.mark.parametrize(
    "origin",
    [
        "https://dbbm.example.com",
        "https://dbbm.example.com:8443",
        "http://localhost:8080",
        "http://127.0.0.1:8080",
        "http://[::1]:8080",
        "http://dbbm.localhost",
    ],
)
def test_redirect_uri_is_the_installs_own_callback(origin: str) -> None:
    assert redirect_uri_for(origin) == f"{origin}/api/oauth/google/callback"


@pytest.mark.parametrize(
    "origin", [None, "http://192.168.1.10:8080", "http://dbbm.example.com", "https://h/path"]
)
def test_redirect_uri_needs_https_or_loopback(origin: str | None) -> None:
    with pytest.raises(ValueError):
        redirect_uri_for(origin)


def test_token_from_response_builds_rclone_token() -> None:
    now = datetime(2026, 11, 26, 10, 0, tzinfo=UTC)
    token = json.loads(token_from_response(GOOD_RESPONSE, "drive.file", now))
    assert token == {
        "access_token": "ya29.ConnectedAccess",
        "token_type": "Bearer",
        "refresh_token": REFRESH,
        "expiry": "2026-11-26T10:59:59Z",
    }


@pytest.mark.parametrize(
    ("over", "scope", "code"),
    [
        ({"refresh_token": None}, "drive.file", "no_refresh_token"),
        ({"scope": "openid"}, "drive.file", "scope_not_granted"),
        ({}, "drive", "scope_not_granted"),
        ({"expires_in": "soon"}, "drive.file", "exchange_failed"),
    ],
)
def test_token_from_response_refusals(over: dict[str, Any], scope: str, code: str) -> None:
    data = {k: v for k, v in {**GOOD_RESPONSE, **over}.items() if v is not None}
    with pytest.raises(OAuthFlowError) as info:
        token_from_response(data, scope, datetime.now(UTC))
    assert info.value.code == code


def test_flows_expire_are_single_use_and_session_bound() -> None:
    clock = [0.0]
    flows = OAuthFlows(monotonic=lambda: clock[0])
    flow, _ = flows.start(
        client_id=CLIENT_ID, client_secret="s", scope="drive", redirect_uri=REDIRECT_URI, sid="a"
    )
    assert flows.get(flow.id, "b") is None
    with pytest.raises(OAuthFlowError) as other:
        flows.claim(flow.nonce, "b")
    assert other.value.code == "not_signed_in"
    assert flows.claim(flow.nonce, "a") is flow
    with pytest.raises(OAuthFlowError):
        flows.claim(flow.nonce, "a")  # already claimed
    clock[0] = FLOW_TTL + 1
    assert flows.get(flow.id, "a") is None


def test_flows_per_session_are_capped() -> None:
    flows = OAuthFlows()
    args = {"client_id": CLIENT_ID, "client_secret": "s", "scope": "drive"}
    ids = [
        flows.start(**args, redirect_uri=REDIRECT_URI, sid="a")[0].id
        for _ in range(MAX_FLOWS_PER_SESSION + 2)
    ]
    alive = [i for i in ids if flows.get(i, "a") is not None]
    assert alive == ids[-MAX_FLOWS_PER_SESSION:]


def test_oauth_code_is_redacted_from_logs() -> None:
    line = f'"GET /api/oauth/google/callback?state=abc&code={CODE}&scope=x HTTP/1.1" 303'
    out = redact(line)
    assert CODE not in out and "state=abc" in out


# ---------- API ----------

Handler = Callable[[httpx.Request], httpx.Response]


class FakeGoogle(list[httpx.Request]):
    """Google's token endpoint: records requests, answers GOOD_RESPONSE unless `respond` changes."""

    def __init__(self) -> None:
        super().__init__()
        self.respond: Handler = lambda _r: httpx.Response(200, json=GOOD_RESPONSE)

    def handle(self, request: httpx.Request) -> httpx.Response:
        assert str(request.url) == TOKEN_URL
        self.append(request)
        return self.respond(request)


@pytest.fixture
def google(authed: TestClient) -> FakeGoogle:
    fake = FakeGoogle()
    flows: OAuthFlows = authed.app.state.gdrive_oauth  # type: ignore[attr-defined]
    flows.transport = httpx.MockTransport(fake.handle)
    return fake


def start(client: TestClient, **over: Any) -> httpx.Response:
    body = {"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET, "scope": "drive.file", **over}
    headers = {**csrf_headers(client), "Origin": ORIGIN}
    response: httpx.Response = client.post(
        "/api/destinations/gdrive/oauth/start", json=body, headers=headers
    )
    return response


def callback(client: TestClient, authorize_url: str, **query: str) -> httpx.Response:
    state = parse_qs(urlsplit(authorize_url).query)["state"][0]
    params = {"state": state, **({"code": CODE} if not query else {}), **query}
    response: httpx.Response = client.get(
        "/api/oauth/google/callback", params=params, follow_redirects=False
    )
    return response


def connect(client: TestClient) -> str:
    started = start(client)
    assert started.status_code == 200, started.text
    response = callback(client, started.json()["authorize_url"])
    assert response.status_code == 303
    flow_id: str = started.json()["flow_id"]
    assert response.headers["location"] == f"/oauth/google/done?flow={flow_id}"
    return flow_id


def drive_body(**over: Any) -> dict[str, Any]:
    return {
        "type": "gdrive",
        "name": "drive1",
        "config": {"remote_folder": "db-backups", "client_id": CLIENT_ID},
        **over,
    }


def test_start_builds_google_url(google: FakeGoogle, authed: TestClient) -> None:
    response = start(authed)
    assert response.status_code == 200, response.text
    url = urlsplit(response.json()["authorize_url"])
    query = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert (
        f"{url.scheme}://{url.netloc}{url.path}" == "https://accounts.google.com/o/oauth2/v2/auth"
    )
    assert query["client_id"] == CLIENT_ID
    assert query["redirect_uri"] == REDIRECT_URI
    assert query["scope"] == "https://www.googleapis.com/auth/drive.file"
    assert query["access_type"] == "offline" and query["prompt"] == "consent"
    assert query["code_challenge_method"] == "S256"
    assert len(query["state"]) >= 32  # a random nonce, nothing else
    assert CLIENT_SECRET not in response.text


def test_start_needs_origin_and_secret(authed: TestClient) -> None:
    no_origin = authed.post(
        "/api/destinations/gdrive/oauth/start",
        json={"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET},
        headers=csrf_headers(authed),
    )
    assert no_origin.status_code == 400
    plain_http = authed.post(
        "/api/destinations/gdrive/oauth/start",
        json={"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET},
        headers={**csrf_headers(authed), "Origin": "http://192.168.1.10:8080"},
    )
    assert plain_http.status_code == 400 and "https" in plain_http.json()["detail"]
    assert start(authed, client_secret=None).status_code == 422
    assert start(authed, client_id="bad id!").status_code == 422


def test_start_requires_login(client: TestClient) -> None:
    setup_admin(client)
    client.post("/api/auth/logout", headers=csrf_headers(client))
    assert start(client).status_code in (401, 403)


def test_connect_and_create(google: FakeGoogle, authed: TestClient) -> None:
    flow_id = connect(authed)
    form = parse_qs(google[0].content.decode())
    assert form["code"] == [CODE] and form["client_secret"] == [CLIENT_SECRET]
    assert form["redirect_uri"] == [REDIRECT_URI]
    assert len(form["code_verifier"][0]) >= 43

    status = authed.get(f"/api/destinations/gdrive/oauth/{flow_id}")
    assert status.json() == {"status": "connected", "error": None, "message": None}
    assert REFRESH not in status.text

    created = api_post(authed, "/api/destinations", drive_body(token_flow_id=flow_id))
    assert created.status_code == 201, created.text
    assert created.json()["secrets_set"] == {"token": True, "client_secret": True}
    with _db() as db:
        row = db.get(Destination, uuid.UUID(created.json()["id"]))
        assert row is not None
        stored = load_secrets(row.secrets_enc)
    assert json.loads(stored["token"])["refresh_token"] == REFRESH
    assert stored["client_secret"] == CLIENT_SECRET
    # The flow is used up once saved.
    assert authed.get(f"/api/destinations/gdrive/oauth/{flow_id}").status_code == 404


def _db() -> Session:
    return Session(get_engine())


def test_reconnect_existing_destination_with_stored_secret(
    google: FakeGoogle, authed: TestClient
) -> None:
    first = connect(authed)
    created = api_post(authed, "/api/destinations", drive_body(token_flow_id=first)).json()
    started = start(authed, client_secret=None, destination_id=created["id"])
    assert started.status_code == 200, started.text
    assert callback(authed, started.json()["authorize_url"]).status_code == 303
    assert parse_qs(google[-1].content.decode())["client_secret"] == [CLIENT_SECRET]
    updated = api_put(
        authed,
        f"/api/destinations/{created['id']}",
        drive_body(token_flow_id=started.json()["flow_id"]),
    )
    assert updated.status_code == 200, updated.text


def test_stored_secret_needs_same_client_id(google: FakeGoogle, authed: TestClient) -> None:
    created = api_post(authed, "/api/destinations", drive_body(token_flow_id=connect(authed)))
    other = start(
        authed,
        client_id="999-other.apps.googleusercontent.com",
        client_secret=None,
        destination_id=created.json()["id"],
    )
    assert other.status_code == 422


def test_callback_is_single_use(google: FakeGoogle, authed: TestClient) -> None:
    started = start(authed)
    url = started.json()["authorize_url"]
    assert callback(authed, url).headers["location"].endswith(started.json()["flow_id"])
    replay = callback(authed, url)
    assert replay.headers["location"] == "/oauth/google/done?error=expired"
    assert len(google) == 1


def test_callback_access_denied(google: FakeGoogle, authed: TestClient) -> None:
    started = start(authed)
    flow_id = started.json()["flow_id"]
    response = callback(authed, started.json()["authorize_url"], error="access_denied")
    assert response.headers["location"] == f"/oauth/google/done?flow={flow_id}&error=access_denied"
    status = authed.get(f"/api/destinations/gdrive/oauth/{flow_id}").json()
    assert status["status"] == "failed" and status["error"] == "access_denied"
    assert google == []


@pytest.mark.parametrize(
    ("respond", "code"),
    [
        (lambda _r: httpx.Response(400, json={"error": "invalid_grant"}), "exchange_failed"),
        (
            lambda _r: httpx.Response(200, json={**GOOD_RESPONSE, "refresh_token": ""}),
            "no_refresh_token",
        ),
        (
            lambda _r: httpx.Response(200, json={**GOOD_RESPONSE, "scope": "email"}),
            "scope_not_granted",
        ),
    ],
)
def test_callback_exchange_failures(
    google: FakeGoogle, authed: TestClient, respond: Handler, code: str
) -> None:
    google.respond = respond
    started = start(authed)
    response = callback(authed, started.json()["authorize_url"])
    assert response.headers["location"].endswith(f"&error={code}")
    saved = api_post(
        authed, "/api/destinations", drive_body(token_flow_id=started.json()["flow_id"])
    )
    assert saved.status_code == 422


def test_callback_unreachable_google(google: FakeGoogle, authed: TestClient) -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    google.respond = boom
    started = start(authed)
    response = callback(authed, started.json()["authorize_url"])
    assert response.headers["location"].endswith("&error=unreachable")


def test_callback_without_session(google: FakeGoogle, authed: TestClient) -> None:
    started = start(authed)
    authed.cookies.clear()
    response = callback(authed, started.json()["authorize_url"])
    assert response.headers["location"] == "/oauth/google/done?error=not_signed_in"
    assert google == []


def test_flow_is_private_to_its_session(google: FakeGoogle, authed: TestClient) -> None:
    flow_id = connect(authed)
    with TestClient(authed.app) as other:
        other.get("/api/auth/status")
        api_post(
            other,
            "/api/auth/login",
            {"username": "admin", "password": "correct horse battery staple"},
        )
        assert other.get(f"/api/destinations/gdrive/oauth/{flow_id}").status_code == 404
        refused = api_post(other, "/api/destinations", drive_body(token_flow_id=flow_id))
        assert refused.status_code == 422


def test_token_flow_rules_on_save(google: FakeGoogle, authed: TestClient) -> None:
    flow_id = connect(authed)
    both = drive_body(token_flow_id=flow_id, secrets={"token": json.dumps(GOOD_RESPONSE)})
    assert api_post(authed, "/api/destinations", both).status_code == 422
    other_scope = drive_body(token_flow_id=flow_id)
    other_scope["config"]["scope"] = "drive"
    assert api_post(authed, "/api/destinations", other_scope).status_code == 422
    unknown = api_post(authed, "/api/destinations", drive_body(token_flow_id="nope"))
    assert unknown.status_code == 422
    # A failed save leaves the flow usable.
    assert (
        api_post(authed, "/api/destinations", drive_body(token_flow_id=flow_id)).status_code == 201
    )


def test_unsaved_test_can_use_flow_without_consuming_it(
    google: FakeGoogle, authed: TestClient, fake_rclone: Any
) -> None:
    flow_id = connect(authed)
    tested = api_post(authed, "/api/destinations/test", drive_body(token_flow_id=flow_id))
    assert tested.status_code == 200, tested.text
    assert authed.get(f"/api/destinations/gdrive/oauth/{flow_id}").json()["status"] == "connected"


def test_pkce_challenge_matches_verifier(google: FakeGoogle, authed: TestClient) -> None:
    started = start(authed)
    challenge = parse_qs(urlsplit(started.json()["authorize_url"]).query)["code_challenge"][0]
    callback(authed, started.json()["authorize_url"])
    verifier = parse_qs(google[0].content.decode())["code_verifier"][0]
    digest = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=")
    assert digest.decode() == challenge

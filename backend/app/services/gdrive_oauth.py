"""Google Drive "Connect" flow: OAuth code + PKCE through a static redirect relay page.

Google only accepts public https redirect URIs, so the relay page (GDRIVE_OAUTH_REDIRECT_URL)
is the registered redirect URI and forwards the browser to this install. The origin to return
to travels in `state`. Pending flows and the tokens they produce live in memory only (single
process, docs/DESIGN.md section 3) and expire after FLOW_TTL seconds.
"""

import base64
import hashlib
import json
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal
from urllib.parse import urlencode, urlsplit

import httpx

from app.services.rclone_token import normalize_token

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105  (an endpoint, not a secret)
FLOW_TTL = 600.0
MAX_FLOWS_PER_SESSION = 5
EXCHANGE_TIMEOUT = 20.0
SCOPE_URLS = {
    "drive.file": "https://www.googleapis.com/auth/drive.file",
    "drive": "https://www.googleapis.com/auth/drive",
}

Status = Literal["pending", "connected", "failed"]

# Error codes shown to the user (never Google's raw response, which may echo input).
ERROR_MESSAGES = {
    "access_denied": "Access was denied on the Google consent screen.",
    "expired": "The connect request expired or was already used. Click Connect again.",
    "not_signed_in": "Sign in to DBBM in this browser, then click Connect again.",
    "exchange_failed": "Google refused the authorization code. Check the client ID and secret.",
    "unreachable": "Could not reach Google to finish connecting. Try again.",
    "no_refresh_token": "Google returned no refresh token. Remove DBBM's access in your Google "
    "account settings, then connect again.",
    "scope_not_granted": "Google Drive access was not granted. Tick the Drive permission on "
    "the consent screen.",
    "google_error": "Google reported an error. Try again.",
}


class OAuthFlowError(Exception):
    """A flow failed; `code` is a key of ERROR_MESSAGES."""

    def __init__(self, code: str) -> None:
        super().__init__(ERROR_MESSAGES.get(code, ERROR_MESSAGES["google_error"]))
        self.code = code if code in ERROR_MESSAGES else "google_error"


@dataclass
class Flow:
    id: str
    nonce: str
    verifier: str
    client_id: str
    client_secret: str
    scope: str
    sid: str
    created: float
    status: Status = "pending"
    error: str | None = None
    token: str | None = field(default=None, repr=False)
    exchanging: bool = False


def check_origin(value: str | None) -> str:
    """A bare http(s) origin: scheme, host, optional port; nothing else."""
    if not value:
        raise ValueError("The request has no Origin header.")
    parts = urlsplit(value)
    if (
        parts.scheme not in ("http", "https")
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.path not in ("", "/")
        or parts.query
        or parts.fragment
    ):
        raise ValueError("The request Origin is not a plain http(s) origin.")
    try:
        parts.port  # noqa: B018  (raises ValueError on a bad port)
    except ValueError:
        raise ValueError("The request Origin has an invalid port.") from None
    return f"{parts.scheme}://{parts.netloc}"


def encode_state(origin: str, nonce: str) -> str:
    raw = json.dumps({"v": 1, "o": origin, "n": nonce}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def decode_state_nonce(state: str) -> str:
    try:
        data = json.loads(base64.urlsafe_b64decode(state + "=" * (-len(state) % 4)))
    except (ValueError, TypeError):
        raise OAuthFlowError("expired") from None
    nonce = data.get("n") if isinstance(data, dict) else None
    if not isinstance(nonce, str) or not nonce:
        raise OAuthFlowError("expired")
    return nonce


def _challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def token_from_response(data: object, scope: str, now: datetime) -> str:
    """Google's token response as rclone token JSON, validated by normalize_token."""
    if not isinstance(data, dict):
        raise OAuthFlowError("exchange_failed")
    access, refresh = data.get("access_token"), data.get("refresh_token")
    expires_in = data.get("expires_in")
    if not isinstance(refresh, str) or not refresh:
        raise OAuthFlowError("no_refresh_token")
    if not isinstance(access, str) or not isinstance(expires_in, int):
        raise OAuthFlowError("exchange_failed")
    granted = str(data.get("scope", "")).split()
    if SCOPE_URLS[scope] not in granted:
        raise OAuthFlowError("scope_not_granted")
    expiry = (now + timedelta(seconds=expires_in)).strftime("%Y-%m-%dT%H:%M:%SZ")
    token = {
        "access_token": access,
        "token_type": data.get("token_type") or "Bearer",
        "refresh_token": refresh,
        "expiry": expiry,
    }
    try:
        return normalize_token(json.dumps(token))
    except ValueError:
        raise OAuthFlowError("exchange_failed") from None


class OAuthFlows:
    """Pending and finished connect flows, keyed by id. Thread-safe: sync routes use it too."""

    def __init__(
        self,
        redirect_url: str,
        transport: httpx.AsyncBaseTransport | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.redirect_url = redirect_url
        self.transport = transport
        self._monotonic = monotonic
        self._lock = threading.Lock()
        self._flows: dict[str, Flow] = {}

    # ---------- bookkeeping ----------

    def _sweep(self) -> None:
        cutoff = self._monotonic() - FLOW_TTL
        for key in [k for k, f in self._flows.items() if f.created < cutoff]:
            del self._flows[key]

    def _trim_session(self, sid: str) -> None:
        mine = sorted((f for f in self._flows.values() if f.sid == sid), key=lambda f: f.created)
        for old in mine[: max(0, len(mine) - MAX_FLOWS_PER_SESSION + 1)]:
            del self._flows[old.id]

    def get(self, flow_id: str, sid: str) -> Flow | None:
        with self._lock:
            self._sweep()
            flow = self._flows.get(flow_id)
            return flow if flow is not None and flow.sid == sid else None

    def discard(self, flow_id: str) -> None:
        with self._lock:
            self._flows.pop(flow_id, None)

    # ---------- start ----------

    def start(
        self, *, client_id: str, client_secret: str, scope: str, origin: str, sid: str
    ) -> tuple[Flow, str]:
        flow = Flow(
            id=secrets.token_urlsafe(16),
            nonce=secrets.token_urlsafe(24),
            verifier=secrets.token_urlsafe(48),
            client_id=client_id,
            client_secret=client_secret,
            scope=scope,
            sid=sid,
            created=self._monotonic(),
        )
        with self._lock:
            self._sweep()
            self._trim_session(sid)
            self._flows[flow.id] = flow
        query = {
            "client_id": client_id,
            "redirect_uri": self.redirect_url,
            "response_type": "code",
            "scope": SCOPE_URLS[scope],
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "false",
            "code_challenge": _challenge(flow.verifier),
            "code_challenge_method": "S256",
            "state": encode_state(origin, flow.nonce),
        }
        return flow, f"{AUTHORIZE_URL}?{urlencode(query)}"

    # ---------- callback ----------

    def claim(self, nonce: str, sid: str) -> Flow:
        """The pending flow for a callback; a flow can be claimed once."""
        with self._lock:
            self._sweep()
            flow = next((f for f in self._flows.values() if f.nonce == nonce), None)
            if flow is None or flow.status != "pending" or flow.exchanging:
                raise OAuthFlowError("expired")
            if flow.sid != sid:
                raise OAuthFlowError("not_signed_in")
            flow.exchanging = True
            return flow

    def fail(self, flow: Flow, code: str) -> None:
        with self._lock:
            flow.status, flow.error, flow.exchanging = "failed", code, False

    async def complete(self, flow: Flow, code: str) -> None:
        try:
            token = await self._exchange(flow, code)
        except OAuthFlowError as exc:
            self.fail(flow, exc.code)
            raise
        with self._lock:
            flow.status, flow.token, flow.exchanging = "connected", token, False

    async def _exchange(self, flow: Flow, code: str) -> str:
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": flow.client_id,
            "client_secret": flow.client_secret,
            "code_verifier": flow.verifier,
            "redirect_uri": self.redirect_url,
        }
        try:
            async with httpx.AsyncClient(
                timeout=EXCHANGE_TIMEOUT, transport=self.transport, follow_redirects=False
            ) as client:
                response = await client.post(TOKEN_URL, data=form)
        except httpx.HTTPError:
            raise OAuthFlowError("unreachable") from None
        if response.status_code != 200:
            raise OAuthFlowError("exchange_failed")
        try:
            data = response.json()
        except ValueError:
            raise OAuthFlowError("exchange_failed") from None
        return token_from_response(data, flow.scope, datetime.now(UTC))

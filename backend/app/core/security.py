import hashlib
import hmac
import math
import secrets
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Request, Response
from itsdangerous import BadSignature, URLSafeTimedSerializer

from app.core.config import Settings, get_settings
from app.core.crypto import derive_key

SESSION_COOKIE = "dbbackup_session"
CSRF_COOKIE = "dbbackup_csrf"
CSRF_HEADER = "X-CSRF-Token"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

_hasher = PasswordHasher()
_DUMMY_HASH = _hasher.hash("dbbackup-timing-equalizer")


# --- passwords ---------------------------------------------------------------


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def burn_password_check(password: str) -> None:
    """Spend the same time as a real check so unknown users are not detectable by timing."""
    verify_password(_DUMMY_HASH, password)


# --- session cookie ----------------------------------------------------------


@dataclass(frozen=True)
class SessionData:
    user_id: uuid.UUID
    session_version: int
    sid: str


def _serializer(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(
        derive_key(settings.master_key, b"session"), salt="dbbackup.session"
    )


def create_session_token(
    settings: Settings, user_id: uuid.UUID, session_version: int
) -> tuple[str, SessionData]:
    data = SessionData(user_id, session_version, secrets.token_urlsafe(16))
    token = _serializer(settings).dumps(
        {"uid": str(data.user_id), "sv": data.session_version, "sid": data.sid}
    )
    return token, data


def read_session(settings: Settings, token: str | None) -> SessionData | None:
    if not token:
        return None
    try:
        payload = _serializer(settings).loads(token, max_age=settings.session_ttl_seconds)
        return SessionData(uuid.UUID(payload["uid"]), int(payload["sv"]), str(payload["sid"]))
    except (BadSignature, KeyError, ValueError, TypeError):
        return None


# --- signed download links ---------------------------------------------------

DOWNLOAD_TTL_SECONDS = 60


def _download_serializer(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(
        derive_key(settings.master_key, b"download"), salt="dbbackup.download"
    )


def create_download_token(settings: Settings, artifact_id: uuid.UUID, username: str) -> str:
    """A short-lived token that lets one plain GET fetch one backup."""
    return _download_serializer(settings).dumps(
        {"aid": str(artifact_id), "u": username, "n": secrets.token_urlsafe(12)}
    )


@dataclass(frozen=True)
class DownloadGrant:
    artifact_id: uuid.UUID
    username: str
    nonce: str


def read_download_token(settings: Settings, token: str) -> DownloadGrant | None:
    try:
        payload = _download_serializer(settings).loads(token, max_age=DOWNLOAD_TTL_SECONDS)
        return DownloadGrant(uuid.UUID(payload["aid"]), str(payload["u"]), str(payload["n"]))
    except (BadSignature, KeyError, ValueError, TypeError):
        return None


class UsedTokens:
    """Remembers redeemed download tokens until they expire, so each link works once."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._seen: dict[str, float] = {}

    def redeem(self, nonce: str) -> bool:
        now = self._clock()
        with self._lock:
            self._seen = {n: t for n, t in self._seen.items() if now - t < DOWNLOAD_TTL_SECONDS}
            if nonce in self._seen:
                return False
            self._seen[nonce] = now
            return True


used_download_tokens = UsedTokens()


# --- CSRF (double-submit token, HMAC-signed) ---------------------------------


def _csrf_signature(settings: Settings, nonce: str) -> str:
    key = derive_key(settings.master_key, b"csrf")
    return hmac.new(key, nonce.encode(), hashlib.sha256).hexdigest()


def make_csrf_token(settings: Settings, nonce: str) -> str:
    return f"{nonce}.{_csrf_signature(settings, nonce)}"


def new_anonymous_csrf_token(settings: Settings) -> str:
    return make_csrf_token(settings, secrets.token_urlsafe(16))


def _csrf_nonce(settings: Settings, token: str) -> str | None:
    nonce, _, signature = token.partition(".")
    if not nonce or not hmac.compare_digest(signature, _csrf_signature(settings, nonce)):
        return None
    return nonce


def validate_csrf(request: Request, settings: Settings, session: SessionData | None) -> bool:
    header = request.headers.get(CSRF_HEADER)
    cookie = request.cookies.get(CSRF_COOKIE)
    if not header or not cookie or not hmac.compare_digest(header, cookie):
        return False
    nonce = _csrf_nonce(settings, header)
    if nonce is None:
        return False
    return session is None or hmac.compare_digest(nonce, session.sid)


def ensure_csrf_cookie(
    request: Request, response: Response, settings: Settings, session: SessionData | None
) -> None:
    """Issue a CSRF cookie if the current one is missing or does not match the session."""
    current = request.cookies.get(CSRF_COOKIE)
    nonce = _csrf_nonce(settings, current) if current else None
    if nonce is not None and (session is None or hmac.compare_digest(nonce, session.sid)):
        return
    token = (
        make_csrf_token(settings, session.sid) if session else new_anonymous_csrf_token(settings)
    )
    set_csrf_cookie(response, settings, token)


# --- cookie helpers ----------------------------------------------------------


def set_csrf_cookie(response: Response, settings: Settings, token: str) -> None:
    response.set_cookie(
        CSRF_COOKIE,
        token,
        max_age=settings.session_ttl_seconds,
        httponly=False,
        samesite="lax",
        secure=settings.cookie_secure,
        path="/",
    )


def set_session_cookies(
    response: Response, settings: Settings, session_token: str, data: SessionData
) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        session_token,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path="/",
    )
    set_csrf_cookie(response, settings, make_csrf_token(settings, data.sid))


def clear_session_cookies(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        SESSION_COOKIE, path="/", httponly=True, samesite="lax", secure=settings.cookie_secure
    )
    response.delete_cookie(CSRF_COOKIE, path="/", samesite="lax", secure=settings.cookie_secure)


# --- login rate limiting -----------------------------------------------------

_PRUNE_THRESHOLD = 1000
_IP_LIMIT_FACTOR = 4


class LoginRateLimiter:
    """In-memory failure counter with temporary lockout; valid for a single worker process."""

    def __init__(
        self,
        max_attempts: int,
        lockout_seconds: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max_attempts = max_attempts
        self._lockout = lockout_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._failures: dict[str, list[float]] = {}
        self._locked_until: dict[str, float] = {}

    def _buckets(self, ip: str, username: str) -> list[tuple[str, int]]:
        # Per (ip, username) stops guessing one account; a wider per-IP bucket stops spraying.
        return [
            (f"pair:{ip}:{username}", self._max_attempts),
            (f"ip:{ip}", self._max_attempts * _IP_LIMIT_FACTOR),
        ]

    def retry_after(self, ip: str, username: str) -> int:
        """Seconds until another attempt is allowed; 0 when not locked."""
        now = self._clock()
        with self._lock:
            remaining = 0.0
            for key, _ in self._buckets(ip, username):
                until = self._locked_until.get(key)
                if until is None:
                    continue
                if until <= now:
                    del self._locked_until[key]
                else:
                    remaining = max(remaining, until - now)
        return math.ceil(remaining)

    def record_failure(self, ip: str, username: str) -> None:
        now = self._clock()
        with self._lock:
            self._prune(now)
            for key, limit in self._buckets(ip, username):
                recent = [t for t in self._failures.get(key, []) if now - t < self._lockout]
                recent.append(now)
                if len(recent) >= limit:
                    self._locked_until[key] = now + self._lockout
                    recent = []
                self._failures[key] = recent

    def record_success(self, ip: str, username: str) -> None:
        with self._lock:
            key = self._buckets(ip, username)[0][0]
            self._failures.pop(key, None)
            self._locked_until.pop(key, None)

    def _prune(self, now: float) -> None:
        if len(self._failures) + len(self._locked_until) < _PRUNE_THRESHOLD:
            return
        self._locked_until = {k: v for k, v in self._locked_until.items() if v > now}
        self._failures = {
            k: kept
            for k, times in self._failures.items()
            if (kept := [t for t in times if now - t < self._lockout])
        }


@lru_cache
def get_rate_limiter() -> LoginRateLimiter:
    settings = get_settings()
    return LoginRateLimiter(settings.login_max_attempts, settings.login_lockout_seconds)

import json
import logging
import re
import sys
import threading
from collections.abc import Iterable
from urllib.parse import urlsplit

REDACTED = "[REDACTED]"
_SENSITIVE_PAIR = re.compile(
    r"""(?ix)
    (\b(?:password|passwd|secret|token|api[_-]?key|authorization|cookie|uri|dsn)\w*
    ["']?\s*[:=]\s*["']?)
    [^\s,;"'}&]+
    """
)
_HANDLER_MARK = "_dbbm_handler"
_UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")

_MONGO_USERINFO = re.compile(r"(?i)(mongodb(?:\+srv)?://)[^/@\s]+@")
_PEM_BLOCK = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)", re.DOTALL
)
# Quoted values may hold escaped quotes and "\n", so they are matched whole, not up to a delimiter.
_QUOTED = r'"(?:\\.|[^"\\])*"|\'[^\']*\''
_KEY_FIELDS = re.compile(
    rf'(?i)("?\b(?:private_key(?:_id)?|client_email|client_secret|access_token|refresh_token'
    rf'|id_token|token)"?\s*[:=]\s*)(?:{_QUOTED}|[^\s,;}}]+)'
)
_YAML_SECRET_LINE = re.compile(rf"(?im)^(\s*(?:password|uri)\s*:\s*)(?:{_QUOTED}|\S+)")
# rclone config lines hold the whole token JSON: `token = {"access_token": ...}`.
_CONFIG_TOKEN_LINE = re.compile(r"(?im)^(\s*(?:token|client_secret)\s*=\s*).*$")
_AUTH_HEADER = re.compile(r"(?i)\b(authorization\s*[:=]\s*)(?:(?:bearer|basic)\s+)?[^\s,;\"']+")
_ACCESS_TOKEN = re.compile(r"\bya29\.[\w.-]+")
_REFRESH_TOKEN = re.compile(r"\b1//0[\w-]{10,}")
_SLACK_HOOK = re.compile(r"(?i)(hooks\.slack\.com/services/)[^\s\"'<>]+")
_SMTP_AUTH = re.compile(r"(?i)\b(AUTH\s+(?:PLAIN|LOGIN|CRAM-MD5|XOAUTH2))\s+[^\s\"'\\]+")
_SIGNATURE = re.compile(r"(?i)\b(x-signature-256\"?\s*[:=]\s*\"?)[^\s,;\"'}]+")


class _SecretRegistry:
    """Secrets known at runtime (channel URLs, ping URLs, header values), keyed by owner."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._values: dict[str, frozenset[str]] = {}
        self._hosts: dict[str, frozenset[str]] = {}
        self._snapshot: tuple[tuple[str, ...], re.Pattern[str] | None] = ((), None)

    def set(self, owner: str, values: Iterable[str] = (), urls: Iterable[str] = ()) -> None:
        plain = {v for v in values if v and len(v) >= 4}
        hosts: set[str] = set()
        for url in urls:
            if not url:
                continue
            plain.add(url)
            host = urlsplit(url).hostname
            if host:
                hosts.add(host.lower())
        with self._lock:
            self._values[owner] = frozenset(plain)
            self._hosts[owner] = frozenset(hosts)
            self._rebuild()

    def remove(self, owner: str) -> None:
        with self._lock:
            self._values.pop(owner, None)
            self._hosts.pop(owner, None)
            self._rebuild()

    def clear(self) -> None:
        with self._lock:
            self._values.clear()
            self._hosts.clear()
            self._rebuild()

    def _rebuild(self) -> None:
        values = sorted({v for s in self._values.values() for v in s}, key=len, reverse=True)
        hosts = sorted({h for s in self._hosts.values() for h in s})
        pattern = None
        if hosts:
            alternatives = "|".join(re.escape(h) for h in hosts)
            pattern = re.compile(rf"(?i)((?:https?://)?(?:{alternatives})(?::\d+)?)/[^\s\"'<>]*")
        self._snapshot = (tuple(values), pattern)

    def apply(self, text: str) -> str:
        values, pattern = self._snapshot
        for value in values:
            if value in text:
                masked = _mask_url(value)
                text = text.replace(value, masked)
        if pattern is not None:
            text = pattern.sub(lambda m: f"{m.group(1)}/{REDACTED}", text)
        return text


def _mask_url(value: str) -> str:
    """Keep scheme and host of a URL so messages stay useful; anything else is replaced."""
    parts = urlsplit(value)
    if parts.scheme in ("http", "https") and parts.hostname:
        return f"{parts.scheme}://{parts.hostname}/{REDACTED}"
    return REDACTED


SECRET_REGISTRY = _SecretRegistry()


def redact(text: str, secrets: Iterable[str] = ()) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, REDACTED)
    text = SECRET_REGISTRY.apply(text)
    text = _SLACK_HOOK.sub(lambda m: m.group(1) + REDACTED, text)
    text = _SMTP_AUTH.sub(lambda m: f"{m.group(1)} {REDACTED}", text)
    text = _SIGNATURE.sub(lambda m: m.group(1) + REDACTED, text)
    text = _PEM_BLOCK.sub(REDACTED, text)
    text = _CONFIG_TOKEN_LINE.sub(lambda m: m.group(1) + REDACTED, text)
    text = _KEY_FIELDS.sub(lambda m: m.group(1) + f'"{REDACTED}"', text)
    text = _YAML_SECRET_LINE.sub(lambda m: m.group(1) + f'"{REDACTED}"', text)
    text = _MONGO_USERINFO.sub(lambda m: f"{m.group(1)}{REDACTED}@", text)
    text = _AUTH_HEADER.sub(lambda m: m.group(1) + REDACTED, text)
    text = _ACCESS_TOKEN.sub(REDACTED, text)
    text = _REFRESH_TOKEN.sub(REDACTED, text)
    return _SENSITIVE_PAIR.sub(lambda m: m.group(1) + REDACTED, text)


class JsonRedactingFormatter(logging.Formatter):
    def __init__(self, secrets: Iterable[str] = ()) -> None:
        super().__init__()
        self._secrets = tuple(secrets)

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": redact(record.getMessage(), self._secrets),
        }
        if record.exc_info:
            payload["exc"] = redact(self.formatException(record.exc_info), self._secrets)
        return json.dumps(payload)


def setup_logging(level: str, secrets: Iterable[str] = ()) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonRedactingFormatter(secrets))
    setattr(handler, _HANDLER_MARK, True)

    root = logging.getLogger()
    root.handlers = [h for h in root.handlers if not getattr(h, _HANDLER_MARK, False)]
    root.addHandler(handler)
    root.setLevel(level)

    # httpx logs every request URL at INFO; outbound URLs (webhooks, pings) are secrets.
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)

    # Route uvicorn's own loggers through the same redacting formatter.
    for name in _UVICORN_LOGGERS:
        logger = logging.getLogger(name)
        logger.handlers = [handler]
        logger.propagate = False

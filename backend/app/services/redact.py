import re
from collections.abc import Iterable
from urllib.parse import quote, unquote

from app.core.logging import REDACTED, redact

MAX_MESSAGE_LENGTH = 300

_URI_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^\s'\"<>]+")
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[\w.~+/=-]+")
_TOKEN_RE = re.compile(r"\b(?:ya29\.[\w.-]+|eyJ[\w-]+\.[\w-]+\.[\w-]*)")
_PEM_RE = re.compile(r"-----BEGIN [A-Z ]+-----.*?(?:-----END [A-Z ]+-----|$)", re.DOTALL)
# MySQL says "(using password: YES)"; that is a flag, not a value.
_PASSWORD_FLAG_RE = re.compile(r"using password: (YES|NO)")
_FLAG_MARK = "using-password-flag-"


def scrub(text: str, secrets: Iterable[str] = ()) -> str:
    """Strip secret values, URIs, tokens and key material, keeping line structure."""
    variants: set[str] = set()
    for secret in secrets:
        if secret:
            variants.update({secret, quote(secret, safe=""), unquote(secret)})
    # Longest first so a secret containing another is replaced whole.
    text = _PASSWORD_FLAG_RE.sub(lambda m: f"{_FLAG_MARK}{m.group(1)}", text)
    text = redact(text, sorted(variants, key=len, reverse=True))
    text = _PEM_RE.sub(REDACTED, text)
    text = _URI_RE.sub(REDACTED, text)
    text = _BEARER_RE.sub(f"Bearer {REDACTED}", text)
    text = _TOKEN_RE.sub(REDACTED, text)
    return text.replace(_FLAG_MARK, "using password: ")


def sanitize(text: str, secrets: Iterable[str] = ()) -> str:
    """Strip secret values, URIs, tokens and key material from an error message."""
    text = " ".join(scrub(text, secrets).split())
    if len(text) > MAX_MESSAGE_LENGTH:
        text = text[: MAX_MESSAGE_LENGTH - 1] + "…"
    return text


def describe_error(exc: BaseException, secrets: Iterable[str] = ()) -> str:
    """Sanitized one-line description of an exception, safe to store and return."""
    detail = sanitize(str(exc), secrets)
    name = type(exc).__name__
    return f"{name}: {detail}" if detail else name

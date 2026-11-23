"""OAuth tokens as printed by `rclone authorize`: validation, comparison, redaction."""

import base64
import binascii
import json
import re
from datetime import datetime
from typing import Any

TOKEN_FIELDS = ("access_token", "token_type", "refresh_token", "expiry")
_FRACTION_RE = re.compile(r"(\.\d{6})\d+")


def parse_expiry(value: object) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("The token has no expiry.")
    text = _FRACTION_RE.sub(r"\1", value.strip())  # rclone writes nanoseconds
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        raise ValueError("The token expiry is not a valid timestamp.") from None
    if moment.tzinfo is None:
        raise ValueError("The token expiry must include a timezone.")
    return moment


def _extract_pasted(raw: str) -> str:
    """Accept rclone authorize's full output, not just the token line."""
    text = raw.strip()
    if "--->" in text:
        text = text.split("--->", 1)[1]
    if "<---" in text:
        text = text.split("<---", 1)[0]
    return text.strip()


def _decode_blob(text: str) -> Any:
    """`rclone authorize drive <blob>` prints a base64 config blob holding {"token": "..."}."""
    try:
        decoded = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
        blob = json.loads(decoded)
    except (binascii.Error, ValueError):
        raise ValueError("Paste the token JSON or the text printed by rclone authorize.") from None
    if not isinstance(blob, dict) or not isinstance(blob.get("token"), str):
        raise ValueError("The pasted rclone output does not contain a token.")
    try:
        return json.loads(blob["token"])
    except ValueError:
        raise ValueError("The pasted rclone output does not contain a valid token.") from None


def normalize_token(raw: str) -> str:
    """Validate an rclone OAuth token and return it as compact JSON. Messages never echo it."""
    text = _extract_pasted(raw)
    if not text:
        raise ValueError("The token is empty.")
    if text.startswith("{"):
        try:
            data = json.loads(text)
        except ValueError:
            raise ValueError("The token is not valid JSON.") from None
    else:
        data = _decode_blob(text)
    if not isinstance(data, dict):
        raise ValueError("The token must be a JSON object.")
    refresh = data.get("refresh_token")
    if not isinstance(refresh, str) or not refresh:
        raise ValueError(
            "The token has no refresh_token. Run rclone authorize again and paste its output."
        )
    parse_expiry(data.get("expiry"))
    for name in ("access_token", "token_type"):
        if name in data and not isinstance(data[name], str):
            raise ValueError(f"The token field {name} must be a string.")
    clean = {k: data[k] for k in TOKEN_FIELDS if k in data}
    clean.setdefault("token_type", "Bearer")
    return json.dumps(clean, separators=(",", ":"))


def token_values(token: str | None) -> list[str]:
    """The token and the credentials inside it, for redaction."""
    if not token:
        return []
    values = [token]
    try:
        data = json.loads(token)
    except ValueError:
        return values
    if isinstance(data, dict):
        values += [
            str(data[k]) for k in ("access_token", "refresh_token") if isinstance(data.get(k), str)
        ]
    return [v for v in values if v]


def token_expiry(token: str) -> str | None:
    try:
        value = json.loads(token).get("expiry")
    except (ValueError, AttributeError):
        return None
    return value if isinstance(value, str) else None


def same_token(a: str | None, b: str | None) -> bool:
    if a is None or b is None:
        return a == b
    try:
        return bool(json.loads(a) == json.loads(b))
    except ValueError:
        return a == b

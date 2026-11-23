import ipaddress
import re
from collections.abc import Mapping
from datetime import datetime
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StringConstraints,
)

_LABEL_RE = re.compile(r"^(?!-)[A-Za-z0-9_-]{1,63}(?<!-)$")
_NUMERIC_RE = re.compile(r"^[\d.]+$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


class SecretsInvalid(ValueError):  # noqa: N818  (reads better as a domain error)
    """A secret value is missing or malformed. The message never contains the value."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field
        self.message = message


def _validate_host(value: str) -> str:
    host = value.strip()
    if not host or len(host) > 253:
        raise ValueError("Host must be 1-253 characters.")
    bare = host[1:-1] if host.startswith("[") and host.endswith("]") else host
    try:
        return str(ipaddress.ip_address(bare))
    except ValueError:
        pass
    if _NUMERIC_RE.match(host):
        raise ValueError("Host is not a valid IP address.")
    labels = host.rstrip(".").split(".")
    if not all(_LABEL_RE.match(label) for label in labels):
        raise ValueError("Host must be a hostname or IP address, without scheme, port or path.")
    return host


def _no_control_chars(value: str) -> str:
    if _CONTROL_RE.search(value):
        raise ValueError("Control characters are not allowed.")
    return value


Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
Host = Annotated[str, AfterValidator(_validate_host)]
Port = Annotated[int, Field(strict=True, ge=1, le=65535)]
Username = Annotated[
    str, StringConstraints(min_length=1, max_length=128), AfterValidator(_no_control_chars)
]
DbName = Annotated[
    str, StringConstraints(min_length=1, max_length=128), AfterValidator(_no_control_chars)
]
Secret = Annotated[SecretStr, Field(min_length=1, max_length=1024)]


def _dedupe(names: list[str]) -> list[str]:
    if not names:
        raise ValueError("Select at least one database, or use 'all'.")
    if len(names) > 500:
        raise ValueError("Too many databases (max 500).")
    return list(dict.fromkeys(names))


Databases = Literal["all"] | Annotated[list[DbName], AfterValidator(_dedupe)]


class ConfigBase(BaseModel):
    """Non-secret settings. Subclasses declare which secret fields they use."""

    model_config = ConfigDict(extra="forbid")

    def secret_fields(self) -> tuple[str, ...]:
        return ()

    def required_secrets(self) -> tuple[str, ...]:
        return ()

    def check_secrets(self, secrets: Mapping[str, str]) -> None:
        """Cross-field checks on the merged secrets; raise SecretsInvalid."""


class SecretsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LastTestOut(BaseModel):
    at: datetime
    ok: bool
    message: str | None


class CheckOut(BaseModel):
    name: str
    ok: bool
    message: str


class TestResultOut(BaseModel):
    __test__ = False  # not a pytest class

    ok: bool
    message: str
    server_version: str | None = None
    databases: list[str] | None = None
    free_bytes: int | None = None
    total_bytes: int | None = None
    checks: list[CheckOut] = Field(default_factory=list)

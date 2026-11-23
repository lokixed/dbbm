import json
import re
from collections.abc import Mapping
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from app.schemas.common import ConfigBase, Host, LastTestOut, Name, Port, SecretsIn, SecretsInvalid
from app.schemas.job import RefOut
from app.services.outbound import validate_url

MAX_RECIPIENTS = 20
MAX_HEADERS = 10
_EMAIL_RE = re.compile(r"^[^@\s<>,;\"]{1,64}@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,63}$")
_HEADER_NAME_RE = re.compile(r"^[A-Za-z0-9!#$%&'*+.^_`|~-]{1,64}$")
# Set by the sender itself; a custom value would break or forge the request.
_RESERVED_HEADERS = frozenset(
    {"host", "content-length", "content-type", "transfer-encoding", "connection", "x-signature-256"}
)

SecretUrl = Annotated[SecretStr, Field(min_length=1, max_length=2048)]


def _email(value: str) -> str:
    value = value.strip()
    if not _EMAIL_RE.match(value):
        raise ValueError(f"Not a valid email address: {value[:80]!r}.")
    return value


def check_secret_url(field: str, url: str | None, *, https_only: bool = False) -> None:
    if url is None:
        return
    try:
        validate_url(url, https_only=https_only)
    except ValueError as exc:
        raise SecretsInvalid(field, str(exc)) from None


def parse_headers(raw: str | None) -> dict[str, str]:
    if not raw:
        return {}
    data = json.loads(raw)
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


class SlackConfig(ConfigBase):
    def secret_fields(self) -> tuple[str, ...]:
        return ("webhook_url",)

    def required_secrets(self) -> tuple[str, ...]:
        return ("webhook_url",)

    def check_secrets(self, secrets: Mapping[str, str]) -> None:
        check_secret_url("webhook_url", secrets.get("webhook_url"), https_only=True)


class EmailConfig(ConfigBase):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    host: Host
    port: Port = 587
    security: Literal["starttls", "ssl", "none"] = "starttls"
    username: Annotated[str, Field(max_length=256)] | None = None
    from_address: Annotated[str, Field(alias="from")]
    to: Annotated[list[str], Field(min_length=1, max_length=MAX_RECIPIENTS)]

    @field_validator("username")
    @classmethod
    def _username(cls, value: str | None) -> str | None:
        value = (value or "").strip()
        if any(ord(c) < 32 for c in value):
            raise ValueError("Control characters are not allowed.")
        return value or None

    @field_validator("from_address")
    @classmethod
    def _from(cls, value: str) -> str:
        return _email(value)

    @field_validator("to")
    @classmethod
    def _to(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(_email(v) for v in value))

    def secret_fields(self) -> tuple[str, ...]:
        return ("password",)

    def check_secrets(self, secrets: Mapping[str, str]) -> None:
        if bool(self.username) != bool(secrets.get("password")):
            raise SecretsInvalid(
                "password", "Provide the SMTP username and password together, or neither."
            )


class WebhookConfig(ConfigBase):
    def secret_fields(self) -> tuple[str, ...]:
        return ("url", "headers", "hmac_secret")

    def required_secrets(self) -> tuple[str, ...]:
        return ("url",)

    def check_secrets(self, secrets: Mapping[str, str]) -> None:
        check_secret_url("url", secrets.get("url"))


class SlackSecretsIn(SecretsIn):
    webhook_url: SecretUrl | None = None


class EmailSecretsIn(SecretsIn):
    password: Annotated[SecretStr, Field(min_length=1, max_length=1024)] | None = None


class WebhookSecretsIn(SecretsIn):
    url: SecretUrl | None = None
    headers: dict[str, str] | None = None
    hmac_secret: Annotated[SecretStr, Field(min_length=16, max_length=1024)] | None = None

    @field_validator("headers")
    @classmethod
    def _headers(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        if value is None:
            return None
        if len(value) > MAX_HEADERS:
            raise ValueError(f"At most {MAX_HEADERS} headers.")
        for name, header_value in value.items():
            if not _HEADER_NAME_RE.match(name):
                raise ValueError(f"Invalid header name: {name[:64]!r}.")
            if name.lower() in _RESERVED_HEADERS:
                raise ValueError(f"The {name} header is set automatically.")
            if len(header_value) > 4096 or any(c in header_value for c in "\r\n\x00"):
                raise ValueError(f"Invalid value for header {name}.")
        return value

    def plain_values(self) -> dict[str, str | None]:
        """The provided fields as stored strings (headers as JSON), for merging."""
        result: dict[str, str | None] = {}
        for name in self.model_fields_set:
            value: Any = getattr(self, name)
            if value is None:
                result[name] = None
            elif name == "headers":
                result[name] = json.dumps(value, sort_keys=True) if value else None
            else:
                result[name] = value.get_secret_value()
        return result


def secret_values(secrets: SecretsIn) -> dict[str, str | None]:
    if isinstance(secrets, WebhookSecretsIn):
        return secrets.plain_values()
    result: dict[str, str | None] = {}
    for name in secrets.model_fields_set:
        value: SecretStr | None = getattr(secrets, name)
        result[name] = None if value is None else value.get_secret_value()
    return result


class _ChannelBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Name
    enabled: bool = True


class SlackChannelIn(_ChannelBase):
    type: Literal["slack"]
    config: SlackConfig = Field(default_factory=SlackConfig)
    secrets: SlackSecretsIn = Field(default_factory=SlackSecretsIn)


class EmailChannelIn(_ChannelBase):
    type: Literal["email"]
    config: EmailConfig
    secrets: EmailSecretsIn = Field(default_factory=EmailSecretsIn)


class WebhookChannelIn(_ChannelBase):
    type: Literal["webhook"]
    config: WebhookConfig = Field(default_factory=WebhookConfig)
    secrets: WebhookSecretsIn = Field(default_factory=WebhookSecretsIn)


ChannelIn = Annotated[
    SlackChannelIn | EmailChannelIn | WebhookChannelIn, Field(discriminator="type")
]

_CONFIGS: dict[str, type[ConfigBase]] = {
    "slack": SlackConfig,
    "email": EmailConfig,
    "webhook": WebhookConfig,
}


def parse_channel_config(channel_type: str, raw: dict[str, Any]) -> ConfigBase:
    model = _CONFIGS.get(channel_type)
    if model is None:
        raise ValueError(f"Unknown channel type: {channel_type}")
    return model.model_validate(raw)


def dump_channel_config(config: ConfigBase) -> dict[str, Any]:
    return config.model_dump(mode="json", by_alias=True)


class ChannelOut(BaseModel):
    id: str
    name: str
    type: str
    enabled: bool
    config: dict[str, Any]
    secrets_set: dict[str, bool]
    secrets_error: str | None = None
    header_names: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    last_test: LastTestOut | None
    jobs: list[RefOut]
    created_at: datetime
    updated_at: datetime


class EventOut(BaseModel):
    id: str
    channel_id: str
    channel_name: str | None = None
    run_id: str | None
    job_id: str | None
    event_type: str
    status: str
    error: str | None
    attempts: int
    created_at: datetime


class EventPage(BaseModel):
    items: list[EventOut]
    total: int


class TestSendOut(BaseModel):
    __test__ = False  # not a pytest class

    ok: bool
    message: str
    event: EventOut | None = None

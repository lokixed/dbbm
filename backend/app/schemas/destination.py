import re
from collections.abc import Mapping
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, TypeAdapter, field_validator

from app.schemas.common import ConfigBase, LastTestOut, Name, SecretsIn, SecretsInvalid
from app.services.paths import check_lexical
from app.services.rclone_token import normalize_token

MAX_FOLDER_LENGTH = 1024
MAX_SEGMENT_LENGTH = 255
CHUNK_SIZES_MB = (8, 16, 32, 64, 128, 256)
# Backslash, colon and wildcards are legal in Drive but break rclone paths or local mirrors.
_FOLDER_FORBIDDEN_RE = re.compile(r'[\x00-\x1f\x7f\\:*?"<>|]')
_SHARED_DRIVE_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,256}$")


def normalize_remote_folder(value: str) -> str:
    """Path inside Drive: no leading slash, repeated slashes collapsed, no '..'."""
    if len(value) > MAX_FOLDER_LENGTH:
        raise ValueError(f"Folder must be at most {MAX_FOLDER_LENGTH} characters.")
    if _FOLDER_FORBIDDEN_RE.search(value):
        raise ValueError('Folder must not contain control characters or \\ : * ? " < > |.')
    segments = [s for s in value.strip().split("/") if s]
    if not segments:
        raise ValueError("Folder must not be empty.")
    for segment in segments:
        if segment in (".", ".."):
            raise ValueError("'.' and '..' are not allowed in the folder path.")
        if segment != segment.strip():
            raise ValueError("Folder names must not start or end with spaces.")
        if len(segment) > MAX_SEGMENT_LENGTH:
            raise ValueError(f"Folder names must be at most {MAX_SEGMENT_LENGTH} characters.")
    return "/".join(segments)


class LocalConfig(ConfigBase):
    path: str

    @field_validator("path")
    @classmethod
    def _path(cls, value: str) -> str:
        check_lexical(value)
        return value.strip()


class GdriveConfig(ConfigBase):
    remote_folder: str = "db-backups"
    scope: Literal["drive.file", "drive"] = "drive.file"
    shared_drive_id: str | None = None
    client_id: str | None = None
    delete_permanently: bool = False
    chunk_size_mb: Annotated[int, Field(strict=True)] = 64

    @field_validator("remote_folder")
    @classmethod
    def _folder(cls, value: str) -> str:
        return normalize_remote_folder(value)

    @field_validator("shared_drive_id")
    @classmethod
    def _shared_drive(cls, value: str | None) -> str | None:
        value = (value or "").strip()
        if not value:
            return None
        if not _SHARED_DRIVE_RE.match(value):
            raise ValueError("Shared drive ID may only contain letters, digits, '-' and '_'.")
        return value

    @field_validator("client_id")
    @classmethod
    def _client_id(cls, value: str | None) -> str | None:
        value = (value or "").strip()
        if not value:
            return None
        if not _CLIENT_ID_RE.match(value):
            raise ValueError("Invalid OAuth client ID.")
        return value

    @field_validator("chunk_size_mb")
    @classmethod
    def _chunk(cls, value: int) -> int:
        if value not in CHUNK_SIZES_MB:
            raise ValueError(f"Chunk size must be one of {', '.join(map(str, CHUNK_SIZES_MB))}.")
        return value

    def secret_fields(self) -> tuple[str, ...]:
        return ("token", "client_secret")

    def required_secrets(self) -> tuple[str, ...]:
        return ("token",)

    def check_secrets(self, secrets: Mapping[str, str]) -> None:
        if bool(self.client_id) != bool(secrets.get("client_secret")):
            raise SecretsInvalid(
                "client_secret",
                "Provide the OAuth client ID and client secret together, or neither.",
            )
        try:
            normalize_token(secrets.get("token", ""))
        except ValueError as exc:
            raise SecretsInvalid("token", str(exc)) from None


def normalize_gdrive_secrets(secrets: Mapping[str, str]) -> dict[str, str]:
    """Store the token as compact JSON, whatever form it was pasted in."""
    result = dict(secrets)
    if result.get("token"):
        try:
            result["token"] = normalize_token(result["token"])
        except ValueError as exc:
            raise SecretsInvalid("token", str(exc)) from None
    return result


class GdriveSecretsIn(SecretsIn):
    token: Annotated[SecretStr, Field(min_length=1, max_length=65536)] | None = None
    client_secret: Annotated[SecretStr, Field(min_length=1, max_length=1024)] | None = None


class Retention(BaseModel):
    """Stored now; enforced by a later milestone."""

    model_config = ConfigDict(extra="forbid")

    keep_last: Annotated[int, Field(strict=True, ge=1, le=100_000)] | None = None
    keep_days: Annotated[int, Field(strict=True, ge=1, le=36_500)] | None = None


class LocalDestinationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["local"]
    name: Name
    config: LocalConfig
    retention: Retention = Field(default_factory=Retention)


class GdriveDestinationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["gdrive"]
    name: Name
    config: GdriveConfig = Field(default_factory=GdriveConfig)
    secrets: GdriveSecretsIn = Field(default_factory=GdriveSecretsIn)
    retention: Retention = Field(default_factory=Retention)


DestinationIn = Annotated[LocalDestinationIn | GdriveDestinationIn, Field(discriminator="type")]

_CONFIG_ADAPTERS: dict[str, TypeAdapter[Any]] = {
    "local": TypeAdapter(LocalConfig),
    "gdrive": TypeAdapter(GdriveConfig),
}


def parse_destination_config(destination_type: str, raw: dict[str, Any]) -> ConfigBase:
    adapter = _CONFIG_ADAPTERS.get(destination_type)
    if adapter is None:
        raise ValueError(f"Unknown destination type: {destination_type}")
    config: ConfigBase = adapter.validate_python(raw)
    return config


class DestinationOut(BaseModel):
    id: str
    name: str
    type: str
    config: dict[str, Any]
    retention: Retention
    secrets_set: dict[str, bool] = Field(default_factory=dict)
    secrets_error: str | None = None
    auth_status: str
    auth_checked_at: datetime | None
    last_test: LastTestOut | None
    created_at: datetime
    updated_at: datetime

import json
import re
from collections.abc import Mapping
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StringConstraints,
    TypeAdapter,
    field_validator,
)

from app.schemas.common import (
    ConfigBase,
    Databases,
    DbName,
    Host,
    LastTestOut,
    Name,
    Port,
    Secret,
    SecretsIn,
    SecretsInvalid,
    Username,
)

MONGO_DUMP_MODES = Literal["per_database", "full_oplog"]
MYSQL_TLS_MODES = Literal["disabled", "preferred", "required", "verify_ca", "verify_identity"]
POSTGRES_TLS_MODES = Literal["disable", "allow", "prefer", "require", "verify-ca", "verify-full"]

_OPTION_VALUE = Annotated[
    str, StringConstraints(min_length=1, max_length=128, pattern=r"^[\w.-]+$")
]
_PROJECT_ID_RE = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
_DATABASE_ID_RE = re.compile(r"^(\(default\)|[a-z][a-z0-9-]{2,61}[a-z0-9])$")
_BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,61}[a-z0-9]$")
_IPV4_LIKE_RE = re.compile(r"^\d+\.\d+\.\d+\.\d+$")
_PREFIX_RE = re.compile(r"^[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*$")
_COLLECTION_RE = re.compile(r"^(?!__.*__$)(?!\.{1,2}$)[^/\x00-\x1f\x7f]{1,100}$")
_MONGO_URI_SCHEMES = ("mongodb://", "mongodb+srv://")


def _check_options(options: dict[str, str], allowed: frozenset[str]) -> dict[str, str]:
    unknown = sorted(set(options) - allowed)
    if unknown:
        raise ValueError(f"Unsupported option(s): {', '.join(unknown)}.")
    return options


# ---------- MySQL ----------


class MySQLConfig(ConfigBase):
    host: Host
    port: Port = 3306
    username: Username
    databases: Databases = "all"
    tls_mode: MYSQL_TLS_MODES = "preferred"
    extra_options: dict[str, _OPTION_VALUE] = Field(default_factory=dict, max_length=10)

    @field_validator("extra_options")
    @classmethod
    def _options(cls, value: dict[str, str]) -> dict[str, str]:
        return _check_options(value, frozenset({"charset"}))

    def secret_fields(self) -> tuple[str, ...]:
        return ("password",)


class PasswordSecretsIn(SecretsIn):
    password: Secret | None = None


# ---------- PostgreSQL ----------


class PostgresConfig(ConfigBase):
    host: Host
    port: Port = 5432
    username: Username
    connection_database: DbName = "postgres"
    databases: Databases = "all"
    tls_mode: POSTGRES_TLS_MODES = "prefer"
    include_globals: bool = False
    extra_options: dict[str, _OPTION_VALUE] = Field(default_factory=dict, max_length=10)

    @field_validator("extra_options")
    @classmethod
    def _options(cls, value: dict[str, str]) -> dict[str, str]:
        return _check_options(value, frozenset({"application_name"}))

    def secret_fields(self) -> tuple[str, ...]:
        return ("password",)


# ---------- MongoDB ----------


class MongoFieldsConfig(ConfigBase):
    mode: Literal["fields"]
    host: Host
    port: Port = 27017
    username: Username | None = None
    auth_database: DbName = "admin"
    tls: bool = False
    replica_set: Annotated[str, StringConstraints(min_length=1, max_length=64)] | None = None
    databases: Databases = "all"
    dump_mode: MONGO_DUMP_MODES = "per_database"

    def secret_fields(self) -> tuple[str, ...]:
        return ("password",)


class MongoUriConfig(ConfigBase):
    mode: Literal["uri"]
    databases: Databases = "all"
    dump_mode: MONGO_DUMP_MODES = "per_database"

    def secret_fields(self) -> tuple[str, ...]:
        return ("uri",)

    def required_secrets(self) -> tuple[str, ...]:
        return ("uri",)

    def check_secrets(self, secrets: Mapping[str, str]) -> None:
        uri = secrets.get("uri", "")
        if len(uri) > 4096 or any(c.isspace() or ord(c) < 32 for c in uri):
            raise SecretsInvalid("uri", "Connection URI is malformed.")
        if not uri.startswith(_MONGO_URI_SCHEMES):
            raise SecretsInvalid(
                "uri", "Connection URI must start with mongodb:// or mongodb+srv://."
            )


MongoConfig = Annotated[MongoFieldsConfig | MongoUriConfig, Field(discriminator="mode")]


class MongoSecretsIn(SecretsIn):
    password: Secret | None = None
    uri: Secret | None = None


# ---------- Firestore ----------


def parse_service_account(raw: str) -> dict[str, Any]:
    """Validate service-account JSON structure. Error messages never echo the content."""
    try:
        data = json.loads(raw)
    except (ValueError, RecursionError):
        raise ValueError("Service account must be valid JSON.") from None
    if not isinstance(data, dict) or data.get("type") != "service_account":
        raise ValueError('Service account JSON must have "type": "service_account".')
    email = data.get("client_email")
    key = data.get("private_key")
    if not isinstance(email, str) or "@" not in email:
        raise ValueError("Service account JSON is missing a valid client_email.")
    if not isinstance(key, str) or "-----BEGIN" not in key:
        raise ValueError("Service account JSON is missing a valid private_key.")
    project = data.get("project_id")
    if project is not None and not isinstance(project, str):
        raise ValueError("Service account project_id must be a string.")
    return data


def split_staging_bucket(value: str) -> tuple[str, str]:
    """Return (bucket, prefix) from a normalised gs:// URL."""
    bucket, _, prefix = value.removeprefix("gs://").partition("/")
    return bucket, prefix


def _validate_staging_bucket(value: str) -> str:
    value = value.strip()
    if not value.startswith("gs://"):
        raise ValueError("Staging bucket must start with gs://.")
    bucket, prefix = split_staging_bucket(value.rstrip("/"))
    if (
        not _BUCKET_RE.match(bucket)
        or ".." in bucket
        or _IPV4_LIKE_RE.match(bucket)
        or bucket.startswith("goog")
        or "google" in bucket
    ):
        raise ValueError("Invalid Cloud Storage bucket name.")
    if prefix:
        if not _PREFIX_RE.match(prefix) or any(p in (".", "..") for p in prefix.split("/")):
            raise ValueError("Invalid prefix in staging bucket URL.")
        return f"gs://{bucket}/{prefix}"
    return f"gs://{bucket}"


class FirestoreConfig(ConfigBase):
    project_id: str
    database_id: str = "(default)"
    staging_bucket: Annotated[str, AfterValidator(_validate_staging_bucket)]
    collection_ids: list[str] | None = Field(default=None, max_length=100)
    delete_staging_after_download: bool = True

    @field_validator("project_id")
    @classmethod
    def _project(cls, value: str) -> str:
        if not _PROJECT_ID_RE.match(value):
            raise ValueError("Invalid Google Cloud project ID.")
        return value

    @field_validator("database_id")
    @classmethod
    def _database(cls, value: str) -> str:
        if not _DATABASE_ID_RE.match(value):
            raise ValueError("Invalid Firestore database ID.")
        return value

    @field_validator("collection_ids")
    @classmethod
    def _collections(cls, value: list[str] | None) -> list[str] | None:
        if not value:
            return None
        if not all(_COLLECTION_RE.match(v) for v in value):
            raise ValueError("Invalid collection ID.")
        return list(dict.fromkeys(value))

    def secret_fields(self) -> tuple[str, ...]:
        return ("service_account_json",)

    def required_secrets(self) -> tuple[str, ...]:
        return ("service_account_json",)

    def check_secrets(self, secrets: Mapping[str, str]) -> None:
        try:
            account = parse_service_account(secrets.get("service_account_json", ""))
        except ValueError as exc:
            raise SecretsInvalid("service_account_json", str(exc)) from None
        if account.get("project_id") != self.project_id:
            raise SecretsInvalid(
                "service_account_json",
                "Service account project_id does not match the configured project ID.",
            )


class FirestoreSecretsIn(SecretsIn):
    service_account_json: Annotated[SecretStr, Field(min_length=1, max_length=65536)] | None = None


# ---------- Requests ----------


class _SourceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Name


class MySQLSourceIn(_SourceIn):
    type: Literal["mysql"]
    config: MySQLConfig
    secrets: PasswordSecretsIn = Field(default_factory=PasswordSecretsIn)


class PostgresSourceIn(_SourceIn):
    type: Literal["postgres"]
    config: PostgresConfig
    secrets: PasswordSecretsIn = Field(default_factory=PasswordSecretsIn)


class MongoSourceIn(_SourceIn):
    type: Literal["mongo"]
    config: MongoConfig
    secrets: MongoSecretsIn = Field(default_factory=MongoSecretsIn)


class FirestoreSourceIn(_SourceIn):
    type: Literal["firestore"]
    config: FirestoreConfig
    secrets: FirestoreSecretsIn = Field(default_factory=FirestoreSecretsIn)


SourceIn = Annotated[
    MySQLSourceIn | PostgresSourceIn | MongoSourceIn | FirestoreSourceIn,
    Field(discriminator="type"),
]

_CONFIG_ADAPTERS: dict[str, TypeAdapter[Any]] = {
    "mysql": TypeAdapter(MySQLConfig),
    "postgres": TypeAdapter(PostgresConfig),
    "mongo": TypeAdapter(MongoConfig),
    "firestore": TypeAdapter(FirestoreConfig),
}


def parse_source_config(source_type: str, raw: dict[str, Any]) -> ConfigBase:
    config: ConfigBase = _CONFIG_ADAPTERS[source_type].validate_python(raw)
    return config


# ---------- Responses ----------


class SourceOut(BaseModel):
    id: str
    name: str
    type: str
    config: dict[str, Any]
    secrets_set: dict[str, bool]
    secrets_error: str | None = None
    last_test: LastTestOut | None
    created_at: datetime
    updated_at: datetime

from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from app.schemas.destination import LocalDestinationIn
from app.schemas.source import (
    FirestoreConfig,
    MongoConfig,
    MySQLConfig,
    PostgresConfig,
    SourceIn,
    parse_service_account,
)

source_adapter: TypeAdapter[Any] = TypeAdapter(SourceIn)


def mysql_config(**over: Any) -> dict[str, Any]:
    return {"host": "db.internal", "username": "backup", **over}


@pytest.mark.parametrize("host", ["db", "db.internal", "10.0.0.5", "::1", "[::1]", "my_db-1.svc"])
def test_valid_hosts(host: str) -> None:
    assert MySQLConfig.model_validate(mysql_config(host=host)).host


@pytest.mark.parametrize(
    "host",
    ["", " ", "http://db", "db:3306", "db/path", "999.1.1.1", "-bad.example", "a b", "x" * 300],
)
def test_invalid_hosts(host: str) -> None:
    with pytest.raises(ValidationError):
        MySQLConfig.model_validate(mysql_config(host=host))


@pytest.mark.parametrize("port", [0, 65536, -1, "3306", True, 3306.5])
def test_invalid_ports(port: object) -> None:
    with pytest.raises(ValidationError):
        MySQLConfig.model_validate(mysql_config(port=port))


def test_defaults() -> None:
    mysql = MySQLConfig.model_validate(mysql_config())
    assert (mysql.port, mysql.databases, mysql.tls_mode) == (3306, "all", "preferred")
    pg = PostgresConfig.model_validate(mysql_config())
    assert (pg.port, pg.connection_database, pg.tls_mode) == (5432, "postgres", "prefer")


def test_databases_dedupe_and_reject_empty() -> None:
    config = MySQLConfig.model_validate(mysql_config(databases=["a", "b", "a"]))
    assert config.databases == ["a", "b"]
    with pytest.raises(ValidationError):
        MySQLConfig.model_validate(mysql_config(databases=[]))


def test_unknown_fields_and_options_rejected() -> None:
    with pytest.raises(ValidationError):
        MySQLConfig.model_validate(mysql_config(bogus=1))
    with pytest.raises(ValidationError):
        MySQLConfig.model_validate(mysql_config(extra_options={"init_command": "DROP DATABASE x"}))


def test_mongo_modes_are_discriminated() -> None:
    adapter: TypeAdapter[Any] = TypeAdapter(MongoConfig)
    fields = adapter.validate_python({"mode": "fields", "host": "mongo", "username": "u"})
    assert fields.port == 27017 and fields.auth_database == "admin"
    uri = adapter.validate_python({"mode": "uri"})
    assert uri.required_secrets() == ("uri",)
    with pytest.raises(ValidationError):
        adapter.validate_python({"host": "mongo"})


def firestore_config(**over: Any) -> dict[str, Any]:
    return {"project_id": "my-project-1", "staging_bucket": "gs://my-bucket", **over}


@pytest.mark.parametrize(
    ("bucket", "expected"),
    [
        ("gs://my-bucket", "gs://my-bucket"),
        ("gs://my-bucket/", "gs://my-bucket"),
        ("gs://my.bucket_1/backups/staging/", "gs://my.bucket_1/backups/staging"),
    ],
)
def test_valid_buckets(bucket: str, expected: str) -> None:
    assert (
        FirestoreConfig.model_validate(firestore_config(staging_bucket=bucket)).staging_bucket
        == expected
    )


@pytest.mark.parametrize(
    "bucket",
    [
        "my-bucket",
        "https://my-bucket",
        "gs://",
        "gs://AB",
        "gs://Upper-Case",
        "gs://-bad",
        "gs://bad-",
        "gs://192.168.0.1",
        "gs://goog-bucket",
        "gs://my-google-bucket",
        "gs://my-bucket/../x",
        "gs://my-bucket/a b",
    ],
)
def test_invalid_buckets(bucket: str) -> None:
    with pytest.raises(ValidationError):
        FirestoreConfig.model_validate(firestore_config(staging_bucket=bucket))


def test_firestore_ids() -> None:
    config = FirestoreConfig.model_validate(firestore_config(collection_ids=["a", "a", "b"]))
    assert config.database_id == "(default)" and config.collection_ids == ["a", "b"]
    for bad in ({"project_id": "X"}, {"database_id": "Bad_ID"}, {"collection_ids": ["a/b"]}):
        with pytest.raises(ValidationError):
            FirestoreConfig.model_validate(firestore_config(**bad))


def sa(**over: Any) -> str:
    import json

    data = {
        "type": "service_account",
        "client_email": "bk@my-project-1.iam.gserviceaccount.com",
        "private_key": "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----\n",
        "project_id": "my-project-1",
        **over,
    }
    return json.dumps({k: v for k, v in data.items() if v is not None})


def test_service_account_structure() -> None:
    assert parse_service_account(sa())["client_email"].startswith("bk@")
    for bad in (
        "not json",
        "[]",
        sa(type="authorized_user"),
        sa(client_email=None),
        sa(client_email="nope"),
        sa(private_key=None),
        sa(private_key="plain"),
    ):
        with pytest.raises(ValueError, match=r"^[^{}]*$"):
            parse_service_account(bad)


def test_service_account_project_must_match_config() -> None:
    from app.schemas.common import SecretsInvalid

    config = FirestoreConfig.model_validate(firestore_config())
    config.check_secrets({"service_account_json": sa()})
    with pytest.raises(SecretsInvalid, match="project_id"):
        config.check_secrets({"service_account_json": sa(project_id="other-project")})


def test_mongo_uri_secret_check() -> None:
    from app.schemas.common import SecretsInvalid
    from app.schemas.source import MongoUriConfig

    config = MongoUriConfig(mode="uri")
    config.check_secrets({"uri": "mongodb://u:p@h:27017/db"})
    config.check_secrets({"uri": "mongodb+srv://u:p@cluster.example.net"})
    for bad in ("http://h", "mongodb://h /x", ""):
        with pytest.raises(SecretsInvalid):
            config.check_secrets({"uri": bad})


def test_discriminated_union_picks_by_type() -> None:
    body = source_adapter.validate_python(
        {"type": "postgres", "name": " pg ", "config": mysql_config(), "secrets": {"password": "x"}}
    )
    assert body.name == "pg" and body.config.connection_database == "postgres"
    with pytest.raises(ValidationError):
        source_adapter.validate_python({"type": "oracle", "name": "x", "config": {}})


def test_secret_fields_must_be_non_empty_and_known() -> None:
    base = {"type": "mysql", "name": "m", "config": mysql_config()}
    with pytest.raises(ValidationError):
        source_adapter.validate_python({**base, "secrets": {"password": ""}})
    with pytest.raises(ValidationError):
        source_adapter.validate_python({**base, "secrets": {"token": "x"}})


def test_secret_validation_errors_do_not_echo_the_value() -> None:
    value = "x" * 70000
    with pytest.raises(ValidationError) as info:
        source_adapter.validate_python(
            {
                "type": "firestore",
                "name": "f",
                "config": firestore_config(),
                "secrets": {"service_account_json": value},
            }
        )
    assert value not in str(info.value.errors(include_input=False))


@pytest.mark.parametrize("path", ["", "  ", "a/../b", "..", "a\\b", "a\x00b"])
def test_local_destination_rejects_bad_paths(path: str) -> None:
    with pytest.raises(ValidationError):
        LocalDestinationIn.model_validate({"type": "local", "name": "d", "config": {"path": path}})


def test_local_destination_only_local_and_retention_bounds() -> None:
    ok = LocalDestinationIn.model_validate(
        {"type": "local", "name": "d", "config": {"path": "x"}, "retention": {"keep_last": 5}}
    )
    assert ok.retention.keep_last == 5 and ok.retention.keep_days is None
    with pytest.raises(ValidationError):
        LocalDestinationIn.model_validate({"type": "gdrive", "name": "d", "config": {"path": "x"}})
    with pytest.raises(ValidationError):
        LocalDestinationIn.model_validate(
            {"type": "local", "name": "d", "config": {"path": "x"}, "retention": {"keep_days": 0}}
        )


def test_milestone_4_options_have_safe_defaults() -> None:
    fields = TypeAdapter(MongoConfig).validate_python({"mode": "fields", "host": "m"})
    uri = TypeAdapter(MongoConfig).validate_python({"mode": "uri"})
    assert fields.dump_mode == uri.dump_mode == "per_database"
    firestore = FirestoreConfig(project_id="my-project-1", staging_bucket="gs://my-bucket")
    assert firestore.delete_staging_after_download is True


@pytest.mark.parametrize("mode", ["", "oplog", "FULL_OPLOG", None, 1])
def test_invalid_mongo_dump_mode_is_rejected(mode: Any) -> None:
    for config in ({"mode": "fields", "host": "m"}, {"mode": "uri"}):
        with pytest.raises(ValidationError):
            TypeAdapter(MongoConfig).validate_python({**config, "dump_mode": mode})


def test_delete_staging_flag_must_be_a_bool() -> None:
    base = {"project_id": "my-project-1", "staging_bucket": "gs://my-bucket"}
    assert (
        FirestoreConfig(**base, delete_staging_after_download=False).delete_staging_after_download
        is False
    )
    with pytest.raises(ValidationError):
        FirestoreConfig.model_validate({**base, "delete_staging_after_download": "maybe"})

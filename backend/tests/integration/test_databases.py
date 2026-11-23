import pytest

from app.drivers.mongo import MongoDriver
from app.drivers.mysql import MySQLDriver
from app.drivers.postgres import PostgresDriver
from tests.integration.conftest import Endpoint, free_port

pytestmark = pytest.mark.integration


def mysql(ep: Endpoint, **over: object) -> dict[str, object]:
    return {
        "host": ep.host,
        "port": ep.port,
        "username": ep.username,
        "databases": "all",
        "tls_mode": "preferred",
        "extra_options": {},
        **over,
    }


def test_mysql_success_and_database_listing(mysql_server: Endpoint) -> None:
    result = MySQLDriver(mysql(mysql_server), {"password": mysql_server.password}).test_connection()
    assert result.ok, result.message
    assert result.server_version and result.server_version.startswith("8.0")
    assert result.databases is not None
    assert {"shop", "blog"} <= set(result.databases)
    assert "hidden" not in result.databases and "information_schema" not in result.databases


def test_mysql_wrong_password_is_sanitized(mysql_server: Endpoint) -> None:
    result = MySQLDriver(mysql(mysql_server), {"password": "wrong-pw-123"}).test_connection()
    assert not result.ok and "wrong-pw-123" not in result.message
    assert "Access denied" in result.message


def test_mysql_unreachable_host() -> None:
    config = {"host": "127.0.0.1", "port": free_port(), "username": "u", "tls_mode": "disabled"}
    result = MySQLDriver(config, {"password": "pw-unreachable"}).test_connection()
    assert not result.ok and "pw-unreachable" not in result.message


@pytest.mark.parametrize("tls_mode", ["disabled", "required"])
def test_mysql_tls_modes(mysql_server: Endpoint, tls_mode: str) -> None:
    config = mysql(mysql_server, tls_mode=tls_mode)
    assert MySQLDriver(config, {"password": mysql_server.password}).test_connection().ok


def postgres(ep: Endpoint, **over: object) -> dict[str, object]:
    return {
        "host": ep.host,
        "port": ep.port,
        "username": ep.username,
        "connection_database": "postgres",
        "databases": "all",
        "tls_mode": "prefer",
        "extra_options": {},
        **over,
    }


def test_postgres_success_and_database_listing(postgres_server: Endpoint) -> None:
    driver = PostgresDriver(postgres(postgres_server), {"password": postgres_server.password})
    result = driver.test_connection()
    assert result.ok, result.message
    assert result.server_version and result.server_version.startswith("16")
    assert result.databases is not None
    assert {"postgres", "shop", "blog"} <= set(result.databases)
    assert "hidden" not in result.databases
    assert not {"template0", "template1"} & set(result.databases)


def test_postgres_wrong_password_is_sanitized(postgres_server: Endpoint) -> None:
    result = PostgresDriver(
        postgres(postgres_server), {"password": "wrong-pw-123"}
    ).test_connection()
    assert not result.ok and "wrong-pw-123" not in result.message
    assert "authentication failed" in result.message


def test_postgres_unreachable_host() -> None:
    config = {"host": "127.0.0.1", "port": free_port(), "username": "u", "tls_mode": "disable"}
    result = PostgresDriver(config, {"password": "pw-unreachable"}).test_connection()
    assert not result.ok and "pw-unreachable" not in result.message


def mongo_fields(ep: Endpoint, **over: object) -> dict[str, object]:
    return {
        "mode": "fields",
        "host": ep.host,
        "port": ep.port,
        "username": ep.username,
        "auth_database": "admin",
        "tls": False,
        "replica_set": None,
        "databases": "all",
        **over,
    }


def test_mongo_success_and_only_authorized_databases_listed(mongo_server: Endpoint) -> None:
    driver = MongoDriver(mongo_fields(mongo_server), {"password": mongo_server.password})
    result = driver.test_connection()
    assert result.ok, result.message
    assert result.server_version and result.server_version.startswith("7.")
    assert result.databases == ["shop"]


def test_mongo_uri_mode_success(mongo_server: Endpoint) -> None:
    uri = (
        f"mongodb://{mongo_server.username}:{mongo_server.password}"
        f"@{mongo_server.host}:{mongo_server.port}/?authSource=admin"
    )
    result = MongoDriver({"mode": "uri", "databases": "all"}, {"uri": uri}).test_connection()
    assert result.ok, result.message
    assert result.databases == ["shop"]


def test_mongo_wrong_password_is_sanitized(mongo_server: Endpoint) -> None:
    result = MongoDriver(mongo_fields(mongo_server), {"password": "wrong-pw-123"}).test_connection()
    assert not result.ok and "wrong-pw-123" not in result.message


def test_mongo_uri_wrong_password_never_echoes_uri(mongo_server: Endpoint) -> None:
    uri = (
        f"mongodb://{mongo_server.username}:wrong-uri-pw"
        f"@{mongo_server.host}:{mongo_server.port}/?authSource=admin"
    )
    result = MongoDriver({"mode": "uri", "databases": "all"}, {"uri": uri}).test_connection()
    assert not result.ok
    assert "wrong-uri-pw" not in result.message and "mongodb://" not in result.message


def test_mongo_unreachable_host() -> None:
    config = {
        "mode": "fields",
        "host": "127.0.0.1",
        "port": free_port(),
        "username": "u",
        "auth_database": "admin",
        "tls": False,
        "replica_set": None,
        "databases": "all",
    }
    result = MongoDriver(config, {"password": "pw-unreachable"}).test_connection()
    assert not result.ok and "pw-unreachable" not in result.message


def test_mongo_malformed_uri_is_generic() -> None:
    result = MongoDriver(
        {"mode": "uri", "databases": "all"}, {"uri": "mongodb://u:pw-bad@:::bad"}
    ).test_connection()
    assert not result.ok and "pw-bad" not in result.message

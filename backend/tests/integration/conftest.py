import socket
from collections.abc import Iterator
from dataclasses import dataclass

import psycopg
import pymysql
import pytest
from pymongo import MongoClient
from testcontainers.community.mongodb import MongoDbContainer
from testcontainers.community.mysql import MySqlContainer
from testcontainers.community.postgres import PostgresContainer


def docker_available() -> bool:
    try:
        import docker

        docker.from_env().ping()
    except Exception:
        return False
    return True


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    available = docker_available()
    skip = pytest.mark.skip(reason="Docker is not available")
    for item in items:
        if "tests/integration" in str(item.path).replace("\\", "/"):
            item.add_marker(pytest.mark.integration)
            if not available:
                item.add_marker(skip)


@dataclass(frozen=True)
class Endpoint:
    host: str
    port: int
    username: str
    password: str


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="session")
def mysql_server() -> Iterator[Endpoint]:
    container = MySqlContainer(
        "mysql:8.0", username="backup", password="backup-pw", dbname="shop", root_password="root-pw"
    )
    with container:
        host, port = container.get_container_host_ip(), int(container.get_exposed_port(3306))
        root = pymysql.connect(host=host, port=port, user="root", password="root-pw")
        with root.cursor() as cur:
            cur.execute("CREATE DATABASE blog")
            cur.execute("CREATE DATABASE hidden")
            cur.execute("GRANT SELECT ON blog.* TO 'backup'@'%'")
        root.commit()
        root.close()
        yield Endpoint(host, port, "backup", "backup-pw")


@pytest.fixture(scope="session")
def postgres_server() -> Iterator[Endpoint]:
    container = PostgresContainer(
        "postgres:16", username="admin", password="admin-pw", dbname="shop", driver=None
    )
    with container:
        host, port = container.get_container_host_ip(), int(container.get_exposed_port(5432))
        with psycopg.connect(
            host=host, port=port, user="admin", password="admin-pw", dbname="shop", autocommit=True
        ) as conn:
            conn.execute("CREATE DATABASE blog")
            conn.execute("CREATE DATABASE hidden")
            conn.execute("REVOKE CONNECT ON DATABASE hidden FROM PUBLIC")
            conn.execute("CREATE ROLE reader LOGIN PASSWORD 'reader-pw'")
        yield Endpoint(host, port, "reader", "reader-pw")


@pytest.fixture(scope="session")
def mongo_server() -> Iterator[Endpoint]:
    container = MongoDbContainer("mongo:7", username="root", password="root-pw")
    with container:
        host, port = container.get_container_host_ip(), int(container.get_exposed_port(27017))
        client: MongoClient[dict[str, object]] = MongoClient(
            host, port, username="root", password="root-pw"
        )
        client["shop"]["items"].insert_one({"sku": 1})
        client["blog"]["posts"].insert_one({"title": "x"})
        client["admin"].command(
            "createUser", "backup", pwd="backup-pw", roles=[{"role": "read", "db": "shop"}]
        )
        client.close()
        yield Endpoint(host, port, "backup", "backup-pw")

from collections.abc import Mapping
from typing import Any

from app.drivers.base import BaseDriver, CheckResult, ConnectionTestResult, Driver
from app.drivers.firestore import FirestoreDriver
from app.drivers.mongo import MongoDriver
from app.drivers.mysql import MySQLDriver
from app.drivers.postgres import PostgresDriver

DRIVERS: dict[str, type[BaseDriver]] = {
    "mysql": MySQLDriver,
    "postgres": PostgresDriver,
    "mongo": MongoDriver,
    "firestore": FirestoreDriver,
}


def get_driver(source_type: str, config: dict[str, Any], secrets: Mapping[str, str]) -> BaseDriver:
    return DRIVERS[source_type](config, secrets)


__all__ = [
    "DRIVERS",
    "BaseDriver",
    "CheckResult",
    "ConnectionTestResult",
    "Driver",
    "get_driver",
]

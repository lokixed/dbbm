from collections.abc import Mapping
from typing import Any

from app.storage.base import (
    BaseStorage,
    Storage,
    StorageContext,
    StorageTestResult,
    StoredObject,
    VerifyResult,
)
from app.storage.gdrive import GdriveStorage
from app.storage.local import LocalStorage

STORAGES: dict[str, type[BaseStorage]] = {"local": LocalStorage, "gdrive": GdriveStorage}


def get_storage(
    destination_type: str,
    config: dict[str, Any],
    secrets: Mapping[str, str],
    context: StorageContext | None = None,
) -> BaseStorage:
    return STORAGES[destination_type](config, secrets, context)


__all__ = [
    "STORAGES",
    "BaseStorage",
    "Storage",
    "StorageContext",
    "StorageTestResult",
    "StoredObject",
    "VerifyResult",
    "get_storage",
]

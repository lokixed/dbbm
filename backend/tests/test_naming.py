from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.services.naming import (
    artifact_key,
    database_label,
    format_timestamp,
    manifest_key,
    safe_component,
)
from app.services.paths import PathNotAllowed
from app.storage.local import LocalStorage

MOMENT = datetime(2026, 3, 7, 4, 5, 6, tzinfo=UTC)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("daily-backup", "daily-backup"),
        ("My App DB", "My_App_DB"),
        ("a/b\\c", "a_b_c"),
        ("../../etc/passwd", "_.._etc_passwd"),
        ("..hidden", "hidden"),
        ("héllo", "h_llo"),
        ("...", "unnamed"),
        ("", "unnamed"),
        ("keep.dots_and-dashes", "keep.dots_and-dashes"),
    ],
)
def test_safe_component(raw: str, expected: str) -> None:
    assert safe_component(raw) == expected


def test_safe_component_is_bounded() -> None:
    assert len(safe_component("x" * 500)) == 100


def test_artifact_key_format() -> None:
    key = artifact_key("nightly", "shop", MOMENT, "sql.gz")
    assert key == "nightly/2026/03/nightly_shop_20260307T040506Z.sql.gz"
    assert manifest_key(key) == key + ".manifest.json"
    assert format_timestamp(MOMENT) == "20260307T040506Z"


def test_artifact_key_suffix_breaks_clashes() -> None:
    assert artifact_key("j", "db", MOMENT, "dump", suffix=2).endswith(
        "j_db-2_20260307T040506Z.dump"
    )


def test_artifact_key_never_escapes() -> None:
    key = artifact_key("../../evil", "../db", MOMENT, "dump")
    parts = key.split("/")
    assert len(parts) == 4  # job/YYYY/MM/file
    assert not any(part in (".", "..") for part in parts)


def test_storage_rejects_escaping_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path))
    from app.core.config import get_settings

    get_settings.cache_clear()
    storage = LocalStorage({"path": "dest"}, {})
    source = tmp_path / "src.bin"
    source.write_bytes(b"x")
    for bad in ("../outside.bin", "a/../../outside.bin", "/etc/passwd", "a\\b", ""):
        with pytest.raises(PathNotAllowed):
            storage.put_sync(source, bad)
    assert not (tmp_path / "outside.bin").exists()


@pytest.mark.parametrize(
    ("database_id", "label"),
    [("(default)", "default"), ("my-db", "my-db"), ("a/b", "a_b"), ("(x)y", "x_y")],
)
def test_database_label_for_firestore(database_id: str, label: str) -> None:
    assert database_label(database_id) == label
    assert artifact_key("job", database_label(database_id), MOMENT, "tar.gz").endswith(".tar.gz")

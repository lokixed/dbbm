import json
import os
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, col, select

from app.core.config import get_settings
from app.core.db import get_engine
from app.models import AuditLog
from tests.conftest import api_delete, api_post, api_put


def body(name: str = "local1", path: str = "daily", **over: Any) -> dict[str, Any]:
    result: dict[str, Any] = {"type": "local", "name": name, "config": {"path": path}}
    result.update(over)
    return result


def create(client: TestClient, payload: dict[str, Any]) -> dict[str, Any]:
    response = api_post(client, "/api/destinations", payload)
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


def test_create_and_defaults(authed: TestClient) -> None:
    created = create(authed, body())
    assert created["config"] == {"path": "daily"}
    assert created["retention"] == {"keep_last": None, "keep_days": None}
    assert created["last_test"] is None
    assert "secrets" not in created
    assert authed.get("/api/destinations").json()[0]["id"] == created["id"]


def test_retention_is_stored(authed: TestClient) -> None:
    created = create(authed, body(retention={"keep_last": 7, "keep_days": 30}))
    assert created["retention"] == {"keep_last": 7, "keep_days": 30}
    updated = api_put(
        authed, f"/api/destinations/{created['id']}", body(retention={"keep_last": 3})
    )
    assert updated.json()["retention"] == {"keep_last": 3, "keep_days": None}
    bad = api_put(authed, f"/api/destinations/{created['id']}", body(retention={"keep_last": 0}))
    assert bad.status_code == 422


def test_absolute_path_inside_backup_dir_is_stored_relative(authed: TestClient) -> None:
    inside = str(get_settings().backup_dir / "weekly")
    assert create(authed, body(path=inside))["config"]["path"] == "weekly"


@pytest.mark.parametrize("path", ["../../etc", "a/../..", "/etc", "/etc/passwd", ""])
def test_path_escapes_rejected(authed: TestClient, path: str) -> None:
    response = api_post(authed, "/api/destinations", body(path=path))
    assert response.status_code == 422
    assert authed.get("/api/destinations").json() == []


def test_symlink_escape_rejected_on_create_and_update(authed: TestClient, tmp_path: Path) -> None:
    backup_dir = get_settings().backup_dir
    backup_dir.mkdir(parents=True, exist_ok=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (backup_dir / "escape").symlink_to(outside, target_is_directory=True)

    assert api_post(authed, "/api/destinations", body(path="escape")).status_code == 422
    assert api_post(authed, "/api/destinations", body(path="escape/x/y")).status_code == 422
    created = create(authed, body())
    response = api_put(authed, f"/api/destinations/{created['id']}", body(path="escape"))
    assert response.status_code == 422
    assert not any(outside.iterdir())


def test_only_local_type_is_accepted(authed: TestClient) -> None:
    payload = body(type="gdrive")
    assert api_post(authed, "/api/destinations", payload).status_code == 422


def test_duplicate_names_return_409(authed: TestClient) -> None:
    create(authed, body("dup", "a"))
    assert api_post(authed, "/api/destinations", body("dup", "b")).status_code == 409
    other = create(authed, body("other", "c"))
    assert api_put(authed, f"/api/destinations/{other['id']}", body("dup", "c")).status_code == 409


def test_test_reports_writable_and_free_space(authed: TestClient) -> None:
    created = create(authed, body(path="nested/dir"))
    response = api_post(authed, f"/api/destinations/{created['id']}/test")
    result = response.json()
    assert response.status_code == 200 and result["ok"] is True
    assert "Writable" in result["message"] and result["free_bytes"] > 0
    assert [c["name"] for c in result["checks"]] == ["path", "writable"]

    directory = get_settings().backup_dir / "nested" / "dir"
    assert directory.is_dir() and list(directory.iterdir()) == []
    last = authed.get(f"/api/destinations/{created['id']}").json()["last_test"]
    assert last["ok"] is True


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_test_reports_unwritable_directory(authed: TestClient) -> None:
    created = create(authed, body(path="ro"))
    api_post(authed, f"/api/destinations/{created['id']}/test")
    directory = get_settings().backup_dir / "ro"
    directory.chmod(0o500)
    try:
        result = api_post(authed, f"/api/destinations/{created['id']}/test").json()
    finally:
        directory.chmod(0o700)
    assert result["ok"] is False and str(directory) not in result["message"]


def test_test_fails_when_path_later_becomes_a_symlink_escape(
    authed: TestClient, tmp_path: Path
) -> None:
    created = create(authed, body(path="later"))
    backup_dir = get_settings().backup_dir
    backup_dir.mkdir(parents=True, exist_ok=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (backup_dir / "later").symlink_to(outside, target_is_directory=True)

    result = api_post(authed, f"/api/destinations/{created['id']}/test").json()
    assert result["ok"] is False and "outside" in result["message"]
    assert not any(outside.iterdir())


def test_delete_and_audit(authed: TestClient) -> None:
    created = create(authed, body())
    url = f"/api/destinations/{created['id']}"
    api_put(authed, url, body("renamed"))
    api_post(authed, f"{url}/test")
    assert api_delete(authed, url).status_code == 204
    assert authed.get(url).status_code == 404

    with Session(get_engine()) as db:
        logged = db.exec(select(AuditLog).order_by(col(AuditLog.timestamp))).all()
    rows = [r for r in logged if r.object_type == "destination"]
    assert [r.action for r in rows] == [
        "destination.create",
        "destination.update",
        "destination.test",
        "destination.delete",
    ]
    assert rows[2].details["ok"] is True
    assert "path" not in json.dumps(rows[3].details)

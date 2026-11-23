"""Backups browser: list, download, verify, delete and drift detection."""

import asyncio
import hashlib
import os
import tracemalloc
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, col, select

from app.core.config import get_settings
from app.core.db import get_engine
from app.models import Artifact, AuditLog, Destination
from app.services import backups as svc
from app.services.hashing import sha256_file
from tests.conftest import api_delete, api_post
from tests.fake_rclone import FakeRclone
from tests.runs_helpers import make_destination, make_job, make_source, run_now
from tests.test_runs_m5 import make_drive

pytestmark = pytest.mark.usefixtures("fake_bins", "fake_drivers")


def backed_up(client: TestClient, *, drive: bool = False) -> dict[str, Any]:
    destinations = [make_destination(client)]
    if drive:
        destinations.append(make_drive(client))
    job = make_job(client, make_source(client), destinations)
    run = run_now(client, job)
    assert run["status"] == "success", run["error"]
    return run


def listing(client: TestClient, query: str = "") -> dict[str, Any]:
    response = client.get(f"/api/backups{query}")
    assert response.status_code == 200, response.text
    page: dict[str, Any] = response.json()
    return page


def audit_actions() -> list[str]:
    with Session(get_engine()) as db:
        return list(db.exec(select(AuditLog.action).order_by(col(AuditLog.timestamp))).all())


def local_file(path: str) -> Any:
    return get_settings().backup_dir / "daily" / path


def test_requires_login(client: TestClient) -> None:
    assert client.get("/api/backups").status_code == 401


def test_list_shows_artifacts_with_filters(authed: TestClient) -> None:
    run = backed_up(authed)
    page = listing(authed)
    assert page["total"] == 2
    first = page["items"][0]
    assert first["job_name"] == "nightly" and first["source_name"] == "mysql-main"
    assert first["destination_name"] == "local1" and first["destination_type"] == "local"
    assert first["missing"] is False and first["verified_at"] is None
    assert {i["source_database"] for i in page["items"]} == {"shop", "blog"}

    assert listing(authed, "?database=shop")["total"] == 1
    assert listing(authed, "?database=nope")["total"] == 0
    assert listing(authed, f"?job_id={run['job_id']}")["total"] == 2
    assert listing(authed, f"?destination_id={first['destination_id']}")["total"] == 2
    assert listing(authed, f"?source_id={first['source_id']}")["total"] == 2
    assert listing(authed, "?limit=1")["total"] == 2
    assert len(listing(authed, "?limit=1&offset=1")["items"]) == 1
    assert authed.get("/api/backups?status=bogus").status_code == 422


def test_download_streams_the_file_and_is_audited(authed: TestClient) -> None:
    backed_up(authed)
    item = listing(authed)["items"][0]
    response = authed.get(f"/api/backups/{item['id']}/download")
    assert response.status_code == 200
    assert hashlib.sha256(response.content).hexdigest() == item["sha256"]
    disposition = response.headers["content-disposition"]
    assert item["path"].rsplit("/", 1)[1] in disposition and "attachment" in disposition
    assert "backup.download" in audit_actions()


def test_signed_download_link_works_once_without_cookies(authed: TestClient) -> None:
    backed_up(authed)
    item = listing(authed)["items"][0]
    link = api_post(authed, f"/api/backups/{item['id']}/download-link")
    assert link.status_code == 200 and link.json()["expires_in"] == 60
    url = link.json()["url"]
    authed.cookies.clear()
    first = authed.get(url)
    assert first.status_code == 200
    assert hashlib.sha256(first.content).hexdigest() == item["sha256"]
    assert authed.get(url).status_code == 403  # one use only
    assert {"backup.download_link", "backup.download"} <= set(audit_actions())


def test_signed_download_link_rejects_tampering_and_needs_login(
    authed: TestClient, client: TestClient
) -> None:
    backed_up(authed)
    item = listing(authed)["items"][0]
    url = api_post(authed, f"/api/backups/{item['id']}/download-link").json()["url"]
    assert authed.get(url[:-2] + "xx").status_code == 403
    authed.cookies.clear()
    assert api_post(authed, f"/api/backups/{item['id']}/download-link").status_code in (401, 403)


def test_download_refuses_a_corrupt_file(authed: TestClient) -> None:
    backed_up(authed)
    item = listing(authed)["items"][0]
    local_file(item["path"]).write_bytes(b"tampered")
    response = authed.get(f"/api/backups/{item['id']}/download")
    assert response.status_code == 409 and "corrupt" in response.json()["detail"]
    assert b"tampered" not in response.content
    assert "backup.download_failed" in audit_actions()


def test_verify_ok_corrupt_and_missing(authed: TestClient) -> None:
    backed_up(authed)
    item = listing(authed)["items"][0]
    path = local_file(item["path"])

    ok = api_post(authed, f"/api/backups/{item['id']}/verify").json()
    assert ok["status"] == "ok" and ok["verified_at"]
    assert any(i["verified_at"] for i in listing(authed)["items"])

    original = path.read_bytes()
    path.write_bytes(original + b"x")
    assert api_post(authed, f"/api/backups/{item['id']}/verify").json()["status"] == "corrupt"

    path.unlink()
    assert api_post(authed, f"/api/backups/{item['id']}/verify").json()["status"] == "missing"
    assert listing(authed, "?status=missing")["total"] == 1
    assert authed.get(f"/api/backups/{item['id']}/download").status_code == 404

    path.write_bytes(original)
    assert api_post(authed, f"/api/backups/{item['id']}/verify").json()["status"] == "ok"
    assert listing(authed, "?status=missing")["total"] == 0


def test_delete_removes_file_manifest_and_listing(authed: TestClient) -> None:
    backed_up(authed)
    item = listing(authed)["items"][0]
    path = local_file(item["path"])
    manifest = path.with_name(path.name + ".manifest.json")
    assert path.exists() and manifest.exists()

    assert api_delete(authed, f"/api/backups/{item['id']}").status_code == 204
    assert not path.exists() and not manifest.exists()
    assert listing(authed)["total"] == 1
    assert api_delete(authed, f"/api/backups/{item['id']}").status_code == 404
    assert "backup.delete" in audit_actions()


def test_rescan_marks_and_clears_drift(authed: TestClient) -> None:
    backed_up(authed)
    items = listing(authed)["items"]
    dest_id = items[0]["destination_id"]
    local_file(items[0]["path"]).unlink()

    result = api_post(authed, f"/api/backups/rescan?destination_id={dest_id}").json()
    assert result == {"destination_id": dest_id, "checked": 2, "missing": 1}
    missing = listing(authed, "?status=missing")
    assert [i["id"] for i in missing["items"]] == [items[0]["id"]]

    local_file(items[0]["path"]).write_bytes(b"back again")
    api_post(authed, f"/api/backups/rescan?destination_id={dest_id}")
    assert listing(authed, "?status=missing")["total"] == 0
    assert "backup.rescan" in audit_actions()
    unknown = api_post(authed, "/api/backups/rescan?destination_id=" + "0" * 32)
    assert unknown.status_code == 404


def copies_of(client: TestClient, database: str) -> dict[str, dict[str, Any]]:
    items = [i for i in listing(client)["items"] if i["source_database"] == database]
    return {i["destination_type"]: i for i in items}


def test_download_prefers_local_then_falls_back_to_drive(
    authed: TestClient, fake_rclone: FakeRclone
) -> None:
    backed_up(authed, drive=True)
    copies = copies_of(authed, "shop")
    assert set(copies) == {"local", "gdrive"}
    local, drive = copies["local"], copies["gdrive"]
    work = get_settings().work_dir

    # Asking for the Drive copy serves the local one: nothing is downloaded from Drive.
    calls_before = fake_rclone.commands().count("copyto")
    assert authed.get(f"/api/backups/{drive['id']}/download").status_code == 200
    assert fake_rclone.commands().count("copyto") == calls_before

    # With the local file gone, the same request is served from Drive and cleaned up after.
    local_file(local["path"]).unlink()
    response = authed.get(f"/api/backups/{local['id']}/download")
    assert response.status_code == 200
    assert hashlib.sha256(response.content).hexdigest() == local["sha256"]
    assert not list((work / "downloads").glob("*"))


def test_corrupt_local_copy_is_not_replaced_by_the_drive_copy(
    authed: TestClient, fake_rclone: FakeRclone
) -> None:
    backed_up(authed, drive=True)
    local = copies_of(authed, "shop")["local"]
    local_file(local["path"]).write_bytes(b"tampered")
    response = authed.get(f"/api/backups/{local['id']}/download")
    assert response.status_code == 409 and "corrupt" in response.json()["detail"]


def test_drive_verify_uses_md5_and_detects_corruption(
    authed: TestClient, fake_rclone: FakeRclone
) -> None:
    backed_up(authed, drive=True)
    drive = copies_of(authed, "shop")["gdrive"]
    assert api_post(authed, f"/api/backups/{drive['id']}/verify").json()["status"] == "ok"
    assert "get" not in fake_rclone.commands() and "copyto" in fake_rclone.commands()

    fake_rclone.set("md5_override", "0" * 32)
    assert api_post(authed, f"/api/backups/{drive['id']}/verify").json()["status"] == "corrupt"


# ---------- large files and ranges ----------


def test_download_supports_range_requests(authed: TestClient) -> None:
    backed_up(authed)
    item = listing(authed)["items"][0]
    full = authed.get(f"/api/backups/{item['id']}/download").content

    part = authed.get(f"/api/backups/{item['id']}/download", headers={"Range": "bytes=2-9"})
    assert part.status_code == 206 and part.content == full[2:10]
    assert part.headers["content-range"] == f"bytes 2-9/{len(full)}"
    assert part.headers["accept-ranges"] == "bytes"


def test_verifying_a_large_file_uses_constant_memory(authed: TestClient) -> None:
    backed_up(authed)
    item = listing(authed)["items"][0]
    path = local_file(item["path"])
    chunk = os.urandom(1024 * 1024)
    with path.open("wb") as handle:
        for _ in range(96):  # 96 MiB
            handle.write(chunk)
    digest, size = sha256_file(path)
    with Session(get_engine()) as db:
        artifact = db.get(Artifact, uuid.UUID(item["id"]))
        assert artifact is not None
        artifact.sha256, artifact.size_bytes = digest, size
        row = db.get(Destination, artifact.destination_id)
        assert row is not None
        db.add(artifact)
        db.commit()
        db.refresh(artifact)

        tracemalloc.start()
        try:
            download = asyncio.run(svc.prepare_download(db, artifact, row))
            _current, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
    assert download.path == path and download.cleanup_dir is None
    assert peak < 16 * 1024 * 1024, f"peak {peak} bytes for a {size}-byte file"

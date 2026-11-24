"""Firestore restore: unpack, stage, import, clean up (fake Google REST, no Google Cloud)."""

import io
import json
import tarfile
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.api import backups as backups_api
from app.core.config import get_settings
from app.core.db import get_engine
from app.drivers import firestore
from app.models import Artifact
from app.services.hashing import sha256_file
from tests.conftest import api_post
from tests.fake_google import IMPORT_OPERATION, FakeGoogle
from tests.runs_helpers import (
    make_destination,
    make_job,
    run_now,
    wait_final,
    wait_until,
    work_entries,
)
from tests.test_restore_api import restore, restore_body
from tests.test_runs_m4 import make_firestore
from tests.test_sources_api import SA_KEY, firestore_body

pytestmark = pytest.mark.usefixtures("fake_bins", "fake_drivers", "fake_google")

SECRET_PARTS = ("TOPSECRETKEYMATERIAL", "BEGIN PRIVATE KEY", "ya29.")
BUCKET = "my-bucket"


@pytest.fixture(autouse=True)
def fast_polling(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(firestore, "POLL_SECONDS", 0.01)
    backups_api._restore_starts.clear()


def make_target(client: TestClient, name: str = "fs-target") -> dict[str, Any]:
    response = api_post(client, "/api/sources", firestore_body(name))
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


def firestore_backup(client: TestClient) -> tuple[dict[str, Any], dict[str, Any]]:
    source = make_firestore(client)
    assert (
        run_now(client, make_job(client, source, [make_destination(client)]))["status"] == "success"
    )
    return source, client.get("/api/backups").json()["items"][0]


def start(client: TestClient, backup: dict[str, Any], target: dict[str, Any], **over: Any) -> str:
    response = restore(client, backup, restore_body(target, "default", **over))
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def replace_artifact(backup: dict[str, Any], data: bytes) -> None:
    """Swap the stored file for `data`, keeping the recorded checksum consistent with it."""
    path = get_settings().backup_dir / "daily" / backup["path"]
    path.write_bytes(data)
    digest, size = sha256_file(path)
    with Session(get_engine()) as db:
        artifact = db.get(Artifact, uuid.UUID(backup["id"]))
        assert artifact is not None
        artifact.sha256, artifact.size_bytes = digest, size
        db.add(artifact)
        db.commit()


def tarball(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, content in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


def test_firestore_restore_stages_imports_and_cleans_up(
    authed: TestClient, fake_google: FakeGoogle
) -> None:
    _, backup = firestore_backup(authed)
    target = make_target(authed)
    run = wait_final(authed, start(authed, backup, target))

    assert run["status"] == "success", run["error"]
    assert run["kind"] == "restore"
    assert run["step_results"]["restore"]["target_database"] == "default"
    uploaded = fake_google.uploaded
    assert len(uploaded) == 2 and all(n.startswith("restore/") for n in uploaded)
    assert any(n.endswith("/all_namespaces/kind_items/output-0") for n in uploaded)
    [body] = fake_google.import_bodies
    uri = body["inputUriPrefix"]
    assert uri.startswith(f"gs://{BUCKET}/restore/") and uri.endswith(".overall_export_metadata")
    assert uri.removeprefix(f"gs://{BUCKET}/") in uploaded  # the import reads what was uploaded
    assert set(uploaded) <= set(fake_google.deleted)  # then the staging copy is removed
    assert fake_google.objects_under(BUCKET, "restore/") == []
    log = authed.get(f"/api/runs/{run['id']}/logs?format=text").text
    assert "Import operation" in log and "Import state" in log
    assert not any(p in log + json.dumps(run) for p in (*SECRET_PARTS, SA_KEY[:20]))
    assert work_entries() == []


def test_the_import_database_is_fixed_by_the_target_source(authed: TestClient) -> None:
    _, backup = firestore_backup(authed)
    target = make_target(authed)
    wrong = restore(authed, backup, restore_body(target, "other_db"))
    assert wrong.status_code == 422 and "database 'default'" in wrong.json()["detail"]
    drop = restore(authed, backup, restore_body(target, "default", drop=True))
    assert drop.status_code == 422 and "never deletes" in drop.json()["detail"]


def test_importing_into_the_original_source_needs_extra_confirmation(
    authed: TestClient, fake_google: FakeGoogle
) -> None:
    source, backup = firestore_backup(authed)
    refused = restore(authed, backup, restore_body(source, "default"))
    assert refused.status_code == 409 and "overwrite the original" in refused.json()["detail"]
    assert fake_google.import_bodies == []
    accepted = restore(
        authed, backup, restore_body(source, "default", confirm_overwrite_original=True)
    )
    assert accepted.status_code == 201
    assert wait_final(authed, accepted.json()["id"])["status"] == "success"


@pytest.mark.parametrize(
    ("kind", "mode", "message"),
    [
        ("import", "fail", "Starting the Firestore import failed"),
        ("upload", "fail", "Uploading the backup to the staging bucket failed"),
        ("describe", "error", "The Firestore import failed"),
    ],
)
def test_failures_are_reported_and_the_staging_copy_is_removed(
    authed: TestClient, fake_google: FakeGoogle, kind: str, mode: str, message: str
) -> None:
    _, backup = firestore_backup(authed)
    target = make_target(authed)
    fake_google.modes[kind] = mode

    run = wait_final(authed, start(authed, backup, target))

    assert run["status"] == "failed" and message in run["error"]
    assert fake_google.objects_under(BUCKET, "restore/") == []  # nothing is left in the bucket
    assert not any(p in json.dumps(run) for p in SECRET_PARTS) and work_entries() == []


def test_a_failed_staging_cleanup_is_a_warning_not_a_failure(
    authed: TestClient, fake_google: FakeGoogle
) -> None:
    _, backup = firestore_backup(authed)
    target = make_target(authed)
    fake_google.modes["delete"] = "fail"
    run = wait_final(authed, start(authed, backup, target))
    assert run["status"] == "success"
    log = authed.get(f"/api/runs/{run['id']}/logs?format=text").text
    assert "Could not delete the staging copy" in log and "lifecycle rule" in log


def test_cancelling_during_the_import_cancels_the_operation(
    authed: TestClient, fake_google: FakeGoogle
) -> None:
    _, backup = firestore_backup(authed)
    target = make_target(authed)
    fake_google.modes["describe"] = "forever"
    polls_before = fake_google.describe_count  # left by the backup run
    run_id = start(authed, backup, target)
    wait_until(lambda: fake_google.describe_count > polls_before)

    assert api_post(authed, f"/api/runs/{run_id}/cancel").status_code == 200
    assert wait_final(authed, run_id)["status"] == "cancelled"
    assert fake_google.cancelled == [IMPORT_OPERATION]
    assert fake_google.objects_under(BUCKET, "restore/") == [] and work_entries() == []


def test_an_archive_without_export_metadata_is_refused(
    authed: TestClient, fake_google: FakeGoogle
) -> None:
    _, backup = firestore_backup(authed)
    target = make_target(authed)
    replace_artifact(backup, tarball({"ts/all_namespaces/kind_items/output-0": b"data"}))
    run = wait_final(authed, start(authed, backup, target))
    assert run["status"] == "failed" and "not a complete export" in run["error"]
    assert fake_google.uploaded == [] and work_entries() == []


def test_an_archive_cannot_write_outside_the_work_directory(
    authed: TestClient, fake_google: FakeGoogle
) -> None:
    _, backup = firestore_backup(authed)
    target = make_target(authed)
    escaped = get_settings().work_dir.parent / "escaped.txt"
    replace_artifact(
        backup, tarball({"../../escaped.txt": b"pwned", "ts/ts.overall_export_metadata": b"m"})
    )
    run = wait_final(authed, start(authed, backup, target))
    assert run["status"] == "failed" and "Cannot unpack" in run["error"]
    assert not escaped.exists() and fake_google.uploaded == []

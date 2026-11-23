"""Backup encryption with age (fake `age` tool): keys, encrypted backups, restore with a key."""

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, col, delete, select

from app.api import backups as backups_api
from app.core.config import get_settings
from app.core.db import get_engine
from app.models import Artifact, AuditLog, JobRecipient
from app.services.age import fingerprint
from app.services.hashing import sha256_file
from tests.conftest import api_delete, api_post, api_put
from tests.fake_rclone import FakeRclone
from tests.fakes import FakeBins
from tests.runs_helpers import (
    PW,
    backup_files,
    make_destination,
    make_job,
    make_source,
    run_now,
    wait_final,
    work_entries,
)
from tests.test_restore_api import restore, restore_body
from tests.test_runs_m4 import make_mongo
from tests.test_runs_m5 import make_drive

pytestmark = pytest.mark.usefixtures("fake_bins", "fake_drivers")

ALPHABET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"  # the bech32 characters age keys are made of


def keypair(seed: str) -> tuple[str, str]:
    """A (public, private) key pair in age's format; the fake `age` matches them by body."""
    digest = hashlib.sha256(seed.encode()).digest() * 2
    body = "".join(ALPHABET[b % 32] for b in digest[:58])
    return "age1" + body, "AGE-SECRET-KEY-1" + body.upper()


@pytest.fixture(autouse=True)
def reset_rate_limit() -> None:
    backups_api._restore_starts.clear()


def add_key(client: TestClient, name: str, seed: str) -> dict[str, Any]:
    public, _private = keypair(seed)
    response = api_post(client, "/api/recipients", {"name": name, "public_key": public})
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


def encrypted_job(
    client: TestClient, source: dict[str, Any], keys: list[dict[str, Any]], **over: Any
) -> dict[str, Any]:
    destinations = over.pop("destinations", None) or [make_destination(client)]
    return make_job(
        client, source, destinations, encrypt=True, recipient_ids=[k["id"] for k in keys], **over
    )


def backups_of(client: TestClient, database: str) -> list[dict[str, Any]]:
    items = client.get("/api/backups?limit=200").json()["items"]
    return [i for i in items if i["source_database"] == database]


def stored_file(path: str) -> Path:
    return get_settings().backup_dir / "daily" / path


def audit_text() -> str:
    with Session(get_engine()) as db:
        rows = db.exec(select(AuditLog)).all()
    return json.dumps([[r.action, r.details] for r in rows], default=str)


def everything_on_disk() -> bytes:
    """All bytes under the data, backup and work directories (logs, database, artifacts)."""
    settings = get_settings()
    blobs = []
    for root in (settings.data_dir, settings.backup_dir):
        blobs += [p.read_bytes() for p in root.rglob("*") if p.is_file()]
    return b"".join(blobs)


# ---------- keys ----------


def test_recipient_keys_are_validated_and_listed(authed: TestClient) -> None:
    public, private = keypair("alice")
    added = api_post(authed, "/api/recipients", {"name": "alice", "public_key": public}).json()
    assert added["fingerprint"] == fingerprint(public) and added["used_by"] == []
    assert authed.get("/api/recipients").json()[0]["public_key"] == public

    as_private = api_post(authed, "/api/recipients", {"name": "x", "public_key": private})
    assert as_private.status_code == 422 and "private key" in as_private.text
    assert private not in as_private.text  # the rejected secret is not echoed back
    junk = api_post(authed, "/api/recipients", {"name": "y", "public_key": "age1short"})
    assert junk.status_code == 422 and "Not a valid age public key" in junk.text
    dup = api_post(authed, "/api/recipients", {"name": "alice2", "public_key": public})
    assert dup.status_code == 409
    assert "recipient.add" in audit_text() and fingerprint(public) in audit_text()
    assert private not in audit_text()


def test_a_key_in_use_cannot_be_deleted(authed: TestClient) -> None:
    key = add_key(authed, "alice", "alice")
    job = encrypted_job(authed, make_source(authed), [key])
    assert job["encrypt"] is True and [r["name"] for r in job["recipients"]] == ["alice"]
    refused = api_delete(authed, f"/api/recipients/{key['id']}")
    assert refused.status_code == 409 and "nightly" in refused.json()["detail"]
    body = {
        "name": "nightly",
        "source_id": job["source"]["id"],
        "destination_ids": [d["id"] for d in job["destinations"]],
    }
    assert api_put(authed, f"/api/jobs/{job['id']}", body).status_code == 200
    assert api_delete(authed, f"/api/recipients/{key['id']}").status_code == 204


def test_a_job_needs_a_key_to_encrypt(authed: TestClient) -> None:
    source, dest = make_source(authed), make_destination(authed)
    body = {
        "name": "j",
        "source_id": source["id"],
        "destination_ids": [dest["id"]],
        "encrypt": True,
    }
    none = api_post(authed, "/api/jobs", body)
    assert none.status_code == 422 and "at least one encryption key" in none.text
    unknown = api_post(authed, "/api/jobs", body | {"recipient_ids": [str(uuid.uuid4())]})
    assert unknown.status_code == 422 and "key not found" in unknown.text


# ---------- encrypted backups ----------


def test_encrypted_backup_is_ciphertext_to_every_key(
    authed: TestClient, fake_bins: FakeBins
) -> None:
    keys = [add_key(authed, "alice", "alice"), add_key(authed, "recovery", "recovery")]
    job = encrypted_job(authed, make_source(authed, databases=["shop"]), keys)
    run = run_now(authed, job)

    assert run["status"] == "success", run["error"]
    (artifact,) = run["artifacts"]
    assert artifact["path"].endswith(".sql.gz.age") and artifact["encrypted"] is True
    assert artifact["recipients"] == [k["fingerprint"] for k in keys]
    data = stored_file(artifact["path"]).read_bytes()
    assert data.startswith(b"AGEFAKE") and b"CREATE TABLE" not in data
    assert (
        hashlib.sha256(data).hexdigest() == artifact["sha256"]
    )  # the checksum is of the ciphertext
    assert artifact["size_bytes"] == len(data)
    args = fake_bins.read("age.encrypt.args").split()
    assert args.count("-r") == 2 and keypair("alice")[0] in args and keypair("recovery")[0] in args

    manifest_path = stored_file(artifact["path"] + ".manifest.json")
    manifest = json.loads(manifest_path.read_text())
    assert manifest["encrypted"] is True and manifest["recipients"] == artifact["recipients"]
    assert manifest["sha256"] == artifact["sha256"]
    assert [s["name"] for s in run["step_results"]["units"][0]["dump"]["steps"]][-1] == "encrypt"
    assert work_entries() == [] and len(backup_files()) == 2  # the artifact and its manifest
    (listed,) = backups_of(authed, "shop")
    assert listed["encrypted"] is True and listed["recipients"] == artifact["recipients"]


def test_plain_jobs_are_not_encrypted(authed: TestClient, fake_bins: FakeBins) -> None:
    run = run_now(authed, make_job(authed, make_source(authed), [make_destination(authed)]))
    assert run["status"] == "success"
    assert all(not a["encrypted"] and not a["path"].endswith(".age") for a in run["artifacts"])
    assert not fake_bins.exists("age.encrypt.args")


def test_encryption_fails_closed_when_age_fails(authed: TestClient, fake_bins: FakeBins) -> None:
    job = encrypted_job(
        authed, make_source(authed, databases=["shop"]), [add_key(authed, "a", "a")]
    )
    fake_bins.set_mode("age", "fail")
    run = run_now(authed, job)

    assert run["status"] == "failed" and "Encryption failed" in run["error"]
    assert run["artifacts"] == [] and backup_files() == [] and work_entries() == []
    log = authed.get(f"/api/runs/{run['id']}/logs?format=text").text
    assert "Encryption of shop failed" in log


def test_encryption_fails_closed_without_keys(authed: TestClient, fake_bins: FakeBins) -> None:
    job = encrypted_job(authed, make_source(authed), [add_key(authed, "a", "a")])
    with Session(get_engine()) as db:  # as if the link were lost
        db.exec(delete(JobRecipient))
        db.commit()
    run = run_now(authed, job)
    assert run["status"] == "failed" and "no encryption key" in run["error"]
    assert backup_files() == [] and not fake_bins.exists("age.encrypt.args")


def test_encryption_fails_closed_when_age_is_missing(
    authed: TestClient, fake_bins: FakeBins
) -> None:
    job = encrypted_job(authed, make_source(authed), [add_key(authed, "a", "a")])
    (fake_bins.dir / "age").unlink()
    run = run_now(authed, job)
    assert run["status"] == "failed" and "age is not installed" in run["error"]
    assert backup_files() == []


def test_download_serves_the_ciphertext_as_stored(authed: TestClient) -> None:
    job = encrypted_job(
        authed, make_source(authed, databases=["shop"]), [add_key(authed, "a", "a")]
    )
    run_now(authed, job)
    (item,) = backups_of(authed, "shop")
    response = authed.get(f"/api/backups/{item['id']}/download")
    assert response.status_code == 200 and response.content.startswith(b"AGEFAKE")
    assert ".sql.gz.age" in response.headers["content-disposition"]


def test_drive_stores_and_verifies_the_ciphertext(
    authed: TestClient, fake_rclone: FakeRclone
) -> None:
    key = add_key(authed, "a", "a")
    destinations = [make_destination(authed), make_drive(authed)]
    job = encrypted_job(
        authed, make_source(authed, databases=["shop"]), [key], destinations=destinations
    )
    run = run_now(authed, job)
    assert run["status"] == "success", run["error"]
    remote = [p for p in fake_rclone.remote.rglob("*.age") if p.is_file()]
    assert len(remote) == 1 and remote[0].read_bytes().startswith(b"AGEFAKE")
    drive = next(a for a in run["artifacts"] if a["md5"])
    assert drive["encrypted"] is True


def test_retry_copies_the_already_encrypted_file(
    authed: TestClient, fake_rclone: FakeRclone, fake_bins: FakeBins
) -> None:
    key = add_key(authed, "a", "a")
    destinations = [make_destination(authed), make_drive(authed)]
    job = encrypted_job(
        authed, make_source(authed, databases=["shop"]), [key], destinations=destinations
    )
    fake_rclone.fail("copyto", 1, "ERROR : Failed to copy: dial tcp: lookup x: no such host")
    run = run_now(authed, job)
    assert run["status"] == "partial"
    encryptions = fake_bins.read("age.encrypt.args")
    fake_rclone.clear("copyto")

    response = api_post(authed, f"/api/runs/{run['id']}/retry")
    assert response.status_code == 201, response.text
    assert wait_final(authed, response.json()["id"])["status"] == "success"
    assert fake_bins.read("age.encrypt.args") == encryptions  # not encrypted a second time
    remote = [p for p in fake_rclone.remote.rglob("*.age") if p.is_file()]
    assert len(remote) == 1 and remote[0].read_bytes().startswith(b"AGEFAKE")
    stored = [i for i in backups_of(authed, "shop") if i["destination_type"] == "gdrive"]
    assert (
        stored
        and stored[0]["encrypted"] is True
        and stored[0]["recipients"] == [key["fingerprint"]]
    )


# ---------- restore ----------


def encrypted_mongo(client: TestClient, keys: list[dict[str, Any]]) -> dict[str, Any]:
    source = make_mongo(client, databases=["shop"])
    assert run_now(client, encrypted_job(client, source, keys))["status"] == "success"
    (backup,) = backups_of(client, "shop")
    return backup


def restore_encrypted(
    client: TestClient, backup: dict[str, Any], private_key: str | None, **over: Any
) -> Any:
    existing = [s for s in client.get("/api/sources").json() if s["name"] == "mongo-scratch"]
    target = existing[0] if existing else make_mongo(client, "mongo-scratch")
    body = restore_body(target, "shop_copy", **over)
    if private_key is not None:
        body["private_key"] = private_key
    return restore(client, backup, body)


def test_restore_decrypts_with_the_private_key(authed: TestClient, fake_bins: FakeBins) -> None:
    key = add_key(authed, "alice", "alice")
    backup = encrypted_mongo(authed, [key])
    _public, private = keypair("alice")

    started = restore_encrypted(authed, backup, private)
    assert started.status_code == 201, started.text
    run = wait_final(authed, started.json()["id"])

    assert run["status"] == "success", run["error"]
    assert [s["name"] for s in run["step_results"]["steps"]] == [
        "preflight", "fetch backup", "decrypt", "restore", "cleanup",
    ]  # fmt: skip
    assert fake_bins.read("mongorestore.archive") == "fake-mongo-archive"  # plaintext arrived
    assert run["step_results"]["restore"]["encrypted"] is True
    assert work_entries() == []


def test_the_private_key_is_never_recorded_anywhere(
    authed: TestClient, fake_bins: FakeBins
) -> None:
    key = add_key(authed, "alice", "alice")
    backup = encrypted_mongo(authed, [key])
    _public, private = keypair("alice")
    started = restore_encrypted(authed, backup, private)
    run = wait_final(authed, started.json()["id"])
    assert run["status"] == "success"
    secret = private.encode()

    log = authed.get(f"/api/runs/{run['id']}/logs?format=text").text
    for text in (started.text, json.dumps(run), log, audit_text(), authed.get("/api/backups").text):
        assert private not in text
    assert private not in fake_bins.read("age.decrypt.args")  # not in the process arguments
    assert "-i -" in fake_bins.read("age.decrypt.args")  # it was read from standard input
    assert secret not in everything_on_disk()  # not in the database, logs or files
    assert PW not in log


def test_a_wrong_key_fails_cleanly_and_changes_nothing(
    authed: TestClient, fake_bins: FakeBins
) -> None:
    backup = encrypted_mongo(authed, [add_key(authed, "alice", "alice")])
    _public, wrong = keypair("someone-else")
    run = wait_final(authed, restore_encrypted(authed, backup, wrong).json()["id"])
    assert run["status"] == "failed"
    assert "does not match any key" in run["error"] and "Nothing was changed" in run["error"]
    assert not fake_bins.exists("mongorestore.args") and work_entries() == []
    assert wrong not in json.dumps(run)


def test_any_one_of_several_keys_can_decrypt(authed: TestClient, fake_bins: FakeBins) -> None:
    keys = [add_key(authed, "alice", "alice"), add_key(authed, "recovery", "recovery")]
    backup = encrypted_mongo(authed, keys)
    for seed in ("recovery", "alice"):
        started = restore_encrypted(authed, backup, keypair(seed)[1])
        assert started.status_code == 201, started.text
        assert wait_final(authed, started.json()["id"])["status"] == "success"
        backups_api._restore_starts.clear()


def test_a_missing_or_malformed_key_is_refused_up_front(
    authed: TestClient, fake_bins: FakeBins
) -> None:
    backup = encrypted_mongo(authed, [add_key(authed, "alice", "alice")])
    missing = restore_encrypted(authed, backup, None)
    assert missing.status_code == 422 and "encrypted" in missing.json()["detail"]
    for text in ("hunter2", keypair("alice")[0], "AGE-SECRET-KEY-1TOOSHORT"):
        bad = restore_encrypted(authed, backup, text)
        assert bad.status_code == 422 and "not an age private key" in bad.json()["detail"]
        assert text not in bad.text
    assert not fake_bins.exists("age.decrypt.args")


def test_a_key_file_with_comments_is_accepted(authed: TestClient) -> None:
    backup = encrypted_mongo(authed, [add_key(authed, "alice", "alice")])
    public, private = keypair("alice")
    pasted = f"# created: 2026-11-23\n# public key: {public}\n{private}\n"
    started = restore_encrypted(authed, backup, pasted)
    assert started.status_code == 201, started.text
    assert wait_final(authed, started.json()["id"])["status"] == "success"


def damage(backup: dict[str, Any], change: Any) -> None:
    """Rewrite the stored ciphertext and keep the recorded checksum honest, so the checksum
    check passes and the failure comes from decryption."""
    path = stored_file(backup["path"])
    path.write_bytes(change(path.read_bytes()))
    digest, size = sha256_file(path)
    with Session(get_engine()) as db:
        artifact = db.exec(
            select(Artifact).where(col(Artifact.id) == uuid.UUID(backup["id"]))
        ).one()
        artifact.sha256, artifact.size_bytes = digest, size
        db.add(artifact)
        db.commit()


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda data: data[: len(data) - 6], "truncated, corrupted or has been tampered with"),
        (lambda data: data[:-3] + b"XYZ", "truncated, corrupted or has been tampered with"),
        (lambda data: data[:40], "truncated or incomplete"),  # cut inside the header
        (lambda data: b"hello\n", "not an age-encrypted backup"),
        (lambda data: b"", "not an age-encrypted backup"),
    ],
)
def test_damaged_backups_are_refused_with_a_clear_reason(
    authed: TestClient, fake_bins: FakeBins, change: Any, message: str
) -> None:
    backup = encrypted_mongo(authed, [add_key(authed, "alice", "alice")])
    damage(backup, change)
    run = wait_final(authed, restore_encrypted(authed, backup, keypair("alice")[1]).json()["id"])
    assert run["status"] == "failed" and message in run["error"]
    assert not fake_bins.exists("mongorestore.args")


@pytest.mark.parametrize("kind", ["mysql", "postgres"])
def test_encrypted_sql_backups_restore_too(
    authed: TestClient, fake_bins: FakeBins, kind: str
) -> None:
    key = add_key(authed, "alice", "alice")
    source = make_source(authed, f"{kind}-main", kind, databases=["shop"])
    assert run_now(authed, encrypted_job(authed, source, [key]))["status"] == "success"
    (backup,) = backups_of(authed, "shop")
    assert backup["path"].endswith(".age") and backup["source_type"] == kind
    target = make_source(authed, f"{kind}-scratch", kind)
    body = restore_body(target) | {"private_key": keypair("alice")[1]}
    started = restore(authed, backup, body)
    assert started.status_code == 201, started.text
    run = wait_final(authed, started.json()["id"])
    assert run["status"] == "success", run["error"]
    if kind == "mysql":
        assert fake_bins.read("mysql.stdin").startswith("-- fake dump of shop")
    else:
        assert fake_bins.read("pg_restore.input") == "PGDMP-fake-custom-format"


def test_encrypted_firestore_backup_restores_too(
    authed: TestClient, fake_bins: FakeBins, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.drivers import firestore
    from tests.test_runs_m4 import make_firestore
    from tests.test_sources_api import firestore_body

    monkeypatch.setattr(firestore, "POLL_SECONDS", 0.01)
    key = add_key(authed, "alice", "alice")
    source = make_firestore(authed)
    assert run_now(authed, encrypted_job(authed, source, [key]))["status"] == "success"
    backup = authed.get("/api/backups").json()["items"][0]
    assert backup["path"].endswith(".tar.gz.age") and backup["encrypted"] is True
    target = api_post(authed, "/api/sources", firestore_body("fs-target")).json()

    body = restore_body(target, "default") | {"private_key": keypair("alice")[1]}
    started = restore(authed, backup, body)
    assert started.status_code == 201, started.text
    run = wait_final(authed, started.json()["id"])

    assert run["status"] == "success", run["error"]
    assert fake_bins.read("gcloud.upload.files").split() == [
        "all_namespaces/kind_items/output-0",
        "ts.overall_export_metadata",
    ]  # the decrypted export was imported, not the ciphertext

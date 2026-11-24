"""The Firestore export flow against a FAKE Google (REST over a mock transport); no network."""

import asyncio
import hashlib
import json
import tarfile
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest

from app.drivers import firestore, firestore_api
from app.drivers.base import DumpContext, DumpedArtifact, DumpError, DumpReporter, StepHandle
from app.drivers.dumping import write_tar_gz
from app.drivers.firestore import FirestoreDriver, parse_operation_status
from app.drivers.firestore_api import GoogleApiError, parse_operation_name
from app.services.hashing import OperationCancelledError
from tests.fake_google import EXPORT_OPERATION, FakeGoogle
from tests.fakes import FakeFirestore
from tests.test_drivers_dump import ListLog, files
from tests.test_firestore_driver import CONFIG, PRIVATE_KEY_PEM, SA_JSON

pytestmark = pytest.mark.usefixtures("fake_google", "fast_polling")


@pytest.fixture
def fast_polling(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(firestore, "POLL_SECONDS", 0.01)


class Recorder(DumpReporter):
    def __init__(self) -> None:
        self.steps: list[tuple[str, str, str | None]] = []
        self.warnings: list[str] = []

    @asynccontextmanager
    async def step(self, name: str) -> AsyncIterator[StepHandle]:
        handle = StepHandle(name)
        try:
            yield handle
        except BaseException:
            self.steps.append((name, "failed", None))
            raise
        self.steps.append((name, "warning" if handle.warned else "ok", handle.detail))

    def warning(self, message: str) -> None:
        self.warnings.append(message)

    def names(self) -> list[str]:
        return [name for name, _status, _detail in self.steps]

    def status(self, name: str) -> str:
        return next(status for n, status, _d in self.steps if n == name)


def driver(**over: Any) -> FirestoreDriver:
    return FakeFirestore({**CONFIG, **over}, {"service_account_json": SA_JSON})


def make_ctx(work: Path, timeout: int = 30) -> tuple[DumpContext, ListLog, Recorder]:
    work.mkdir(parents=True, exist_ok=True)
    log, recorder = ListLog(), Recorder()
    ctx = DumpContext(work, timeout, True, log, job_name="nightly", run_id="r1", report=recorder)
    return ctx, log, recorder


async def run_flow(drv: FirestoreDriver, ctx: DumpContext) -> DumpedArtifact:
    await drv.preflight(ctx)
    return await drv.dump_database("default", ctx)


def run(drv: FirestoreDriver, ctx: DumpContext) -> DumpedArtifact:
    return asyncio.run(run_flow(drv, ctx))


def staged(fake: FakeGoogle) -> list[str]:
    return fake.objects_under("my-staging", "backups/")


# ---------- parsing ----------


def test_operation_name_is_parsed_from_the_response() -> None:
    data = {"name": EXPORT_OPERATION, "metadata": {"operationState": "INITIALIZING"}}
    assert parse_operation_name(data) == EXPORT_OPERATION


@pytest.mark.parametrize(
    "data",
    [None, [], {}, {"name": 3}, {"name": "--project=evil"}, {"name": "a/b"}],
)
def test_bad_operation_response_is_rejected(data: Any) -> None:
    with pytest.raises(GoogleApiError, match="valid Firestore operation name"):
        parse_operation_name(data)


def test_operation_status_running_with_progress() -> None:
    data = {
        "done": False,
        "metadata": {
            "operationState": "PROCESSING",
            "progressDocuments": {"completedWork": "5", "estimatedWork": "10"},
            "progressBytes": {"completedWork": "100"},
        },
    }
    status = parse_operation_status(data)
    assert not status.done and status.state == "PROCESSING" and status.error is None
    assert status.progress == "5/10 documents, 100/? bytes"


def test_operation_status_done_and_failed() -> None:
    ok = parse_operation_status({"done": True, "metadata": {"operationState": "SUCCESSFUL"}})
    assert ok.done and ok.error is None
    failed = parse_operation_status({"done": True, "error": {"code": 7, "message": "denied"}})
    assert failed.done and failed.error == "denied"
    cancelled = parse_operation_status({"metadata": {"operationState": "CANCELLED"}})
    assert cancelled.done and cancelled.error is not None


def test_operation_status_garbage_is_an_error() -> None:
    with pytest.raises(DumpError):
        parse_operation_status("oops")


# ---------- naming, versions, manifest ----------


def test_database_label_and_manifest_hold_no_credentials() -> None:
    assert driver().select_databases([]) == ["default"]
    assert driver(database_id="my-db").select_databases([]) == ["my-db"]
    extra = driver(collection_ids=["users"]).manifest_extra()
    assert extra == {
        "project_id": "my-project-1",
        "database_id": "(default)",
        "collection_ids": ["users"],
        "bucket": "my-staging",
    }
    assert "private" not in json.dumps(extra)


def test_tool_versions() -> None:
    versions = asyncio.run(driver().tool_versions())
    assert set(versions) == {"google-auth", "firestore-api"} and versions["firestore-api"] == "v1"


# ---------- full success ----------


def test_full_export_flow(fake_google: FakeGoogle, tmp_path: Path) -> None:
    ctx, log, report = make_ctx(tmp_path / "work")
    art = run(driver(), ctx)

    assert report.names() == ["export", "poll", "download", "pack", "cleanup-staging"]
    assert {s for _n, s, _d in report.steps} == {"ok"} and report.warnings == []
    assert art.database == "default" and art.extension == "tar.gz" and art.compressed
    assert art.path.name.endswith(".tar.gz")
    raw = art.path.read_bytes()
    assert art.size == len(raw) and art.sha256 == hashlib.sha256(raw).hexdigest()
    with tarfile.open(art.path) as archive:
        names = archive.getnames()
    assert any(n.endswith(".overall_export_metadata") for n in names)
    assert any(n.endswith("output-0") for n in names)

    [body] = fake_google.export_bodies
    url = body["outputUriPrefix"]
    assert url.startswith("gs://my-staging/backups/nightly/r1/") and url.endswith("Z")
    assert "collectionIds" not in body
    assert len(fake_google.deleted) == 2 and staged(fake_google) == []  # staging removed
    assert any("Export state: PROCESSING (5/10 documents)" in line for line in log.lines)

    # Only the artifact remains; the session folder and the export tree are gone.
    assert files(tmp_path / "work") == [art.path.name]


def test_collection_ids_are_passed(fake_google: FakeGoogle, tmp_path: Path) -> None:
    ctx, _, _ = make_ctx(tmp_path / "work")
    run(driver(collection_ids=["users", "orders"]), ctx)
    assert fake_google.export_bodies[0]["collectionIds"] == ["users", "orders"]


def test_the_key_never_reaches_the_disk_or_the_log(fake_google: FakeGoogle, tmp_path: Path) -> None:
    work = tmp_path / "work"
    ctx, log, _ = make_ctx(work)
    drv = driver()
    seen: list[list[Path]] = []

    async def go() -> None:
        await drv.preflight(ctx)
        seen.append([p for p in work.rglob("*") if p.is_file()])
        await drv.dump_database("default", ctx)

    asyncio.run(go())
    assert seen == [[]]  # no key file, no CLI config
    requests = " ".join(f"{method} {url}" for method, url in fake_google.requests)
    for secret in (PRIVATE_KEY_PEM.strip().splitlines()[1], "bk@my-project-1", "key-id-123"):
        assert secret not in requests and not any(secret in line for line in log.lines)


def test_staging_is_kept_when_deletion_is_turned_off(
    fake_google: FakeGoogle, tmp_path: Path
) -> None:
    ctx, _, report = make_ctx(tmp_path / "work")
    run(driver(delete_staging_after_download=False), ctx)
    assert fake_google.deleted == [] and len(staged(fake_google)) == 2
    assert "cleanup-staging" not in report.names()


def test_listing_follows_result_pages(fake_google: FakeGoogle, tmp_path: Path) -> None:
    fake_google.page_size = 1
    ctx, _, _ = make_ctx(tmp_path / "work")
    art = run(driver(), ctx)
    with tarfile.open(art.path) as archive:
        assert len(archive.getnames()) >= 2
    assert len(fake_google.deleted) == 2


def test_a_transient_server_error_is_retried(
    fake_google: FakeGoogle, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(firestore_api, "RETRY_DELAYS", (0.0, 0.0))
    fake_google.modes["describe"] = "flaky"
    ctx, _, _ = make_ctx(tmp_path / "work")
    art = run(driver(), ctx)
    assert art.path.exists() and fake_google.describe_count == 2


# ---------- preflight ----------


def test_preflight_logs_and_checks_the_bucket(fake_google: FakeGoogle, tmp_path: Path) -> None:
    ctx, log, _ = make_ctx(tmp_path / "work")
    asyncio.run(driver().preflight(ctx))
    assert any("/storage/v1/b/my-staging/o" in url for _m, url in fake_google.requests)
    assert any("reachable" in line for line in log.lines)
    assert any("raise the job's timeout" in line for line in log.lines)


def test_invalid_service_account_fails_sanitized_and_cleans_up(
    fake_google: FakeGoogle, tmp_path: Path
) -> None:
    fake_google.modes["auth"] = "fail"
    fake_google.leak = f"invalid key {PRIVATE_KEY_PEM} ya29.leaked-token"
    ctx, log, _ = make_ctx(tmp_path / "work")
    drv = driver()
    with pytest.raises(DumpError, match="Authenticating with the service account failed") as caught:
        asyncio.run(drv.preflight(ctx))
    for text in [str(caught.value), *log.lines]:
        assert "BEGIN PRIVATE KEY" not in text and "ya29" not in text and "MII" not in text
    assert files(tmp_path / "work") == []
    assert drv._session is None


def test_unreachable_bucket_fails_preflight(fake_google: FakeGoogle, tmp_path: Path) -> None:
    fake_google.modes["bucket"] = "fail"
    ctx, _, _ = make_ctx(tmp_path / "work")
    drv = driver()
    with pytest.raises(DumpError, match=r"Reaching the staging bucket gs://my-staging failed"):
        asyncio.run(drv.preflight(ctx))
    assert files(tmp_path / "work") == [] and drv._session is None


# ---------- failures ----------


def test_export_start_failure(fake_google: FakeGoogle, tmp_path: Path) -> None:
    fake_google.modes["export"] = "fail"
    ctx, log, report = make_ctx(tmp_path / "work")
    with pytest.raises(DumpError, match="Starting the Firestore export failed") as caught:
        run(driver(), ctx)
    assert "ya29" not in str(caught.value) and not any("ya29" in line for line in log.lines)
    assert report.status("export") == "failed" and "download" not in report.names()
    assert fake_google.deleted == [] and fake_google.describe_count == 0
    assert files(tmp_path / "work") == []


def test_export_operation_error_is_sanitized(fake_google: FakeGoogle, tmp_path: Path) -> None:
    fake_google.modes["describe"] = "error"
    ctx, _, report = make_ctx(tmp_path / "work")
    with pytest.raises(DumpError, match="The Firestore export failed: PERMISSION_DENIED") as caught:
        run(driver(), ctx)
    assert "ya29" not in str(caught.value)
    assert report.status("poll") == "failed" and "download" not in report.names()
    assert files(tmp_path / "work") == []


def test_repeated_describe_failures_fail_the_poll(fake_google: FakeGoogle, tmp_path: Path) -> None:
    fake_google.modes["describe"] = "broken"
    ctx, _, _ = make_ctx(tmp_path / "work")
    with pytest.raises(DumpError, match="Checking the export operation failed"):
        run(driver(), ctx)
    assert fake_google.describe_count == 3
    assert files(tmp_path / "work") == []


def test_poll_timeout_cancels_the_operation(fake_google: FakeGoogle, tmp_path: Path) -> None:
    fake_google.modes["describe"] = "forever"
    ctx, log, _ = make_ctx(tmp_path / "work", timeout=1)
    with pytest.raises(DumpError, match="did not finish within 1 seconds"):
        run(driver(), ctx)
    assert fake_google.cancelled == [EXPORT_OPERATION]
    assert any("Requested cancellation" in line for line in log.lines)
    assert not any("alt=media" in url for _m, url in fake_google.requests)
    assert files(tmp_path / "work") == []


def test_missing_metadata_file_fails_the_run(fake_google: FakeGoogle, tmp_path: Path) -> None:
    fake_google.modes["download"] = "nometa"
    ctx, _, report = make_ctx(tmp_path / "work")
    with pytest.raises(DumpError, match="overall_export_metadata"):
        run(driver(), ctx)
    assert report.status("download") == "failed" and "pack" not in report.names()
    assert fake_google.deleted == []
    assert files(tmp_path / "work") == []


def test_download_failure(fake_google: FakeGoogle, tmp_path: Path) -> None:
    fake_google.modes["download"] = "fail"
    ctx, _, _ = make_ctx(tmp_path / "work")
    with pytest.raises(DumpError, match="Downloading the export failed"):
        run(driver(), ctx)
    assert fake_google.deleted == []
    assert files(tmp_path / "work") == []


def test_an_unsafe_object_name_is_refused(fake_google: FakeGoogle, tmp_path: Path) -> None:
    fake_google.modes["download"] = "evilname"
    work = tmp_path / "work"
    ctx, _, _ = make_ctx(work)
    with pytest.raises(DumpError, match="not safe to download"):
        run(driver(), ctx)
    assert not (tmp_path / "escape.txt").exists() and files(work) == []


def test_staging_delete_failure_is_only_a_warning(fake_google: FakeGoogle, tmp_path: Path) -> None:
    fake_google.modes["delete"] = "fail"
    ctx, log, report = make_ctx(tmp_path / "work")
    art = run(driver(), ctx)
    assert art.path.exists() and report.status("cleanup-staging") == "warning"
    assert len(report.warnings) == 1
    warning = report.warnings[0]
    assert "lifecycle rule" in warning and "permission to delete" in warning
    assert "storage.objects.delete" in warning and "gs://my-staging/backups/nightly/r1/" in warning
    assert any("lifecycle rule" in line for line in log.lines)
    assert files(tmp_path / "work") == [art.path.name]


# ---------- cancel ----------


def test_cancel_mid_poll_stops_polling_cancels_the_operation_and_cleans_up(
    fake_google: FakeGoogle, tmp_path: Path
) -> None:
    fake_google.modes["describe"] = "forever"
    ctx, log, report = make_ctx(tmp_path / "work", timeout=60)
    drv = driver()

    async def go() -> int:
        task = asyncio.ensure_future(run_flow(drv, ctx))
        for _ in range(500):
            if fake_google.describe_count:
                break
            await asyncio.sleep(0.02)
        assert fake_google.describe_count
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return fake_google.describe_count

    polls = asyncio.run(go())
    assert fake_google.cancelled == [EXPORT_OPERATION]
    assert any("Requested cancellation" in line for line in log.lines)
    assert fake_google.describe_count == polls  # polling really stopped
    assert report.status("poll") == "failed" and "download" not in report.names()
    assert fake_google.deleted == []
    assert files(tmp_path / "work") == [] and drv._session is None


def test_a_failed_cancel_request_is_only_logged(fake_google: FakeGoogle, tmp_path: Path) -> None:
    fake_google.modes["describe"] = "forever"
    fake_google.modes["cancel"] = "fail"
    ctx, log, _ = make_ctx(tmp_path / "work", timeout=1)
    with pytest.raises(DumpError, match="did not finish"):
        run(driver(), ctx)
    assert any("Could not cancel" in line for line in log.lines)
    assert files(tmp_path / "work") == []


# ---------- packing ----------


def test_pack_writes_a_streaming_tarball_with_matching_checksum(tmp_path: Path) -> None:
    source = tmp_path / "export"
    (source / "a" / "b").mkdir(parents=True)
    (source / "a" / "x.overall_export_metadata").write_text("meta")
    (source / "a" / "b" / "output-0").write_bytes(b"\x00" * 100_000)
    (source / "link").symlink_to("/etc/passwd")
    partial = tmp_path / "out.tar.gz.partial"
    size, digest = write_tar_gz(source, partial)
    raw = partial.read_bytes()
    assert size == len(raw) and digest == hashlib.sha256(raw).hexdigest()
    with tarfile.open(partial) as archive:
        names = archive.getnames()
        member = archive.extractfile("a/b/output-0")
        assert member is not None and len(member.read()) == 100_000
    assert "link" not in names and "a/x.overall_export_metadata" in names


def test_pack_can_be_cancelled(tmp_path: Path) -> None:
    source = tmp_path / "export"
    source.mkdir()
    (source / "f").write_text("x")
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(OperationCancelledError):
        write_tar_gz(source, tmp_path / "out.partial", cancel)

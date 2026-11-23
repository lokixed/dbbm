"""The Firestore export flow against a FAKE gcloud on PATH; no Google Cloud access."""

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

from app.drivers import firestore
from app.drivers.base import DumpContext, DumpedArtifact, DumpError, DumpReporter, StepHandle
from app.drivers.dumping import write_tar_gz
from app.drivers.firestore import (
    FirestoreDriver,
    parse_operation_name,
    parse_operation_status,
)
from app.services.hashing import OperationCancelledError
from tests.fakes import FakeBins, FakeFirestore
from tests.test_drivers_dump import ListLog, files
from tests.test_firestore_driver import CONFIG, PRIVATE_KEY_PEM, SA_JSON

pytestmark = [pytest.mark.firestore_fake, pytest.mark.usefixtures("fast_polling")]

OPERATION = "projects/p1/databases/(default)/operations/OP123"


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


# ---------- parsing ----------


def test_operation_name_is_parsed_from_export_json() -> None:
    raw = json.dumps({"name": OPERATION, "metadata": {"operationState": "INITIALIZING"}})
    assert parse_operation_name(raw) == OPERATION


@pytest.mark.parametrize(
    "raw",
    ["", "not json", "[]", "{}", '{"name": 3}', '{"name": "--project=evil"}', '{"name": "a/b"}'],
)
def test_bad_operation_output_is_rejected(raw: str) -> None:
    with pytest.raises(DumpError, match="valid Firestore operation name"):
        parse_operation_name(raw)


def test_operation_status_running_with_progress() -> None:
    raw = json.dumps(
        {
            "done": False,
            "metadata": {
                "operationState": "PROCESSING",
                "progressDocuments": {"completedWork": "5", "estimatedWork": "10"},
                "progressBytes": {"completedWork": "100"},
            },
        }
    )
    status = parse_operation_status(raw)
    assert not status.done and status.state == "PROCESSING" and status.error is None
    assert status.progress == "5/10 documents, 100/? bytes"


def test_operation_status_done_and_failed() -> None:
    ok = parse_operation_status('{"done": true, "metadata": {"operationState": "SUCCESSFUL"}}')
    assert ok.done and ok.error is None
    failed = parse_operation_status('{"done": true, "error": {"code": 7, "message": "denied"}}')
    assert failed.done and failed.error == "denied"
    cancelled = parse_operation_status('{"metadata": {"operationState": "CANCELLED"}}')
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


def test_tool_versions(fake_bins: FakeBins) -> None:
    assert asyncio.run(driver().tool_versions()) == {"gcloud": "Google Cloud SDK 999.0.0"}


# ---------- full success ----------


def test_full_export_flow(fake_bins: FakeBins, tmp_path: Path) -> None:
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

    export_args = fake_bins.read("gcloud.export.args").split()
    url = export_args[2]
    assert url.startswith("gs://my-staging/backups/nightly/r1/") and url.endswith("Z")
    assert export_args[0:2] == ["firestore", "export"]
    assert "--project=my-project-1" in export_args and "--database=(default)" in export_args
    assert "--async" in export_args and "--format=json" in export_args
    assert not any(a.startswith("--collection-ids") for a in export_args)
    assert fake_bins.read("gcloud.rm.args").split() == ["storage", "rm", "-r", url]
    assert fake_bins.read("gcloud.cp.args").split()[:3] == ["storage", "cp", "-r"]
    assert "gs://my-staging" in fake_bins.read("gcloud.calls")
    assert any("Export state: PROCESSING (5/10 documents)" in line for line in log.lines)

    # Only the artifact remains; the session (config, key, export tree) is gone.
    assert files(tmp_path / "work") == [art.path.name]


def test_collection_ids_are_passed(fake_bins: FakeBins, tmp_path: Path) -> None:
    ctx, _, _ = make_ctx(tmp_path / "work")
    run(driver(collection_ids=["users", "orders"]), ctx)
    assert "--collection-ids=users,orders" in fake_bins.read("gcloud.export.args").split()


def test_gcloud_is_isolated_and_the_key_file_is_short_lived(
    fake_bins: FakeBins, tmp_path: Path
) -> None:
    work = tmp_path / "work"
    ctx, log, _ = make_ctx(work)
    run(driver(), ctx)
    config_dir, home, prompts, usage, updates = fake_bins.read("gcloud.env").strip().split("|")
    assert config_dir.startswith(str(work)) and config_dir.endswith("gcloud-config")
    assert home == str(Path(config_dir).parent) and home.startswith(str(work))
    assert (prompts, usage, updates) == ("1", "true", "true")
    assert fake_bins.read("gcloud.key") == SA_JSON
    assert fake_bins.read("gcloud.key.mode").strip() == "600"
    assert not Path(fake_bins.read("gcloud.key.path").strip()).exists()
    assert not config_dir.startswith(str(tmp_path / "fakebin"))
    assert not Path(config_dir).exists()
    calls = fake_bins.read("gcloud.calls")
    for secret in (PRIVATE_KEY_PEM.strip().splitlines()[1], "bk@my-project-1", "key-id-123"):
        assert secret not in calls and not any(secret in line for line in log.lines)


def test_key_file_is_deleted_right_after_activation(fake_bins: FakeBins, tmp_path: Path) -> None:
    ctx, _, _ = make_ctx(tmp_path / "work")
    drv = driver()
    key_files: list[list[str]] = []

    async def go() -> None:
        await drv.preflight(ctx)
        key_files.append([p.name for p in (tmp_path / "work").rglob("sa-key.json")])
        await drv.dump_database("default", ctx)

    asyncio.run(go())
    assert key_files == [[]]


def test_staging_is_kept_when_deletion_is_turned_off(fake_bins: FakeBins, tmp_path: Path) -> None:
    ctx, _, report = make_ctx(tmp_path / "work")
    run(driver(delete_staging_after_download=False), ctx)
    assert not fake_bins.exists("gcloud.rm.args")
    assert "cleanup-staging" not in report.names()


# ---------- preflight ----------


def test_preflight_logs_and_checks_the_bucket(fake_bins: FakeBins, tmp_path: Path) -> None:
    ctx, log, _ = make_ctx(tmp_path / "work")
    asyncio.run(driver().preflight(ctx))
    assert "storage ls gs://my-staging --project=my-project-1" in fake_bins.read("gcloud.calls")
    assert any("reachable" in line for line in log.lines)
    assert any("raise the job's timeout" in line for line in log.lines)


def test_invalid_service_account_fails_sanitized_and_cleans_up(
    fake_bins: FakeBins, tmp_path: Path
) -> None:
    fake_bins.set_mode("gcloud_auth", "fail")
    fake_bins.set_text("gcloud_stderr", f"ERROR: invalid key {PRIVATE_KEY_PEM} ya29.leaked-token")
    ctx, log, _ = make_ctx(tmp_path / "work")
    drv = driver()
    with pytest.raises(DumpError, match="Authenticating with the service account failed") as caught:
        asyncio.run(drv.preflight(ctx))
    for text in [str(caught.value), *log.lines]:
        assert "BEGIN PRIVATE KEY" not in text and "ya29" not in text and "MII" not in text
    assert files(tmp_path / "work") == []
    assert drv._session is None
    assert not Path(fake_bins.read("gcloud.key.path").strip()).exists()


def test_unreachable_bucket_fails_preflight(fake_bins: FakeBins, tmp_path: Path) -> None:
    fake_bins.set_mode("gcloud_ls", "fail")
    ctx, _, _ = make_ctx(tmp_path / "work")
    with pytest.raises(DumpError, match=r"Reaching the staging bucket gs://my-staging failed"):
        asyncio.run(driver().preflight(ctx))
    assert files(tmp_path / "work") == []


# ---------- failures ----------


def test_export_start_failure(fake_bins: FakeBins, tmp_path: Path) -> None:
    fake_bins.set_mode("gcloud_export", "fail")
    fake_bins.set_text("gcloud_stderr", "ERROR: permission denied Bearer ya29.abc123")
    ctx, log, report = make_ctx(tmp_path / "work")
    with pytest.raises(DumpError, match="Starting the Firestore export failed") as caught:
        run(driver(), ctx)
    assert "ya29" not in str(caught.value) and not any("ya29" in line for line in log.lines)
    assert report.status("export") == "failed" and "download" not in report.names()
    assert not fake_bins.exists("gcloud.cp.args") and not fake_bins.exists("gcloud.rm.args")
    assert files(tmp_path / "work") == []


def test_export_operation_error_is_sanitized(fake_bins: FakeBins, tmp_path: Path) -> None:
    fake_bins.set_mode("gcloud_describe", "error")
    ctx, _, report = make_ctx(tmp_path / "work")
    with pytest.raises(DumpError, match="The Firestore export failed: PERMISSION_DENIED") as caught:
        run(driver(), ctx)
    assert "ya29" not in str(caught.value)
    assert report.status("poll") == "failed" and not fake_bins.exists("gcloud.cp.args")
    assert files(tmp_path / "work") == []


def test_repeated_describe_failures_fail_the_poll(fake_bins: FakeBins, tmp_path: Path) -> None:
    fake_bins.set_mode("gcloud_describe", "broken")
    ctx, _, _ = make_ctx(tmp_path / "work")
    with pytest.raises(DumpError, match="Checking the export operation failed"):
        run(driver(), ctx)
    assert fake_bins.read("describe.count").strip() == "3"
    assert files(tmp_path / "work") == []


def test_poll_timeout_cancels_the_operation(fake_bins: FakeBins, tmp_path: Path) -> None:
    fake_bins.set_mode("gcloud_describe", "forever")
    ctx, log, _ = make_ctx(tmp_path / "work", timeout=1)
    with pytest.raises(DumpError, match="did not finish within 1 seconds"):
        run(driver(), ctx)
    assert OPERATION in fake_bins.read("cancel.called")
    assert any("Requested cancellation" in line for line in log.lines)
    assert not fake_bins.exists("gcloud.cp.args")
    assert files(tmp_path / "work") == []


def test_missing_metadata_file_fails_the_run(fake_bins: FakeBins, tmp_path: Path) -> None:
    fake_bins.set_mode("gcloud_cp", "nometa")
    ctx, _, report = make_ctx(tmp_path / "work")
    with pytest.raises(DumpError, match="overall_export_metadata"):
        run(driver(), ctx)
    assert report.status("download") == "failed" and "pack" not in report.names()
    assert not fake_bins.exists("gcloud.rm.args")
    assert files(tmp_path / "work") == []


def test_download_failure(fake_bins: FakeBins, tmp_path: Path) -> None:
    fake_bins.set_mode("gcloud_cp", "fail")
    ctx, _, _ = make_ctx(tmp_path / "work")
    with pytest.raises(DumpError, match="Downloading the export failed"):
        run(driver(), ctx)
    assert not fake_bins.exists("gcloud.rm.args")
    assert files(tmp_path / "work") == []


def test_staging_delete_failure_is_only_a_warning(fake_bins: FakeBins, tmp_path: Path) -> None:
    fake_bins.set_mode("gcloud_rm", "fail")
    fake_bins.set_text("gcloud_stderr", "AccessDeniedException: 403 no storage.objects.delete")
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
    fake_bins: FakeBins, tmp_path: Path
) -> None:
    fake_bins.set_mode("gcloud_describe", "forever")
    ctx, log, report = make_ctx(tmp_path / "work", timeout=60)
    drv = driver()

    async def go() -> int:
        task = asyncio.ensure_future(run_flow(drv, ctx))
        for _ in range(500):
            if fake_bins.exists("describe.count"):
                break
            await asyncio.sleep(0.02)
        assert fake_bins.exists("describe.count")
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return int(fake_bins.read("describe.count"))

    polls = asyncio.run(go())
    assert OPERATION in fake_bins.read("cancel.called")
    assert any("Requested cancellation" in line for line in log.lines)
    assert int(fake_bins.read("describe.count")) == polls  # polling really stopped
    assert report.status("poll") == "failed" and "download" not in report.names()
    assert not fake_bins.exists("gcloud.rm.args")
    assert files(tmp_path / "work") == []
    assert not Path(fake_bins.read("gcloud.key.path").strip()).exists()


def test_a_failed_cancel_request_is_only_logged(fake_bins: FakeBins, tmp_path: Path) -> None:
    fake_bins.set_mode("gcloud_describe", "forever")
    fake_bins.set_mode("gcloud_cancel", "fail")
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

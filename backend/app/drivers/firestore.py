import asyncio
import importlib.metadata
import os
import shutil
import tarfile
import threading
import uuid
from collections.abc import Awaitable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, ClassVar, TypeVar
from urllib.parse import quote

import httpx

from app.drivers import firestore_api
from app.drivers.base import (
    CONNECT_TIMEOUT,
    BaseDriver,
    CheckResult,
    ConnectionTestResult,
    DumpContext,
    DumpedArtifact,
    DumpError,
    RestoreContext,
    RestoreError,
    RestoreOptions,
    StepHandle,
)
from app.drivers.dumping import summarize_stderr, work_file, write_tar_gz
from app.drivers.firestore_api import (
    FIRESTORE_API,
    STORAGE_API,
    GoogleApiError,
    GoogleClient,
    SyncRequest,
    service_account_credentials,
)
from app.schemas.source import FirestoreConfig, parse_service_account, split_staging_bucket
from app.services.naming import database_label, format_timestamp, safe_component
from app.services.redact import describe_error, sanitize

# Module-level so tests can shorten it.
POLL_SECONDS = 10.0
CANCEL_OPERATION_SECONDS = 60
MAX_DESCRIBE_FAILURES = 3
METADATA_GLOB = "*.overall_export_metadata"
_FINAL_STATES = frozenset({"SUCCESSFUL", "FAILED", "CANCELLED"})
# Dumps and restores share the Google helpers; both contexts carry a work dir, log and timeout.
Ctx = DumpContext | RestoreContext
T = TypeVar("T")


@dataclass(frozen=True)
class OperationStatus:
    done: bool
    state: str
    error: str | None
    progress: str | None


def _progress(metadata: dict[str, Any]) -> str | None:
    parts = []
    for key, label in (("progressDocuments", "documents"), ("progressBytes", "bytes")):
        value = metadata.get(key)
        if isinstance(value, dict) and "completedWork" in value:
            parts.append(f"{value['completedWork']}/{value.get('estimatedWork', '?')} {label}")
    return ", ".join(parts) or None


def parse_operation_status(data: Any) -> OperationStatus:
    """Interpret a Firestore long-running operation resource."""
    if not isinstance(data, dict):
        raise DumpError("Unexpected response while checking the export operation.")
    metadata = data.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    done = data.get("done") is True
    state = str(metadata.get("operationState") or ("DONE" if done else "RUNNING"))
    error = data.get("error")
    message: str | None = None
    if isinstance(error, dict):
        message = str(error.get("message") or error.get("code") or "unknown error")
    elif error:
        message = str(error)
    if message is None and state in ("FAILED", "CANCELLED"):
        message = f"the export operation ended in state {state}"
    return OperationStatus(
        done=done or state in _FINAL_STATES,
        state=state,
        error=message,
        progress=_progress(metadata),
    )


@dataclass
class _Session:
    root: Path
    client: GoogleClient


def _describe_status(status: int) -> str:
    if status in (401, 403):
        return f"permission denied (HTTP {status})"
    if status == 404:
        return "not found (HTTP 404)"
    return f"unexpected response (HTTP {status})"


def _safe_destination(root: Path, relative: str) -> Path:
    """Where a bucket object goes under `root`; refuses names that could escape it."""
    parts = relative.split("/")
    if any(part in ("", ".", "..") or "\\" in part for part in parts):
        raise DumpError("The export contains an object name that is not safe to download.")
    return root.joinpath(*parts)


class FirestoreDriver(BaseDriver):
    source_type = "firestore"
    reports_substeps = True
    # Tests inject an httpx.MockTransport here.
    default_transport: ClassVar[httpx.BaseTransport | None] = None
    _session: _Session | None = None

    def __init__(
        self,
        config: dict[str, Any],
        secrets: Mapping[str, str],
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        super().__init__(config, secrets)
        self._transport = transport or self.default_transport

    def sensitive_values(self) -> list[str]:
        values = super().sensitive_values()
        try:
            account = parse_service_account(self._secrets.get("service_account_json", ""))
        except ValueError:
            return values
        keys = ("private_key", "private_key_id", "client_email")
        values += [str(account.get(k)) for k in keys if account.get(k)]
        return values

    def _get_check(
        self,
        client: httpx.Client,
        name: str,
        label: str,
        url: str,
        token: str,
        params: dict[str, str] | None = None,
    ) -> CheckResult:
        try:
            response = client.get(url, params=params, headers={"Authorization": f"Bearer {token}"})
        except httpx.HTTPError as exc:
            return CheckResult(name, False, f"{label}: request failed ({type(exc).__name__})")
        if response.status_code == 200:
            return CheckResult(name, True, f"{label}: OK")
        return CheckResult(name, False, f"{label}: {_describe_status(response.status_code)}")

    def _probe(self) -> ConnectionTestResult:
        config = FirestoreConfig.model_validate(self._config)
        bucket, _ = split_staging_bucket(config.staging_bucket)

        with httpx.Client(
            timeout=CONNECT_TIMEOUT, follow_redirects=False, transport=self._transport
        ) as client:
            try:
                credentials = service_account_credentials(self._secrets["service_account_json"])
                credentials.refresh(SyncRequest(client))
                token = str(credentials.token)
            except Exception as exc:
                reason = describe_error(exc, self.sensitive_values())
                skipped = "skipped (authentication failed)"
                return ConnectionTestResult(
                    ok=False,
                    message=f"Authentication failed: {reason}",
                    checks=[
                        CheckResult("authentication", False, reason),
                        CheckResult("database", False, f"Database access: {skipped}"),
                        CheckResult("bucket", False, f"Bucket access: {skipped}"),
                    ],
                )

            path = firestore_api.database_path(config.project_id, config.database_id)
            database_url = f"{FIRESTORE_API}/{path}"
            bucket_url = f"{STORAGE_API}/b/{quote(bucket, safe='')}/o"
            checks = [
                CheckResult("authentication", True, "Authenticated with the service account."),
                self._get_check(client, "database", "Database access", database_url, token),
                self._get_check(
                    client,
                    "bucket",
                    "Bucket access",
                    bucket_url,
                    token,
                    params={"maxResults": "1", "fields": "kind"},
                ),
            ]
        ok = all(c.ok for c in checks)
        summary = "; ".join(c.message for c in checks[1:])
        return ConnectionTestResult(ok=ok, message=summary, checks=checks)

    # ---------- shared helpers ----------

    def _config_model(self) -> FirestoreConfig:
        return FirestoreConfig.model_validate(self._config)

    def manifest_extra(self) -> dict[str, Any]:
        config = self._config_model()
        return {
            "project_id": config.project_id,
            "database_id": config.database_id,
            "collection_ids": config.collection_ids,
            "bucket": split_staging_bucket(config.staging_bucket)[0],
        }

    def select_databases(self, listed: list[str]) -> list[str]:
        return [database_label(self._config_model().database_id)]

    async def tool_versions(self) -> dict[str, str]:
        return {
            "google-auth": importlib.metadata.version("google-auth"),
            "firestore-api": "v1",
        }

    def _failure(self, what: str, error: GoogleApiError) -> DumpError:
        reason = summarize_stderr(error.detail, self.sensitive_values())
        status = f" (HTTP {error.status})" if error.status else ""
        return DumpError(f"{what} failed{status}: {reason}")

    async def _call(
        self, what: str, ctx: Ctx, call: Awaitable[T], *, timeout: int | None = None
    ) -> T:
        """Await one API call under the step timeout; failures become sanitized DumpErrors."""
        limit = timeout or ctx.timeout_seconds
        try:
            async with asyncio.timeout(limit):
                return await call
        except GoogleApiError as exc:
            raise self._failure(what, exc) from None
        except TimeoutError:
            raise DumpError(f"{what} timed out after {limit} seconds.") from None

    def _api(self) -> GoogleClient:
        if self._session is None:
            raise DumpError("Internal error: no Google session.")
        return self._session.client

    async def _open_session(self, ctx: Ctx) -> None:
        """Create the private work folder and authenticate; the key never reaches the disk."""
        root = ctx.work_dir / f"firestore-{uuid.uuid4().hex[:8]}"
        try:
            root.mkdir(mode=0o700)
        except OSError as exc:
            raise DumpError(f"Cannot prepare the export: {exc.strerror}.") from None
        client = GoogleClient(self._secrets.get("service_account_json", ""), self._transport)
        self._session = _Session(root=root, client=client)
        try:
            await client.authenticate()
        except Exception as exc:
            reason = describe_error(exc, self.sensitive_values())
            await self._drop_session()
            raise DumpError(f"Authenticating with the service account failed: {reason}") from None
        ctx.log.info("Authenticated with the service account.")

    async def _drop_session(self) -> None:
        session, self._session = self._session, None
        if session is not None:
            try:
                await session.client.close()
            finally:
                shutil.rmtree(session.root, ignore_errors=True)

    async def _check_bucket(self, bucket: str, ctx: Ctx) -> None:
        await self._call(
            f"Reaching the staging bucket gs://{bucket}", ctx, self._api().check_bucket(bucket)
        )

    # ---------- dump ----------

    async def preflight(self, ctx: DumpContext) -> None:
        config = self._config_model()
        bucket, _prefix = split_staging_bucket(config.staging_bucket)
        await self._open_session(ctx)
        try:
            await self._check_bucket(bucket, ctx)
        except BaseException:
            await self._drop_session()
            raise
        ctx.log.info(f"Staging bucket gs://{bucket} is reachable.")
        ctx.log.info(
            f"Each step may take up to {ctx.timeout_seconds} seconds. Exports of large databases "
            "can take much longer: raise the job's timeout if a step runs out of time."
        )

    def _staging_prefix(self, config: FirestoreConfig, ctx: DumpContext) -> str:
        _bucket, base = split_staging_bucket(config.staging_bucket)
        parts = [
            safe_component(ctx.job_name or "job"),
            safe_component(ctx.run_id or uuid.uuid4().hex),
            format_timestamp(datetime.now(UTC)),
        ]
        return "/".join(([base] if base else []) + parts)

    async def dump_database(self, database: str, ctx: DumpContext) -> DumpedArtifact:
        config = self._config_model()
        bucket, _base = split_staging_bucket(config.staging_bucket)
        prefix = self._staging_prefix(config, ctx)
        staging_url = f"gs://{bucket}/{prefix}"
        try:
            if self._session is None:
                await self._open_session(ctx)
            session = self._session
            assert session is not None  # noqa: S101
            async with ctx.report.step("export") as step:
                operation = await self._start_export(config, staging_url, ctx)
                step.detail = "Export started."
            async with ctx.report.step("poll") as step:
                await self._poll(operation, ctx, step)
            export_dir = session.root / "export"
            async with ctx.report.step("download") as step:
                await self._download(bucket, prefix, export_dir, ctx)
                step.detail = "Export downloaded."
            final = work_file(ctx, safe_component(database), "tar.gz")
            async with ctx.report.step("pack") as step:
                size, digest = await self._pack(export_dir, final, ctx)
            await self._cleanup_staging(config, bucket, prefix, ctx)
        finally:
            await self._drop_session()
        return DumpedArtifact(
            database=database,
            path=final,
            extension="tar.gz",
            size=size,
            sha256=digest,
            compressed=True,
            compression="gzip",
        )

    async def _start_export(
        self, config: FirestoreConfig, staging_url: str, ctx: DumpContext
    ) -> str:
        ctx.log.info(f"Starting the Firestore export to {staging_url}")
        operation = await self._call(
            "Starting the Firestore export",
            ctx,
            self._api().start_export(
                config.project_id, config.database_id, staging_url, config.collection_ids
            ),
        )
        ctx.log.info(f"Export operation: {operation}")
        return operation

    async def _cancel_operation(self, operation: str, ctx: Ctx, label: str = "export") -> None:
        """Best effort; the outcome is only logged."""
        try:
            await self._call(
                f"Cancelling the {label} operation",
                ctx,
                self._api().cancel_operation(operation),
                timeout=CANCEL_OPERATION_SECONDS,
            )
        except DumpError as exc:
            ctx.log.warning(f"Could not cancel the Firestore {label} operation: {exc}")
            return
        ctx.log.info(f"Requested cancellation of the Firestore {label} operation.")

    async def _poll(
        self, operation: str, ctx: Ctx, step: StepHandle, label: str = "export"
    ) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + ctx.timeout_seconds
        failures = 0
        last: tuple[str, str | None] | None = None
        what = f"Checking the {label} operation"
        try:
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise TimeoutError
                try:
                    data = await self._call(
                        what,
                        ctx,
                        self._api().get_operation(operation),
                        timeout=max(1, int(remaining)),
                    )
                except DumpError as exc:
                    if deadline - loop.time() <= 0:
                        raise TimeoutError from None
                    failures += 1
                    ctx.log.warning(f"Checking the {label} failed (attempt {failures}): {exc}")
                    if failures >= MAX_DESCRIBE_FAILURES:
                        raise
                else:
                    failures = 0
                    status = parse_operation_status(data)
                    if (status.state, status.progress) != last:
                        last = (status.state, status.progress)
                        extra = f" ({status.progress})" if status.progress else ""
                        ctx.log.info(f"{label.capitalize()} state: {status.state}{extra}")
                    if status.done:
                        if status.error:
                            message = sanitize(status.error, self.sensitive_values())
                            raise DumpError(f"The Firestore {label} failed: {message}")
                        step.detail = f"{label.capitalize()} finished ({status.state})."
                        return
                await asyncio.sleep(POLL_SECONDS)
        except asyncio.CancelledError:
            ctx.log.warning(f"Run cancelled while the {label} was running.")
            await self._cancel_operation(operation, ctx, label)
            raise
        except TimeoutError:
            ctx.log.warning(f"Timed out waiting for the {label}; cancelling it.")
            await self._cancel_operation(operation, ctx, label)
            raise DumpError(
                f"The Firestore {label} did not finish within {ctx.timeout_seconds} seconds. "
                "Increase the job's timeout for large databases."
            ) from None

    async def _fetch_export(self, bucket: str, prefix: str, root: Path) -> int:
        """Download every object under the staging prefix into root; returns the file count."""
        api = self._api()
        count = 0
        for name, size in await api.list_objects(bucket, prefix + "/"):
            relative = name[len(prefix) + 1 :]
            if not relative or relative.endswith("/"):
                continue
            target = _safe_destination(root, relative)
            await asyncio.to_thread(target.parent.mkdir, parents=True, exist_ok=True)
            await api.download_object(bucket, name, target, size)
            count += 1
        return count

    async def _download(self, bucket: str, prefix: str, export_dir: Path, ctx: DumpContext) -> None:
        try:
            export_dir.mkdir(mode=0o700)
        except OSError as exc:
            raise DumpError(f"Cannot prepare the download: {exc.strerror}.") from None
        root = export_dir / PurePosixPath(prefix).name
        count = await self._call(
            "Downloading the export", ctx, self._fetch_export(bucket, prefix, root)
        )
        ctx.log.info(f"Downloaded {count} files from gs://{bucket}/{prefix}.")
        found = await asyncio.to_thread(lambda: next(export_dir.rglob(METADATA_GLOB), None))
        if found is None:
            raise DumpError(
                f"The downloaded export has no {METADATA_GLOB} file, so it is incomplete."
            )

    async def _pack(self, export_dir: Path, final: Path, ctx: DumpContext) -> tuple[int, str]:
        partial = final.with_name(final.name + ".partial")
        cancel = threading.Event()
        future = asyncio.ensure_future(asyncio.to_thread(write_tar_gz, export_dir, partial, cancel))
        try:
            async with asyncio.timeout(ctx.timeout_seconds):
                size, digest = await asyncio.shield(future)
            os.replace(partial, final)
        except TimeoutError:
            raise DumpError(f"Packing timed out after {ctx.timeout_seconds} seconds.") from None
        except OSError as exc:
            raise DumpError(f"Cannot write the archive: {exc.strerror}.") from None
        except BaseException:
            cancel.set()
            await asyncio.wait({future})
            if not future.cancelled():
                future.exception()  # mark as retrieved
            raise
        finally:
            partial.unlink(missing_ok=True)
        ctx.log.info(f"Packed the export into {final.name}.")
        return size, digest

    async def _cleanup_staging(
        self, config: FirestoreConfig, bucket: str, prefix: str, ctx: DumpContext
    ) -> None:
        staging_url = f"gs://{bucket}/{prefix}"
        if not config.delete_staging_after_download:
            ctx.log.info(f"Keeping the staging export at {staging_url} (deletion is turned off).")
            return
        async with ctx.report.step("cleanup-staging") as step:
            try:
                await self._call(
                    "Deleting the staging export",
                    ctx,
                    self._api().delete_prefix(bucket, prefix + "/"),
                )
                problem = None
            except DumpError as exc:
                problem = str(exc)
            if problem is None:
                step.detail = "Staging export deleted."
                return
            message = (
                f"Could not delete the staging export {staging_url}: {problem} "
                "Add a lifecycle rule to the bucket (for example delete after 1 day) or grant "
                "the service account permission to delete objects."
            )
            step.warn(message)
            ctx.warn(message)

    # ---------- restore ----------

    async def restore_tool_versions(self) -> dict[str, str]:
        return await self.tool_versions()

    async def restore_database(
        self, artifact: Path, options: RestoreOptions, ctx: RestoreContext
    ) -> None:
        """Unpack the export, copy it to the staging bucket, import it, delete the staging copy.

        The import goes into the source's own project and database, adding to and overwriting
        documents that have the same ID; it never deletes documents.
        """
        config = self._config_model()
        label = database_label(config.database_id)
        if options.target_database != label:
            raise RestoreError(
                f"A Firestore import goes into the target source's database '{label}'; "
                f"it cannot be renamed to '{options.target_database}'."
            )
        bucket, base = split_staging_bucket(config.staging_bucket)
        parts = ["restore", uuid.uuid4().hex[:12], format_timestamp(datetime.now(UTC))]
        prefix = "/".join(([base] if base else []) + parts)
        await self._open_session(ctx)
        session = self._session
        assert session is not None  # noqa: S101
        uploading = False
        try:
            await self._check_bucket(bucket, ctx)
            metadata = await self._unpack(artifact, session.root / "export", ctx)
            uploading = True
            await self._upload(metadata.parent, bucket, prefix, ctx)
            uri = f"gs://{bucket}/{prefix}/{metadata.parent.name}/{metadata.name}"
            operation = await self._start_import(config, uri, ctx)
            await self._poll(operation, ctx, StepHandle("import"), "import")
        finally:
            try:
                if uploading:
                    await self._remove_staging(bucket, prefix, ctx)
            finally:
                await self._drop_session()

    async def _unpack(self, artifact: Path, export_dir: Path, ctx: RestoreContext) -> Path:
        """Extract the .tar.gz export; returns its .overall_export_metadata file."""

        def extract() -> Path:
            export_dir.mkdir(mode=0o700)
            with tarfile.open(artifact, "r:gz") as archive:
                archive.extractall(export_dir, filter="data")  # refuses links and ../ paths
            found = next(export_dir.rglob(METADATA_GLOB), None)
            if found is None:
                raise RestoreError(
                    f"The backup has no {METADATA_GLOB} file, so it is not a complete export."
                )
            return found

        try:
            return await asyncio.to_thread(extract)
        except (tarfile.TarError, OSError) as exc:
            detail = exc.strerror if isinstance(exc, OSError) else type(exc).__name__
            raise RestoreError(f"Cannot unpack the backup: {detail}.") from None

    async def _send_tree(self, export_root: Path, bucket: str, prefix: str) -> None:
        api = self._api()
        files = await asyncio.to_thread(
            lambda: sorted(p for p in export_root.rglob("*") if p.is_file() and not p.is_symlink())
        )
        for path in files:
            relative = path.relative_to(export_root).as_posix()
            await api.upload_object(bucket, f"{prefix}/{export_root.name}/{relative}", path)

    async def _upload(
        self, export_root: Path, bucket: str, prefix: str, ctx: RestoreContext
    ) -> None:
        await self._call(
            "Uploading the backup to the staging bucket",
            ctx,
            self._send_tree(export_root, bucket, prefix),
        )
        ctx.log.info(f"Uploaded the export to gs://{bucket}/{prefix}.")

    async def _start_import(self, config: FirestoreConfig, uri: str, ctx: RestoreContext) -> str:
        operation = await self._call(
            "Starting the Firestore import",
            ctx,
            self._api().start_import(config.project_id, config.database_id, uri),
        )
        ctx.log.info(f"Import operation: {operation}")
        return operation

    async def _remove_staging(self, bucket: str, prefix: str, ctx: RestoreContext) -> None:
        """Delete the uploaded copy; failures are only logged (the restore itself has finished)."""
        try:
            await self._call(
                "Deleting the staging copy", ctx, self._api().delete_prefix(bucket, prefix + "/")
            )
        except DumpError as exc:
            ctx.log.warning(
                f"Could not delete the staging copy gs://{bucket}/{prefix}: {exc} Delete it by "
                "hand or add a lifecycle rule to the bucket (for example delete after 1 day)."
            )
            return
        ctx.log.info("Deleted the staging copy of the export.")

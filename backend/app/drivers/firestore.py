import asyncio
import json
import os
import re
import shlex
import shutil
import tarfile
import tempfile
import threading
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import quote

import httpx
from google.auth import exceptions as auth_exceptions
from google.auth import transport as auth_transport
from google.oauth2 import service_account

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
from app.drivers.dumping import capture_output, summarize_stderr, work_file, write_tar_gz
from app.schemas.source import FirestoreConfig, parse_service_account, split_staging_bucket
from app.services.naming import database_label, format_timestamp, safe_component
from app.services.proc import StderrTail, StepTimeoutError, clean_env, run_process
from app.services.redact import describe_error, sanitize, scrub

# Pinned so a crafted service-account file cannot redirect the token request elsewhere.
TOKEN_URI = "https://oauth2.googleapis.com/token"  # noqa: S105
SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]
FIRESTORE_API = "https://firestore.googleapis.com/v1"
STORAGE_API = "https://storage.googleapis.com/storage/v1"

# Module-level so tests can shorten it.
POLL_SECONDS = 10.0
CANCEL_OPERATION_SECONDS = 60
MAX_DESCRIBE_FAILURES = 3
METADATA_GLOB = "*.overall_export_metadata"
_OPERATION_RE = re.compile(
    r"^projects/[A-Za-z0-9._:-]+/databases/(?:\(default\)|[a-z0-9-]+)/operations/[A-Za-z0-9_=.-]+$"
)
_FINAL_STATES = frozenset({"SUCCESSFUL", "FAILED", "CANCELLED"})
# Dumps and restores share the gcloud helpers; both contexts carry a work dir, log and timeout.
Ctx = DumpContext | RestoreContext


@dataclass(frozen=True)
class OperationStatus:
    done: bool
    state: str
    error: str | None
    progress: str | None


def parse_operation_name(raw: str) -> str:
    """The long-running operation name from `gcloud firestore export --async --format=json`."""
    try:
        data = json.loads(raw)
    except ValueError:
        data = None
    name = data.get("name") if isinstance(data, dict) else None
    if not isinstance(name, str) or not _OPERATION_RE.match(name):
        raise DumpError("gcloud did not return a valid Firestore operation name.")
    return name


def _progress(metadata: dict[str, Any]) -> str | None:
    parts = []
    for key, label in (("progressDocuments", "documents"), ("progressBytes", "bytes")):
        value = metadata.get(key)
        if isinstance(value, dict) and "completedWork" in value:
            parts.append(f"{value['completedWork']}/{value.get('estimatedWork', '?')} {label}")
    return ", ".join(parts) or None


def parse_operation_status(raw: str) -> OperationStatus:
    """Interpret `gcloud firestore operations describe --format=json`."""
    try:
        data = json.loads(raw)
    except ValueError:
        data = None
    if not isinstance(data, dict):
        raise DumpError("Unexpected output from gcloud while checking the export operation.")
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
    env: dict[str, str]


class _Response(auth_transport.Response):
    def __init__(self, response: httpx.Response) -> None:
        self._response = response

    @property
    def status(self) -> int:
        return self._response.status_code

    @property
    def headers(self) -> Mapping[str, str]:
        return self._response.headers

    @property
    def data(self) -> bytes:
        return self._response.content


class _Request(auth_transport.Request):
    """google-auth transport backed by httpx, so timeouts and redirects are ours to control."""

    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def __call__(
        self,
        url: str,
        method: str = "GET",
        body: bytes | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> auth_transport.Response:
        try:
            response = self._client.request(
                method, url, content=body, headers=headers, timeout=timeout or CONNECT_TIMEOUT
            )
        except httpx.HTTPError as exc:
            raise auth_exceptions.TransportError(type(exc).__name__) from None
        return _Response(response)


def _describe_status(status: int) -> str:
    if status in (401, 403):
        return f"permission denied (HTTP {status})"
    if status == 404:
        return "not found (HTTP 404)"
    return f"unexpected response (HTTP {status})"


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
        info = parse_service_account(self._secrets["service_account_json"])
        info["token_uri"] = TOKEN_URI
        bucket, _ = split_staging_bucket(config.staging_bucket)

        with httpx.Client(
            timeout=CONNECT_TIMEOUT, follow_redirects=False, transport=self._transport
        ) as client:
            try:
                credentials = service_account.Credentials.from_service_account_info(
                    info, scopes=SCOPES
                )
                credentials.refresh(_Request(client))
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

            database_url = (
                f"{FIRESTORE_API}/projects/{quote(config.project_id, safe='')}"
                f"/databases/{quote(config.database_id, safe='()')}"
            )
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

    # ---------- dump ----------

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

    @staticmethod
    def _gcloud_env(root: Path) -> dict[str, str]:
        return clean_env(
            {
                "CLOUDSDK_CONFIG": str(root / "gcloud-config"),
                "CLOUDSDK_CORE_DISABLE_PROMPTS": "1",
                "CLOUDSDK_CORE_DISABLE_USAGE_REPORTING": "true",
                "CLOUDSDK_COMPONENT_MANAGER_DISABLE_UPDATE_CHECK": "true",
            },
            root,
        )

    async def tool_versions(self) -> dict[str, str]:
        root = Path(tempfile.mkdtemp(prefix="gcloud-version-"))
        try:
            output = await capture_output(["gcloud", "--version"], self._gcloud_env(root))
        finally:
            shutil.rmtree(root, ignore_errors=True)
        return {"gcloud": (output.strip().splitlines() or ["unknown"])[0]}

    def _new_session(self, ctx: Ctx) -> _Session:
        root = ctx.work_dir / f"firestore-{uuid.uuid4().hex[:8]}"
        try:
            root.mkdir(mode=0o700)
            (root / "gcloud-config").mkdir(mode=0o700)
        except OSError as exc:
            raise DumpError(f"Cannot prepare the export: {exc.strerror}.") from None
        return _Session(root=root, env=self._gcloud_env(root))

    def _drop_session(self) -> None:
        if self._session is not None:
            shutil.rmtree(self._session.root, ignore_errors=True)
            self._session = None

    async def _gcloud(
        self,
        args: list[str],
        ctx: Ctx,
        *,
        capture: bool = False,
        timeout: int | None = None,
    ) -> tuple[int, str, str]:
        """Run gcloud in the session; returns (exit code, stdout, sanitized stderr tail)."""
        if self._session is None:
            raise DumpError("Internal error: no gcloud session.")
        argv = ["gcloud", *args]
        ctx.log.info("Running: " + " ".join(shlex.quote(a) for a in argv))
        chunks: list[bytes] = []

        async def collect(data: bytes) -> None:
            chunks.append(data)

        tail = StderrTail()
        limit = timeout or ctx.timeout_seconds
        try:
            code = await run_process(
                argv,
                env=self._session.env,
                timeout=limit,
                tail=tail,
                stdout_sink=collect if capture else None,
            )
        except FileNotFoundError:
            raise DumpError("gcloud is not installed in this environment.") from None
        except StepTimeoutError:
            raise DumpError(f"gcloud {args[0]} timed out after {limit} seconds.") from None
        stdout = b"".join(chunks).decode("utf-8", errors="replace")
        return code, stdout, scrub(tail.text(), self.sensitive_values()).strip()

    def _failure(self, what: str, code: int, stderr: str) -> DumpError:
        reason = summarize_stderr(stderr, self.sensitive_values())
        return DumpError(f"{what} failed (gcloud exit code {code}): {reason}")

    async def _activate(self, ctx: Ctx) -> None:
        assert self._session is not None  # noqa: S101
        key_path = self._session.root / "sa-key.json"
        try:
            descriptor = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(self._secrets["service_account_json"])
        except (OSError, KeyError):
            key_path.unlink(missing_ok=True)
            raise DumpError("Cannot prepare the service account credentials.") from None
        try:
            code, _out, stderr = await self._gcloud(
                ["auth", "activate-service-account", f"--key-file={key_path}"], ctx
            )
        finally:
            key_path.unlink(missing_ok=True)
        if code != 0:
            ctx.log.info(f"gcloud stderr:\n{stderr}")
            raise self._failure("Authenticating with the service account", code, stderr)
        ctx.log.info("Authenticated with the service account.")

    async def preflight(self, ctx: DumpContext) -> None:
        config = self._config_model()
        bucket, _prefix = split_staging_bucket(config.staging_bucket)
        self._session = self._new_session(ctx)
        try:
            await self._activate(ctx)
            code, _out, stderr = await self._gcloud(
                ["storage", "ls", f"gs://{bucket}", f"--project={config.project_id}"], ctx
            )
            if code != 0:
                ctx.log.info(f"gcloud stderr:\n{stderr}")
                raise self._failure(f"Reaching the staging bucket gs://{bucket}", code, stderr)
        except BaseException:
            self._drop_session()
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
                self._session = self._new_session(ctx)
                await self._activate(ctx)
            session = self._session
            async with ctx.report.step("export") as step:
                operation = await self._start_export(config, staging_url, ctx)
                step.detail = "Export started."
            async with ctx.report.step("poll") as step:
                await self._poll(operation, config, ctx, step)
            export_dir = session.root / "export"
            async with ctx.report.step("download") as step:
                await self._download(staging_url, export_dir, ctx)
                step.detail = "Export downloaded."
            final = work_file(ctx, safe_component(database), "tar.gz")
            async with ctx.report.step("pack") as step:
                size, digest = await self._pack(export_dir, final, ctx)
            await self._cleanup_staging(config, staging_url, ctx)
        finally:
            self._drop_session()
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
        args = [
            "firestore",
            "export",
            staging_url,
            f"--project={config.project_id}",
            f"--database={config.database_id}",
        ]
        if config.collection_ids:
            args.append(f"--collection-ids={','.join(config.collection_ids)}")
        args += ["--async", "--format=json"]
        code, out, stderr = await self._gcloud(args, ctx, capture=True)
        if code != 0:
            ctx.log.info(f"gcloud stderr:\n{stderr}")
            raise self._failure("Starting the Firestore export", code, stderr)
        operation = parse_operation_name(out)
        ctx.log.info(f"Export operation: {operation}")
        return operation

    def _operation_args(self, verb: str, operation: str, config: FirestoreConfig) -> list[str]:
        return [
            "firestore",
            "operations",
            verb,
            operation,
            f"--project={config.project_id}",
            f"--database={config.database_id}",
        ]

    async def _cancel_operation(
        self, operation: str, config: FirestoreConfig, ctx: Ctx, label: str = "export"
    ) -> None:
        """Best effort; the outcome is only logged."""
        try:
            code, _out, stderr = await self._gcloud(
                self._operation_args("cancel", operation, config),
                ctx,
                timeout=CANCEL_OPERATION_SECONDS,
            )
        except DumpError as exc:
            ctx.log.warning(f"Could not cancel the Firestore {label} operation: {exc}")
            return
        if code == 0:
            ctx.log.info(f"Requested cancellation of the Firestore {label} operation.")
        else:
            ctx.log.warning(
                f"Could not cancel the Firestore {label} operation: "
                + summarize_stderr(stderr, self.sensitive_values())
            )

    async def _poll(
        self,
        operation: str,
        config: FirestoreConfig,
        ctx: Ctx,
        step: StepHandle,
        label: str = "export",
    ) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + ctx.timeout_seconds
        failures = 0
        last: tuple[str, str | None] | None = None
        try:
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise TimeoutError
                try:
                    code, out, stderr = await self._gcloud(
                        [*self._operation_args("describe", operation, config), "--format=json"],
                        ctx,
                        capture=True,
                        timeout=max(1, int(remaining)),
                    )
                except DumpError:
                    if deadline - loop.time() <= 0:
                        raise TimeoutError from None
                    raise
                if code != 0:
                    failures += 1
                    ctx.log.warning(f"Checking the {label} failed (attempt {failures}): {stderr}")
                    if failures >= MAX_DESCRIBE_FAILURES:
                        raise self._failure(f"Checking the {label} operation", code, stderr)
                else:
                    failures = 0
                    status = parse_operation_status(out)
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
            await self._cancel_operation(operation, config, ctx, label)
            raise
        except TimeoutError:
            ctx.log.warning(f"Timed out waiting for the {label}; cancelling it.")
            await self._cancel_operation(operation, config, ctx, label)
            raise DumpError(
                f"The Firestore {label} did not finish within {ctx.timeout_seconds} seconds. "
                "Increase the job's timeout for large databases."
            ) from None

    async def _download(self, staging_url: str, export_dir: Path, ctx: DumpContext) -> None:
        try:
            export_dir.mkdir(mode=0o700)
        except OSError as exc:
            raise DumpError(f"Cannot prepare the download: {exc.strerror}.") from None
        code, _out, stderr = await self._gcloud(
            ["storage", "cp", "-r", staging_url, str(export_dir)], ctx
        )
        if code != 0:
            ctx.log.info(f"gcloud stderr:\n{stderr}")
            raise self._failure("Downloading the export", code, stderr)
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
        self, config: FirestoreConfig, staging_url: str, ctx: DumpContext
    ) -> None:
        if not config.delete_staging_after_download:
            ctx.log.info(f"Keeping the staging export at {staging_url} (deletion is turned off).")
            return
        async with ctx.report.step("cleanup-staging") as step:
            try:
                code, _out, stderr = await self._gcloud(["storage", "rm", "-r", staging_url], ctx)
                problem = None if code == 0 else summarize_stderr(stderr, self.sensitive_values())
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
        try:
            return await self.tool_versions()
        except DumpError as exc:
            raise RestoreError(str(exc)) from None

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
        staging_url = f"gs://{bucket}/" + "/".join(([base] if base else []) + parts)
        self._session = self._new_session(ctx)
        session = self._session
        uploading = False
        try:
            await self._activate(ctx)
            code, _out, stderr = await self._gcloud(
                ["storage", "ls", f"gs://{bucket}", f"--project={config.project_id}"], ctx
            )
            if code != 0:
                ctx.log.info(f"gcloud stderr:\n{stderr}")
                raise self._failure(f"Reaching the staging bucket gs://{bucket}", code, stderr)
            metadata = await self._unpack(artifact, session.root / "export", ctx)
            uploading = True
            await self._upload(metadata.parent, staging_url, ctx)
            uri = f"{staging_url}/{metadata.parent.name}/{metadata.name}"
            operation = await self._start_import(config, uri, ctx)
            await self._poll(operation, config, ctx, StepHandle("import"), "import")
        finally:
            try:
                if uploading:
                    await self._remove_staging(staging_url, ctx)
            finally:
                self._drop_session()

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

    async def _upload(self, export_root: Path, staging_url: str, ctx: RestoreContext) -> None:
        code, _out, stderr = await self._gcloud(
            ["storage", "cp", "-r", str(export_root), staging_url + "/"], ctx
        )
        if code != 0:
            ctx.log.info(f"gcloud stderr:\n{stderr}")
            raise self._failure("Uploading the backup to the staging bucket", code, stderr)
        ctx.log.info(f"Uploaded the export to {staging_url}.")

    async def _start_import(self, config: FirestoreConfig, uri: str, ctx: RestoreContext) -> str:
        args = [
            "firestore",
            "import",
            uri,
            f"--project={config.project_id}",
            f"--database={config.database_id}",
            "--async",
            "--format=json",
        ]
        code, out, stderr = await self._gcloud(args, ctx, capture=True)
        if code != 0:
            ctx.log.info(f"gcloud stderr:\n{stderr}")
            raise self._failure("Starting the Firestore import", code, stderr)
        operation = parse_operation_name(out)
        ctx.log.info(f"Import operation: {operation}")
        return operation

    async def _remove_staging(self, staging_url: str, ctx: RestoreContext) -> None:
        """Delete the uploaded copy; failures are only logged (the restore itself has finished)."""
        try:
            code, _out, stderr = await self._gcloud(["storage", "rm", "-r", staging_url], ctx)
            problem = None if code == 0 else summarize_stderr(stderr, self.sensitive_values())
        except DumpError as exc:
            problem = str(exc)
        if problem is None:
            ctx.log.info("Deleted the staging copy of the export.")
            return
        ctx.log.warning(
            f"Could not delete the staging copy {staging_url}: {problem} Delete it by hand or "
            "add a lifecycle rule to the bucket (for example delete after 1 day)."
        )

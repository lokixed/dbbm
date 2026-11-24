"""Google REST access for the Firestore driver: OAuth token, Firestore operations, Cloud Storage.

Everything here talks to Google over HTTPS with the service account's access token. The key never
touches the disk and no Google command-line tool is involved. Errors are raised as
GoogleApiError with a plain description; callers redact it before it reaches a log or the UI.
"""

import asyncio
import os
import re
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote

import httpx
from google.auth import exceptions as auth_exceptions
from google.auth import transport as auth_transport
from google.oauth2 import service_account

from app.drivers.base import CONNECT_TIMEOUT
from app.schemas.source import parse_service_account

# Pinned so a crafted service-account file cannot redirect the token request elsewhere.
TOKEN_URI = "https://oauth2.googleapis.com/token"  # noqa: S105
SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]
FIRESTORE_API = "https://firestore.googleapis.com/v1"
STORAGE_API = "https://storage.googleapis.com/storage/v1"
STORAGE_UPLOAD_API = "https://storage.googleapis.com/upload/storage/v1"

# Module-level so tests can shorten them.
RETRY_DELAYS = (1.0, 2.0, 4.0)
CHUNK_BYTES = 1024 * 1024
READ_TIMEOUT = 120.0
_RETRY_STATUSES = frozenset({408, 429, 500, 502, 503, 504})
_OPERATION_RE = re.compile(
    r"^projects/[A-Za-z0-9._:-]+/databases/(?:\(default\)|[a-z0-9-]+)/operations/[A-Za-z0-9_=.-]+$"
)


class GoogleApiError(Exception):
    """A failed Google API call. `status` is the HTTP status, or None when there was none."""

    def __init__(self, status: int | None, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


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


class SyncRequest(auth_transport.Request):
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


def service_account_credentials(raw_json: str) -> service_account.Credentials:
    info = parse_service_account(raw_json)
    info["token_uri"] = TOKEN_URI
    credentials: service_account.Credentials = (
        service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    )
    return credentials


def database_path(project_id: str, database_id: str) -> str:
    return f"projects/{quote(project_id, safe='')}/databases/{quote(database_id, safe='()')}"


def _error_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        body = None
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict) and error.get("message"):
        return f"{error.get('status') or 'error'}: {error['message']}"
    return f"unexpected response (HTTP {response.status_code})"


def _object_url(bucket: str, name: str) -> str:
    return f"{STORAGE_API}/b/{quote(bucket, safe='')}/o/{quote(name, safe='')}"


class GoogleClient:
    """One service account's access to Firestore and Cloud Storage for one run."""

    def __init__(
        self, service_account_json: str, transport: httpx.BaseTransport | None = None
    ) -> None:
        self._raw = service_account_json
        # Tests inject an httpx.MockTransport, which serves both the sync and the async client.
        self._transport = transport
        self._credentials: service_account.Credentials | None = None
        self._refresh_lock = asyncio.Lock()
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(READ_TIMEOUT, connect=CONNECT_TIMEOUT),
            follow_redirects=False,
            transport=cast(httpx.AsyncBaseTransport | None, transport),
        )

    async def close(self) -> None:
        await self._client.aclose()

    # ---------- authentication ----------

    def _refresh_sync(self) -> None:
        if self._credentials is None:
            self._credentials = service_account_credentials(self._raw)
        with httpx.Client(
            timeout=CONNECT_TIMEOUT, follow_redirects=False, transport=self._transport
        ) as client:
            self._credentials.refresh(SyncRequest(client))

    async def authenticate(self) -> None:
        """Fetch the first access token. Raises whatever google-auth raises (caller redacts)."""
        async with self._refresh_lock:
            await asyncio.to_thread(self._refresh_sync)

    async def _token(self) -> str:
        async with self._refresh_lock:
            if self._credentials is None or not self._credentials.valid:
                await asyncio.to_thread(self._refresh_sync)
            assert self._credentials is not None  # noqa: S101
            return str(self._credentials.token)

    def _forget_token(self) -> None:
        if self._credentials is not None:
            self._credentials.token = None

    # ---------- requests ----------

    async def _send(
        self,
        method: str,
        url: str,
        *,
        retry: bool = True,
        content: AsyncIterator[bytes] | None = None,
        stream_to: Path | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        """One API call with a few retries on throttling, server errors and network failures."""
        delays = RETRY_DELAYS if retry else ()
        refreshed = False
        attempt = 0
        while True:
            try:
                return await self._attempt(method, url, content, stream_to, kwargs)
            except GoogleApiError as exc:
                if exc.status == 401 and not refreshed:
                    refreshed = True
                    self._forget_token()
                    continue
                transient = exc.status is None or exc.status in _RETRY_STATUSES
                if not transient or attempt >= len(delays):
                    raise
                await asyncio.sleep(delays[attempt])
                attempt += 1

    async def _attempt(
        self,
        method: str,
        url: str,
        content: AsyncIterator[bytes] | None,
        stream_to: Path | None,
        kwargs: dict[str, Any],
    ) -> httpx.Response:
        headers = {"Authorization": f"Bearer {await self._token()}"}
        if content is not None:
            headers["Content-Type"] = "application/octet-stream"
            kwargs["content"] = content
        try:
            if stream_to is None:
                response = await self._client.request(method, url, headers=headers, **kwargs)
            else:
                response = await self._download(url, headers, kwargs, stream_to)
        except httpx.HTTPError as exc:
            raise GoogleApiError(None, f"request failed ({type(exc).__name__})") from None
        if response.status_code >= 400:
            raise GoogleApiError(response.status_code, _error_detail(response))
        return response

    async def _download(
        self, url: str, headers: dict[str, str], kwargs: dict[str, Any], destination: Path
    ) -> httpx.Response:
        async with self._client.stream("GET", url, headers=headers, **kwargs) as response:
            if response.status_code >= 400:
                await response.aread()
                return response
            with destination.open("wb") as handle:
                async for chunk in response.aiter_bytes(CHUNK_BYTES):
                    handle.write(chunk)
        return response

    # ---------- Firestore ----------

    async def start_export(
        self,
        project_id: str,
        database_id: str,
        output_uri: str,
        collection_ids: list[str] | None,
    ) -> str:
        body: dict[str, Any] = {"outputUriPrefix": output_uri}
        if collection_ids:
            body["collectionIds"] = collection_ids
        url = f"{FIRESTORE_API}/{database_path(project_id, database_id)}:exportDocuments"
        return operation_name(await self._send("POST", url, retry=False, json=body))

    async def start_import(self, project_id: str, database_id: str, input_uri: str) -> str:
        url = f"{FIRESTORE_API}/{database_path(project_id, database_id)}:importDocuments"
        response = await self._send("POST", url, retry=False, json={"inputUriPrefix": input_uri})
        return operation_name(response)

    async def get_operation(self, name: str) -> dict[str, Any]:
        response = await self._send("GET", f"{FIRESTORE_API}/{name}")
        data = _json_object(response)
        if data is None:
            raise GoogleApiError(response.status_code, "unexpected response from Firestore")
        return data

    async def cancel_operation(self, name: str) -> None:
        await self._send("POST", f"{FIRESTORE_API}/{name}:cancel", retry=False, json={})

    # ---------- Cloud Storage ----------

    async def check_bucket(self, bucket: str) -> None:
        url = f"{STORAGE_API}/b/{quote(bucket, safe='')}/o"
        await self._send("GET", url, params={"maxResults": "1", "fields": "kind"})

    async def list_objects(self, bucket: str, prefix: str) -> list[tuple[str, int]]:
        """Every (object name, size) under the prefix, following result pages."""
        url = f"{STORAGE_API}/b/{quote(bucket, safe='')}/o"
        found: list[tuple[str, int]] = []
        token: str | None = None
        while True:
            params = {"prefix": prefix, "fields": "nextPageToken,items(name,size)"}
            if token:
                params["pageToken"] = token
            data = _json_object(await self._send("GET", url, params=params)) or {}
            for item in data.get("items") or []:
                found.append((str(item["name"]), int(item.get("size") or 0)))
            token = data.get("nextPageToken")
            if not token:
                return found

    async def download_object(self, bucket: str, name: str, destination: Path, size: int) -> None:
        """Download to `destination` (written through a .partial file); the size must match."""
        partial = destination.with_name(destination.name + ".partial")
        try:
            await self._send(
                "GET", _object_url(bucket, name), params={"alt": "media"}, stream_to=partial
            )
            if partial.stat().st_size != size:
                raise GoogleApiError(None, f"downloaded size of {name} does not match the listing")
            os.replace(partial, destination)
        finally:
            partial.unlink(missing_ok=True)

    async def upload_object(self, bucket: str, name: str, source: Path) -> None:
        url = f"{STORAGE_UPLOAD_API}/b/{quote(bucket, safe='')}/o"
        params = {"uploadType": "media", "name": name}
        delays = RETRY_DELAYS
        attempt = 0
        while True:
            try:
                await self._send(
                    "POST", url, retry=False, params=params, content=_file_chunks(source)
                )
                return
            except GoogleApiError as exc:
                transient = exc.status is None or exc.status in _RETRY_STATUSES
                if not transient or attempt >= len(delays):
                    raise
                await asyncio.sleep(delays[attempt])
                attempt += 1

    async def delete_object(self, bucket: str, name: str) -> None:
        try:
            await self._send("DELETE", _object_url(bucket, name))
        except GoogleApiError as exc:
            if exc.status != 404:
                raise

    async def delete_prefix(self, bucket: str, prefix: str) -> int:
        """Delete every object under the prefix; returns how many were removed."""
        objects = await self.list_objects(bucket, prefix)
        for name, _size in objects:
            await self.delete_object(bucket, name)
        return len(objects)


def _json_object(response: httpx.Response) -> dict[str, Any] | None:
    try:
        data = response.json()
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def parse_operation_name(data: Any) -> str:
    """The long-running operation name from a start-export or start-import response."""
    name = data.get("name") if isinstance(data, dict) else None
    if not isinstance(name, str) or not _OPERATION_RE.match(name):
        raise GoogleApiError(None, "no valid Firestore operation name returned")
    return name


def operation_name(response: httpx.Response) -> str:
    return parse_operation_name(_json_object(response))


async def _file_chunks(path: Path) -> AsyncIterator[bytes]:
    with path.open("rb") as handle:
        while chunk := await asyncio.to_thread(handle.read, CHUNK_BYTES):
            yield chunk

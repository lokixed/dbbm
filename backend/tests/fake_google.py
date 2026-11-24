"""A fake Google (OAuth, Firestore and Cloud Storage REST) behind an httpx.MockTransport.

Tests change `modes` to make one kind of call fail; everything the driver sent is recorded.
No network and no Google Cloud access. Fixture: `fake_google`.
"""

import json
from collections.abc import Callable
from typing import Any
from urllib.parse import unquote

import httpx
import pytest
from google.auth import exceptions as auth_exceptions

from app.drivers import firestore_api
from app.drivers.firestore import FirestoreDriver

TOKEN = "ya29.fake-access-token-value"
EXPORT_OPERATION = "projects/p1/databases/(default)/operations/OP123"
IMPORT_OPERATION = "projects/p1/databases/(default)/operations/IMP456"


def error_response(status: int, message: str, name: str = "PERMISSION_DENIED") -> httpx.Response:
    return httpx.Response(
        status, json={"error": {"code": status, "message": message, "status": name}}
    )


class FakeCredentials:
    """Stands in for google-auth when a test's service-account key is not a real PEM key."""

    def __init__(self) -> None:
        self.token: str | None = None

    @property
    def valid(self) -> bool:
        return self.token is not None

    def refresh(self, request: Callable[..., Any]) -> None:
        response = request(firestore_api.TOKEN_URI, method="POST", body=b"assertion=fake")
        if response.status != 200:
            raise auth_exceptions.RefreshError(response.data.decode())
        self.token = str(json.loads(response.data)["access_token"])


class FakeGoogle:
    def __init__(self) -> None:
        # call kind -> "ok" or a failure mode; see the handlers below
        self.modes: dict[str, str] = {}
        self.leak = "ya29.leaked-token"  # secret-looking text a failing call puts in its message
        self.objects: dict[str, bytes] = {}  # "bucket/name" -> content
        self.page_size: int | None = None  # list results per page; None = all at once
        self.requests: list[tuple[str, str]] = []
        self.export_bodies: list[dict[str, Any]] = []
        self.import_bodies: list[dict[str, Any]] = []
        self.cancelled: list[str] = []
        self.describe_count = 0
        self.uploaded: list[str] = []  # object names
        self.deleted: list[str] = []
        self.transport = httpx.MockTransport(self.handle)

    def mode(self, kind: str) -> str:
        return self.modes.get(kind, "ok")

    def objects_under(self, bucket: str, prefix: str) -> list[str]:
        start = f"{bucket}/{prefix}"
        return sorted(k[len(bucket) + 1 :] for k in self.objects if k.startswith(start))

    # ---------- routing ----------

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append((request.method, str(request.url)))
        host, path = request.url.host, request.url.path
        if host == "oauth2.googleapis.com":
            return self._token()
        if request.headers.get("Authorization") != f"Bearer {TOKEN}":
            return error_response(401, "missing or wrong token", "UNAUTHENTICATED")
        if host == "firestore.googleapis.com":
            return self._firestore(request, path)
        if host == "storage.googleapis.com":
            return self._storage(request, path)
        raise AssertionError(f"unexpected host {host}")

    def _token(self) -> httpx.Response:
        if self.mode("auth") == "fail":
            return httpx.Response(
                400, json={"error": "invalid_grant", "error_description": self.leak}
            )
        return httpx.Response(200, json={"access_token": TOKEN, "expires_in": 3600})

    # ---------- Firestore ----------

    def _firestore(self, request: httpx.Request, path: str) -> httpx.Response:
        if path.endswith(":exportDocuments"):
            return self._export(request)
        if path.endswith(":importDocuments"):
            return self._import(request)
        if path.endswith(":cancel"):
            self.cancelled.append(path.removeprefix("/v1/").removesuffix(":cancel"))
            if self.mode("cancel") == "fail":
                return error_response(500, "cancel is broken", "INTERNAL")
            return httpx.Response(200, json={})
        if "/operations/" in path:
            return self._describe()
        return httpx.Response(200, json={"name": path})  # the database itself

    def _export(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.export_bodies.append(body)
        if self.mode("export") == "fail":
            return error_response(403, f"Permission denied Bearer {self.leak}")
        bucket, _, prefix = body["outputUriPrefix"].removeprefix("gs://").partition("/")
        if self.mode("download") != "nometa":
            name = prefix.rsplit("/", 1)[-1]
            self.objects[f"{bucket}/{prefix}/{name}.overall_export_metadata"] = b"meta"
        self.objects[f"{bucket}/{prefix}/all_namespaces/kind_items/output-0"] = b"data"
        if self.mode("download") == "evilname":
            self.objects[f"{bucket}/{prefix}/../escape.txt"] = b"x"
        return httpx.Response(
            200, json={"name": EXPORT_OPERATION, "metadata": {"operationState": "INITIALIZING"}}
        )

    def _import(self, request: httpx.Request) -> httpx.Response:
        self.import_bodies.append(json.loads(request.content))
        if self.mode("import") == "fail":
            return error_response(403, f"Permission denied Bearer {self.leak}")
        return httpx.Response(200, json={"name": IMPORT_OPERATION})

    def _describe(self) -> httpx.Response:
        self.describe_count += 1
        mode = self.mode("describe")
        if mode == "broken" or (mode == "flaky" and self.describe_count == 1):
            return error_response(503, "backend unavailable", "UNAVAILABLE")
        if mode == "forever":
            return httpx.Response(
                200, json={"done": False, "metadata": {"operationState": "PROCESSING"}}
            )
        if mode == "error":
            return httpx.Response(
                200,
                json={
                    "done": True,
                    "metadata": {"operationState": "FAILED"},
                    "error": {"code": 7, "message": f"PERMISSION_DENIED token={self.leak}"},
                },
            )
        if self.describe_count <= 1:
            progress = {"completedWork": "5", "estimatedWork": "10"}
            metadata = {"operationState": "PROCESSING", "progressDocuments": progress}
            return httpx.Response(200, json={"done": False, "metadata": metadata})
        return httpx.Response(
            200, json={"done": True, "metadata": {"operationState": "SUCCESSFUL"}}
        )

    # ---------- Cloud Storage ----------

    def _storage(self, request: httpx.Request, path: str) -> httpx.Response:
        raw = request.url.raw_path.decode().split("?", 1)[0]
        bucket = path.split("/b/", 1)[1].split("/", 1)[0]
        if "/upload/" in path:
            return self._upload(request, bucket)
        if "/o/" in raw:
            name = unquote(raw.split("/o/", 1)[1])
            if request.method == "DELETE":
                return self._delete(bucket, name)
            return self._download(bucket, name)
        return self._list(request, bucket)

    def _list(self, request: httpx.Request, bucket: str) -> httpx.Response:
        if self.mode("bucket") == "fail":
            return error_response(403, "no access to the bucket")
        prefix = request.url.params.get("prefix", "")
        if "prefix" not in request.url.params:
            return httpx.Response(200, json={"kind": "storage#objects"})
        names = self.objects_under(bucket, prefix)
        start = int(request.url.params.get("pageToken", "0"))
        end = len(names) if self.page_size is None else start + self.page_size
        payload: dict[str, Any] = {
            "items": [
                {"name": n, "size": str(len(self.objects[f"{bucket}/{n}"]))}
                for n in names[start:end]
            ]
        }
        if end < len(names):
            payload["nextPageToken"] = str(end)
        return httpx.Response(200, json=payload)

    def _download(self, bucket: str, name: str) -> httpx.Response:
        if self.mode("download") == "fail":
            return error_response(403, "no access to the object")
        content = self.objects.get(f"{bucket}/{name}")
        if content is None:
            return error_response(404, "no such object", "NOT_FOUND")
        return httpx.Response(200, content=content)

    def _upload(self, request: httpx.Request, bucket: str) -> httpx.Response:
        if self.mode("upload") == "fail":
            return error_response(403, f"cannot write Bearer {self.leak}")
        name = request.url.params["name"]
        self.objects[f"{bucket}/{name}"] = request.content
        self.uploaded.append(name)
        return httpx.Response(200, json={"name": name})

    def _delete(self, bucket: str, name: str) -> httpx.Response:
        if self.mode("delete") == "fail":
            return error_response(403, "AccessDeniedException: 403 no storage.objects.delete")
        self.objects.pop(f"{bucket}/{name}", None)
        self.deleted.append(name)
        return httpx.Response(204)


@pytest.fixture
def fake_google(monkeypatch: pytest.MonkeyPatch) -> FakeGoogle:
    fake = FakeGoogle()
    monkeypatch.setattr(FirestoreDriver, "default_transport", fake.transport)
    monkeypatch.setattr(firestore_api, "RETRY_DELAYS", ())
    monkeypatch.setattr(firestore_api, "service_account_credentials", lambda raw: FakeCredentials())
    return fake

import json
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.db import get_engine
from app.drivers.base import BaseDriver, CheckResult, ConnectionTestResult
from app.models import AuditLog, Source
from app.services.secrets import load_secrets
from tests.conftest import api_delete, api_post, api_put

PW = "s3cret-Pw-do-not-leak"
URI = "mongodb://admin:uri-secret-pw@mongo.internal:27017/?authSource=admin"
SA_KEY = "-----BEGIN PRIVATE KEY-----\nTOPSECRETKEYMATERIAL\n-----END PRIVATE KEY-----\n"
ALL_SECRETS = (PW, "uri-secret-pw", "TOPSECRETKEYMATERIAL")


def mysql_body(name: str = "m1", **over: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "type": "mysql",
        "name": name,
        "config": {"host": "db.internal", "username": "backup", "databases": ["app"]},
        "secrets": {"password": PW},
    }
    body.update(over)
    return body


def sa_json(project: str = "my-project-1") -> str:
    return json.dumps(
        {
            "type": "service_account",
            "client_email": f"bk@{project}.iam.gserviceaccount.com",
            "private_key": SA_KEY,
            "project_id": project,
        }
    )


def firestore_body(name: str = "f1", **over: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "type": "firestore",
        "name": name,
        "config": {"project_id": "my-project-1", "staging_bucket": "gs://my-bucket"},
        "secrets": {"service_account_json": sa_json()},
    }
    body.update(over)
    return body


def mongo_uri_body(name: str = "g1") -> dict[str, Any]:
    return {
        "type": "mongo",
        "name": name,
        "config": {"mode": "uri"},
        "secrets": {"uri": URI},
    }


def stored_secrets(source_id: str) -> dict[str, str]:
    with Session(get_engine()) as db:
        row = db.get(Source, uuid.UUID(source_id))
        assert row is not None
        return load_secrets(row.secrets_enc)


def audit_rows() -> list[AuditLog]:
    with Session(get_engine()) as db:
        return list(db.exec(select(AuditLog).order_by(AuditLog.timestamp)))  # type: ignore[arg-type]


def create(client: TestClient, body: dict[str, Any]) -> dict[str, Any]:
    response = api_post(client, "/api/sources", body)
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


class FakeDriver(BaseDriver):
    source_type = "fake"
    calls: list[dict[str, str]] = []

    def _probe(self) -> ConnectionTestResult:
        FakeDriver.calls.append(dict(self._secrets))
        return ConnectionTestResult(
            ok=True,
            message="Connected to Fake 1.0.",
            server_version="1.0",
            databases=["app", "other"],
            checks=[CheckResult("x", True, "ok")],
        )


@pytest.fixture
def fake_driver(monkeypatch: pytest.MonkeyPatch) -> type[FakeDriver]:
    FakeDriver.calls = []
    monkeypatch.setattr(
        "app.api.sources.get_driver", lambda _t, config, secrets: FakeDriver(config, secrets)
    )
    return FakeDriver


# ---------- auth and CSRF ----------

ID = "00000000-0000-0000-0000-000000000000"
ROUTES = [
    ("GET", "/api/sources"),
    ("POST", "/api/sources"),
    ("GET", f"/api/sources/{ID}"),
    ("PUT", f"/api/sources/{ID}"),
    ("DELETE", f"/api/sources/{ID}"),
    ("POST", f"/api/sources/{ID}/test"),
    ("GET", "/api/destinations"),
    ("POST", "/api/destinations"),
    ("GET", f"/api/destinations/{ID}"),
    ("PUT", f"/api/destinations/{ID}"),
    ("DELETE", f"/api/destinations/{ID}"),
    ("POST", f"/api/destinations/{ID}/test"),
]


@pytest.mark.parametrize(("method", "path"), ROUTES)
def test_every_route_requires_auth(client: TestClient, method: str, path: str) -> None:
    client.get("/api/auth/status")
    from tests.conftest import csrf_headers

    response = client.request(method, path, json={}, headers=csrf_headers(client))
    assert response.status_code == 401


@pytest.mark.parametrize(("method", "path"), [r for r in ROUTES if r[0] != "GET"])
def test_state_changing_routes_require_csrf(authed: TestClient, method: str, path: str) -> None:
    response = authed.request(method, path, json={})
    assert response.status_code == 403
    assert response.json()["detail"] == "csrf_failed"


# ---------- write-only secrets ----------


def test_create_never_returns_secrets(authed: TestClient) -> None:
    created = create(authed, mysql_body())
    assert created["secrets_set"] == {"password": True}
    assert created["last_test"] is None
    for response in (
        authed.get("/api/sources"),
        authed.get(f"/api/sources/{created['id']}"),
    ):
        assert response.status_code == 200
        assert not any(s in response.text for s in ALL_SECRETS)
        assert "secrets_enc" not in response.text
    assert stored_secrets(created["id"]) == {"password": PW}


def test_each_type_reports_its_own_secret_fields(authed: TestClient) -> None:
    pg = create(
        authed,
        {**mysql_body("p1"), "type": "postgres", "secrets": {}},
    )
    assert pg["secrets_set"] == {"password": False}
    assert pg["config"]["connection_database"] == "postgres"
    uri = create(authed, mongo_uri_body())
    assert uri["secrets_set"] == {"uri": True}
    fs = create(authed, firestore_body())
    assert fs["secrets_set"] == {"service_account_json": True}
    assert not any(s in authed.get("/api/sources").text for s in ALL_SECRETS)


def test_update_omit_keeps_null_clears_value_replaces(authed: TestClient) -> None:
    created = create(authed, mysql_body())
    url = f"/api/sources/{created['id']}"

    omitted = mysql_body(secrets={})
    omitted["config"]["host"] = "db2.internal"
    response = api_put(authed, url, omitted)
    assert response.status_code == 200 and response.json()["secrets_set"] == {"password": True}
    assert response.json()["config"]["host"] == "db2.internal"
    assert stored_secrets(created["id"]) == {"password": PW}

    replaced = api_put(authed, url, mysql_body(secrets={"password": "brand-new-pw"}))
    assert replaced.json()["secrets_set"] == {"password": True}
    assert stored_secrets(created["id"]) == {"password": "brand-new-pw"}

    cleared = api_put(authed, url, mysql_body(secrets={"password": None}))
    assert cleared.json()["secrets_set"] == {"password": False}
    assert stored_secrets(created["id"]) == {}


def test_empty_string_secret_is_rejected(authed: TestClient) -> None:
    created = create(authed, mysql_body())
    response = api_put(
        authed, f"/api/sources/{created['id']}", mysql_body(secrets={"password": ""})
    )
    assert response.status_code == 422
    assert stored_secrets(created["id"]) == {"password": PW}


def test_required_secrets_cannot_be_omitted_or_cleared(authed: TestClient) -> None:
    assert api_post(authed, "/api/sources", firestore_body(secrets={})).status_code == 422
    created = create(authed, firestore_body())
    response = api_put(
        authed,
        f"/api/sources/{created['id']}",
        firestore_body(secrets={"service_account_json": None}),
    )
    assert response.status_code == 422


def test_firestore_project_must_match_service_account(authed: TestClient) -> None:
    body = firestore_body(secrets={"service_account_json": sa_json("another-project")})
    response = api_post(authed, "/api/sources", body)
    assert response.status_code == 422
    assert "project_id" in response.text and "TOPSECRETKEYMATERIAL" not in response.text
    created = create(authed, firestore_body())
    changed = firestore_body(secrets={})
    changed["config"]["project_id"] = "another-project"
    assert api_put(authed, f"/api/sources/{created['id']}", changed).status_code == 422


def test_mongo_mode_switch_prunes_old_secret(authed: TestClient) -> None:
    created = create(authed, mongo_uri_body())
    fields = {
        "type": "mongo",
        "name": "g1",
        "config": {"mode": "fields", "host": "mongo.internal", "username": "u"},
        "secrets": {"password": "pw2"},
    }
    response = api_put(authed, f"/api/sources/{created['id']}", fields)
    assert response.status_code == 200
    assert stored_secrets(created["id"]) == {"password": "pw2"}
    bad = {**fields, "secrets": {"uri": URI}}
    assert api_put(authed, f"/api/sources/{created['id']}", bad).status_code == 422


def test_validation_errors_do_not_echo_secrets(authed: TestClient) -> None:
    body = mysql_body()
    body["config"]["port"] = 99999
    response = api_post(authed, "/api/sources", body)
    assert response.status_code == 422 and PW not in response.text


# ---------- conflicts and lookups ----------


def test_duplicate_names_return_409(authed: TestClient) -> None:
    first = create(authed, mysql_body("dup"))
    assert api_post(authed, "/api/sources", mysql_body("dup")).status_code == 409
    other = create(authed, mysql_body("other"))
    assert api_put(authed, f"/api/sources/{other['id']}", mysql_body("dup")).status_code == 409
    assert api_put(authed, f"/api/sources/{first['id']}", mysql_body("dup")).status_code == 200


def test_unknown_id_404_and_bad_id_422(authed: TestClient) -> None:
    assert authed.get(f"/api/sources/{ID}").status_code == 404
    assert api_delete(authed, f"/api/sources/{ID}").status_code == 404
    assert api_post(authed, f"/api/sources/{ID}/test").status_code == 404
    assert authed.get("/api/sources/not-a-uuid").status_code == 422


def test_type_cannot_change(authed: TestClient) -> None:
    created = create(authed, mysql_body())
    body = {**mysql_body(), "type": "postgres"}
    assert api_put(authed, f"/api/sources/{created['id']}", body).status_code == 422


def test_delete(authed: TestClient) -> None:
    created = create(authed, mysql_body())
    assert api_delete(authed, f"/api/sources/{created['id']}").status_code == 204
    assert authed.get("/api/sources").json() == []


# ---------- test connection ----------


def test_test_endpoint_uses_decrypted_secrets_and_stores_result(
    authed: TestClient, fake_driver: type[FakeDriver]
) -> None:
    created = create(authed, mysql_body())
    response = api_post(authed, f"/api/sources/{created['id']}/test")
    assert response.status_code == 200
    result = response.json()
    assert result["ok"] and result["server_version"] == "1.0"
    assert result["databases"] == ["app", "other"]
    assert fake_driver.calls == [{"password": PW}]
    assert PW not in response.text

    stored = authed.get(f"/api/sources/{created['id']}").json()["last_test"]
    assert stored["ok"] is True and stored["message"] == "Connected to Fake 1.0."


def test_failed_test_is_stored_and_sanitized(authed: TestClient) -> None:
    body = mysql_body()
    body["config"]["host"] = "127.0.0.1"
    body["config"]["port"] = 1
    body["config"]["tls_mode"] = "disabled"
    created = create(authed, body)
    response = api_post(authed, f"/api/sources/{created['id']}/test")
    assert response.status_code == 200 and response.json()["ok"] is False
    assert PW not in response.text
    last = authed.get(f"/api/sources/{created['id']}").json()["last_test"]
    assert last["ok"] is False and PW not in json.dumps(last)


def test_undecryptable_secrets_give_clear_error(authed: TestClient) -> None:
    created = create(authed, mysql_body())
    with Session(get_engine()) as db:
        row = db.exec(select(Source)).one()
        row.secrets_enc = b"corrupted-or-wrong-master-key"
        db.add(row)
        db.commit()

    listing = authed.get("/api/sources")
    assert listing.status_code == 200
    item = listing.json()[0]
    assert item["secrets_set"] == {"password": False} and "master key" in item["secrets_error"]

    url = f"/api/sources/{created['id']}"
    tested = api_post(authed, f"{url}/test")
    assert tested.status_code == 409 and "master key" in tested.json()["detail"]
    assert "Traceback" not in tested.text

    keep = api_put(authed, url, mysql_body(secrets={}))
    assert keep.status_code == 409
    reenter = api_put(authed, url, mysql_body(secrets={"password": "fresh-pw"}))
    assert reenter.status_code == 200 and reenter.json()["secrets_error"] is None
    assert stored_secrets(created["id"]) == {"password": "fresh-pw"}


# ---------- audit ----------


def test_audit_entries_exist_and_hold_no_secrets(
    authed: TestClient, fake_driver: type[FakeDriver]
) -> None:
    created = create(authed, mysql_body())
    url = f"/api/sources/{created['id']}"
    api_put(authed, url, mysql_body("renamed", secrets={"password": "another-pw-xyz"}))
    api_put(authed, url, mysql_body("renamed", secrets={"password": None}))
    api_post(authed, f"{url}/test")
    fs = create(authed, firestore_body())
    api_delete(authed, url)
    api_delete(authed, f"/api/sources/{fs['id']}")

    rows = [r for r in audit_rows() if r.object_type == "source"]
    assert [r.action for r in rows] == [
        "source.create",
        "source.update",
        "source.update",
        "source.test",
        "source.create",
        "source.delete",
        "source.delete",
    ]
    dump = json.dumps([r.details for r in rows])
    for secret in (*ALL_SECRETS, "another-pw-xyz", "fresh-pw"):
        assert secret not in dump
    update = rows[1].details
    assert update["secrets_replaced"] == ["password"] and update["secrets_cleared"] == []
    assert rows[2].details["secrets_cleared"] == ["password"]
    assert all(r.user_id is not None for r in rows)

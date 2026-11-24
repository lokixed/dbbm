"""Testing a source or destination without saving it: the dialogs' "Test connection" button."""

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.db import get_engine
from app.models import AuditLog, Destination
from tests.conftest import api_post
from tests.fake_rclone import FakeRclone, token_json
from tests.test_destinations_api import body as local_body
from tests.test_sources_api import PW, FakeDriver, create, firestore_body, mysql_body

DRIVE_BODY: dict[str, Any] = {
    "type": "gdrive",
    "name": "drive1",
    "config": {"remote_folder": "db-backups"},
    "secrets": {"token": token_json()},
}


@pytest.fixture
def fake_driver(monkeypatch: pytest.MonkeyPatch) -> type[FakeDriver]:
    FakeDriver.calls = []
    monkeypatch.setattr(
        "app.api.sources.get_driver", lambda _t, config, secrets: FakeDriver(config, secrets)
    )
    return FakeDriver


def audit() -> list[AuditLog]:
    with Session(get_engine()) as db:
        return list(db.exec(select(AuditLog).where(AuditLog.action.like("%.test"))))  # type: ignore[attr-defined]


# ---------- sources ----------


def test_a_new_source_can_be_tested_without_being_saved(
    authed: TestClient, fake_driver: type[FakeDriver]
) -> None:
    response = api_post(authed, "/api/sources/test", mysql_body())

    assert response.status_code == 200
    result = response.json()
    assert result["ok"] and result["databases"] == ["app", "other"]
    assert fake_driver.calls == [{"password": PW}]  # the form's own password was used
    assert PW not in response.text
    assert authed.get("/api/sources").json() == []  # nothing was saved
    (entry,) = audit()
    assert entry.details["saved"] is False and PW not in json.dumps(entry.details)


def test_editing_uses_the_stored_secret_unless_the_form_changes_it(
    authed: TestClient, fake_driver: type[FakeDriver]
) -> None:
    saved = create(authed, mysql_body())
    url = f"/api/sources/test?source_id={saved['id']}"

    assert api_post(authed, url, mysql_body(secrets={})).status_code == 200
    assert fake_driver.calls[-1] == {"password": PW}  # the form left it blank: stored value
    assert api_post(authed, url, mysql_body(secrets={"password": "typed-pw"})).status_code == 200
    assert fake_driver.calls[-1] == {"password": "typed-pw"}  # a typed value wins, unsaved

    after = authed.get(f"/api/sources/{saved['id']}").json()
    assert after["last_test"] is None  # an unsaved test is not recorded on the source


def test_a_source_test_validates_the_form_like_a_save(
    authed: TestClient, fake_driver: type[FakeDriver]
) -> None:
    no_key = {**firestore_body("f1"), "secrets": {}}  # the service account key is required
    assert api_post(authed, "/api/sources/test", no_key).status_code == 422
    bad_type = api_post(authed, "/api/sources/test", {**mysql_body(), "type": "nope"})
    assert bad_type.status_code == 422
    ghost = "00000000-0000-0000-0000-000000000000"
    unknown = api_post(authed, f"/api/sources/test?source_id={ghost}", mysql_body())
    assert unknown.status_code == 404
    saved = create(authed, mysql_body())
    other_type = {
        "type": "postgres",
        "name": "x",
        "config": {"host": "h", "username": "u", "databases": ["a"]},
        "secrets": {"password": "p"},
    }
    wrong = api_post(authed, f"/api/sources/test?source_id={saved['id']}", other_type)
    assert wrong.status_code == 422 and "cannot be changed" in wrong.text
    assert fake_driver.calls == []


def test_a_failing_source_test_is_reported_and_sanitized(authed: TestClient) -> None:
    body = mysql_body()
    body["config"].update(host="127.0.0.1", port=1, tls_mode="disabled")
    response = api_post(authed, "/api/sources/test", body)
    assert response.status_code == 200 and response.json()["ok"] is False
    assert PW not in response.text


# ---------- destinations ----------


def test_a_new_local_destination_can_be_tested_without_being_saved(authed: TestClient) -> None:
    response = api_post(authed, "/api/destinations/test", local_body(path="try/this"))

    result = response.json()
    assert response.status_code == 200 and result["ok"] is True and "Writable" in result["message"]
    assert authed.get("/api/destinations").json() == []
    (entry,) = audit()
    assert entry.details["saved"] is False


def test_an_escaping_local_path_is_refused(authed: TestClient) -> None:
    response = api_post(authed, "/api/destinations/test", local_body(path="../outside"))
    assert response.status_code == 422


def test_a_drive_destination_is_tested_with_the_form_token(
    authed: TestClient, fake_rclone: FakeRclone
) -> None:
    response = api_post(authed, "/api/destinations/test", DRIVE_BODY)
    assert response.status_code == 200 and response.json()["ok"] is True, response.text
    assert authed.get("/api/destinations").json() == []
    assert "rclone" not in response.text.lower() or "token" not in response.text.lower()
    missing = {**DRIVE_BODY, "secrets": {}}
    assert api_post(authed, "/api/destinations/test", missing).status_code == 422


def test_editing_a_drive_destination_tests_without_changing_it(
    authed: TestClient, fake_rclone: FakeRclone
) -> None:
    created = api_post(authed, "/api/destinations", DRIVE_BODY).json()
    with Session(get_engine()) as db:
        before = db.exec(select(Destination)).one()
        stored = (before.secrets_enc, before.auth_status, before.last_test_at)

    url = f"/api/destinations/test?destination_id={created['id']}"
    leave_token = {**DRIVE_BODY, "secrets": {}}  # blank in the form: the stored token is used
    assert api_post(authed, url, leave_token).json()["ok"] is True
    fake_rclone.fail("lsjson", 1, 'ERROR : couldn\'t fetch token: invalid_grant: "Token expired"')
    api_post(authed, url, DRIVE_BODY)  # a failing test must not flip the saved auth status

    with Session(get_engine()) as db:
        after = db.exec(select(Destination)).one()
    assert (after.secrets_enc, after.auth_status, after.last_test_at) == stored


@pytest.mark.parametrize("path", ["/api/sources/test", "/api/destinations/test"])
def test_unsaved_tests_need_a_login(client: TestClient, path: str) -> None:
    from tests.conftest import csrf_headers

    client.get("/api/auth/status")
    assert client.post(path, json={}, headers=csrf_headers(client)).status_code == 401

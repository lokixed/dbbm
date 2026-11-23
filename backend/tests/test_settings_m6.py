"""Settings API, milestone-6 redaction rules and migration 0005."""

import json
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from app.core.config import get_settings
from app.core.db import get_engine
from app.core.logging import SECRET_REGISTRY, redact
from app.main import create_app
from app.models import AuditLog
from app.services.heartbeat import register_heartbeat
from app.services.notifier import register_channel_secrets
from app.services.redact import sanitize
from app.services.scheduler import HOUSEKEEPING_ID
from tests.conftest import api_put, login, setup_admin
from tests.m6 import services_of
from tests.test_migrations import _alembic, _seed_m4_rows, _sql

DEFAULTS = {
    "public_base_url": "",
    "default_timezone": "UTC",
    "log_retention_days": 90,
    "run_history_days": 365,
    "misfire_grace_seconds": 3600,
    "housekeeping_time": "03:30",
    "low_disk_threshold_percent": 10,
    "overdue_grace_percent": 50,
}


def _settings(client: TestClient) -> dict[str, Any]:
    body: dict[str, Any] = client.get("/api/settings").json()
    body.pop("updated_at")
    return body


def test_defaults(authed: TestClient) -> None:
    assert _settings(authed) == DEFAULTS


def test_public_base_url_env_is_the_initial_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://backup.example.com/")
    get_settings.cache_clear()
    with TestClient(create_app()) as client:
        setup_admin(client)
        login(client)
        assert _settings(client)["public_base_url"] == "https://backup.example.com"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("default_timezone", "Mars/Base"),
        ("housekeeping_time", "25:00"),
        ("housekeeping_time", "3:30"),
        ("log_retention_days", 0),
        ("run_history_days", "365"),
        ("low_disk_threshold_percent", 95),
        ("misfire_grace_seconds", -1),
        ("public_base_url", "ftp://backup.example.com"),
        ("public_base_url", "https://user:pw@backup.example.com"),
    ],
)
def test_invalid_values_rejected(authed: TestClient, field: str, value: Any) -> None:
    response = api_put(authed, "/api/settings", {**DEFAULTS, field: value})
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"][-1] == field


def test_update_reschedules_housekeeping_and_is_audited(m6_client: TestClient) -> None:
    body = {**DEFAULTS, "housekeeping_time": "04:15", "default_timezone": "Asia/Kolkata"}
    saved = api_put(m6_client, "/api/settings", body)
    assert saved.status_code == 200, saved.text
    assert saved.json()["housekeeping_time"] == "04:15"
    scheduler = services_of(m6_client).scheduler
    job = scheduler._aps.get_job(HOUSEKEEPING_ID) if scheduler._aps else None
    assert job is not None
    local = job.next_run_time.astimezone(job.trigger.timezone)
    assert (local.hour, local.minute, str(job.trigger.timezone)) == (4, 15, "Asia/Kolkata")
    with Session(get_engine()) as db:
        entry = db.exec(select(AuditLog).where(col(AuditLog.action) == "settings.update")).one()
    assert entry.details["changed"] == {
        "default_timezone": "Asia/Kolkata",
        "housekeeping_time": "04:15",
    }


# ---------- redaction ----------


@pytest.fixture(autouse=True)
def _clean_registry() -> Any:
    SECRET_REGISTRY.clear()
    yield
    SECRET_REGISTRY.clear()


def test_slack_hook_urls_are_masked() -> None:
    text = "posting to https://hooks.slack.com/services/T0/B0/abcdefSECRET failed"
    assert "abcdefSECRET" not in redact(text)
    assert "hooks.slack.com/services/" in redact(text)


def test_registered_channel_and_heartbeat_urls_are_masked_after_the_host() -> None:
    register_channel_secrets(
        uuid.uuid4(),
        "webhook",
        {
            "url": "https://hooks.example.com/path/tok3n",
            "hmac_secret": "hmac-secret-value-123",
            "headers": json.dumps({"X-Api-Key": "header-value-999"}),
        },
    )
    register_heartbeat(uuid.uuid4(), "https://hc-ping.com/1b2c3d4e-0000-4000-8000-000000000001")
    text = (
        "url=https://hooks.example.com/path/tok3n hmac hmac-secret-value-123 header-value-999 "
        "ping https://hc-ping.com/1b2c3d4e-0000-4000-8000-000000000001/fail "
        "other https://hc-ping.com/aaaaaaaa-0000-4000-8000-000000000009"
    )
    out = redact(text)
    for secret in ("tok3n", "hmac-secret-value-123", "header-value-999", "1b2c3d4e", "aaaaaaaa"):
        assert secret not in out
    assert "https://hooks.example.com/[REDACTED]" in out
    assert "https://hc-ping.com/[REDACTED]" in out


def test_smtp_auth_lines_and_signatures_are_masked() -> None:
    text = (
        "send: 'AUTH PLAIN AHJvYm90AHNtdHAtUGE1NXdvcmQh\\r\\n' X-Signature-256: sha256=abcdef0123"
    )
    out = redact(text)
    assert "AHJvYm90" not in out and "abcdef0123" not in out


def test_sanitize_uses_the_registry() -> None:
    register_heartbeat(uuid.uuid4(), "https://ping.example.org/abc-secret")
    assert "abc-secret" not in sanitize("ping to ping.example.org/abc-secret timed out")


# ---------- migration 0005 ----------


def test_0005_upgrade_defaults_and_downgrade() -> None:
    _alembic("upgrade", "0004")
    _seed_m4_rows()
    _alembic("upgrade", "head")
    assert _sql(
        "SELECT timezone, notify_on, notify_on_recovery, heartbeat_secret_enc FROM jobs"
    ) == [("UTC", "failure", 1, None)]
    assert _sql("SELECT scheduled_for FROM runs") == [(None,)]
    with pytest.raises(IntegrityError):
        _sql("UPDATE jobs SET notify_on = 'sometimes'")
    _sql(
        "INSERT INTO notification_channels (id, name, type, config, enabled, created_at, "
        "updated_at) VALUES ('c1', 'hook', 'webhook', '{}', 1, '2026-01-01', '2026-01-01')"
    )
    _sql("INSERT INTO job_notification_channels VALUES ('j1', 'c1')")
    with pytest.raises(IntegrityError):
        _sql(
            "INSERT INTO notification_events (id, channel_id, event_type, status, attempts, "
            "created_at) VALUES ('e1', 'c1', 'bogus', 'sent', 1, '2026-01-01')"
        )
    _alembic("downgrade", "0004")
    assert _sql("SELECT id FROM jobs") == [("j1",)]
    assert _sql("SELECT id FROM artifacts") == [("a1",)]
    _alembic("upgrade", "head")
    assert _sql("SELECT count(*) FROM notification_channels") == [(0,)]

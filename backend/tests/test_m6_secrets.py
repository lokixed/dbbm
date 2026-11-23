"""No notification or heartbeat secret leaks into logs, API responses, audit, events or payloads."""

import json
import logging
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.db import get_engine
from app.models import AuditLog, NotificationEvent
from tests.conftest import api_post
from tests.m6 import Outbound, drain
from tests.runs_helpers import make_destination, make_job, make_source, run_now

SLACK = "https://hooks.slack.com/services/T111/B222/slackSecretPart"
HOOK = "https://hooks.example.com/hook/webhookSecretPart"
HMAC = "hmacSecretPart-0123456789"
HEADER = "headerSecretPart-42"
SMTP = "smtpSecretPart!"
PING = "https://hc-ping.com/9d8c7b6a-1111-4222-8333-444455556666"
SECRETS = ["slackSecretPart", "webhookSecretPart", HMAC, HEADER, SMTP, "9d8c7b6a"]


def _create_channels(client: TestClient) -> list[str]:
    bodies: list[dict[str, Any]] = [
        {"type": "slack", "name": "slack", "secrets": {"webhook_url": SLACK}},
        {
            "type": "webhook",
            "name": "hook",
            "secrets": {"url": HOOK, "hmac_secret": HMAC, "headers": {"X-Token": HEADER}},
        },
        {
            "type": "email",
            "name": "mail",
            "config": {
                "host": "smtp.example.com",
                "username": "robot",
                "from": "a@example.com",
                "to": ["b@example.com"],
            },
            "secrets": {"password": SMTP},
        },
    ]
    ids = []
    for body in bodies:
        response = api_post(client, "/api/notifications", body)
        assert response.status_code == 201, response.text
        ids.append(response.json()["id"])
    return ids


def _clean(text: str, where: str) -> None:
    for secret in SECRETS:
        assert secret not in text, f"{secret!r} leaked into {where}"


@pytest.mark.usefixtures("fake_bins", "fake_drivers")
def test_no_secret_anywhere(
    m6_client: TestClient,
    outbound: Outbound,
    fake_bins: Any,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    caplog.set_level(logging.DEBUG)
    channels = _create_channels(m6_client)
    job = make_job(
        m6_client,
        make_source(m6_client),
        [make_destination(m6_client)],
        notification_channel_ids=channels,
        notify_on="always",
        heartbeat_url=PING,
    )

    def failing(request: httpx.Request) -> httpx.Response:
        if request.headers["host"] == "hc-ping.com":
            raise httpx.ConnectError(f"refused {request.url}")
        return httpx.Response(500, text=f"echo {request.url} {request.headers}")

    outbound.responder = failing
    outbound.fail_login = True
    fake_bins.set_mode("mysqldump", "fail")
    fake_bins.set_text("stderr_text", f"error contacting {HOOK} with {HMAC}")
    run = run_now(m6_client, job)
    drain(m6_client)
    for channel in channels:
        api_post(m6_client, f"/api/notifications/{channel}/test")

    outbound.responder = lambda _r: httpx.Response(200)
    fake_bins.set_mode("mysqldump", "ok")
    run_now(m6_client, job)
    drain(m6_client)

    _clean(caplog.text, "captured log records")
    _clean(capsys.readouterr().out, "stdout JSON logs")
    for path in (
        "/api/notifications",
        *(f"/api/notifications/{c}" for c in channels),
        *(f"/api/notifications/{c}/events" for c in channels),
        "/api/jobs",
        f"/api/jobs/{job['id']}",
        f"/api/runs/{run['id']}",
        "/api/dashboard",
    ):
        _clean(m6_client.get(path).text, path)
    with Session(get_engine()) as db:
        _clean(json.dumps([a.details for a in db.exec(select(AuditLog)).all()]), "audit log")
        _clean(
            json.dumps([e.error for e in db.exec(select(NotificationEvent)).all()]),
            "notification events",
        )
    for request in outbound.requests:
        _clean(request.content.decode(), f"payload to {request.headers['host']}")
    for mail in outbound.mail:
        for message in mail.messages:
            _clean(message.as_string(), "email body")
    assert any(r.headers["host"] == "hooks.example.com" for r in outbound.requests)

"""Outbound safety, Slack/webhook/email delivery, notifier queue, retries and rate limit."""

import asyncio
import hashlib
import hmac
import json
import uuid
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, col, select

from app.core.db import get_engine
from app.models import NotificationChannel, NotificationEvent
from app.services.messages import Message, channel_test_message
from app.services.notifier import Notifier
from app.services.outbound import OutboundBlocked, check_address, expect_success, send_http
from app.services.secrets import store_secrets
from tests.conftest import api_delete, api_post, api_put
from tests.m6 import PUBLIC_IP, FakeClock, Outbound, db_ready, seed_job

SLACK_URL = "https://hooks.slack.com/services/T000/B000/XXXXsecretXXXX"
HOOK_URL = "https://hooks.example.com/dbbm/s3cr3t-path-token"
HMAC_SECRET = "hmac-secret-0123456789abcdef"
HEADER_VALUE = "Bearer header-secret-value-42"
SMTP_PASSWORD = "smtp-Pa55word!"


# ---------- address rules ----------


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "::1",
        "169.254.169.254",
        "fe80::1",
        "0.0.0.0",  # noqa: S104
        "224.0.0.1",
        "::ffff:127.0.0.1",
    ],
)
def test_never_allowed_addresses(address: str) -> None:
    for allow_private in (False, True):
        with pytest.raises(OutboundBlocked):
            check_address(address, allow_private)


@pytest.mark.parametrize("address", ["10.1.2.3", "192.168.1.10", "172.20.0.5", "fd00::5"])
def test_private_addresses_need_the_override(address: str) -> None:
    with pytest.raises(OutboundBlocked, match="ALLOW_PRIVATE_NOTIFICATION_TARGETS"):
        check_address(address, False)
    check_address(address, True)


def test_public_address_allowed() -> None:
    check_address(PUBLIC_IP, False)


def test_any_bad_dns_answer_blocks(outbound: Outbound) -> None:
    outbound.addresses["mixed.example.com"] = [PUBLIC_IP, "10.0.0.1"]
    with pytest.raises(OutboundBlocked):
        asyncio.run(outbound.policy().resolve("mixed.example.com", 443))
    outbound.addresses["gone.example.com"] = []
    with pytest.raises(OutboundBlocked, match="could not be resolved"):
        asyncio.run(outbound.policy().resolve("gone.example.com", 443))


def test_request_goes_to_the_checked_address(outbound: Outbound) -> None:
    answers = iter([[PUBLIC_IP], ["127.0.0.1"]])
    outbound.resolve = lambda host, port: next(answers)  # type: ignore[method-assign]
    response = asyncio.run(
        send_http(outbound.policy(), "POST", "https://hooks.example.com:8443/x?y=1", content=b"{}")
    )
    assert response.status_code == 200
    request = outbound.requests[0]
    assert request.url.host == PUBLIC_IP
    assert request.url.port == 8443
    assert request.url.path == "/x" and request.url.query == b"y=1"
    assert request.headers["host"] == "hooks.example.com:8443"
    assert request.extensions["sni_hostname"] == "hooks.example.com"
    assert next(answers) == ["127.0.0.1"]  # resolved once: a changed answer is never used


def test_rebinding_to_loopback_is_blocked_before_connecting(outbound: Outbound) -> None:
    outbound.addresses["rebind.example.com"] = ["127.0.0.1"]
    with pytest.raises(OutboundBlocked):
        asyncio.run(send_http(outbound.policy(), "GET", "https://rebind.example.com/"))
    assert outbound.requests == []


def test_redirects_are_not_followed(outbound: Outbound) -> None:
    outbound.responder = lambda _r: httpx.Response(
        302, headers={"location": "http://169.254.169.254/latest/meta-data"}
    )
    response = asyncio.run(send_http(outbound.policy(), "GET", "https://hooks.example.com/"))
    with pytest.raises(OutboundBlocked, match="redirect"):
        expect_success(response)
    assert len(outbound.requests) == 1


# ---------- notifier queue ----------


def _channel(
    kind: str = "webhook", name: str = "hook", enabled: bool = True, **secrets: str
) -> uuid.UUID:
    with Session(get_engine()) as db:
        row = NotificationChannel(
            name=name,
            type=kind,
            enabled=enabled,
            config={},
            secrets_enc=store_secrets(secrets or {"url": HOOK_URL}),
        )
        db.add(row)
        db.commit()
        return row.id


def _events() -> list[NotificationEvent]:
    with Session(get_engine()) as db:
        return list(
            db.exec(select(NotificationEvent).order_by(col(NotificationEvent.created_at))).all()
        )


def _message(event: str = "run_finished", title: str = "Backup job 'x': failed") -> Message:
    return Message(event=event, title=title, status="failed")


def _with_notifier(outbound: Outbound, clock: FakeClock, work: Any, **kw: Any) -> Notifier:
    notifier = Notifier(outbound.policy(), clock, **kw)

    async def go() -> None:
        await notifier.start()
        try:
            await work(notifier)
            await notifier.wait_idle()
        finally:
            await notifier.stop()

    asyncio.run(go())
    return notifier


def test_retry_with_backoff_then_success(outbound: Outbound, fake_clock: FakeClock) -> None:
    db_ready()
    _channel()
    statuses = iter([500, 503, 200])
    outbound.responder = lambda _r: httpx.Response(next(statuses))
    _with_notifier(outbound, fake_clock, lambda n: n.notify(_message()))
    [event] = _events()
    assert (event.status, event.attempts, event.error) == ("sent", 3, None)
    assert fake_clock.sleeps == [2.0, 4.0]


def test_all_attempts_fail_and_error_is_sanitized(
    outbound: Outbound, fake_clock: FakeClock
) -> None:
    db_ready()
    _channel()

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot connect to {request.url}")

    outbound.responder = refuse
    _with_notifier(outbound, fake_clock, lambda n: n.notify(_message()))
    [event] = _events()
    assert event.status == "failed" and event.attempts == 3
    assert event.error and "ConnectError" in event.error
    assert "s3cr3t" not in event.error and PUBLIC_IP not in event.error


def test_routing_to_job_channels_vs_all(outbound: Outbound, fake_clock: FakeClock) -> None:
    db_ready()
    from app.models import JobNotificationChannel

    selected = _channel(name="selected", url="https://a.example.com/1")
    _channel(name="other", url="https://b.example.com/2")
    _channel(name="off", enabled=False, url="https://c.example.com/3")
    job_id = seed_job()
    with Session(get_engine()) as db:
        db.add(JobNotificationChannel(job_id=job_id, notification_channel_id=selected))
        db.commit()

    async def work(notifier: Notifier) -> None:
        await notifier.notify_job(job_id, _message())
        await notifier.notify(_message("low_disk", "Low disk space: Data volume"))

    _with_notifier(outbound, fake_clock, work)
    hosts = sorted(outbound.hosts())
    assert hosts == ["a.example.com", "a.example.com", "b.example.com"]


def test_rate_limit_and_summary(outbound: Outbound, fake_clock: FakeClock) -> None:
    db_ready()
    _channel()

    async def work(notifier: Notifier) -> None:
        for _ in range(5):
            await notifier.notify(_message())
        await notifier.wait_idle()
        fake_clock.advance(minutes=59)
        await notifier.flush_summaries()
        fake_clock.advance(minutes=2)
        await notifier.flush_summaries()

    _with_notifier(outbound, fake_clock, work, limit=3)
    statuses = [e.status for e in _events()]
    assert statuses.count("rate_limited") == 2
    assert statuses.count("sent") == 4  # three messages and one summary
    summary = json.loads(outbound.requests[-1].content)
    assert summary["suppressed"] == 2 and summary["status"] == "rate_limited"


def test_test_send_is_recorded(outbound: Outbound, fake_clock: FakeClock) -> None:
    db_ready()
    channel = _channel()
    result: list[Any] = []

    async def work(notifier: Notifier) -> None:
        result.append(await notifier.send_test(channel, channel_test_message("hook")))

    _with_notifier(outbound, fake_clock, work)
    ok, _message_text, event_id = result[0]
    assert ok is True
    [event] = _events()
    assert event.id == event_id and event.event_type == "test" and event.status == "sent"


# ---------- channels through the API ----------


def _webhook(client: TestClient, **secrets: Any) -> dict[str, Any]:
    body = {
        "type": "webhook",
        "name": "echo",
        "secrets": {
            "url": HOOK_URL,
            "hmac_secret": HMAC_SECRET,
            "headers": {"Authorization": HEADER_VALUE},
            **secrets,
        },
    }
    response = api_post(client, "/api/notifications", body)
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


def test_webhook_test_send_is_signed(m6_client: TestClient, outbound: Outbound) -> None:
    channel = _webhook(m6_client)
    assert channel["secrets_set"] == {"url": True, "headers": True, "hmac_secret": True}
    assert channel["header_names"] == ["Authorization"]
    for secret in (HOOK_URL, HMAC_SECRET, HEADER_VALUE):
        assert secret not in json.dumps(channel)
    result = api_post(m6_client, f"/api/notifications/{channel['id']}/test").json()
    assert result["ok"] is True, result
    request = outbound.requests[0]
    expected = (
        "sha256=" + hmac.new(HMAC_SECRET.encode(), request.content, hashlib.sha256).hexdigest()
    )
    assert request.headers["x-signature-256"] == expected
    assert request.headers["authorization"] == HEADER_VALUE
    payload = json.loads(request.content)
    assert payload["event"] == "test" and payload["timestamp"].endswith("Z")
    channel_after = m6_client.get(f"/api/notifications/{channel['id']}").json()
    assert channel_after["last_test"]["ok"] is True
    events = m6_client.get(f"/api/notifications/{channel['id']}/events").json()
    assert events["total"] == 1 and events["items"][0]["status"] == "sent"


def test_slack_payload(m6_client: TestClient, outbound: Outbound) -> None:
    body = {"type": "slack", "name": "slack", "secrets": {"webhook_url": SLACK_URL}}
    channel = api_post(m6_client, "/api/notifications", body).json()
    assert api_post(m6_client, f"/api/notifications/{channel['id']}/test").json()["ok"] is True
    request = outbound.requests[0]
    assert request.headers["host"] == "hooks.slack.com"
    assert request.url.path == "/services/T000/B000/XXXXsecretXXXX"
    payload = json.loads(request.content)
    assert payload["text"] == "DBBM test notification"
    assert payload["blocks"][0]["type"] == "section"


def test_slack_requires_https(authed: TestClient) -> None:
    body = {"type": "slack", "name": "s", "secrets": {"webhook_url": "http://hooks.slack.com/x"}}
    response = api_post(authed, "/api/notifications", body)
    assert response.status_code == 422
    assert "https://" in response.text


def _email(client: TestClient, security: str = "starttls") -> dict[str, Any]:
    body = {
        "type": "email",
        "name": "mail",
        "config": {
            "host": "smtp.example.com",
            "port": 587,
            "security": security,
            "username": "robot",
            "from": "dbbm@example.com",
            "to": ["ops@example.com", "oncall@example.com"],
        },
        "secrets": {"password": SMTP_PASSWORD},
    }
    response = api_post(client, "/api/notifications", body)
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


def test_email_via_stubbed_smtp(m6_client: TestClient, outbound: Outbound) -> None:
    channel = _email(m6_client)
    assert channel["config"]["from"] == "dbbm@example.com"
    assert channel["warnings"] == []
    assert api_post(m6_client, f"/api/notifications/{channel['id']}/test").json()["ok"] is True
    [mail] = outbound.mail
    assert (mail.address, mail.host, mail.port) == (PUBLIC_IP, "smtp.example.com", 587)
    assert mail.calls == ["ehlo", "starttls", "ehlo", "login", "send", "quit"]
    assert mail.login == ("robot", SMTP_PASSWORD)
    message = mail.messages[0]
    assert message["To"] == "ops@example.com, oncall@example.com"
    assert {p.get_content_type() for p in message.iter_parts()} == {"text/plain", "text/html"}


def test_email_failure_is_sanitized(m6_client: TestClient, outbound: Outbound) -> None:
    channel = _email(m6_client)
    outbound.fail_login = True
    result = api_post(m6_client, f"/api/notifications/{channel['id']}/test").json()
    assert result["ok"] is False
    assert result["message"] == "SMTP authentication failed (code 535)."


def test_email_security_none_warns(authed: TestClient) -> None:
    channel = _email(authed, security="none")
    assert "unencrypted" in channel["warnings"][0]


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data",
        "http://127.0.0.1:8080/x",
        "http://localhost/x",
        "http://10.0.0.5/x",
    ],
)
def test_webhook_to_internal_addresses_rejected_at_save(authed: TestClient, url: str) -> None:
    body = {"type": "webhook", "name": "bad", "secrets": {"url": url}}
    response = api_post(authed, "/api/notifications", body)
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "secrets", "url"]


def test_webhook_resolving_to_private_fails_at_send(
    m6_client: TestClient, outbound: Outbound
) -> None:
    outbound.addresses["internal.example.com"] = ["10.0.0.7"]
    channel = _webhook(m6_client, url="https://internal.example.com/hook")
    result = api_post(m6_client, f"/api/notifications/{channel['id']}/test").json()
    assert result["ok"] is False
    assert "Private network targets" in result["message"]
    assert outbound.requests == []


def test_private_targets_allowed_with_override(
    outbound: Outbound, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.config import get_settings
    from app.main import create_app
    from tests.conftest import login, setup_admin

    monkeypatch.setenv("ALLOW_PRIVATE_NOTIFICATION_TARGETS", "true")
    get_settings.cache_clear()
    outbound.addresses["echo"] = ["172.18.0.4"]
    with TestClient(create_app(outbound_policy=outbound.policy(allow_private=True))) as client:
        setup_admin(client)
        login(client)
        body = {"type": "webhook", "name": "echo", "secrets": {"url": "http://echo:8080/hook"}}
        channel = api_post(client, "/api/notifications", body).json()
        assert api_post(client, f"/api/notifications/{channel['id']}/test").json()["ok"] is True
        loopback = {"type": "webhook", "name": "lo", "secrets": {"url": "http://127.0.0.1/x"}}
        assert api_post(client, "/api/notifications", loopback).status_code == 422


def test_secret_update_rules(authed: TestClient) -> None:
    channel = _webhook(authed)
    base = {"type": "webhook", "name": "echo", "enabled": True}
    kept = api_put(authed, f"/api/notifications/{channel['id']}", {**base, "secrets": {}}).json()
    assert kept["secrets_set"] == {"url": True, "headers": True, "hmac_secret": True}
    cleared = api_put(
        authed,
        f"/api/notifications/{channel['id']}",
        {**base, "secrets": {"hmac_secret": None, "headers": None}},
    ).json()
    assert cleared["secrets_set"] == {"url": True, "headers": False, "hmac_secret": False}
    no_url = api_put(
        authed, f"/api/notifications/{channel['id']}", {**base, "secrets": {"url": None}}
    )
    assert no_url.status_code == 422
    retyped = api_put(
        authed,
        f"/api/notifications/{channel['id']}",
        {"type": "slack", "name": "echo", "secrets": {}},
    )
    assert retyped.status_code == 422


def test_reserved_headers_rejected(authed: TestClient) -> None:
    body = {"type": "webhook", "name": "h", "secrets": {"url": HOOK_URL, "headers": {"Host": "x"}}}
    assert api_post(authed, "/api/notifications", body).status_code == 422


def test_delete_channel_removes_it_from_jobs(authed: TestClient) -> None:
    from tests.runs_helpers import make_destination, make_job, make_source

    channel = _webhook(authed)
    job = make_job(
        authed,
        make_source(authed),
        [make_destination(authed)],
        notification_channel_ids=[channel["id"]],
    )
    assert job["notification_channels"] == [{"id": channel["id"], "name": "echo"}]
    listed = authed.get(f"/api/notifications/{channel['id']}").json()
    assert listed["jobs"] == [{"id": job["id"], "name": job["name"]}]
    assert api_delete(authed, f"/api/notifications/{channel['id']}").status_code == 204
    assert authed.get(f"/api/jobs/{job['id']}").json()["notification_channels"] == []


def test_events_pagination(m6_client: TestClient) -> None:
    channel = _webhook(m6_client)
    for _ in range(3):
        api_post(m6_client, f"/api/notifications/{channel['id']}/test")
    page = m6_client.get(f"/api/notifications/{channel['id']}/events", params={"limit": 2}).json()
    assert page["total"] == 3 and len(page["items"]) == 2
    rest = m6_client.get(
        f"/api/notifications/{channel['id']}/events", params={"limit": 2, "offset": 2}
    ).json()
    assert len(rest["items"]) == 1


def test_notification_routes_need_auth_and_csrf(authed: TestClient) -> None:
    response = authed.post(
        "/api/notifications",
        json={"type": "slack", "name": "x", "secrets": {"webhook_url": SLACK_URL}},
    )
    assert response.status_code == 403


def test_unsaved_test_sends_without_saving(m6_client: TestClient, outbound: Outbound) -> None:
    body = {"type": "webhook", "name": "draft", "secrets": {"url": HOOK_URL}}
    response = api_post(m6_client, "/api/notifications/test", body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["ok"] is True and result["event"] is None
    assert HOOK_URL not in response.text
    assert len(outbound.requests) == 1
    assert m6_client.get("/api/notifications").json() == []
    with Session(get_engine()) as db:
        assert db.exec(select(NotificationEvent)).all() == []


def test_unsaved_test_of_an_edit_uses_the_stored_secret(
    m6_client: TestClient, outbound: Outbound
) -> None:
    channel = _webhook(m6_client)
    body = {"type": "webhook", "name": "echo", "secrets": {}}
    result = api_post(m6_client, f"/api/notifications/test?channel_id={channel['id']}", body).json()
    assert result["ok"] is True, result
    assert outbound.requests[0].headers["authorization"] == HEADER_VALUE
    after = m6_client.get(f"/api/notifications/{channel['id']}").json()
    assert after["last_test"] is None

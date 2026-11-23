"""Delivery to Slack, email and generic webhooks. Senders raise on failure; callers sanitize."""

import asyncio
import hashlib
import hmac
import json
import smtplib
from collections.abc import Mapping
from datetime import datetime
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import Any

from app.schemas.notification import EmailConfig, parse_headers
from app.services.messages import Message
from app.services.outbound import (
    DEFAULT_TIMEOUT,
    OutboundBlocked,
    OutboundPolicy,
    PinnedSMTP,
    PinnedSMTPSSL,
    expect_success,
    send_http,
    tls_context,
)

SIGNATURE_HEADER = "X-Signature-256"
USER_AGENT = "DBBM-Notifier/1"


def sign(secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def slack_payload(message: Message) -> dict[str, Any]:
    text = message.title + (f" <{message.url}|Open run>" if message.url else "")
    blocks: list[dict[str, Any]] = [{"type": "section", "text": {"type": "mrkdwn", "text": text}}]
    for line in message.lines:
        blocks.append({"type": "section", "text": {"type": "plain_text", "text": line}})
    if message.fields:
        blocks.append(
            {
                "type": "section",
                # Slack allows at most 10 fields per section.
                "fields": [
                    {"type": "mrkdwn", "text": f"*{name}*\n{value}"}
                    for name, value in message.fields[:10]
                ],
            }
        )
    return {"text": message.title, "blocks": blocks}


async def send_slack(
    policy: OutboundPolicy, secrets: Mapping[str, str], message: Message, now: datetime
) -> None:
    body = json.dumps(slack_payload(message)).encode("utf-8")
    response = await send_http(
        policy,
        "POST",
        secrets["webhook_url"],
        content=body,
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
    )
    expect_success(response)


def webhook_request(
    secrets: Mapping[str, str], message: Message, now: datetime
) -> tuple[bytes, dict[str, str]]:
    body = json.dumps(message.payload(now), sort_keys=True).encode("utf-8")
    headers = {
        **parse_headers(secrets.get("headers")),
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
    }
    if secrets.get("hmac_secret"):
        headers[SIGNATURE_HEADER] = sign(secrets["hmac_secret"], body)
    return body, headers


async def send_webhook(
    policy: OutboundPolicy, secrets: Mapping[str, str], message: Message, now: datetime
) -> None:
    body, headers = webhook_request(secrets, message, now)
    response = await send_http(policy, "POST", secrets["url"], content=body, headers=headers)
    expect_success(response)


def email_message(config: EmailConfig, message: Message, now: datetime) -> EmailMessage:
    mail = EmailMessage()
    mail["Subject"] = f"[DBBM] {message.title}"
    mail["From"] = config.from_address
    mail["To"] = ", ".join(config.to)
    mail["Date"] = formatdate(now.timestamp(), usegmt=True)
    mail["Message-ID"] = make_msgid(domain=config.from_address.rsplit("@", 1)[-1])
    mail.set_content(message.text)
    mail.add_alternative(message.html(), subtype="html")
    return mail


def _open_smtp(security: str, address: str, host: str, port: int, timeout: float) -> smtplib.SMTP:
    if security == "ssl":
        return PinnedSMTPSSL(address, host=host, port=port, timeout=timeout, context=tls_context())
    return PinnedSMTP(address, host=host, port=port, timeout=timeout)


def _deliver_email(
    policy: OutboundPolicy,
    config: EmailConfig,
    password: str | None,
    address: str,
    mail: EmailMessage,
) -> None:
    factory = policy.smtp_factory or _open_smtp
    smtp = factory(config.security, address, config.host, config.port, DEFAULT_TIMEOUT)
    try:
        smtp.ehlo()
        if config.security == "starttls":
            if not smtp.has_extn("starttls"):
                raise OutboundBlocked("The mail server does not offer STARTTLS.")
            smtp.starttls(context=tls_context())
            smtp.ehlo()
        if config.username and password:
            smtp.login(config.username, password)
        smtp.send_message(mail)
    finally:
        try:
            smtp.quit()
        except (smtplib.SMTPException, OSError):
            smtp.close()


async def send_email(
    policy: OutboundPolicy,
    config: EmailConfig,
    secrets: Mapping[str, str],
    message: Message,
    now: datetime,
) -> None:
    address = await policy.resolve(config.host, config.port)
    mail = email_message(config, message, now)
    await asyncio.to_thread(_deliver_email, policy, config, secrets.get("password"), address, mail)

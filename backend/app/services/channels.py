"""Delivery to Slack, email and generic webhooks. Senders raise on failure; callers sanitize."""

import asyncio
import hashlib
import hmac
import json
import smtplib
from collections.abc import Mapping
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import Any

from app.models.notification import EventType
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
from app.storage.local import format_bytes

SIGNATURE_HEADER = "X-Signature-256"
USER_AGENT = "DBBM-Notifier/1"


def sign(secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


# Run status -> (emoji, side-bar colour) for Slack run messages.
_SLACK_STATUS = {
    "success": (":white_check_mark:", "#2eb67d"),
    "partial": (":warning:", "#ecb22e"),
    "failed": (":x:", "#e01e5a"),
    "interrupted": (":warning:", "#ecb22e"),
    "cancelled": (":no_entry_sign:", "#9e9e9e"),
}
_SLACK_DEST = {"ok": ":white_check_mark:", "partial": ":warning:", "failed": ":x:"}
NO_LINK_TIP = (
    "Set *Public base URL* in DBBM under Settings &gt; General to link these messages to the run."
)


def _slack_escape(text: str) -> str:
    """Slack mrkdwn treats &, < and > as markup."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _duration(seconds: int | None) -> str:
    if seconds is None:
        return "-"
    if seconds < 1:
        return "<1s"
    if seconds < 60:
        return f"{seconds}s"
    minutes, rest = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {rest}s"
    return f"{minutes // 60}h {minutes % 60}m"


def _slack_time(iso: str | None) -> str:
    """A timestamp Slack shows in each reader's own time zone, with the UTC text as fallback."""
    if not iso:
        return "-"
    try:
        moment = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        return iso
    return f"<!date^{int(moment.timestamp())}^{{date_short_pretty}} at {{time}}|{iso}>"


def _slack_run_blocks(message: Message, run: dict[str, Any]) -> list[dict[str, Any]]:
    """One glanceable card: status and job, one summary line, destinations, error, buttons."""
    emoji, _ = _SLACK_STATUS.get(message.status, (":information_source:", ""))
    artifacts = run.get("artifacts") or {}
    count = int(artifacts.get("count") or 0)
    size = format_bytes(int(artifacts.get("total_bytes") or 0))
    summary = [
        _slack_escape(str(run.get("trigger") or "-").capitalize()),
        # Escaped: "<1s" would otherwise start a Slack link or mention.
        f"took {_slack_escape(_duration(run.get('duration_seconds')))}",
        f"{count} file{'' if count == 1 else 's'}, {size}",
        f"started {_slack_time(run.get('started_at'))}",
    ]
    blocks: list[dict[str, Any]] = [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"{emoji} *{_slack_escape(message.title)}*"},
        },
        {"type": "context", "elements": [{"type": "mrkdwn", "text": " · ".join(summary)}]},
    ]
    destinations = run.get("destinations") or []
    shown: list[str] = []
    if destinations:
        parts = []
        for dest in destinations:
            mark = _SLACK_DEST.get(dest.get("status"), ":grey_question:")
            line = f"{mark} {_slack_escape(str(dest.get('name')))}"
            if dest.get("status") != "ok" and dest.get("error"):
                shown.append(str(dest["error"]))
                line += f": {_slack_escape(str(dest['error']))}"
            parts.append(line)
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(parts)}})
    error = str(run.get("error") or "")
    # Skip the error block when a destination line above already says the same thing.
    if error and not any(text in error for text in shown):
        code = _slack_escape(error.replace("```", "'''"))
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"```{code}```"}})
    if message.links:
        blocks.append(
            {
                "type": "actions",
                "elements": [
                    {"type": "button", "text": {"type": "plain_text", "text": label}, "url": url}
                    for label, url in message.links
                ],
            }
        )
    else:
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": NO_LINK_TIP}]})
    return blocks


def slack_payload(message: Message) -> dict[str, Any]:
    run = message.data.get("run")
    if message.event in (EventType.RUN_FINISHED, EventType.RECOVERY) and isinstance(run, dict):
        _, colour = _SLACK_STATUS.get(message.status, ("", "#9e9e9e"))
        title, *details = _slack_run_blocks(message, run)
        # The title is the message's own block, so Slack shows it once; without top-level blocks
        # Slack prints "text" above the card as well. "text" stays as the push-notification
        # preview. The attachment holds the details and draws the coloured side bar.
        return {
            "text": message.title,
            "blocks": [title],
            "attachments": [{"color": colour, "blocks": details}],
        }
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

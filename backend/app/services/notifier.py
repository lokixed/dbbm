"""Background delivery of notifications: queue, per-channel rate limit, retries, event records."""

import asyncio
import logging
import smtplib
import uuid
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import update
from sqlmodel import Session, col, select

from app.core.clock import SYSTEM_CLOCK, Clock
from app.core.db import get_engine
from app.core.logging import SECRET_REGISTRY
from app.models import JobNotificationChannel, NotificationChannel, NotificationEvent
from app.models.notification import ChannelType, EventStatus, EventType
from app.schemas.notification import EmailConfig, parse_headers
from app.services.channels import send_email, send_slack, send_webhook
from app.services.messages import Message, rate_limit_summary, summarize_error
from app.services.outbound import OutboundBlocked, OutboundPolicy
from app.services.redact import describe_error
from app.services.secrets import SecretsUnavailable, load_secrets

logger = logging.getLogger(__name__)

RATE_LIMIT = 20
RATE_WINDOW = timedelta(hours=1)
ATTEMPTS = 3
BACKOFF_SECONDS = (2.0, 4.0)
DELIVERY_TIMEOUT = 60.0
SUMMARY_CHECK_SECONDS = 60.0
CONCURRENCY = 4
STOP_TIMEOUT = 5.0
STALE_MESSAGE = "Not delivered: the service restarted before delivery finished."


class ChannelUnavailable(Exception):
    """The channel was deleted or its secrets cannot be decrypted. The message is safe."""


def register_channel_secrets(
    channel_id: uuid.UUID, channel_type: str, secrets: dict[str, str]
) -> None:
    """Teach the log redaction filter this channel's secret values."""
    urls = [secrets[k] for k in ("webhook_url", "url") if secrets.get(k)]
    values = [secrets[k] for k in ("password", "hmac_secret") if secrets.get(k)]
    values += list(parse_headers(secrets.get("headers")).values())
    SECRET_REGISTRY.set(f"channel:{channel_id}", values=values, urls=urls)


def forget_channel_secrets(channel_id: uuid.UUID) -> None:
    SECRET_REGISTRY.remove(f"channel:{channel_id}")


def register_all_channels() -> None:
    with Session(get_engine()) as db:
        for row in db.exec(select(NotificationChannel)).all():
            with suppress(SecretsUnavailable):
                register_channel_secrets(row.id, row.type, load_secrets(row.secrets_enc))


@dataclass
class _Window:
    started: datetime
    count: int = 0
    suppressed: int = 0
    last_event: str = EventType.RUN_FINISHED.value


@dataclass(frozen=True)
class _Delivery:
    event_id: uuid.UUID
    channel_id: uuid.UUID
    message: Message


@dataclass(frozen=True)
class _Target:
    id: uuid.UUID
    name: str
    type: str
    config: dict[str, Any]
    secrets: dict[str, str]


def describe_delivery_error(exc: BaseException, secrets: Sequence[str] = ()) -> str:
    if isinstance(exc, OutboundBlocked | ChannelUnavailable):
        text = str(exc)
    elif isinstance(exc, TimeoutError | httpx.TimeoutException):
        text = "Timed out."
    elif isinstance(exc, smtplib.SMTPAuthenticationError):
        text = f"SMTP authentication failed (code {exc.smtp_code})."
    elif isinstance(exc, smtplib.SMTPResponseException):
        text = f"{type(exc).__name__} (code {exc.smtp_code})."
    elif isinstance(exc, smtplib.SMTPException):
        text = type(exc).__name__
    elif isinstance(exc, httpx.HTTPError):
        text = f"{type(exc).__name__}: connection failed."
    elif isinstance(exc, OSError):
        text = f"{type(exc).__name__}: {exc.strerror or 'network error'}"
    else:
        text = describe_error(exc, secrets)
    return summarize_error(text, secrets) or ""


class Notifier:
    def __init__(
        self,
        policy: OutboundPolicy,
        clock: Clock = SYSTEM_CLOCK,
        *,
        limit: int = RATE_LIMIT,
        window: timedelta = RATE_WINDOW,
        attempts: int = ATTEMPTS,
        backoff: Sequence[float] = BACKOFF_SECONDS,
    ) -> None:
        self.policy = policy
        self._clock = clock
        self._limit = limit
        self._window = window
        self._attempts = attempts
        self._backoff = tuple(backoff)
        self._queue: asyncio.Queue[_Delivery] = asyncio.Queue()
        self._windows: dict[uuid.UUID, _Window] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._background: list[asyncio.Task[None]] = []
        self._semaphore = asyncio.Semaphore(CONCURRENCY)
        self._loop: asyncio.AbstractEventLoop | None = None

    # ---------- lifecycle ----------

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        await asyncio.to_thread(self._mark_stale)
        await asyncio.to_thread(register_all_channels)
        self._background = [
            asyncio.create_task(self._worker()),
            asyncio.create_task(self._summary_loop()),
        ]

    async def stop(self) -> None:
        with suppress(TimeoutError):
            async with asyncio.timeout(STOP_TIMEOUT):
                await self.wait_idle()
        self._loop = None
        for task in [*self._background, *self._tasks]:
            task.cancel()
        await asyncio.gather(*self._background, *self._tasks, return_exceptions=True)
        self._background = []

    async def wait_idle(self) -> None:
        """Wait until every queued delivery has finished (tests and shutdown)."""
        while not self._queue.empty() or self._tasks:
            await self._queue.join()
            if self._tasks:
                await asyncio.gather(*list(self._tasks), return_exceptions=True)

    # ---------- public API ----------

    async def notify(self, message: Message, channel_ids: Sequence[uuid.UUID] | None = None) -> int:
        """Queue `message` for the given enabled channels (all enabled channels when None)."""
        targets = await asyncio.to_thread(self._enabled_channels, channel_ids)
        for channel_id, name in targets:
            await self._enqueue(channel_id, name, message)
        return len(targets)

    async def notify_job(self, job_id: uuid.UUID, message: Message) -> int:
        channel_ids = await asyncio.to_thread(self._job_channels, job_id)
        if not channel_ids:
            return 0
        return await self.notify(message, channel_ids)

    def notify_threadsafe(self, message: Message) -> None:
        """System event raised from a worker thread (e.g. a Drive token marked expired)."""
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        asyncio.run_coroutine_threadsafe(self.notify(message), loop)

    async def send_test(
        self, channel_id: uuid.UUID, message: Message
    ) -> tuple[bool, str, uuid.UUID]:
        """One immediate attempt, not rate limited; recorded like any other delivery."""
        event_id = await asyncio.to_thread(self._record, channel_id, message, EventStatus.PENDING)
        try:
            async with asyncio.timeout(DELIVERY_TIMEOUT):
                await self._send(channel_id, message)
        except Exception as exc:
            error = describe_delivery_error(exc)
            await asyncio.to_thread(self._update, event_id, EventStatus.FAILED, error, 1)
            return False, error, event_id
        await asyncio.to_thread(self._update, event_id, EventStatus.SENT, None, 1)
        return True, "Test message delivered.", event_id

    async def flush_summaries(self) -> None:
        """Send the suppressed-message summary of every channel whose window has ended."""
        now = self._clock.now()
        for channel_id, window in list(self._windows.items()):
            if now - window.started >= self._window:
                await self._rotate(channel_id, None, now)

    # ---------- queueing ----------

    async def _rotate(self, channel_id: uuid.UUID, name: str | None, now: datetime) -> _Window:
        window = self._windows.get(channel_id)
        if window is not None and now - window.started < self._window:
            return window
        fresh = _Window(started=now)
        self._windows[channel_id] = fresh
        if window is not None and window.suppressed:
            if name is None:
                name = await asyncio.to_thread(self._channel_name, channel_id)
            if name is not None:
                summary = rate_limit_summary(window.suppressed, name, window.last_event)
                fresh.count += 1
                await self._queue_delivery(channel_id, summary)
        return fresh

    async def _enqueue(self, channel_id: uuid.UUID, name: str, message: Message) -> None:
        window = await self._rotate(channel_id, name, self._clock.now())
        if window.count >= self._limit:
            window.suppressed += 1
            window.last_event = message.event
            await asyncio.to_thread(
                self._record,
                channel_id,
                message,
                EventStatus.RATE_LIMITED,
                f"More than {self._limit} messages in an hour; summarized later.",
            )
            return
        window.count += 1
        await self._queue_delivery(channel_id, message)

    async def _queue_delivery(self, channel_id: uuid.UUID, message: Message) -> None:
        event_id = await asyncio.to_thread(self._record, channel_id, message, EventStatus.PENDING)
        await self._queue.put(_Delivery(event_id, channel_id, message))

    async def _worker(self) -> None:
        while True:
            delivery = await self._queue.get()
            await self._semaphore.acquire()
            task = asyncio.create_task(self._deliver(delivery))
            self._tasks.add(task)
            task.add_done_callback(self._delivery_done)

    def _delivery_done(self, task: asyncio.Task[None]) -> None:
        self._tasks.discard(task)
        self._semaphore.release()
        self._queue.task_done()

    async def _summary_loop(self) -> None:
        while True:
            await asyncio.sleep(SUMMARY_CHECK_SECONDS)
            try:
                await self.flush_summaries()
            except Exception as exc:
                logger.warning("Notification summary check failed: %s", type(exc).__name__)

    async def _deliver(self, delivery: _Delivery) -> None:
        for attempt in range(1, self._attempts + 1):
            last = attempt == self._attempts
            try:
                async with asyncio.timeout(DELIVERY_TIMEOUT):
                    await self._send(delivery.channel_id, delivery.message)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                error = describe_delivery_error(exc)
                status = EventStatus.FAILED if last else EventStatus.PENDING
                with suppress(Exception):
                    await asyncio.to_thread(self._update, delivery.event_id, status, error, attempt)
                if isinstance(exc, ChannelUnavailable) or last:
                    logger.warning(
                        "Notification %s via channel %s failed after %d attempt(s): %s",
                        delivery.message.event, delivery.channel_id, attempt, error,
                    )  # fmt: skip
                    return
                await self._clock.sleep(self._backoff[min(attempt - 1, len(self._backoff) - 1)])
                continue
            with suppress(Exception):
                await asyncio.to_thread(
                    self._update, delivery.event_id, EventStatus.SENT, None, attempt
                )
            return

    async def _send(self, channel_id: uuid.UUID, message: Message) -> None:
        target = await asyncio.to_thread(self._load_target, channel_id)
        now = self._clock.now()
        if target.type == ChannelType.SLACK:
            await send_slack(self.policy, target.secrets, message, now)
        elif target.type == ChannelType.WEBHOOK:
            await send_webhook(self.policy, target.secrets, message, now)
        elif target.type == ChannelType.EMAIL:
            config = EmailConfig.model_validate(target.config)
            await send_email(self.policy, config, target.secrets, message, now)
        else:
            raise ChannelUnavailable(f"Unsupported channel type: {target.type}")

    # ---------- database (blocking; always called through to_thread) ----------

    @staticmethod
    def _enabled_channels(channel_ids: Sequence[uuid.UUID] | None) -> list[tuple[uuid.UUID, str]]:
        with Session(get_engine()) as db:
            query = select(NotificationChannel).where(col(NotificationChannel.enabled))
            if channel_ids is not None:
                query = query.where(col(NotificationChannel.id).in_(list(channel_ids)))
            rows = db.exec(query.order_by(col(NotificationChannel.name))).all()
            return [(r.id, r.name) for r in rows]

    @staticmethod
    def _job_channels(job_id: uuid.UUID) -> list[uuid.UUID]:
        with Session(get_engine()) as db:
            return list(
                db.exec(
                    select(JobNotificationChannel.notification_channel_id).where(
                        col(JobNotificationChannel.job_id) == job_id
                    )
                ).all()
            )

    @staticmethod
    def _channel_name(channel_id: uuid.UUID) -> str | None:
        with Session(get_engine()) as db:
            row = db.get(NotificationChannel, channel_id)
            return row.name if row else None

    @staticmethod
    def _load_target(channel_id: uuid.UUID) -> _Target:
        with Session(get_engine()) as db:
            row = db.get(NotificationChannel, channel_id)
            if row is None:
                raise ChannelUnavailable("The channel no longer exists.")
            try:
                secrets = load_secrets(row.secrets_enc)
            except SecretsUnavailable:
                raise ChannelUnavailable(
                    "The channel's secrets cannot be decrypted; re-enter them."
                ) from None
            return _Target(row.id, row.name, row.type, dict(row.config), secrets)

    def _record(
        self,
        channel_id: uuid.UUID,
        message: Message,
        status: EventStatus,
        error: str | None = None,
    ) -> uuid.UUID:
        with Session(get_engine()) as db:
            event = NotificationEvent(
                channel_id=channel_id,
                run_id=uuid.UUID(message.run_id) if message.run_id else None,
                job_id=uuid.UUID(message.job_id) if message.job_id else None,
                event_type=message.event,
                status=status.value,
                error=error,
                attempts=0,
                created_at=self._clock.now(),
            )
            db.add(event)
            db.commit()
            return event.id

    @staticmethod
    def _update(event_id: uuid.UUID, status: EventStatus, error: str | None, attempts: int) -> None:
        with Session(get_engine()) as db:
            event = db.get(NotificationEvent, event_id)
            if event is None:
                return
            event.status = status.value
            event.error = error
            event.attempts = attempts
            db.add(event)
            db.commit()

    @staticmethod
    def _mark_stale() -> None:
        with Session(get_engine()) as db:
            db.exec(
                update(NotificationEvent)
                .where(col(NotificationEvent.status) == EventStatus.PENDING.value)
                .values(status=EventStatus.FAILED.value, error=STALE_MESSAGE)
            )
            db.commit()

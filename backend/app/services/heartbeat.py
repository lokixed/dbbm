"""Healthchecks-style heartbeat pings: <url>/start, <url> on success, <url>/fail on failure.

Best effort: pings run in background tasks and never fail or delay a run. The URL is a secret.
"""

import asyncio
import logging
import uuid
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from sqlmodel import Session, col, select

from app.core.clock import SYSTEM_CLOCK, Clock
from app.core.db import get_engine
from app.core.logging import SECRET_REGISTRY
from app.models import Job
from app.services.notifier import describe_delivery_error
from app.services.outbound import OutboundPolicy, expect_success, send_http
from app.services.secrets import SecretsUnavailable, load_secrets

logger = logging.getLogger(__name__)

PingKind = Literal["start", "success", "fail"]
PING_TIMEOUT = 5.0
PING_RETRIES = 2
RETRY_DELAY = 1.0


def ping_url(url: str, kind: PingKind) -> str:
    if kind == "success":
        return url
    parts = urlsplit(url)
    path = parts.path.rstrip("/") + f"/{kind}"
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, ""))


def register_heartbeat(job_id: uuid.UUID, url: str | None) -> None:
    owner = f"heartbeat:{job_id}"
    if url:
        SECRET_REGISTRY.set(owner, urls=[url, ping_url(url, "start"), ping_url(url, "fail")])
    else:
        SECRET_REGISTRY.remove(owner)


def heartbeat_url(blob: bytes | None) -> str | None:
    try:
        return load_secrets(blob).get("url") or None
    except SecretsUnavailable:
        return None


def register_all_heartbeats() -> None:
    with Session(get_engine()) as db:
        rows = db.exec(select(Job).where(col(Job.heartbeat_secret_enc).is_not(None))).all()
        for job in rows:
            register_heartbeat(job.id, heartbeat_url(job.heartbeat_secret_enc))


class Heartbeat:
    def __init__(self, policy: OutboundPolicy, clock: Clock = SYSTEM_CLOCK) -> None:
        self._policy = policy
        self._clock = clock
        self._tasks: set[asyncio.Task[None]] = set()

    def ping(self, job_id: uuid.UUID, kind: PingKind) -> None:
        task = asyncio.create_task(self._ping(job_id, kind))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def wait_idle(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def stop(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*list(self._tasks), return_exceptions=True)

    @staticmethod
    def _load_url(job_id: uuid.UUID) -> str | None:
        with Session(get_engine()) as db:
            job = db.get(Job, job_id)
            return heartbeat_url(job.heartbeat_secret_enc) if job else None

    async def _ping(self, job_id: uuid.UUID, kind: PingKind) -> None:
        try:
            url = await asyncio.to_thread(self._load_url, job_id)
        except Exception as exc:
            logger.warning(
                "Heartbeat ping (%s) for job %s skipped: %s", kind, job_id, type(exc).__name__
            )
            return
        if not url:
            return
        target = ping_url(url, kind)
        for attempt in range(PING_RETRIES + 1):
            try:
                response = await send_http(self._policy, "GET", target, timeout=PING_TIMEOUT)
                expect_success(response)
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                error = describe_delivery_error(exc, [url, target])
                if attempt == PING_RETRIES:
                    logger.warning("Heartbeat ping (%s) for job %s failed: %s", kind, job_id, error)
                    return
            await self._clock.sleep(RETRY_DELAY * (attempt + 1))

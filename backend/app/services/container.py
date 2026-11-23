"""The long-lived background services of one app instance, wired together."""

import asyncio
import logging
from contextlib import suppress
from dataclasses import dataclass

from app.core.clock import SYSTEM_CLOCK, Clock
from app.core.config import Settings
from app.core.logging import SECRET_REGISTRY
from app.services.app_settings import load_settings
from app.services.destination_tokens import set_auth_expired_handler
from app.services.heartbeat import Heartbeat, register_all_heartbeats
from app.services.housekeeping import Housekeeping
from app.services.messages import auth_expired_message
from app.services.notifier import Notifier
from app.services.outbound import OutboundPolicy
from app.services.overdue import OverdueChecker
from app.services.post_run import PostRun
from app.services.retention import RetentionService
from app.services.runner import Runner
from app.services.scheduler import JobScheduler, check_single_worker

logger = logging.getLogger(__name__)

SHUTDOWN_GRACE = 5.0


@dataclass
class Services:
    settings: Settings
    clock: Clock
    policy: OutboundPolicy
    notifier: Notifier
    heartbeat: Heartbeat
    retention: RetentionService
    post_run: PostRun
    runner: Runner
    overdue: OverdueChecker
    housekeeping: Housekeeping
    scheduler: JobScheduler

    @classmethod
    def build(
        cls, settings: Settings, policy: OutboundPolicy | None = None, clock: Clock | None = None
    ) -> "Services":
        clock = clock or SYSTEM_CLOCK
        policy = policy or OutboundPolicy(allow_private=settings.allow_private_notification_targets)
        notifier = Notifier(policy, clock)
        heartbeat = Heartbeat(policy, clock)
        retention = RetentionService(notifier, clock)
        post_run = PostRun(notifier, heartbeat, retention)
        runner = Runner(settings, hooks=post_run)
        overdue = OverdueChecker(notifier, runner.is_active, clock)
        housekeeping = Housekeeping(settings, retention, notifier, runner.active_run_ids, clock)
        scheduler = JobScheduler(
            settings, runner, clock=clock, housekeeping=housekeeping.run, overdue=overdue.check
        )
        return cls(
            settings, clock, policy, notifier, heartbeat, retention, post_run, runner,
            overdue, housekeeping, scheduler,
        )  # fmt: skip

    def _auth_expired(self, destination_id: object, name: str) -> None:
        try:
            base_url = load_settings().public_base_url
        except Exception:
            base_url = ""
        self.notifier.notify_threadsafe(auth_expired_message(str(destination_id), name, base_url))

    async def start(self) -> None:
        check_single_worker()
        interrupted = await self.runner.start()
        await self.notifier.start()
        await asyncio.to_thread(register_all_heartbeats)
        set_auth_expired_handler(self._auth_expired)
        for run_id in interrupted:
            self.post_run.on_finished(run_id)
        await self.scheduler.start()

    async def stop(self) -> None:
        await self.scheduler.stop()
        await self.runner.stop()
        with suppress(TimeoutError):
            async with asyncio.timeout(SHUTDOWN_GRACE):
                await self.post_run.wait_idle()
        await self.retention.stop()
        await self.heartbeat.stop()
        await self.notifier.stop()
        set_auth_expired_handler(None)
        SECRET_REGISTRY.clear()

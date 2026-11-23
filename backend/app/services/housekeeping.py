"""Daily housekeeping. Every step is independent: one failing never stops the others."""

import asyncio
import logging
import shutil
import uuid
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, cast

from sqlalchemy import CursorResult, delete, exists
from sqlmodel import Session, col, select

from app.core.clock import SYSTEM_CLOCK, Clock
from app.core.config import Settings
from app.core.db import get_engine
from app.models import Artifact, AuditLog, Destination, Run
from app.models.run import ACTIVE_STATUSES
from app.services.app_settings import load_settings
from app.services.audit import write_audit
from app.services.messages import low_disk_message
from app.services.notifier import Notifier
from app.services.paths import PathNotAllowed, resolve_inside, to_relative
from app.services.retention import RetentionService

logger = logging.getLogger(__name__)

LOW_DISK_REPEAT = timedelta(days=1)


@dataclass(frozen=True)
class DiskUsage:
    key: str
    label: str
    kind: str
    destination_id: str | None
    total: int
    used: int
    free: int

    @property
    def free_percent(self) -> float:
        return 100.0 * self.free / self.total if self.total else 100.0


def _usage(path: Path) -> tuple[int, int, int] | None:
    for candidate in (path, *path.parents):
        if candidate.is_dir():
            try:
                total, used, free = shutil.disk_usage(candidate)
                return total, used, free
            except OSError:
                return None
    return None


def disk_usages(settings: Settings) -> list[DiskUsage]:
    """The data volume and every local destination (blocking)."""
    result: list[DiskUsage] = []
    data = _usage(settings.data_dir)
    if data is not None:
        result.append(DiskUsage("data", "Data volume", "data", None, *data))
    with Session(get_engine()) as db:
        rows = db.exec(
            select(Destination)
            .where(col(Destination.type) == "local")
            .order_by(col(Destination.name))
        ).all()
    for row in rows:
        try:
            base = settings.backup_dir
            root = resolve_inside(base, to_relative(str(row.config.get("path", "")), base))
        except PathNotAllowed:
            continue
        usage = _usage(root)
        if usage is not None:
            result.append(
                DiskUsage(f"destination:{row.id}", row.name, "destination", str(row.id), *usage)
            )
    return result


class Housekeeping:
    def __init__(
        self,
        settings: Settings,
        retention: RetentionService,
        notifier: Notifier | None,
        active_runs: Callable[[], Iterable[uuid.UUID]],
        clock: Clock = SYSTEM_CLOCK,
    ) -> None:
        self._settings = settings
        self._retention = retention
        self._notifier = notifier
        self._active_runs = active_runs
        self._clock = clock
        self._low_disk_sent: dict[str, datetime] = {}

    async def run(self) -> dict[str, Any]:
        summary: dict[str, Any] = {}
        steps: list[tuple[str, Callable[[], Awaitable[Any]]]] = [
            ("retention", self.retention),
            ("logs", self.prune_logs),
            ("history", self.prune_history),
            ("audit", self.prune_audit),
            ("work_dirs", self.clean_work_dirs),
            ("low_disk", self.check_disks),
        ]
        for name, step in steps:
            try:
                summary[name] = await step()
            except Exception as exc:
                logger.error("Housekeeping step %s failed: %s", name, type(exc).__name__)
                summary[name] = {"error": type(exc).__name__}
        try:
            await asyncio.to_thread(self._audit, summary)
        except Exception as exc:
            logger.error("Housekeeping audit entry failed: %s", type(exc).__name__)
        logger.info("Housekeeping finished: %s", summary)
        return summary

    async def retention(self) -> dict[str, Any]:
        results = await self._retention.run_all()
        return {
            "destinations": len(results),
            "deleted_runs": sum(r.deleted_runs for r in results),
            "deleted_artifacts": sum(r.deleted_artifacts for r in results),
            "failures": sum(len(r.failures) for r in results),
        }

    async def prune_logs(self) -> dict[str, Any]:
        days = (await asyncio.to_thread(load_settings)).log_retention_days
        return await asyncio.to_thread(self._prune_logs, self._clock.now() - timedelta(days=days))

    def _prune_logs(self, cutoff: datetime) -> dict[str, Any]:
        active = {f"{r}.log" for r in self._active_runs()}
        removed = 0
        logs = self._settings.logs_dir
        if not logs.is_dir():
            return {"removed": 0}
        for path in logs.iterdir():
            if path.name in active or not path.name.endswith(".log") or path.is_symlink():
                continue
            if path.is_file() and path.stat().st_mtime < cutoff.timestamp():
                path.unlink(missing_ok=True)
                removed += 1
        return {"removed": removed}

    async def prune_history(self) -> dict[str, Any]:
        days = (await asyncio.to_thread(load_settings)).run_history_days
        return await asyncio.to_thread(
            self._prune_history, self._clock.now() - timedelta(days=days)
        )

    def _prune_history(self, cutoff: datetime) -> dict[str, Any]:
        live = exists().where(
            col(Artifact.run_id) == col(Run.id), col(Artifact.deleted_at).is_(None)
        )
        with Session(get_engine()) as db:
            rows = db.exec(
                select(Run).where(
                    col(Run.created_at) < cutoff,
                    col(Run.status).not_in(ACTIVE_STATUSES),
                    ~live,
                )
            ).all()
            for run in rows:
                # Only deleted artifact rows remain; the foreign key cascades them.
                if run.log_path:
                    Path(run.log_path).unlink(missing_ok=True)
                db.delete(run)
            db.commit()
            return {"removed": len(rows)}

    async def prune_audit(self) -> dict[str, Any]:
        days = (await asyncio.to_thread(load_settings)).audit_retention_days
        return await asyncio.to_thread(self._prune_audit, self._clock.now() - timedelta(days=days))

    @staticmethod
    def _prune_audit(cutoff: datetime) -> dict[str, Any]:
        with Session(get_engine()) as db:
            result = cast(
                "CursorResult[Any]",
                db.execute(delete(AuditLog).where(col(AuditLog.timestamp) < cutoff)),
            )
            db.commit()
            return {"removed": int(result.rowcount or 0)}

    async def clean_work_dirs(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._clean_work_dirs)

    def _clean_work_dirs(self) -> dict[str, Any]:
        work = self._settings.work_dir
        if not work.is_dir():
            return {"removed": 0}
        active = {str(r) for r in self._active_runs()}
        removed = 0
        for child in work.iterdir():
            if child.name in active:
                continue
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)
            removed += 1
        return {"removed": removed}

    async def check_disks(self) -> dict[str, Any]:
        settings = await asyncio.to_thread(load_settings)
        usages = await asyncio.to_thread(disk_usages, self._settings)
        threshold = settings.low_disk_threshold_percent
        now = self._clock.now()
        low: list[str] = []
        for usage in usages:
            if usage.free_percent >= threshold:
                continue
            low.append(usage.label)
            last = self._low_disk_sent.get(usage.key)
            if last is not None and now - last < LOW_DISK_REPEAT:
                continue
            self._low_disk_sent[usage.key] = now
            logger.warning("Low disk space on %s: %.1f%% free", usage.label, usage.free_percent)
            if self._notifier is not None:
                message = low_disk_message(usage.label, usage.free_percent, threshold, usage.free)
                await self._notifier.notify(message)
        return {"checked": len(usages), "low": low}

    @staticmethod
    def _audit(summary: dict[str, Any]) -> None:
        with Session(get_engine()) as db:
            write_audit(db, "housekeeping.run", username="system", details=summary)
            db.commit()

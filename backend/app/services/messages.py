"""Notification content. Built only from non-secret fields; error text is scrubbed and capped."""

import html
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.models import Run
from app.models.notification import EventType
from app.services.redact import scrub
from app.storage.local import format_bytes

MAX_ERROR_CHARS = 500


def summarize_error(text: str | None, secrets: Sequence[str] = ()) -> str | None:
    if not text:
        return None
    flat = " ".join(scrub(text, secrets).split())
    return flat if len(flat) <= MAX_ERROR_CHARS else flat[: MAX_ERROR_CHARS - 1] + "…"


def _iso(moment: datetime | None) -> str | None:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ") if moment else None


@dataclass
class Message:
    event: str
    title: str
    status: str
    lines: list[str] = field(default_factory=list)
    fields: list[tuple[str, str]] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)
    url: str | None = None
    job_id: str | None = None
    run_id: str | None = None

    @property
    def text(self) -> str:
        body = [self.title, *self.lines]
        body += [f"{name}: {value}" for name, value in self.fields]
        if self.url:
            body.append(f"Details: {self.url}")
        return "\n".join(body)

    def html(self) -> str:
        rows = "".join(
            f"<tr><th align='left'>{html.escape(n)}</th><td>{html.escape(v)}</td></tr>"
            for n, v in self.fields
        )
        lines = "".join(f"<p>{html.escape(line)}</p>" for line in self.lines)
        link = (
            f"<p><a href='{html.escape(self.url, quote=True)}'>Open in DBBM</a></p>"
            if self.url
            else ""
        )
        return (
            f"<html><body><h3>{html.escape(self.title)}</h3>{lines}"
            f"<table>{rows}</table>{link}</body></html>"
        )

    def payload(self, timestamp: datetime) -> dict[str, Any]:
        return {
            "event": self.event,
            "timestamp": _iso(timestamp),
            "status": self.status,
            "title": self.title,
            **self.data,
            "url": self.url,
        }


@dataclass(frozen=True)
class RunFacts:
    """What a run notification needs, loaded from the database."""

    run: Run
    job_name: str
    artifact_count: int
    total_bytes: int


def _destination_results(run: Run) -> list[dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    for unit in run.step_results.get("units", []):
        if unit.get("kind") != "database":
            continue
        for entry in unit.get("destinations", []):
            name = entry.get("destination_name")
            if not name:
                continue
            item = results.setdefault(
                name, {"name": name, "type": entry.get("type"), "ok": 0, "failed": 0, "error": None}
            )
            if entry.get("status") == "ok":
                item["ok"] += 1
            elif entry.get("status") == "failed":
                item["failed"] += 1
                item["error"] = item["error"] or summarize_error(entry.get("error"))
    for item in results.values():
        item["status"] = (
            "failed"
            if item["failed"] and not item["ok"]
            else ("partial" if item["failed"] else "ok")
        )
    return list(results.values())


def run_link(base_url: str, run_id: object) -> str | None:
    return f"{base_url.rstrip('/')}/runs/{run_id}" if base_url else None


def run_message(facts: RunFacts, base_url: str, recovery: bool = False) -> Message:
    run = facts.run
    duration = (
        int((run.finished_at - run.started_at).total_seconds())
        if run.finished_at and run.started_at
        else None
    )
    destinations = _destination_results(run)
    error = summarize_error(run.error)
    if recovery:
        title = f"Recovered: backup job '{facts.job_name}' succeeded again"
    else:
        title = f"Backup job '{facts.job_name}': {run.status}"
    fields = [
        ("Status", run.status),
        ("Trigger", run.trigger),
        ("Started", _iso(run.started_at) or "-"),
        ("Duration", f"{duration}s" if duration is not None else "-"),
        ("Artifacts", f"{facts.artifact_count} ({format_bytes(facts.total_bytes)})"),
    ]
    if run.scheduled_for:
        fields.insert(2, ("Scheduled for", _iso(run.scheduled_for) or "-"))
    for dest in destinations:
        fields.append((f"Destination {dest['name']}", dest["status"]))
    if error:
        fields.append(("Error", error))
    url = run_link(base_url, run.id)
    return Message(
        event=EventType.RECOVERY if recovery else EventType.RUN_FINISHED,
        title=title,
        status=run.status,
        fields=fields,
        url=url,
        job_id=str(run.job_id) if run.job_id else None,
        run_id=str(run.id),
        data={
            "job": {"id": str(run.job_id) if run.job_id else None, "name": facts.job_name},
            "run": {
                "id": str(run.id),
                "status": run.status,
                "trigger": run.trigger,
                "scheduled_for": _iso(run.scheduled_for),
                "started_at": _iso(run.started_at),
                "finished_at": _iso(run.finished_at),
                "duration_seconds": duration,
                "artifacts": {"count": facts.artifact_count, "total_bytes": facts.total_bytes},
                "destinations": [
                    {k: d[k] for k in ("name", "type", "status", "error")} for d in destinations
                ],
                "error": error,
            },
        },
    )


def overdue_message(job_id: str, job_name: str, expected_at: datetime, base_url: str) -> Message:
    url = f"{base_url.rstrip('/')}/jobs" if base_url else None
    return Message(
        event=EventType.OVERDUE,
        title=f"Backup job '{job_name}' is overdue",
        status="overdue",
        lines=[f"No successful run since the scheduled time {_iso(expected_at)}."],
        fields=[("Expected at", _iso(expected_at) or "-")],
        url=url,
        job_id=job_id,
        data={"job": {"id": job_id, "name": job_name}, "expected_at": _iso(expected_at)},
    )


def auth_expired_message(destination_id: str, name: str, base_url: str) -> Message:
    url = f"{base_url.rstrip('/')}/destinations?reconnect={destination_id}" if base_url else None
    return Message(
        event=EventType.AUTH_EXPIRED,
        title=f"Google Drive authorization expired for destination '{name}'",
        status="needs_reauth",
        lines=["Backups to this destination are skipped until you reconnect it."],
        url=url,
        data={"destination": {"id": destination_id, "name": name}},
    )


def retention_failed_message(
    destination_id: str, name: str, passes: int, error: str | None
) -> Message:
    fields = [("Failed passes in a row", str(passes))]
    summary = summarize_error(error)
    if summary:
        fields.append(("Last error", summary))
    return Message(
        event=EventType.RETENTION_FAILED,
        title=f"Retention keeps failing on destination '{name}'",
        status="failed",
        lines=["Expired backups could not be deleted; they are retried at the next pass."],
        fields=fields,
        data={"destination": {"id": destination_id, "name": name}, "error": summary},
    )


def low_disk_message(label: str, free_percent: float, threshold: int, free_bytes: int) -> Message:
    return Message(
        event=EventType.LOW_DISK,
        title=f"Low disk space: {label}",
        status="low_disk",
        fields=[
            ("Free", f"{free_percent:.1f}% ({format_bytes(free_bytes)})"),
            ("Threshold", f"{threshold}%"),
        ],
        data={
            "volume": label,
            "free_percent": round(free_percent, 1),
            "free_bytes": free_bytes,
            "threshold_percent": threshold,
        },
    )


def channel_test_message(channel_name: str) -> Message:
    return Message(
        event=EventType.TEST,
        title="DBBM test notification",
        status="test",
        lines=[f"This is a test message for the channel '{channel_name}'."],
        data={"channel": channel_name},
    )


def rate_limit_summary(suppressed: int, channel_name: str, event: str) -> Message:
    return Message(
        event=event,
        title=f"{suppressed} notification(s) were suppressed",
        status="rate_limited",
        lines=[
            f"The channel '{channel_name}' reached its limit of messages per hour; "
            f"{suppressed} message(s) were not sent. See the notification events in DBBM."
        ],
        data={"suppressed": suppressed},
    )

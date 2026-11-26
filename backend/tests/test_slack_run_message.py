"""Slack layout of run notifications: status colour, one summary line, destinations, buttons."""

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from app.models import Run
from app.services.channels import NO_LINK_TIP, slack_payload
from app.services.messages import RunFacts, run_message

BASE = "https://backup.example.com"
STARTED = datetime(2026, 11, 26, 15, 44, 4, tzinfo=UTC)


def _run(status: str = "success", error: str | None = None, **dest_status: str) -> Run:
    destinations = [
        {
            "destination_name": name,
            "type": "local",
            "status": state,
            "error": "boom" if state == "failed" else None,
        }
        for name, state in (dest_status or {"local_backup": "ok"}).items()
    ]
    return Run(
        id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        job_name="siam_job",
        trigger="manual",
        status=status,
        started_at=STARTED,
        finished_at=STARTED + timedelta(seconds=125),
        error=error,
        step_results={"units": [{"kind": "database", "destinations": destinations}]},
    )


def _payload(run: Run, base_url: str = BASE, recovery: bool = False) -> dict[str, Any]:
    facts = RunFacts(run=run, job_name=run.job_name, artifact_count=2, total_bytes=1_048_576)
    return slack_payload(run_message(facts, base_url, recovery=recovery))


def _card(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """The title block (the message's own) followed by the blocks of the coloured card."""
    blocks: list[dict[str, Any]] = payload["blocks"] + payload["attachments"][0]["blocks"]
    return blocks


def _texts(blocks: list[dict[str, Any]]) -> str:
    return json.dumps(blocks)


def test_recovery_card_has_colour_summary_destinations_and_buttons() -> None:
    run = _run(local_backup="ok", loki_gdrive="ok")
    payload = _payload(run, recovery=True)
    assert payload["text"] == "Recovered: backup job 'siam_job' succeeded again"
    attachment = payload["attachments"][0]
    assert attachment["color"] == "#2eb67d"
    # The title is shown once, as the message's own block; the card holds the details.
    assert len(payload["blocks"]) == 1
    assert all("Recovered" not in json.dumps(b) for b in attachment["blocks"])
    blocks = _card(payload)
    assert blocks[0]["text"]["text"].startswith(":white_check_mark: *Recovered")
    summary = blocks[1]["elements"][0]["text"]
    assert "Manual" in summary and "took 2m 5s" in summary and "2 files, 1.0 MiB" in summary
    assert f"<!date^{int(STARTED.timestamp())}^" in summary and "|2026-11-26T15:44:04Z>" in summary
    assert ":white_check_mark: local_backup" in blocks[2]["text"]["text"]
    assert ":white_check_mark: loki_gdrive" in blocks[2]["text"]["text"]
    buttons = blocks[-1]["elements"]
    assert [(b["text"]["text"], b["url"]) for b in buttons] == [
        ("Open run", f"{BASE}/runs/{run.id}"),
        ("Job's runs", f"{BASE}/runs?job={run.job_id}"),
    ]


def test_failed_destination_error_is_shown_once() -> None:
    payload = _payload(
        _run("partial", error="testdb → loki_gdrive: boom", local_backup="ok", loki_gdrive="failed")
    )
    attachment = payload["attachments"][0]
    assert attachment["color"] == "#ecb22e"
    text = _texts(attachment["blocks"])
    assert ":x: loki_gdrive: boom" in text
    assert "```" not in text  # the run error only repeats the destination's error


def test_a_run_error_of_its_own_goes_in_a_code_block() -> None:
    run = _run("failed", error="Access denied for user <backup>")
    blocks = _card(_payload(run))
    assert _payload(run)["attachments"][0]["color"] == "#e01e5a"
    assert "```Access denied for user &lt;backup&gt;```" in _texts(blocks)


def test_sub_second_duration_is_escaped() -> None:
    run = _run()
    run.finished_at = STARTED + timedelta(milliseconds=400)
    summary = _card(_payload(run))[1]["elements"][0]["text"]
    assert "took &lt;1s" in summary


def test_without_a_base_url_the_card_says_how_to_get_links() -> None:
    blocks = _card(_payload(_run(), base_url=""))
    assert all(b["type"] != "actions" for b in blocks)
    assert blocks[-1]["elements"][0]["text"] == NO_LINK_TIP


def test_job_names_cannot_inject_slack_markup() -> None:
    run = _run()
    facts = RunFacts(run=run, job_name="<!channel> & co", artifact_count=0, total_bytes=0)
    blocks = _card(slack_payload(run_message(facts, BASE)))
    assert "&lt;!channel&gt; &amp; co" in blocks[0]["text"]["text"]
    assert "<!channel>" not in blocks[0]["text"]["text"]
    assert "0 files" in blocks[1]["elements"][0]["text"]

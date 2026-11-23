"""Cron validation, day-of-week translation, minimum interval, time zones, DST and preview."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from app.core.schedule import (
    ScheduleError,
    build_trigger,
    fire_after,
    latest_fire_between,
    next_fires,
    schedule_problem,
    translate_day_of_week,
    validate_schedule,
    validate_timezone,
)
from tests.conftest import api_post

BERLIN = ZoneInfo("Europe/Berlin")


def fires(expr: str, tz: str, start: datetime, count: int) -> list[datetime]:
    return next_fires(build_trigger(expr, ZoneInfo(tz)), start, count)


@pytest.mark.parametrize(
    ("field", "expected"),
    [
        ("*", "*"),
        ("0", "sun"),
        ("7", "sun"),
        ("1-5", "mon,tue,wed,thu,fri"),
        ("5-7", "sun,fri,sat"),
        ("*/2", "sun,tue,thu,sat"),
        ("1/3", "mon,thu"),
        ("mon,wed", "mon,wed"),
        ("SAT", "sat"),
    ],
)
def test_day_of_week_follows_cron_numbering(field: str, expected: str) -> None:
    assert translate_day_of_week(field) == expected


def test_sunday_zero_fires_on_sunday() -> None:
    start = datetime(2026, 11, 23, tzinfo=UTC)  # a Monday
    result = fires("0 3 * * 0", "UTC", start, 2)
    assert [f.strftime("%A") for f in result] == ["Sunday", "Sunday"]
    assert result[0] == datetime(2026, 11, 29, 3, 0, tzinfo=UTC)


def test_weekdays_one_to_five() -> None:
    start = datetime(2026, 11, 22, 12, tzinfo=UTC)  # a Sunday
    days = [f.strftime("%a") for f in fires("0 9 * * 1-5", "UTC", start, 5)]
    assert days == ["Mon", "Tue", "Wed", "Thu", "Fri"]


@pytest.mark.parametrize(
    ("expr", "message"),
    [
        ("* * * *", "exactly 5 fields"),
        ("0 0 * * * *", "exactly 5 fields"),
        ("61 * * * *", "Invalid schedule"),
        ("0 25 * * *", "Invalid schedule"),
        ("0 0 13 * 5", "both day-of-month and day-of-week"),
        ("0 0 * * 8", "day of week"),
        ("0 0 * * 5-1", "range"),
        ("0 0 31 2 *", "never fires"),
        ("* * * * *", "once every 5 minutes"),
        ("*/4 * * * *", "once every 5 minutes"),
        ("0,1 * * * *", "once every 5 minutes"),
        ("58 23 * * *,0 0 * * *", "exactly 5 fields"),
        ("0,59 0,23 * * *", "once every 5 minutes"),
    ],
)
def test_invalid_schedules(expr: str, message: str) -> None:
    with pytest.raises(ScheduleError, match=message):
        validate_schedule(expr, "UTC")


@pytest.mark.parametrize("expr", ["*/5 * * * *", "0 * * * *", "30 2 * * *", "0 9 * * 1-5"])
def test_valid_schedules(expr: str) -> None:
    normalized, zone = validate_schedule(f"  {expr}  ", "Europe/Berlin")
    assert normalized == expr
    assert zone.key == "Europe/Berlin"


@pytest.mark.parametrize("name", ["Mars/Base", "", "../etc/passwd", "/etc/localtime", "X" * 80])
def test_unknown_time_zone(name: str) -> None:
    with pytest.raises(ScheduleError, match="Unknown time zone"):
        validate_timezone(name)


def test_schedule_problem_reports_legacy_text() -> None:
    assert schedule_problem(None, "UTC") is None
    assert schedule_problem("0 3 * * *", "UTC") is None
    assert "5 fields" in (schedule_problem("every night", "UTC") or "")


def test_spring_forward_nonexistent_time_fires_once_an_hour_later() -> None:
    start = datetime(2026, 3, 28, 12, tzinfo=UTC)
    result = fires("30 2 * * *", "Europe/Berlin", start, 3)
    # 02:30 does not exist on 2026-03-29: it runs at 01:30 UTC (03:30 CEST), once.
    assert result[0] == datetime(2026, 3, 29, 1, 30, tzinfo=UTC)
    assert result[1] == datetime(2026, 3, 30, 0, 30, tzinfo=UTC)
    assert result[1].astimezone(BERLIN).hour == 2


def test_fall_back_ambiguous_time_fires_for_both_occurrences() -> None:
    start = datetime(2026, 10, 24, 12, tzinfo=UTC)
    result = fires("30 2 * * *", "Europe/Berlin", start, 3)
    assert result == [
        datetime(2026, 10, 25, 0, 30, tzinfo=UTC),  # 02:30 CEST
        datetime(2026, 10, 25, 1, 30, tzinfo=UTC),  # 02:30 CET
        datetime(2026, 10, 26, 1, 30, tzinfo=UTC),
    ]


def test_hourly_follows_real_time_across_dst() -> None:
    result = fires("15 * * * *", "Europe/Berlin", datetime(2026, 3, 28, 23, tzinfo=UTC), 4)
    gaps = {b - a for a, b in zip(result, result[1:], strict=False)}
    assert gaps == {timedelta(hours=1)}


def test_latest_fire_between_and_fire_after() -> None:
    trigger = build_trigger("0 * * * *", UTC)
    start = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    assert fire_after(trigger, start) == datetime(2026, 1, 1, 11, 0, tzinfo=UTC)
    end = datetime(2026, 1, 1, 13, 30, tzinfo=UTC)
    assert latest_fire_between(trigger, start, end) == datetime(2026, 1, 1, 13, 0, tzinfo=UTC)
    assert latest_fire_between(trigger, start, start + timedelta(minutes=30)) is None


def test_preview_endpoint(authed: TestClient) -> None:
    response = api_post(
        authed, "/api/schedule/preview", {"cron": "0 3 * * *", "timezone": "Asia/Kolkata"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["timezone"] == "Asia/Kolkata"
    assert len(body["fires"]) == 5
    first = body["fires"][0]
    assert first["local"].endswith("T03:00:00+05:30")
    assert first["utc"].endswith("21:30:00Z")


def test_preview_defaults_to_setting_and_rejects_bad_input(authed: TestClient) -> None:
    ok = api_post(authed, "/api/schedule/preview", {"cron": "0 3 * * *"})
    assert ok.json()["timezone"] == "UTC"
    bad = api_post(authed, "/api/schedule/preview", {"cron": "* * * * *", "timezone": "UTC"})
    assert bad.status_code == 422
    assert "5 minutes" in bad.text
    zone = api_post(authed, "/api/schedule/preview", {"cron": "0 3 * * *", "timezone": "No/Where"})
    assert zone.status_code == 422
    assert zone.json()["detail"][0]["loc"] == ["body", "timezone"]


def test_timezones_endpoint(authed: TestClient) -> None:
    names = authed.get("/api/schedule/timezones").json()
    assert "Europe/Berlin" in names and "UTC" in names


def test_preview_requires_auth(client: TestClient) -> None:
    client.get("/api/auth/status")
    assert api_post(client, "/api/schedule/preview", {"cron": "0 3 * * *"}).status_code == 401

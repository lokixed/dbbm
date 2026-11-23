"""Cron expressions: validation, APScheduler triggers and fire-time arithmetic.

Time zones and DST (APScheduler 3.x with zoneinfo; covered by tests):
- A local time that does not exist (spring forward, e.g. 02:30 in Europe/Berlin on the last
  Sunday of March) fires once, one hour later by the wall clock (03:30 local).
- A local time that occurs twice (fall back) fires for both occurrences, an hour apart.
  Sub-hourly and hourly schedules simply follow real elapsed time.
"""

import re
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from apscheduler.triggers.cron import CronTrigger

MAX_CRON_LENGTH = 100
MIN_INTERVAL = timedelta(minutes=5)
MIN_INTERVAL_SAMPLES = 1000
MIN_INTERVAL_HORIZON = timedelta(days=400)
PREVIEW_COUNT = 5

_DAYS = ("sun", "mon", "tue", "wed", "thu", "fri", "sat")
_DOW_TOKEN = re.compile(r"^(\*|[a-z0-9]+(?:-[a-z0-9]+)?)(?:/(\d+))?$")
_ONE_MICROSECOND = timedelta(microseconds=1)


class ScheduleError(ValueError):
    """The schedule or time zone is invalid. The message is safe to show."""


def validate_timezone(name: str) -> ZoneInfo:
    name = name.strip()
    if not name or len(name) > 64 or name.startswith(("/", ".")) or ".." in name:
        raise ScheduleError(f"Unknown time zone: {name!r}.")
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ScheduleError(f"Unknown time zone: {name!r}.") from None


def timezone_names() -> list[str]:
    return sorted(n for n in available_timezones() if "/" in n or n == "UTC")


def _day_number(token: str) -> int:
    if token in _DAYS:
        return _DAYS.index(token)
    if token.isdigit() and 0 <= int(token) <= 7:
        return int(token) % 7
    raise ScheduleError(f"Invalid day of week: {token!r} (use 0-7 or sun-sat).")


def _expand_dow_part(part: str) -> set[int]:
    match = _DOW_TOKEN.match(part)
    if not match:
        raise ScheduleError(f"Invalid day of week: {part!r}.")
    base, step_text = match.groups()
    step = int(step_text) if step_text else 1
    if step < 1:
        raise ScheduleError("A step must be at least 1.")
    if base == "*":
        first, last = 0, 6
    elif "-" in base:
        low, high = base.split("-")
        first, last = _day_number(low), _day_number(high)
        if high == "7":
            last = 7  # "5-7" means Friday to Sunday
        if first > last:
            raise ScheduleError(f"Invalid day-of-week range: {part!r}.")
    else:
        first = _day_number(base)
        last = 6 if step_text else first  # cron: "a/n" means a through the end, every n
    return {day % 7 for day in range(first, last + 1, step)}


def translate_day_of_week(field: str) -> str:
    """Cron counts Sunday as 0; APScheduler 3 counts Monday as 0, so pass explicit names."""
    field = field.lower()
    if field == "*":
        return "*"
    days: set[int] = set()
    for part in field.split(","):
        days |= _expand_dow_part(part)
    return ",".join(_DAYS[d] for d in sorted(days))


def normalize_cron(expression: str) -> str:
    fields = expression.split()
    if len(fields) != 5:
        raise ScheduleError(
            "A schedule needs exactly 5 fields: minute hour day-of-month month day-of-week."
        )
    normalized = " ".join(fields)
    if len(normalized) > MAX_CRON_LENGTH:
        raise ScheduleError(f"Schedule must be at most {MAX_CRON_LENGTH} characters.")
    return normalized


def build_trigger(expression: str, timezone: tzinfo) -> CronTrigger:
    minute, hour, day, month, dow = normalize_cron(expression).split()
    if not day.startswith("*") and not dow.startswith("*"):
        # cron fires when EITHER field matches; APScheduler only when BOTH do.
        raise ScheduleError(
            "Restricting both day-of-month and day-of-week is not supported "
            "(cron and the scheduler disagree on its meaning). Set one of them to '*'."
        )
    try:
        return CronTrigger(
            minute=minute,
            hour=hour,
            day=day,
            month=month,
            day_of_week=translate_day_of_week(dow),
            timezone=timezone,
        )
    except ScheduleError:
        raise
    except (ValueError, TypeError) as exc:
        raise ScheduleError(f"Invalid schedule: {exc}") from None


def fire_after(trigger: CronTrigger, moment: datetime) -> datetime | None:
    """The first fire time strictly after `moment`, in UTC."""
    fire = trigger.get_next_fire_time(None, moment.astimezone(UTC) + _ONE_MICROSECOND)
    return fire.astimezone(UTC) if fire is not None else None


def iter_fires(trigger: CronTrigger, after: datetime) -> Iterator[datetime]:
    current = after
    while (fire := fire_after(trigger, current)) is not None:
        yield fire
        current = fire


def next_fires(trigger: CronTrigger, after: datetime, count: int) -> list[datetime]:
    result: list[datetime] = []
    for fire in iter_fires(trigger, after):
        result.append(fire)
        if len(result) >= count:
            break
    return result


def latest_fire_between(trigger: CronTrigger, start: datetime, end: datetime) -> datetime | None:
    """The last fire time in (start, end], or None."""
    latest: datetime | None = None
    for fire in iter_fires(trigger, start):
        if fire > end:
            break
        latest = fire
    return latest


def check_min_interval(expression: str) -> None:
    # Measured in UTC so DST shifts never count as a short interval.
    trigger = build_trigger(expression, UTC)
    start = datetime(2025, 1, 1, tzinfo=UTC)
    previous: datetime | None = None
    for index, fire in enumerate(iter_fires(trigger, start)):
        if previous is not None and fire - previous < MIN_INTERVAL:
            raise ScheduleError("Schedules may fire at most once every 5 minutes.")
        previous = fire
        if index >= MIN_INTERVAL_SAMPLES or fire - start > MIN_INTERVAL_HORIZON:
            break


def validate_schedule(expression: str, timezone: str) -> tuple[str, ZoneInfo]:
    """Normalized expression and zone, or ScheduleError with a user-facing message."""
    zone = validate_timezone(timezone)
    normalized = normalize_cron(expression)
    trigger = build_trigger(normalized, zone)
    if fire_after(trigger, datetime.now(UTC)) is None:
        raise ScheduleError("This schedule never fires.")
    check_min_interval(normalized)
    return normalized, zone


def schedule_problem(expression: str | None, timezone: str) -> str | None:
    """Why a stored schedule cannot be used (e.g. free text saved before milestone 6)."""
    if not expression:
        return None
    try:
        validate_schedule(expression, timezone)
    except ScheduleError as exc:
        return str(exc)
    return None

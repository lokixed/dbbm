import re
from datetime import datetime
from typing import Annotated
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.schedule import ScheduleError, validate_timezone

_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def normalize_base_url(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    if len(value) > 512:
        raise ValueError("Public base URL must be at most 512 characters.")
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("Public base URL must start with http:// or https:// and name a host.")
    if parts.query or parts.fragment or parts.username or parts.password:
        raise ValueError("Public base URL must not contain credentials, a query or a fragment.")
    return value.rstrip("/")


class AppSettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    public_base_url: str = ""
    default_timezone: str = "UTC"
    log_retention_days: Annotated[int, Field(strict=True, ge=1, le=3650)] = 90
    run_history_days: Annotated[int, Field(strict=True, ge=1, le=36500)] = 365
    misfire_grace_seconds: Annotated[int, Field(strict=True, ge=0, le=7 * 86400)] = 3600
    housekeeping_time: str = "03:30"
    low_disk_threshold_percent: Annotated[int, Field(strict=True, ge=0, le=90)] = 10
    overdue_grace_percent: Annotated[int, Field(strict=True, ge=0, le=1000)] = 50

    @field_validator("public_base_url")
    @classmethod
    def _url(cls, value: str) -> str:
        return normalize_base_url(value)

    @field_validator("default_timezone")
    @classmethod
    def _tz(cls, value: str) -> str:
        try:
            return validate_timezone(value).key
        except ScheduleError as exc:
            raise ValueError(str(exc)) from None

    @field_validator("housekeeping_time")
    @classmethod
    def _time(cls, value: str) -> str:
        value = value.strip()
        if not _HHMM.match(value):
            raise ValueError("Housekeeping time must be HH:MM (24-hour).")
        return value

    @property
    def housekeeping_hour_minute(self) -> tuple[int, int]:
        hour, minute = self.housekeeping_time.split(":")
        return int(hour), int(minute)


class AppSettingsOut(AppSettingsIn):
    updated_at: datetime | None = None

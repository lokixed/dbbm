from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from app.api.common import invalid_field
from app.api.deps import CurrentUser
from app.core.db import SessionDep
from app.core.schedule import (
    PREVIEW_COUNT,
    ScheduleError,
    build_trigger,
    next_fires,
    timezone_names,
    validate_schedule,
)
from app.services.app_settings import read_settings

router = APIRouter(prefix="/api/schedule", tags=["schedule"])


class PreviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cron: Annotated[str, Field(max_length=200)]
    timezone: Annotated[str, Field(max_length=64)] | None = None


class FireOut(BaseModel):
    utc: datetime
    local: str


class PreviewOut(BaseModel):
    cron: str
    timezone: str
    fires: list[FireOut]


@router.post("/preview")
def preview(body: PreviewIn, db: SessionDep, _user: CurrentUser) -> PreviewOut:
    timezone = body.timezone or read_settings(db).default_timezone
    try:
        normalized, zone = validate_schedule(body.cron, timezone)
    except ScheduleError as exc:
        field = "timezone" if str(exc).startswith("Unknown time zone") else "cron"
        raise invalid_field([field], str(exc)) from None
    fires = next_fires(build_trigger(normalized, zone), datetime.now(UTC), PREVIEW_COUNT)
    return PreviewOut(
        cron=normalized,
        timezone=zone.key,
        fires=[FireOut(utc=f, local=f.astimezone(zone).isoformat()) for f in fires],
    )


@router.get("/timezones")
def timezones(_user: CurrentUser) -> list[str]:
    return timezone_names()

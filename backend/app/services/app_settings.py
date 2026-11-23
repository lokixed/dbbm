"""Application settings stored as key/value rows, with typed defaults."""

import logging
from typing import Any

from pydantic import ValidationError
from sqlmodel import Session, select

from app.core.config import get_settings
from app.core.db import get_engine
from app.models import AppSetting
from app.models.base import utcnow
from app.schemas.settings import AppSettingsIn, AppSettingsOut, normalize_base_url

logger = logging.getLogger(__name__)


def _defaults() -> dict[str, Any]:
    values = AppSettingsIn().model_dump()
    try:
        values["public_base_url"] = normalize_base_url(get_settings().public_base_url)
    except ValueError:
        logger.warning("Ignoring PUBLIC_BASE_URL: not a valid http(s) URL")
    return values


def read_settings(db: Session) -> AppSettingsOut:
    values = _defaults()
    updated = None
    for row in db.exec(select(AppSetting)).all():
        if row.key in values:
            values[row.key] = row.value
            updated = row.updated_at if updated is None else max(updated, row.updated_at)
    try:
        parsed = AppSettingsIn.model_validate(values)
    except ValidationError:
        logger.warning("Stored settings are invalid; using defaults for this read")
        parsed = AppSettingsIn.model_validate(_defaults())
    return AppSettingsOut(**parsed.model_dump(), updated_at=updated)


def load_settings() -> AppSettingsOut:
    with Session(get_engine()) as db:
        return read_settings(db)


def write_settings(db: Session, body: AppSettingsIn) -> list[str]:
    """Stage the new values; the caller commits. Returns the keys that changed."""
    current = read_settings(db).model_dump(exclude={"updated_at"})
    changed: list[str] = []
    now = utcnow()
    for key, value in body.model_dump().items():
        row = db.get(AppSetting, key)
        if row is None:
            db.add(AppSetting(key=key, value=value, updated_at=now))
        elif row.value != value:
            row.value = value
            row.updated_at = now
            db.add(row)
        if current.get(key) != value:
            changed.append(key)
    return changed

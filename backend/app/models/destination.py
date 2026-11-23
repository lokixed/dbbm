import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, CheckConstraint, Column, LargeBinary
from sqlmodel import Field, SQLModel

from app.models.base import UTCDateTime, new_uuid, utcnow


class DestinationType(StrEnum):
    # Adding a type needs a migration that widens the "type" CHECK constraint.
    LOCAL = "local"
    GDRIVE = "gdrive"


class AuthStatus(StrEnum):
    OK = "ok"
    NEEDS_REAUTH = "needs_reauth"
    UNKNOWN = "unknown"


def _check(column: str, enum: type[StrEnum]) -> str:
    values = ", ".join(f"'{t.value}'" for t in enum)
    return f"{column} IN ({values})"


class Destination(SQLModel, table=True):
    __tablename__ = "destinations"
    __table_args__ = (
        CheckConstraint(_check("type", DestinationType), name="type"),
        CheckConstraint(_check("auth_status", AuthStatus), name="auth_status"),
    )

    id: uuid.UUID = Field(default_factory=new_uuid, primary_key=True)
    name: str = Field(max_length=64, unique=True)
    type: str = Field(max_length=16)
    config: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    secrets_enc: bytes | None = Field(default=None, sa_column=Column(LargeBinary, nullable=True))
    # Stored now, enforced in a later milestone.
    retention: dict[str, Any] = Field(
        default_factory=lambda: {"keep_last": None, "keep_days": None},
        sa_column=Column(JSON, nullable=False),
    )
    last_test_at: datetime | None = Field(
        default=None, sa_column=Column(UTCDateTime, nullable=True)
    )
    last_test_ok: bool | None = Field(default=None)
    last_test_message: str | None = Field(default=None, max_length=1000)
    # Only meaningful for remote destinations that hold an OAuth token.
    auth_status: str = Field(default=AuthStatus.UNKNOWN.value, max_length=16)
    auth_checked_at: datetime | None = Field(
        default=None, sa_column=Column(UTCDateTime, nullable=True)
    )
    created_at: datetime = Field(
        default_factory=utcnow, sa_column=Column(UTCDateTime, nullable=False)
    )
    updated_at: datetime = Field(
        default_factory=utcnow, sa_column=Column(UTCDateTime, nullable=False)
    )

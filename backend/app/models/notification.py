import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, CheckConstraint, Column, ForeignKey, Index, LargeBinary, Uuid
from sqlmodel import Field, SQLModel

from app.models.base import UTCDateTime, new_uuid, utcnow


class ChannelType(StrEnum):
    SLACK = "slack"
    EMAIL = "email"
    WEBHOOK = "webhook"


class EventType(StrEnum):
    RUN_FINISHED = "run_finished"
    RECOVERY = "recovery"
    OVERDUE = "overdue"
    AUTH_EXPIRED = "auth_expired"
    RETENTION_FAILED = "retention_failed"
    LOW_DISK = "low_disk"
    TEST = "test"


class EventStatus(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    RATE_LIMITED = "rate_limited"


def _check(column: str, enum: type[StrEnum]) -> str:
    values = ", ".join(f"'{m.value}'" for m in enum)
    return f"{column} IN ({values})"


class NotificationChannel(SQLModel, table=True):
    __tablename__ = "notification_channels"
    __table_args__ = (CheckConstraint(_check("type", ChannelType), name="type"),)

    id: uuid.UUID = Field(default_factory=new_uuid, primary_key=True)
    name: str = Field(max_length=64, unique=True)
    type: str = Field(max_length=16)
    config: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    secrets_enc: bytes | None = Field(default=None, sa_column=Column(LargeBinary, nullable=True))
    enabled: bool = Field(default=True)
    last_test_at: datetime | None = Field(
        default=None, sa_column=Column(UTCDateTime, nullable=True)
    )
    last_test_ok: bool | None = Field(default=None)
    last_test_message: str | None = Field(default=None, max_length=1000)
    created_at: datetime = Field(
        default_factory=utcnow, sa_column=Column(UTCDateTime, nullable=False)
    )
    updated_at: datetime = Field(
        default_factory=utcnow, sa_column=Column(UTCDateTime, nullable=False)
    )


class JobNotificationChannel(SQLModel, table=True):
    __tablename__ = "job_notification_channels"

    job_id: uuid.UUID = Field(
        sa_column=Column(
            Uuid, ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True, nullable=False
        )
    )
    notification_channel_id: uuid.UUID = Field(
        sa_column=Column(
            Uuid,
            ForeignKey("notification_channels.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        )
    )


class NotificationEvent(SQLModel, table=True):
    __tablename__ = "notification_events"
    __table_args__ = (
        CheckConstraint(_check("event_type", EventType), name="event_type"),
        CheckConstraint(_check("status", EventStatus), name="status"),
        Index("ix_notification_events_channel_id_created_at", "channel_id", "created_at"),
        Index("ix_notification_events_run_id", "run_id"),
    )

    id: uuid.UUID = Field(default_factory=new_uuid, primary_key=True)
    channel_id: uuid.UUID = Field(
        sa_column=Column(
            Uuid, ForeignKey("notification_channels.id", ondelete="CASCADE"), nullable=False
        )
    )
    run_id: uuid.UUID | None = Field(
        default=None,
        sa_column=Column(Uuid, ForeignKey("runs.id", ondelete="SET NULL"), nullable=True),
    )
    job_id: uuid.UUID | None = Field(
        default=None,
        sa_column=Column(Uuid, ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True),
    )
    event_type: str = Field(max_length=32)
    status: str = Field(max_length=16)
    error: str | None = Field(default=None, max_length=1000)
    attempts: int = Field(default=0)
    created_at: datetime = Field(
        default_factory=utcnow, sa_column=Column(UTCDateTime, nullable=False)
    )

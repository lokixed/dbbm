import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, Column, ForeignKey, LargeBinary, Uuid
from sqlmodel import Field, SQLModel

from app.models.base import UTCDateTime, new_uuid, utcnow


class NotifyOn(StrEnum):
    FAILURE = "failure"
    ALWAYS = "always"
    NEVER = "never"


class Job(SQLModel, table=True):
    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint("notify_on IN ('failure', 'always', 'never')", name="notify_on"),
    )

    id: uuid.UUID = Field(default_factory=new_uuid, primary_key=True)
    name: str = Field(max_length=64, unique=True)
    source_id: uuid.UUID = Field(
        sa_column=Column(Uuid, ForeignKey("sources.id", ondelete="RESTRICT"), nullable=False)
    )
    enabled: bool = Field(default=True)
    # 5-field cron expression; None means manual runs only.
    schedule: str | None = Field(default=None, max_length=100)
    timezone: str = Field(default="UTC", max_length=64)
    compress: bool = Field(default=True)
    timeout_seconds: int = Field(default=3600)
    notify_on: str = Field(default=NotifyOn.FAILURE.value, max_length=16)
    notify_on_recovery: bool = Field(default=True)
    # Encrypted heartbeat (dead-man's switch) ping URL.
    heartbeat_secret_enc: bytes | None = Field(
        default=None, sa_column=Column(LargeBinary, nullable=True)
    )
    last_scheduled_fire_at: datetime | None = Field(
        default=None, sa_column=Column(UTCDateTime, nullable=True)
    )
    overdue_notified_at: datetime | None = Field(
        default=None, sa_column=Column(UTCDateTime, nullable=True)
    )
    created_at: datetime = Field(
        default_factory=utcnow, sa_column=Column(UTCDateTime, nullable=False)
    )
    updated_at: datetime = Field(
        default_factory=utcnow, sa_column=Column(UTCDateTime, nullable=False)
    )


class JobDestination(SQLModel, table=True):
    __tablename__ = "job_destinations"

    job_id: uuid.UUID = Field(
        sa_column=Column(
            Uuid, ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True, nullable=False
        )
    )
    destination_id: uuid.UUID = Field(
        sa_column=Column(
            Uuid,
            ForeignKey("destinations.id", ondelete="RESTRICT"),
            primary_key=True,
            nullable=False,
        )
    )

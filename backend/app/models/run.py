import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, CheckConstraint, Column, ForeignKey, Index, Uuid
from sqlmodel import Field, SQLModel

from app.models.base import UTCDateTime, new_uuid, utcnow


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


ACTIVE_STATUSES = (RunStatus.QUEUED, RunStatus.RUNNING)
FINAL_STATUSES = (
    RunStatus.SUCCESS,
    RunStatus.PARTIAL,
    RunStatus.FAILED,
    RunStatus.CANCELLED,
    RunStatus.INTERRUPTED,
)


class RunTrigger(StrEnum):
    MANUAL = "manual"
    SCHEDULE = "schedule"
    RETRY = "retry"


def _check(column: str, enum: type[StrEnum]) -> str:
    values = ", ".join(f"'{m.value}'" for m in enum)
    return f"{column} IN ({values})"


class Run(SQLModel, table=True):
    __tablename__ = "runs"
    __table_args__ = (
        CheckConstraint(_check("trigger", RunTrigger), name="trigger"),
        CheckConstraint(_check("status", RunStatus), name="status"),
        Index("ix_runs_job_id_created_at", "job_id", "created_at"),
        Index("ix_runs_status", "status"),
        Index("ix_runs_parent_run_id", "parent_run_id"),
    )

    id: uuid.UUID = Field(default_factory=new_uuid, primary_key=True)
    # Nullable: deleting a job keeps its history, with the name snapshot below.
    job_id: uuid.UUID | None = Field(
        default=None,
        sa_column=Column(Uuid, ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True),
    )
    job_name: str = Field(max_length=64)
    # Set on retry runs: the partial run whose failed destinations this run re-uploads.
    parent_run_id: uuid.UUID | None = Field(
        default=None,
        sa_column=Column(Uuid, ForeignKey("runs.id", ondelete="SET NULL"), nullable=True),
    )
    trigger: str = Field(max_length=16)
    status: str = Field(max_length=16)
    # The intended fire time of a scheduled run.
    scheduled_for: datetime | None = Field(
        default=None, sa_column=Column(UTCDateTime, nullable=True)
    )
    started_at: datetime | None = Field(default=None, sa_column=Column(UTCDateTime, nullable=True))
    finished_at: datetime | None = Field(default=None, sa_column=Column(UTCDateTime, nullable=True))
    error: str | None = Field(default=None, max_length=2000)
    log_path: str | None = Field(default=None, max_length=512)
    step_results: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSON, nullable=False)
    )
    created_at: datetime = Field(
        default_factory=utcnow, sa_column=Column(UTCDateTime, nullable=False)
    )


class Artifact(SQLModel, table=True):
    __tablename__ = "artifacts"
    __table_args__ = (Index("ix_artifacts_run_id", "run_id"),)

    id: uuid.UUID = Field(default_factory=new_uuid, primary_key=True)
    run_id: uuid.UUID = Field(
        sa_column=Column(Uuid, ForeignKey("runs.id", ondelete="CASCADE"), nullable=False)
    )
    destination_id: uuid.UUID = Field(
        sa_column=Column(Uuid, ForeignKey("destinations.id", ondelete="RESTRICT"), nullable=False)
    )
    source_database: str = Field(max_length=255)
    path: str = Field(max_length=1024)
    size_bytes: int
    sha256: str = Field(max_length=64)
    # Remote MD5 as reported and verified by Google Drive; None for local copies.
    md5: str | None = Field(default=None, max_length=32)
    compressed: bool
    created_at: datetime = Field(
        default_factory=utcnow, sa_column=Column(UTCDateTime, nullable=False)
    )
    expires_at: datetime | None = Field(default=None, sa_column=Column(UTCDateTime, nullable=True))
    deleted_at: datetime | None = Field(default=None, sa_column=Column(UTCDateTime, nullable=True))
    # Last successful re-read of the stored copy (backups browser "Verify now").
    verified_at: datetime | None = Field(default=None, sa_column=Column(UTCDateTime, nullable=True))
    # Set when the stored file was not found at its destination (drift); cleared when it is back.
    missing_at: datetime | None = Field(default=None, sa_column=Column(UTCDateTime, nullable=True))

import uuid
from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from app.core.config import get_settings
from app.core.schedule import ScheduleError, normalize_cron, validate_timezone
from app.models.job import NotifyOn
from app.schemas.common import Name
from app.services.outbound import OutboundBlocked, check_url_static, validate_url

MAX_SCHEDULE_LENGTH = 100
MIN_TIMEOUT = 10
MAX_TIMEOUT = 7 * 24 * 3600


class JobIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Name
    source_id: uuid.UUID
    destination_ids: Annotated[list[uuid.UUID], Field(min_length=1, max_length=20)]
    enabled: bool = True
    # 5-field cron expression; None means manual runs only.
    schedule: str | None = None
    # IANA name; omitted means the default_timezone setting.
    timezone: str | None = None
    compress: bool = True
    timeout_seconds: Annotated[int, Field(strict=True, ge=MIN_TIMEOUT, le=MAX_TIMEOUT)] = 3600
    notify_on: NotifyOn = NotifyOn.FAILURE
    notify_on_recovery: bool = True
    notification_channel_ids: Annotated[list[uuid.UUID], Field(max_length=20)] = Field(
        default_factory=list
    )
    # Write-only: omit to keep the saved URL, null to clear it, a value to replace it.
    heartbeat_url: Annotated[SecretStr, Field(min_length=1, max_length=2048)] | None = None

    @field_validator("schedule")
    @classmethod
    def _schedule(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        if len(value.strip()) > MAX_SCHEDULE_LENGTH:
            raise ValueError(f"Schedule must be at most {MAX_SCHEDULE_LENGTH} characters.")
        try:
            return normalize_cron(value)
        except ScheduleError as exc:
            raise ValueError(str(exc)) from None

    @field_validator("timezone")
    @classmethod
    def _timezone(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        try:
            return validate_timezone(value).key
        except ScheduleError as exc:
            raise ValueError(str(exc)) from None

    @field_validator("heartbeat_url")
    @classmethod
    def _heartbeat(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return None
        url = validate_url(value.get_secret_value())
        try:
            check_url_static(url, get_settings().allow_private_notification_targets)
        except OutboundBlocked as exc:
            raise ValueError(str(exc)) from None
        return SecretStr(url)

    @field_validator("notification_channel_ids")
    @classmethod
    def _unique_channels(cls, value: list[uuid.UUID]) -> list[uuid.UUID]:
        return list(dict.fromkeys(value))

    @field_validator("destination_ids")
    @classmethod
    def _unique(cls, value: list[uuid.UUID]) -> list[uuid.UUID]:
        return list(dict.fromkeys(value))


class RefOut(BaseModel):
    id: str
    name: str


class SourceRefOut(RefOut):
    type: str


class RunSummaryOut(BaseModel):
    id: str
    job_id: str | None
    job_name: str
    trigger: str
    status: str
    started_at: datetime | None
    finished_at: datetime | None
    error: str | None
    created_at: datetime
    artifact_count: int = 0
    total_size: int = 0
    parent_run_id: str | None = None
    scheduled_for: datetime | None = None


class JobOut(BaseModel):
    id: str
    name: str
    source: SourceRefOut
    destinations: list[RefOut]
    enabled: bool
    schedule: str | None
    timezone: str
    schedule_error: str | None = None
    next_run_at: datetime | None = None
    compress: bool
    timeout_seconds: int
    notify_on: str
    notify_on_recovery: bool
    notification_channels: list[RefOut] = Field(default_factory=list)
    heartbeat_set: bool = False
    last_run: RunSummaryOut | None
    created_at: datetime
    updated_at: datetime


class ArtifactOut(BaseModel):
    id: str
    destination_id: str
    destination_name: str | None
    source_database: str
    path: str
    size_bytes: int
    sha256: str
    md5: str | None = None
    compressed: bool
    created_at: datetime


class RunNotificationOut(BaseModel):
    id: str
    channel_id: str
    channel_name: str | None
    event_type: str
    status: str
    attempts: int
    error: str | None
    created_at: datetime


class RunOut(RunSummaryOut):
    step_results: dict[str, Any]
    artifacts: list[ArtifactOut]
    retry_run_ids: list[str] = Field(default_factory=list)
    notifications: list[RunNotificationOut] = Field(default_factory=list)


class RunPage(BaseModel):
    items: list[RunSummaryOut]
    total: int


class DashboardJobOut(BaseModel):
    job_id: str
    job_name: str
    enabled: bool
    schedule: str | None = None
    next_run_at: datetime | None = None
    overdue: bool = False
    overdue_since: datetime | None = None
    skipped_schedules_24h: int = 0
    latest_run_id: str | None
    latest_status: str | None
    latest_finished_at: datetime | None
    last_success_at: datetime | None
    last_artifact_size: int | None


class DashboardDestinationOut(BaseModel):
    id: str
    name: str
    type: str
    auth_status: str
    auth_checked_at: datetime | None
    last_success_at: datetime | None
    last_error_class: str | None


class DashboardNextRunOut(BaseModel):
    job_id: str
    job_name: str
    next_run_at: datetime
    schedule: str
    timezone: str


class DashboardFailureOut(BaseModel):
    run_id: str
    job_id: str | None
    job_name: str
    status: str
    finished_at: datetime | None
    error: str | None


class DashboardDiskOut(BaseModel):
    key: str
    label: str
    kind: str
    destination_id: str | None
    total_bytes: int
    used_bytes: int
    free_bytes: int
    free_percent: float
    below_threshold: bool


class DashboardSchedulerOut(BaseModel):
    state: str
    owner: bool


class DashboardOut(BaseModel):
    jobs: list[DashboardJobOut]
    destinations: list[DashboardDestinationOut]
    next_runs: list[DashboardNextRunOut] = Field(default_factory=list)
    failures_24h: list[DashboardFailureOut] = Field(default_factory=list)
    disks: list[DashboardDiskOut] = Field(default_factory=list)
    low_disk_threshold_percent: int = 10
    scheduler: DashboardSchedulerOut | None = None

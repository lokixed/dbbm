from datetime import datetime

from pydantic import BaseModel


class RetentionCopyOut(BaseModel):
    run_id: str
    job_name: str
    status: str
    started_at: datetime
    counted: bool
    protected: bool
    artifacts: list[str]
    bytes: int


class RetentionPreviewOut(BaseModel):
    keep_last: int | None
    keep_days: int | None
    delete: list[RetentionCopyOut]
    keep: list[RetentionCopyOut]
    delete_runs: int
    delete_artifacts: int
    delete_bytes: int


class RetentionFailureOut(BaseModel):
    path: str
    error: str


class RetentionRunOut(BaseModel):
    destination_id: str
    deleted_runs: int
    deleted_artifacts: int
    deleted_bytes: int
    failures: list[RetentionFailureOut]
    skipped: str | None

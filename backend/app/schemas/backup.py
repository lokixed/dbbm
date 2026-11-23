from datetime import datetime

from pydantic import BaseModel


class BackupOut(BaseModel):
    id: str
    run_id: str
    job_id: str | None
    job_name: str
    source_id: str | None
    source_name: str | None
    source_database: str
    destination_id: str
    destination_name: str | None
    destination_type: str | None
    path: str
    size_bytes: int
    sha256: str
    md5: str | None = None
    compressed: bool
    created_at: datetime
    # None until "Verify now" has re-read the copy; it was verified when it was uploaded.
    verified_at: datetime | None = None
    missing: bool = False


class BackupPage(BaseModel):
    items: list[BackupOut]
    total: int


class VerifyOut(BaseModel):
    status: str  # ok | missing | corrupt
    detail: str
    verified_at: datetime | None = None


class RescanOut(BaseModel):
    destination_id: str
    checked: int
    missing: int

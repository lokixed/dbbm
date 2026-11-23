import uuid
from datetime import datetime

from pydantic import BaseModel, Field, SecretStr


class BackupOut(BaseModel):
    id: str
    run_id: str
    job_id: str | None
    job_name: str
    source_id: str | None
    source_name: str | None
    source_type: str | None = None
    source_database: str
    destination_id: str
    destination_name: str | None
    destination_type: str | None
    path: str
    size_bytes: int
    sha256: str
    md5: str | None = None
    compressed: bool
    # The stored file is age ciphertext; these are the fingerprints of the keys that open it.
    encrypted: bool = False
    recipients: list[str] = Field(default_factory=list)
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


class RestoreIn(BaseModel):
    target_source_id: uuid.UUID
    # Safe for every engine and for command-line arguments.
    target_database: str = Field(pattern=r"^[A-Za-z0-9_][A-Za-z0-9_-]{0,62}$")
    drop: bool = False
    # The user must type the target database name.
    confirm_database: str
    # Needed only when the restore would overwrite the original database.
    confirm_overwrite_original: bool = False
    # Needed only for an encrypted backup: the age private key. Used in memory for this restore;
    # never stored, logged or echoed.
    private_key: SecretStr | None = Field(default=None, max_length=4096)

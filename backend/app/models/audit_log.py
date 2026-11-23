import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel

from app.models.base import UTCDateTime, new_uuid, utcnow


class AuditLog(SQLModel, table=True):
    __tablename__ = "audit_log"

    id: uuid.UUID = Field(default_factory=new_uuid, primary_key=True)
    timestamp: datetime = Field(
        default_factory=utcnow, sa_column=Column(UTCDateTime, nullable=False, index=True)
    )
    user_id: uuid.UUID | None = Field(default=None, foreign_key="users.id", ondelete="SET NULL")
    username: str | None = Field(default=None, max_length=64)
    action: str = Field(max_length=64, index=True)
    object_type: str | None = Field(default=None, max_length=64)
    object_id: str | None = Field(default=None, max_length=64)
    details: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    ip: str | None = Field(default=None, max_length=64)

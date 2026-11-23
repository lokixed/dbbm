import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, CheckConstraint, Column, LargeBinary
from sqlmodel import Field, SQLModel

from app.models.base import UTCDateTime, new_uuid, utcnow


class SourceType(StrEnum):
    MYSQL = "mysql"
    POSTGRES = "postgres"
    MONGO = "mongo"
    FIRESTORE = "firestore"


def _type_check() -> str:
    values = ", ".join(f"'{t.value}'" for t in SourceType)
    return f"type IN ({values})"


class Source(SQLModel, table=True):
    __tablename__ = "sources"
    __table_args__ = (CheckConstraint(_type_check(), name="type"),)

    id: uuid.UUID = Field(default_factory=new_uuid, primary_key=True)
    name: str = Field(max_length=64, unique=True)
    type: str = Field(max_length=16)
    config: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    secrets_enc: bytes | None = Field(default=None, sa_column=Column(LargeBinary, nullable=True))
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

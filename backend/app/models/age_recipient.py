import uuid
from datetime import datetime

from sqlalchemy import Column, ForeignKey, Uuid
from sqlmodel import Field, SQLModel

from app.models.base import UTCDateTime, new_uuid, utcnow


class AgeRecipient(SQLModel, table=True):
    """An `age` public key that backups can be encrypted to. Only public keys are stored."""

    __tablename__ = "age_recipients"

    id: uuid.UUID = Field(default_factory=new_uuid, primary_key=True)
    name: str = Field(max_length=64, unique=True)
    public_key: str = Field(max_length=128, unique=True)
    fingerprint: str = Field(max_length=32)
    created_at: datetime = Field(
        default_factory=utcnow, sa_column=Column(UTCDateTime, nullable=False)
    )


class JobRecipient(SQLModel, table=True):
    __tablename__ = "job_recipients"

    job_id: uuid.UUID = Field(
        sa_column=Column(
            Uuid, ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True, nullable=False
        )
    )
    recipient_id: uuid.UUID = Field(
        sa_column=Column(
            Uuid,
            ForeignKey("age_recipients.id", ondelete="RESTRICT"),
            primary_key=True,
            nullable=False,
        )
    )

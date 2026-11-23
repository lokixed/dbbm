import uuid
from datetime import datetime

from sqlalchemy import Column
from sqlmodel import Field, SQLModel

from app.models.base import UTCDateTime, new_uuid, utcnow


class User(SQLModel, table=True):
    __tablename__ = "users"

    id: uuid.UUID = Field(default_factory=new_uuid, primary_key=True)
    username: str = Field(max_length=64, unique=True)
    password_hash: str
    session_version: int = Field(default=0)
    created_at: datetime = Field(
        default_factory=utcnow, sa_column=Column(UTCDateTime, nullable=False)
    )

"""Read-only audit log."""

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import ColumnElement, func
from sqlmodel import col, select

from app.api.deps import CurrentUser
from app.core.db import SessionDep
from app.models import AuditLog

router = APIRouter(prefix="/api/audit", tags=["audit"])


class AuditEntryOut(BaseModel):
    id: str
    timestamp: datetime
    username: str | None
    action: str
    object_type: str | None
    object_id: str | None
    details: dict[str, Any]
    ip: str | None


class AuditPage(BaseModel):
    items: list[AuditEntryOut]
    total: int
    actions: list[str]


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@router.get("")
def list_audit(
    db: SessionDep,
    _user: CurrentUser,
    action: Annotated[str | None, Query(max_length=64)] = None,
    username: Annotated[str | None, Query(max_length=64)] = None,
    search: Annotated[str | None, Query(max_length=100)] = None,
    date_from: Annotated[datetime | None, Query(alias="from")] = None,
    date_to: Annotated[datetime | None, Query(alias="to")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AuditPage:
    conditions: list[ColumnElement[bool]] = []
    if action:
        conditions.append(col(AuditLog.action) == action)
    if username:
        conditions.append(col(AuditLog.username) == username)
    if search:
        like = f"%{_escape_like(search)}%"
        conditions.append(
            col(AuditLog.action).like(like, escape="\\")
            | col(AuditLog.object_id).like(like, escape="\\")
        )
    if start := _utc(date_from):
        conditions.append(col(AuditLog.timestamp) >= start)
    if end := _utc(date_to):
        conditions.append(col(AuditLog.timestamp) <= end)
    total = db.exec(select(func.count()).select_from(AuditLog).where(*conditions)).one()
    rows = db.exec(
        select(AuditLog)
        .where(*conditions)
        .order_by(col(AuditLog.timestamp).desc(), col(AuditLog.id))
        .offset(offset)
        .limit(limit)
    ).all()
    actions = db.exec(select(AuditLog.action).distinct().order_by(col(AuditLog.action))).all()
    return AuditPage(
        items=[
            AuditEntryOut(
                id=str(r.id),
                timestamp=r.timestamp,
                username=r.username,
                action=r.action,
                object_type=r.object_type,
                object_id=r.object_id,
                details=r.details,
                ip=r.ip,
            )
            for r in rows
        ],
        total=int(total),
        actions=list(actions),
    )

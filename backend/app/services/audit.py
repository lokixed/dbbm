from typing import Any

from sqlmodel import Session

from app.models import AuditLog, User


def write_audit(
    session: Session,
    action: str,
    *,
    user: User | None = None,
    username: str | None = None,
    object_type: str | None = None,
    object_id: str | None = None,
    details: dict[str, Any] | None = None,
    ip: str | None = None,
) -> None:
    """Stage an audit entry; the caller commits. Never put secrets in `details`."""
    session.add(
        AuditLog(
            action=action,
            user_id=user.id if user else None,
            username=(user.username if user else username),
            object_type=object_type,
            object_id=object_id,
            details=details or {},
            ip=ip,
        )
    )

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request, Response
from sqlalchemy import func
from sqlmodel import Session, col, select

from app.api.common import (
    commit_or_conflict,
    invalid_field,
    invalid_secret,
    last_test_out,
    name_conflict,
    record_test,
    undecryptable,
)
from app.api.deps import CurrentUser, ServicesDep, client_ip
from app.core.config import get_settings
from app.core.db import SessionDep
from app.models import Job, JobNotificationChannel, NotificationChannel, NotificationEvent
from app.models.base import utcnow
from app.schemas.common import ConfigBase, SecretsInvalid
from app.schemas.job import RefOut
from app.schemas.notification import (
    ChannelIn,
    ChannelOut,
    EmailChannelIn,
    EventOut,
    EventPage,
    TestSendOut,
    dump_channel_config,
    parse_channel_config,
    parse_headers,
    secret_values,
)
from app.services.audit import write_audit
from app.services.messages import channel_test_message
from app.services.notifier import forget_channel_secrets, register_channel_secrets
from app.services.outbound import OutboundBlocked, check_host_static, check_url_static
from app.services.secrets import (
    UNDECRYPTABLE_MESSAGE,
    SecretsUnavailable,
    check_required,
    load_secrets,
    merge_values,
    store_secrets,
)

router = APIRouter(prefix="/api/notifications", tags=["notifications"])

URL_SECRETS = ("webhook_url", "url")
INSECURE_EMAIL_WARNING = (
    "Email security is 'none': messages and the SMTP password travel unencrypted. "
    "Use this only on a trusted network."
)


def _jobs(db: Session, channel_id: uuid.UUID) -> list[RefOut]:
    rows = db.exec(
        select(Job.id, Job.name)
        .join(JobNotificationChannel, col(JobNotificationChannel.job_id) == col(Job.id))
        .where(col(JobNotificationChannel.notification_channel_id) == channel_id)
        .order_by(col(Job.name))
    ).all()
    return [RefOut(id=str(i), name=n) for i, n in rows]


def _to_out(db: Session, row: NotificationChannel) -> ChannelOut:
    config = parse_channel_config(row.type, row.config)
    error: str | None = None
    try:
        stored = load_secrets(row.secrets_enc)
    except SecretsUnavailable:
        stored, error = {}, UNDECRYPTABLE_MESSAGE
    warnings: list[str] = []
    if row.type == "email" and row.config.get("security") == "none":
        warnings.append(INSECURE_EMAIL_WARNING)
    return ChannelOut(
        id=str(row.id),
        name=row.name,
        type=row.type,
        enabled=row.enabled,
        config=row.config,
        secrets_set={f: f in stored for f in config.secret_fields()},
        secrets_error=error,
        header_names=sorted(parse_headers(stored.get("headers"))),
        warnings=warnings,
        last_test=last_test_out(row),
        jobs=_jobs(db, row.id),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _event_out(event: NotificationEvent, channel_name: str | None = None) -> EventOut:
    return EventOut(
        id=str(event.id),
        channel_id=str(event.channel_id),
        channel_name=channel_name,
        run_id=str(event.run_id) if event.run_id else None,
        job_id=str(event.job_id) if event.job_id else None,
        event_type=event.event_type,
        status=event.status,
        error=event.error,
        attempts=event.attempts,
        created_at=event.created_at,
    )


def _get_or_404(db: Session, channel_id: uuid.UUID) -> NotificationChannel:
    row = db.get(NotificationChannel, channel_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Notification channel not found")
    return row


def _name_taken(db: Session, name: str, exclude: uuid.UUID | None = None) -> bool:
    found = db.exec(
        select(NotificationChannel.id).where(col(NotificationChannel.name) == name)
    ).first()
    return found is not None and found != exclude


def _check_targets(config: ConfigBase, secrets: dict[str, str], body: ChannelIn) -> None:
    allow_private = get_settings().allow_private_notification_targets
    for field in URL_SECRETS:
        if secrets.get(field):
            try:
                check_url_static(secrets[field], allow_private)
            except OutboundBlocked as exc:
                raise invalid_secret(SecretsInvalid(field, str(exc))) from None
    if isinstance(body, EmailChannelIn):
        try:
            check_host_static(body.config.host, allow_private)
        except OutboundBlocked as exc:
            raise invalid_field(["config", "host"], str(exc)) from None


def _merged(existing: dict[str, str], body: ChannelIn) -> dict[str, str]:
    config = body.config
    try:
        merged = merge_values(existing, secret_values(body.secrets), config.secret_fields())
        check_required(merged, config.required_secrets())
        config.check_secrets(merged)
    except SecretsInvalid as exc:
        raise invalid_secret(exc) from None
    _check_targets(config, merged, body)
    return merged


def _existing(row: NotificationChannel, body: ChannelIn) -> dict[str, str]:
    try:
        return load_secrets(row.secrets_enc)
    except SecretsUnavailable:
        required = set(body.config.required_secrets())
        if required <= body.secrets.model_fields_set:
            return {}
        raise undecryptable() from None


def _changes(
    secrets_before: dict[str, str], merged: dict[str, str], body: ChannelIn
) -> dict[str, Any]:
    provided = set(secret_values(body.secrets))
    return {
        "secrets_replaced": sorted(f for f in provided if f in merged),
        "secrets_cleared": sorted(f for f in provided if f not in merged and f in secrets_before),
    }


@router.get("")
def list_channels(db: SessionDep, _user: CurrentUser) -> list[ChannelOut]:
    rows = db.exec(select(NotificationChannel).order_by(col(NotificationChannel.name))).all()
    return [_to_out(db, r) for r in rows]


@router.post("", status_code=201)
def create_channel(
    body: ChannelIn, request: Request, db: SessionDep, user: CurrentUser
) -> ChannelOut:
    if _name_taken(db, body.name):
        raise name_conflict()
    merged = _merged({}, body)
    row = NotificationChannel(
        name=body.name,
        type=body.type,
        enabled=body.enabled,
        config=dump_channel_config(body.config),
        secrets_enc=store_secrets(merged),
    )
    db.add(row)
    write_audit(
        db,
        "notification.create",
        user=user,
        object_type="notification_channel",
        object_id=str(row.id),
        details={"name": row.name, "type": row.type, "enabled": row.enabled},
        ip=client_ip(request),
    )
    commit_or_conflict(db)
    register_channel_secrets(row.id, row.type, merged)
    return _to_out(db, row)


@router.get("/{channel_id}")
def get_channel(channel_id: uuid.UUID, db: SessionDep, _user: CurrentUser) -> ChannelOut:
    return _to_out(db, _get_or_404(db, channel_id))


@router.put("/{channel_id}")
def update_channel(
    channel_id: uuid.UUID,
    body: ChannelIn,
    request: Request,
    db: SessionDep,
    user: CurrentUser,
) -> ChannelOut:
    row = _get_or_404(db, channel_id)
    if body.type != row.type:
        raise invalid_field(["type"], "Channel type cannot be changed.")
    if _name_taken(db, body.name, exclude=row.id):
        raise name_conflict()
    existing = _existing(row, body)
    merged = _merged(existing, body)
    new_config = dump_channel_config(body.config)
    details: dict[str, Any] = {
        "name": body.name,
        "type": row.type,
        "enabled": body.enabled,
        "config_changed": sorted(
            k
            for k in row.config.keys() | new_config.keys()
            if row.config.get(k) != new_config.get(k)
        ),
        **_changes(existing, merged, body),
    }
    row.name = body.name
    row.enabled = body.enabled
    row.config = new_config
    row.secrets_enc = store_secrets(merged)
    row.updated_at = utcnow()
    db.add(row)
    write_audit(
        db,
        "notification.update",
        user=user,
        object_type="notification_channel",
        object_id=str(row.id),
        details=details,
        ip=client_ip(request),
    )
    commit_or_conflict(db)
    register_channel_secrets(row.id, row.type, merged)
    return _to_out(db, row)


@router.delete("/{channel_id}", status_code=204)
def delete_channel(
    channel_id: uuid.UUID, request: Request, db: SessionDep, user: CurrentUser
) -> Response:
    row = _get_or_404(db, channel_id)
    jobs = [j.name for j in _jobs(db, row.id)]
    write_audit(
        db,
        "notification.delete",
        user=user,
        object_type="notification_channel",
        object_id=str(row.id),
        details={"name": row.name, "type": row.type, "removed_from_jobs": jobs},
        ip=client_ip(request),
    )
    db.delete(row)  # the job links and delivery events cascade
    db.commit()
    forget_channel_secrets(channel_id)
    return Response(status_code=204)


@router.post("/{channel_id}/test")
async def test_channel(
    channel_id: uuid.UUID,
    request: Request,
    db: SessionDep,
    user: CurrentUser,
    services: ServicesDep,
) -> TestSendOut:
    row = _get_or_404(db, channel_id)
    ok, message, event_id = await services.notifier.send_test(
        row.id, channel_test_message(row.name)
    )
    db.refresh(row)
    record_test(row, ok, message)
    db.add(row)
    write_audit(
        db,
        "notification.test",
        user=user,
        object_type="notification_channel",
        object_id=str(row.id),
        details={"name": row.name, "type": row.type, "ok": ok},
        ip=client_ip(request),
    )
    db.commit()
    event = db.get(NotificationEvent, event_id)
    return TestSendOut(ok=ok, message=message, event=_event_out(event, row.name) if event else None)


@router.get("/{channel_id}/events")
def list_events(
    channel_id: uuid.UUID,
    db: SessionDep,
    _user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> EventPage:
    row = _get_or_404(db, channel_id)
    condition = col(NotificationEvent.channel_id) == row.id
    total = db.exec(select(func.count()).select_from(NotificationEvent).where(condition)).one()
    events = db.exec(
        select(NotificationEvent)
        .where(condition)
        .order_by(col(NotificationEvent.created_at).desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return EventPage(items=[_event_out(e, row.name) for e in events], total=int(total))

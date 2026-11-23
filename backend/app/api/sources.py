import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from sqlmodel import Session, col, select

from app.api.common import (
    commit_or_conflict,
    invalid_field,
    invalid_secret,
    last_test_out,
    name_conflict,
    record_test,
    test_out,
    undecryptable,
)
from app.api.deps import CurrentUser, client_ip
from app.core.db import SessionDep
from app.drivers import get_driver
from app.models import Job, Source
from app.models.base import utcnow
from app.schemas.common import SecretsInvalid, TestResultOut
from app.schemas.source import SourceIn, SourceOut, parse_source_config
from app.services.audit import write_audit
from app.services.secrets import (
    UNDECRYPTABLE_MESSAGE,
    SecretsUnavailable,
    check_required,
    load_secrets,
    merge_secrets,
    store_secrets,
)

router = APIRouter(prefix="/api/sources", tags=["sources"])


def _to_out(row: Source) -> SourceOut:
    config = parse_source_config(row.type, row.config)
    error: str | None = None
    try:
        stored = load_secrets(row.secrets_enc)
    except SecretsUnavailable:
        stored, error = {}, UNDECRYPTABLE_MESSAGE
    return SourceOut(
        id=str(row.id),
        name=row.name,
        type=row.type,
        config=row.config,
        secrets_set={f: f in stored for f in config.secret_fields()},
        secrets_error=error,
        last_test=last_test_out(row),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _get_or_404(db: Session, source_id: uuid.UUID) -> Source:
    row = db.get(Source, source_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Source not found")
    return row


def _name_taken(db: Session, name: str, exclude: uuid.UUID | None = None) -> bool:
    query = select(Source.id).where(col(Source.name) == name)
    found = db.exec(query).first()
    return found is not None and found != exclude


def _merged_secrets(existing: dict[str, str], body: SourceIn) -> dict[str, str]:
    try:
        merged = merge_secrets(existing, body.secrets, body.config.secret_fields())
        check_required(merged, body.config.required_secrets())
        body.config.check_secrets(merged)
    except SecretsInvalid as exc:
        raise invalid_secret(exc) from None
    return merged


@router.get("")
def list_sources(db: SessionDep, _user: CurrentUser) -> list[SourceOut]:
    rows = db.exec(select(Source).order_by(col(Source.name))).all()
    return [_to_out(r) for r in rows]


@router.post("", status_code=201)
def create_source(body: SourceIn, request: Request, db: SessionDep, user: CurrentUser) -> SourceOut:
    if _name_taken(db, body.name):
        raise name_conflict()
    merged = _merged_secrets({}, body)
    row = Source(
        name=body.name,
        type=body.type,
        config=body.config.model_dump(mode="json"),
        secrets_enc=store_secrets(merged),
    )
    db.add(row)
    write_audit(
        db,
        "source.create",
        user=user,
        object_type="source",
        object_id=str(row.id),
        details={"name": row.name, "type": row.type},
        ip=client_ip(request),
    )
    commit_or_conflict(db)
    return _to_out(row)


@router.get("/{source_id}")
def get_source(source_id: uuid.UUID, db: SessionDep, _user: CurrentUser) -> SourceOut:
    return _to_out(_get_or_404(db, source_id))


@router.put("/{source_id}")
def update_source(
    source_id: uuid.UUID, body: SourceIn, request: Request, db: SessionDep, user: CurrentUser
) -> SourceOut:
    row = _get_or_404(db, source_id)
    if body.type != row.type:
        raise invalid_field(["type"], "Source type cannot be changed.")
    if _name_taken(db, body.name, exclude=row.id):
        raise name_conflict()

    try:
        existing = load_secrets(row.secrets_enc)
    except SecretsUnavailable:
        # Re-entering every secret is the only way out of an undecryptable blob.
        if not set(body.config.secret_fields()) <= body.secrets.model_fields_set:
            raise undecryptable() from None
        existing = {}
    merged = _merged_secrets(existing, body)

    new_config: dict[str, Any] = body.config.model_dump(mode="json")
    keys = sorted(
        k for k in row.config.keys() | new_config.keys() if row.config.get(k) != new_config.get(k)
    )
    provided = body.secrets.model_fields_set & set(body.config.secret_fields())
    details = {
        "name": body.name,
        "type": row.type,
        "config_changed": keys,
        "secrets_replaced": sorted(f for f in provided if f in merged),
        "secrets_cleared": sorted(f for f in provided if f not in merged and f in existing),
    }

    row.name = body.name
    row.config = new_config
    row.secrets_enc = store_secrets(merged)
    row.updated_at = utcnow()
    db.add(row)
    write_audit(
        db,
        "source.update",
        user=user,
        object_type="source",
        object_id=str(row.id),
        details=details,
        ip=client_ip(request),
    )
    commit_or_conflict(db)
    return _to_out(row)


@router.delete("/{source_id}", status_code=204)
def delete_source(
    source_id: uuid.UUID, request: Request, db: SessionDep, user: CurrentUser
) -> Response:
    row = _get_or_404(db, source_id)
    names = db.exec(
        select(Job.name).where(col(Job.source_id) == row.id).order_by(col(Job.name))
    ).all()
    if names:
        raise HTTPException(
            status_code=409,
            detail=(
                f"This source is used by job(s): {', '.join(names)}. "
                "Delete or change those jobs first."
            ),
        )
    write_audit(
        db,
        "source.delete",
        user=user,
        object_type="source",
        object_id=str(row.id),
        details={"name": row.name, "type": row.type},
        ip=client_ip(request),
    )
    db.delete(row)
    db.commit()
    return Response(status_code=204)


@router.post("/{source_id}/test")
def test_source(
    source_id: uuid.UUID, request: Request, db: SessionDep, user: CurrentUser
) -> TestResultOut:
    row = _get_or_404(db, source_id)
    try:
        secrets = load_secrets(row.secrets_enc)
    except SecretsUnavailable:
        raise undecryptable() from None

    result = get_driver(row.type, row.config, secrets).test_connection()
    record_test(row, result.ok, result.message)
    db.add(row)
    write_audit(
        db,
        "source.test",
        user=user,
        object_type="source",
        object_id=str(row.id),
        details={"name": row.name, "type": row.type, "ok": result.ok},
        ip=client_ip(request),
    )
    db.commit()
    return test_out(result)

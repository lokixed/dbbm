import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from sqlalchemy import func
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
from app.core.config import get_settings
from app.core.db import SessionDep
from app.models import Artifact, AuthStatus, Destination, Job, JobDestination
from app.models.base import utcnow
from app.schemas.common import SecretsInvalid, TestResultOut
from app.schemas.destination import (
    DestinationIn,
    DestinationOut,
    GdriveDestinationIn,
    LocalDestinationIn,
    Retention,
    normalize_gdrive_secrets,
    parse_destination_config,
)
from app.services.audit import write_audit
from app.services.destination_tokens import DestinationTokenStore
from app.services.paths import PathNotAllowed, resolve_inside, to_relative
from app.services.secrets import (
    UNDECRYPTABLE_MESSAGE,
    SecretsUnavailable,
    check_required,
    load_secrets,
    merge_secrets,
    store_secrets,
)
from app.storage import StorageContext, get_storage

TEST_TIMEOUT = 120

router = APIRouter(prefix="/api/destinations", tags=["destinations"])


def _to_out(row: Destination) -> DestinationOut:
    fields = parse_destination_config(row.type, row.config).secret_fields()
    error: str | None = None
    try:
        stored = load_secrets(row.secrets_enc) if fields else {}
    except SecretsUnavailable:
        stored, error = {}, UNDECRYPTABLE_MESSAGE
    return DestinationOut(
        id=str(row.id),
        name=row.name,
        type=row.type,
        config=row.config,
        retention=Retention.model_validate(row.retention),
        secrets_set={f: f in stored for f in fields},
        secrets_error=error,
        auth_status=row.auth_status,
        auth_checked_at=row.auth_checked_at,
        last_test=last_test_out(row),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _get_or_404(db: Session, destination_id: uuid.UUID) -> Destination:
    row = db.get(Destination, destination_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Destination not found")
    return row


def _name_taken(db: Session, name: str, exclude: uuid.UUID | None = None) -> bool:
    found = db.exec(select(Destination.id).where(col(Destination.name) == name)).first()
    return found is not None and found != exclude


def _normalised_config(body: DestinationIn) -> dict[str, Any]:
    if isinstance(body, GdriveDestinationIn):
        return body.config.model_dump(mode="json")
    return _local_config(body)


def _local_config(body: LocalDestinationIn) -> dict[str, Any]:
    """Store the path relative to BACKUP_DIR; reject escapes at save time as well as test time."""
    base = get_settings().backup_dir
    try:
        relative = to_relative(body.config.path, base)
        resolve_inside(base, relative)
    except PathNotAllowed as exc:
        raise invalid_field(["config", "path"], str(exc)) from None
    config: dict[str, Any] = body.config.model_dump(mode="json")
    config["path"] = relative
    return config


def _merged_secrets(existing: dict[str, str], body: DestinationIn) -> dict[str, str]:
    if not isinstance(body, GdriveDestinationIn):
        return {}
    try:
        merged = merge_secrets(existing, body.secrets, body.config.secret_fields())
        merged = normalize_gdrive_secrets(merged)
        check_required(merged, body.config.required_secrets())
        body.config.check_secrets(merged)
    except SecretsInvalid as exc:
        raise invalid_secret(exc) from None
    return merged


def _existing_secrets(row: Destination, body: DestinationIn) -> dict[str, str]:
    try:
        return load_secrets(row.secrets_enc)
    except SecretsUnavailable:
        # Re-entering every secret is the only way out of an undecryptable blob.
        if isinstance(body, GdriveDestinationIn) and "token" in body.secrets.model_fields_set:
            return {}
        raise undecryptable() from None


@router.get("")
def list_destinations(db: SessionDep, _user: CurrentUser) -> list[DestinationOut]:
    rows = db.exec(select(Destination).order_by(col(Destination.name))).all()
    return [_to_out(r) for r in rows]


@router.post("", status_code=201)
def create_destination(
    body: DestinationIn, request: Request, db: SessionDep, user: CurrentUser
) -> DestinationOut:
    if _name_taken(db, body.name):
        raise name_conflict()
    row = Destination(
        name=body.name,
        type=body.type,
        config=_normalised_config(body),
        secrets_enc=store_secrets(_merged_secrets({}, body)),
        retention=body.retention.model_dump(),
    )
    db.add(row)
    write_audit(
        db,
        "destination.create",
        user=user,
        object_type="destination",
        object_id=str(row.id),
        details={"name": row.name, "type": row.type},
        ip=client_ip(request),
    )
    commit_or_conflict(db)
    return _to_out(row)


@router.get("/{destination_id}")
def get_destination(
    destination_id: uuid.UUID, db: SessionDep, _user: CurrentUser
) -> DestinationOut:
    return _to_out(_get_or_404(db, destination_id))


@router.put("/{destination_id}")
def update_destination(
    destination_id: uuid.UUID,
    body: DestinationIn,
    request: Request,
    db: SessionDep,
    user: CurrentUser,
) -> DestinationOut:
    row = _get_or_404(db, destination_id)
    if body.type != row.type:
        raise invalid_field(["type"], "Destination type cannot be changed.")
    if _name_taken(db, body.name, exclude=row.id):
        raise name_conflict()

    new_config = _normalised_config(body)
    new_retention = body.retention.model_dump()
    existing = _existing_secrets(row, body) if row.type == "gdrive" else {}
    merged = _merged_secrets(existing, body)
    config_changed = sorted(
        k for k in row.config.keys() | new_config.keys() if row.config.get(k) != new_config.get(k)
    )
    details: dict[str, Any] = {
        "name": body.name,
        "type": row.type,
        "config_changed": config_changed,
        "retention_changed": row.retention != new_retention,
    }
    if isinstance(body, GdriveDestinationIn):
        provided = body.secrets.model_fields_set & set(body.config.secret_fields())
        details["secrets_replaced"] = sorted(f for f in provided if f in merged)
        details["secrets_cleared"] = sorted(
            f for f in provided if f not in merged and f in existing
        )
        if provided or config_changed:
            # New credentials or settings: forget the old verdict until the next test or run.
            row.auth_status = AuthStatus.UNKNOWN
            row.auth_checked_at = None
        row.secrets_enc = store_secrets(merged)

    row.name = body.name
    row.config = new_config
    row.retention = new_retention
    row.updated_at = utcnow()
    db.add(row)
    write_audit(
        db,
        "destination.update",
        user=user,
        object_type="destination",
        object_id=str(row.id),
        details=details,
        ip=client_ip(request),
    )
    commit_or_conflict(db)
    return _to_out(row)


@router.delete("/{destination_id}", status_code=204)
def delete_destination(
    destination_id: uuid.UUID, request: Request, db: SessionDep, user: CurrentUser
) -> Response:
    row = _get_or_404(db, destination_id)
    names = db.exec(
        select(Job.name)
        .join(JobDestination, col(JobDestination.job_id) == col(Job.id))
        .where(col(JobDestination.destination_id) == row.id)
        .order_by(col(Job.name))
    ).all()
    if names:
        raise HTTPException(
            status_code=409,
            detail=(
                f"This destination is used by job(s): {', '.join(names)}. "
                "Remove it from those jobs first."
            ),
        )
    stored = db.exec(
        select(func.count())
        .select_from(Artifact)
        .where(col(Artifact.destination_id) == row.id, col(Artifact.deleted_at).is_(None))
    ).one()
    if stored:
        raise HTTPException(
            status_code=409,
            detail=f"This destination holds {stored} recorded backup(s) and cannot be deleted.",
        )
    write_audit(
        db,
        "destination.delete",
        user=user,
        object_type="destination",
        object_id=str(row.id),
        details={"name": row.name, "type": row.type},
        ip=client_ip(request),
    )
    db.delete(row)
    db.commit()
    return Response(status_code=204)


@router.post("/test")
async def test_unsaved_destination(
    body: DestinationIn,
    request: Request,
    db: SessionDep,
    user: CurrentUser,
    destination_id: uuid.UUID | None = None,
) -> TestResultOut:
    """Test the settings in a form without saving anything. With `destination_id` (editing a
    saved destination) the stored secrets fill in any secret the form leaves unchanged. Nothing
    is written back: a refreshed Drive token or a new auth status is not stored."""
    existing: dict[str, str] = {}
    if destination_id is not None:
        row = _get_or_404(db, destination_id)
        if body.type != row.type:
            raise invalid_field(["type"], "Destination type cannot be changed.")
        existing = _existing_secrets(row, body) if row.type == "gdrive" else {}
    merged = _merged_secrets(existing, body)
    context = StorageContext(timeout_seconds=TEST_TIMEOUT)
    result = await get_storage(body.type, _normalised_config(body), merged, context).test()
    write_audit(
        db,
        "destination.test",
        user=user,
        object_type="destination",
        object_id=str(destination_id) if destination_id else None,
        details={"name": body.name, "type": body.type, "ok": result.ok, "saved": False},
        ip=client_ip(request),
    )
    db.commit()
    return test_out(result)


@router.post("/{destination_id}/test")
async def test_destination(
    destination_id: uuid.UUID, request: Request, db: SessionDep, user: CurrentUser
) -> TestResultOut:
    row = _get_or_404(db, destination_id)
    context = StorageContext(
        destination_id=row.id,
        timeout_seconds=TEST_TIMEOUT,
        token_store=DestinationTokenStore(row.id) if row.type == "gdrive" else None,
    )
    if row.type == "gdrive":
        try:
            load_secrets(row.secrets_enc)
        except SecretsUnavailable:
            raise undecryptable() from None
    result = await get_storage(row.type, row.config, {}, context).test()
    db.refresh(row)  # the test may have stored a refreshed token or a new auth status
    record_test(row, result.ok, result.message)
    db.add(row)
    write_audit(
        db,
        "destination.test",
        user=user,
        object_type="destination",
        object_id=str(row.id),
        details={"name": row.name, "type": row.type, "ok": result.ok},
        ip=client_ip(request),
    )
    db.commit()
    return test_out(result)

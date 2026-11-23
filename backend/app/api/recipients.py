"""Backup encryption keys: the age public keys (recipients) that backups can be encrypted to."""

import uuid

from fastapi import APIRouter, HTTPException, Request, Response
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from app.api.deps import CurrentUser, client_ip
from app.core.db import SessionDep
from app.models import AgeRecipient, Job, JobRecipient
from app.schemas.recipient import RecipientIn, RecipientOut
from app.services.age import fingerprint
from app.services.audit import write_audit

router = APIRouter(prefix="/api/recipients", tags=["recipients"])


def _used_by(db: Session, recipient_id: uuid.UUID) -> list[str]:
    return list(
        db.exec(
            select(Job.name)
            .join(JobRecipient, col(JobRecipient.job_id) == col(Job.id))
            .where(col(JobRecipient.recipient_id) == recipient_id)
            .order_by(col(Job.name))
        ).all()
    )


def _out(db: Session, row: AgeRecipient) -> RecipientOut:
    return RecipientOut(
        id=str(row.id),
        name=row.name,
        public_key=row.public_key,
        fingerprint=row.fingerprint,
        created_at=row.created_at,
        used_by=_used_by(db, row.id),
    )


@router.get("")
def list_recipients(db: SessionDep, _user: CurrentUser) -> list[RecipientOut]:
    rows = db.exec(select(AgeRecipient).order_by(col(AgeRecipient.name))).all()
    return [_out(db, r) for r in rows]


@router.post("", status_code=201)
def add_recipient(
    body: RecipientIn, request: Request, db: SessionDep, user: CurrentUser
) -> RecipientOut:
    row = AgeRecipient(
        name=body.name, public_key=body.public_key, fingerprint=fingerprint(body.public_key)
    )
    db.add(row)
    write_audit(
        db,
        "recipient.add",
        user=user,
        object_type="recipient",
        object_id=str(row.id),
        details={"name": row.name, "fingerprint": row.fingerprint},
        ip=client_ip(request),
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="A key with this name or this public key already exists."
        ) from None
    return _out(db, row)


@router.delete("/{recipient_id}", status_code=204)
def delete_recipient(
    recipient_id: uuid.UUID, request: Request, db: SessionDep, user: CurrentUser
) -> Response:
    row = db.get(AgeRecipient, recipient_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Key not found")
    jobs = _used_by(db, row.id)
    if jobs:
        raise HTTPException(
            status_code=409,
            detail="This key is used by: " + ", ".join(jobs) + ". Remove it from those jobs first.",
        )
    write_audit(
        db,
        "recipient.delete",
        user=user,
        object_type="recipient",
        object_id=str(row.id),
        details={"name": row.name, "fingerprint": row.fingerprint},
        ip=client_ip(request),
    )
    db.delete(row)
    db.commit()
    return Response(status_code=204)

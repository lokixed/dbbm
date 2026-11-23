from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from app.drivers.base import ConnectionTestResult
from app.models.base import utcnow
from app.models.destination import Destination
from app.models.notification import NotificationChannel
from app.models.source import Source
from app.schemas.common import CheckOut, LastTestOut, SecretsInvalid, TestResultOut
from app.services.secrets import UNDECRYPTABLE_MESSAGE
from app.storage.base import StorageTestResult

MAX_STORED_MESSAGE = 1000


def undecryptable() -> HTTPException:
    return HTTPException(status_code=409, detail=UNDECRYPTABLE_MESSAGE)


def invalid_secret(exc: SecretsInvalid) -> HTTPException:
    detail = [{"loc": ["body", "secrets", exc.field], "msg": exc.message, "type": "value_error"}]
    return HTTPException(status_code=422, detail=detail)


def invalid_field(loc: list[str], message: str) -> HTTPException:
    detail = [{"loc": ["body", *loc], "msg": message, "type": "value_error"}]
    return HTTPException(status_code=422, detail=detail)


def name_conflict() -> HTTPException:
    return HTTPException(status_code=409, detail="A record with this name already exists.")


def commit_or_conflict(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise name_conflict() from None


def last_test_out(row: Source | Destination | NotificationChannel) -> LastTestOut | None:
    if row.last_test_at is None:
        return None
    return LastTestOut(
        at=row.last_test_at, ok=bool(row.last_test_ok), message=row.last_test_message
    )


def record_test(row: Source | Destination | NotificationChannel, ok: bool, message: str) -> None:
    row.last_test_at = utcnow()
    row.last_test_ok = ok
    row.last_test_message = message[:MAX_STORED_MESSAGE]


def test_out(result: ConnectionTestResult | StorageTestResult) -> TestResultOut:
    checks = [CheckOut(name=c.name, ok=c.ok, message=c.message) for c in result.checks]
    if isinstance(result, StorageTestResult):
        return TestResultOut(
            ok=result.ok,
            message=result.message,
            free_bytes=result.free_bytes,
            total_bytes=result.total_bytes,
            checks=checks,
        )
    return TestResultOut(
        ok=result.ok,
        message=result.message,
        server_version=result.server_version,
        databases=result.databases,
        checks=checks,
    )

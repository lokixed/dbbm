"""Credential store for Drive destinations: the encrypted secrets blob and auth status."""

import uuid
from collections.abc import Callable

from sqlmodel import Session

from app.core.db import get_engine
from app.models import AuthStatus, Destination
from app.models.base import utcnow
from app.services.rclone_token import same_token
from app.services.secrets import SecretsUnavailable, load_secrets, store_secrets

AuthExpiredHandler = Callable[[uuid.UUID, str], None]
_auth_expired_handler: AuthExpiredHandler | None = None


def set_auth_expired_handler(handler: AuthExpiredHandler | None) -> None:
    """Called (from a worker thread) when a destination moves to needs_reauth."""
    global _auth_expired_handler
    _auth_expired_handler = handler


class DestinationTokenStore:
    """Writes are compare-and-set on the token: if the user saved a new token while rclone
    was running, a refresh or auth failure of the old token must not overwrite it."""

    def __init__(self, destination_id: uuid.UUID) -> None:
        self._id = destination_id

    def _row(self, db: Session) -> Destination:
        row = db.get(Destination, self._id)
        if row is None:
            raise SecretsUnavailable
        return row

    def load(self) -> dict[str, str]:
        with Session(get_engine()) as db:
            return load_secrets(self._row(db).secrets_enc)

    def save_token(self, previous: str | None, token: str) -> bool:
        with Session(get_engine()) as db:
            row = self._row(db)
            secrets = load_secrets(row.secrets_enc)
            if not same_token(secrets.get("token"), previous):
                return False
            secrets["token"] = token
            row.secrets_enc = store_secrets(secrets)
            db.add(row)
            db.commit()
            return True

    def set_auth(self, status: str, token: str | None) -> None:
        with Session(get_engine()) as db:
            row = self._row(db)
            if not same_token(load_secrets(row.secrets_enc).get("token"), token):
                return
            expired = status == AuthStatus.NEEDS_REAUTH and row.auth_status != status
            row.auth_status = status
            row.auth_checked_at = utcnow()
            db.add(row)
            db.commit()
            name = row.name
        handler = _auth_expired_handler
        if expired and handler is not None:
            handler(self._id, name)

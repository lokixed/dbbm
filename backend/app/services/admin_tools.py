"""Offline administration: master key rotation, a safe copy of the tool's own database,
a configuration export without secrets, and admin password reset.

These run from `python -m app.cli` while the app is stopped.
"""

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlmodel import Session, col, select

from app.core.config import MIN_KEY_LENGTH, Settings
from app.core.crypto import DecryptionError, decrypt_with, encrypt_with
from app.core.security import hash_password
from app.models import Destination, Job, JobDestination, NotificationChannel, Source, User
from app.models.notification import JobNotificationChannel
from app.services.audit import write_audit

# (table, encrypted column) pairs. Add a row here when a new encrypted column is introduced.
ENCRYPTED_COLUMNS = (
    ("sources", "secrets_enc"),
    ("destinations", "secrets_enc"),
    ("notification_channels", "secrets_enc"),
    ("jobs", "heartbeat_secret_enc"),
)

CONFIG_EXPORT_VERSION = 1


class AdminError(Exception):
    """An administration command cannot continue. The message is safe to print."""


@dataclass(frozen=True)
class RotationReport:
    rotated: int
    per_table: dict[str, int]
    dry_run: bool
    backup_path: Path | None


def read_key_file(path: Path) -> bytes:
    try:
        key = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise AdminError(f"Cannot read the key file: {exc.strerror}.") from None
    if len(key) < MIN_KEY_LENGTH:
        raise AdminError(f"The new master key must be at least {MIN_KEY_LENGTH} characters long.")
    return key.encode("utf-8")


def backup_database(settings: Settings, destination: Path) -> Path:
    """A consistent copy of the SQLite database, safe while the app is running (uses the
    SQLite online backup API, so WAL contents are included)."""
    if destination.exists():
        raise AdminError(f"{destination} already exists; choose a new file name.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(f"file:{settings.db_path}?mode=ro", uri=True)
    target = sqlite3.connect(destination)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    destination.chmod(0o600)
    return destination


def _reencrypt(old_key: bytes, new_key: bytes, blob: bytes) -> bytes:
    return encrypt_with(new_key, decrypt_with(old_key, blob))


def rotate_master_key(
    settings: Settings, new_key: bytes, *, dry_run: bool, now: datetime | None = None
) -> RotationReport:
    """Re-encrypt every stored secret from the current master key to `new_key`.

    All-or-nothing: one transaction, and every new value is read back with the new key before
    it commits. Unless it is a dry run, a copy of the database (still readable with the old
    key) is kept first; restoring that copy with the old key undoes the rotation."""
    old_key = settings.master_key
    if new_key == old_key:
        raise AdminError("The new master key is the same as the current one.")
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    backup: Path | None = None
    if not dry_run:
        backup = backup_database(settings, settings.data_dir / f"app.db.pre-rotate-{stamp}")
    connection = sqlite3.connect(settings.db_path, isolation_level=None)
    per_table: dict[str, int] = {}
    try:
        connection.execute("BEGIN IMMEDIATE")
        for table, column in ENCRYPTED_COLUMNS:
            per_table[table] = _rotate_column(connection, table, column, old_key, new_key)
        if dry_run:
            connection.execute("ROLLBACK")
        else:
            connection.execute("COMMIT")
    except BaseException:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    finally:
        connection.close()
    return RotationReport(sum(per_table.values()), per_table, dry_run, backup)


def _rotate_column(
    connection: sqlite3.Connection, table: str, column: str, old_key: bytes, new_key: bytes
) -> int:
    rows = connection.execute(
        f"SELECT id, {column} FROM {table} WHERE {column} IS NOT NULL"  # noqa: S608 (constants)
    ).fetchall()
    for row_id, blob in rows:
        try:
            fresh = _reencrypt(old_key, new_key, bytes(blob))
            decrypt_with(new_key, fresh)
        except DecryptionError:
            raise AdminError(
                f"A stored secret in '{table}' cannot be decrypted with the current master key, "
                "so nothing was changed. Fix or re-enter that credential first."
            ) from None
        connection.execute(f"UPDATE {table} SET {column} = ? WHERE id = ?", (fresh, row_id))  # noqa: S608
    return len(rows)


def export_configuration(session: Session) -> dict[str, Any]:
    """Everything needed to rebuild the setup except secrets, passwords and run history."""
    secret_columns = {"secrets_enc", "heartbeat_secret_enc"}

    def dump(model: Any) -> list[dict[str, Any]]:
        return [
            row.model_dump(mode="json", exclude=secret_columns)
            for row in session.exec(select(model)).all()
        ]

    jobs = dump(Job)
    return {
        "format": "dbbackup-config",
        "version": CONFIG_EXPORT_VERSION,
        "exported_at": datetime.now(UTC).isoformat(),
        "note": "Secrets are not included; re-enter credentials after rebuilding.",
        "sources": dump(Source),
        "destinations": dump(Destination),
        "notification_channels": dump(NotificationChannel),
        "jobs": jobs,
        "job_destinations": dump(JobDestination),
        "job_notification_channels": dump(JobNotificationChannel),
    }


def write_config_export(session: Session, path: Path) -> Path:
    if path.exists():
        raise AdminError(f"{path} already exists; choose a new file name.")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(export_configuration(session), indent=2), encoding="utf-8")
    path.chmod(0o600)
    return path


def reset_password(session: Session, username: str, new_password: str) -> None:
    """Set a new password and sign every session out."""
    if len(new_password) < 12 or len(new_password) > 128:
        raise AdminError("The password must be 12 to 128 characters long.")
    user = session.exec(select(User).where(col(User.username) == username.strip().lower())).first()
    if user is None:
        raise AdminError(f"No user named '{username}'.")
    user.password_hash = hash_password(new_password)
    user.session_version += 1
    session.add(user)
    write_audit(session, "auth.password_reset_cli", user=user, details={"via": "cli"})
    session.commit()

"""Administration commands for the stopped app: `python -m app.cli --help`."""

import argparse
import getpass
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from sqlmodel import Session

from app.core.config import get_settings
from app.core.db import get_engine
from app.core.migrations import run_migrations
from app.services import admin_tools as tools
from app.version import __version__


def _rotate(args: argparse.Namespace) -> int:
    report = tools.rotate_master_key(
        get_settings(), tools.read_key_file(args.new_key_file), dry_run=args.dry_run
    )
    for table, count in report.per_table.items():
        print(f"{table}: {count} secret(s)")
    if report.dry_run:
        print(f"Dry run: {report.rotated} secret(s) can be re-encrypted. Nothing was changed.")
        return 0
    print(f"Re-encrypted {report.rotated} secret(s). Database copy (old key): {report.backup_path}")
    print("Now replace the master key (APP_SECRET_KEY or the key file) with the new key and start.")
    print("To roll back: stop the app, restore that copy as app.db, and use the old key.")
    return 0


def _backup_db(args: argparse.Namespace) -> int:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = args.out or get_settings().data_dir / f"app.db.backup-{stamp}"
    print(f"Database copied to {tools.backup_database(get_settings(), out)}")
    return 0


def _export(args: argparse.Namespace) -> int:
    run_migrations()
    with Session(get_engine()) as session:
        print(f"Configuration written to {tools.write_config_export(session, args.out)}")
    return 0


def _reset_password(args: argparse.Namespace) -> int:
    password = getpass.getpass("New password: ")
    if password != getpass.getpass("Repeat: "):
        raise tools.AdminError("The passwords do not match.")
    with Session(get_engine()) as session:
        tools.reset_password(session, args.username, password)
    print("Password changed; every session was signed out.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description=__doc__)
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    rotate = sub.add_parser("rotate-key", help="re-encrypt every stored secret with a new key")
    rotate.add_argument("--new-key-file", type=Path, required=True)
    rotate.add_argument("--dry-run", action="store_true", help="check only; change nothing")
    rotate.set_defaults(run=_rotate)

    backup = sub.add_parser("backup-db", help="copy the tool's own database safely")
    backup.add_argument("--out", type=Path)
    backup.set_defaults(run=_backup_db)

    export = sub.add_parser("export-config", help="write the configuration without secrets")
    export.add_argument("--out", type=Path, required=True)
    export.set_defaults(run=_export)

    reset = sub.add_parser("reset-password", help="set a new password for a user")
    reset.add_argument("username")
    reset.set_defaults(run=_reset_password)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        code: int = args.run(args)
    except tools.AdminError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return code


if __name__ == "__main__":
    raise SystemExit(main())

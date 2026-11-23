"""google drive destinations, auth status, retry runs, artifact md5

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # SQLite cannot alter CHECK constraints: batch mode recreates the table (env.py turns
    # foreign keys off meanwhile, so dependent rows are neither cascaded nor rejected).
    with op.batch_alter_table("destinations", recreate="always") as batch:
        batch.drop_constraint(op.f("ck_destinations_type"), type_="check")
        batch.create_check_constraint(op.f("ck_destinations_type"), "type IN ('local', 'gdrive')")
        batch.add_column(
            sa.Column("auth_status", sa.String(length=16), nullable=False, server_default="unknown")
        )
        batch.add_column(sa.Column("auth_checked_at", sa.DateTime(), nullable=True))
        batch.create_check_constraint(
            op.f("ck_destinations_auth_status"),
            "auth_status IN ('ok', 'needs_reauth', 'unknown')",
        )

    with op.batch_alter_table("runs") as batch:
        batch.add_column(sa.Column("parent_run_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            op.f("fk_runs_parent_run_id_runs"),
            "runs",
            ["parent_run_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_index("ix_runs_parent_run_id", ["parent_run_id"])

    with op.batch_alter_table("artifacts") as batch:
        batch.add_column(sa.Column("md5", sa.String(length=32), nullable=True))


def downgrade() -> None:
    count = (
        op.get_bind()
        .execute(sa.text("SELECT count(*) FROM destinations WHERE type = 'gdrive'"))
        .scalar_one()
    )
    if count:
        raise RuntimeError(
            f"Cannot downgrade: {count} Google Drive destination(s) exist. "
            "Remove them (and their jobs and recorded backups) first."
        )

    with op.batch_alter_table("artifacts") as batch:
        batch.drop_column("md5")

    with op.batch_alter_table("runs") as batch:
        batch.drop_index("ix_runs_parent_run_id")
        batch.drop_constraint(op.f("fk_runs_parent_run_id_runs"), type_="foreignkey")
        batch.drop_column("parent_run_id")

    with op.batch_alter_table("destinations", recreate="always") as batch:
        batch.drop_constraint(op.f("ck_destinations_auth_status"), type_="check")
        batch.drop_column("auth_checked_at")
        batch.drop_column("auth_status")
        batch.drop_constraint(op.f("ck_destinations_type"), type_="check")
        batch.create_check_constraint(op.f("ck_destinations_type"), "type IN ('local')")

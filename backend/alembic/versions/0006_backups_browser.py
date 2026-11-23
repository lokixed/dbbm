"""artifact verification and drift tracking for the backups browser

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("artifacts") as batch:
        batch.add_column(sa.Column("verified_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("missing_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("artifacts") as batch:
        batch.drop_column("missing_at")
        batch.drop_column("verified_at")

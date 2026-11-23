"""restore runs: a kind column on runs

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("runs") as batch:
        batch.add_column(
            sa.Column("kind", sa.String(length=16), nullable=False, server_default="backup")
        )


def downgrade() -> None:
    with op.batch_alter_table("runs") as batch:
        batch.drop_column("kind")

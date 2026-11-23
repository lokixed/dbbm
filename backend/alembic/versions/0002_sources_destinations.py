"""sources and destinations

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _common_columns() -> list[sa.Column[object]]:
    return [
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("type", sa.String(length=16), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("secrets_enc", sa.LargeBinary(), nullable=True),
    ]


def _test_and_audit_columns() -> list[sa.Column[object]]:
    return [
        sa.Column("last_test_at", sa.DateTime(), nullable=True),
        sa.Column("last_test_ok", sa.Boolean(), nullable=True),
        sa.Column("last_test_message", sa.String(length=1000), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "sources",
        *_common_columns(),
        *_test_and_audit_columns(),
        sa.CheckConstraint(
            "type IN ('mysql', 'postgres', 'mongo', 'firestore')", name=op.f("ck_sources_type")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sources")),
        sa.UniqueConstraint("name", name=op.f("uq_sources_name")),
    )
    op.create_table(
        "destinations",
        *_common_columns(),
        sa.Column("retention", sa.JSON(), nullable=False),
        *_test_and_audit_columns(),
        sa.CheckConstraint("type IN ('local')", name=op.f("ck_destinations_type")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_destinations")),
        sa.UniqueConstraint("name", name=op.f("uq_destinations_name")),
    )


def downgrade() -> None:
    op.drop_table("destinations")
    op.drop_table("sources")

"""backup encryption: age recipients, per-job encryption, encrypted artifacts

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "age_recipients",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("public_key", sa.String(length=128), nullable=False),
        sa.Column("fingerprint", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_age_recipients")),
        sa.UniqueConstraint("name", name=op.f("uq_age_recipients_name")),
        sa.UniqueConstraint("public_key", name=op.f("uq_age_recipients_public_key")),
    )
    op.create_table(
        "job_recipients",
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("recipient_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["job_id"], ["jobs.id"], name=op.f("fk_job_recipients_job_id_jobs"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["recipient_id"],
            ["age_recipients.id"],
            name=op.f("fk_job_recipients_recipient_id_age_recipients"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("job_id", "recipient_id", name=op.f("pk_job_recipients")),
    )
    with op.batch_alter_table("jobs") as batch:
        batch.add_column(
            sa.Column("encrypt", sa.Boolean(), nullable=False, server_default=sa.false())
        )
    with op.batch_alter_table("artifacts") as batch:
        batch.add_column(
            sa.Column("encrypted", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        # Fingerprints of the recipients the file was encrypted to (public information).
        batch.add_column(sa.Column("recipients", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("artifacts") as batch:
        batch.drop_column("recipients")
        batch.drop_column("encrypted")
    with op.batch_alter_table("jobs") as batch:
        batch.drop_column("encrypt")
    op.drop_table("job_recipients")
    op.drop_table("age_recipients")

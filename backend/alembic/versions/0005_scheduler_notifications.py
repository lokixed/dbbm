"""scheduler, notifications, settings

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_EVENT_TYPES = (
    "'run_finished', 'recovery', 'overdue', 'auth_expired', 'retention_failed', 'low_disk', 'test'"
)


def upgrade() -> None:
    with op.batch_alter_table("jobs") as batch:
        batch.add_column(
            sa.Column("timezone", sa.String(length=64), nullable=False, server_default="UTC")
        )
        batch.add_column(
            sa.Column("notify_on", sa.String(length=16), nullable=False, server_default="failure")
        )
        batch.add_column(
            sa.Column("notify_on_recovery", sa.Boolean(), nullable=False, server_default=sa.true())
        )
        batch.add_column(sa.Column("heartbeat_secret_enc", sa.LargeBinary(), nullable=True))
        batch.add_column(sa.Column("last_scheduled_fire_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("overdue_notified_at", sa.DateTime(), nullable=True))
        batch.create_check_constraint(
            op.f("ck_jobs_notify_on"), "notify_on IN ('failure', 'always', 'never')"
        )

    with op.batch_alter_table("runs") as batch:
        batch.add_column(sa.Column("scheduled_for", sa.DateTime(), nullable=True))

    op.create_table(
        "notification_channels",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("type", sa.String(length=16), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("secrets_enc", sa.LargeBinary(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("last_test_at", sa.DateTime(), nullable=True),
        sa.Column("last_test_ok", sa.Boolean(), nullable=True),
        sa.Column("last_test_message", sa.String(length=1000), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "type IN ('slack', 'email', 'webhook')", name=op.f("ck_notification_channels_type")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notification_channels")),
        sa.UniqueConstraint("name", name=op.f("uq_notification_channels_name")),
    )
    op.create_table(
        "job_notification_channels",
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("notification_channel_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["jobs.id"],
            name=op.f("fk_job_notification_channels_job_id_jobs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["notification_channel_id"],
            ["notification_channels.id"],
            name=op.f("fk_job_notification_channels_notification_channel_id_notification_channels"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "job_id", "notification_channel_id", name=op.f("pk_job_notification_channels")
        ),
    )
    op.create_table(
        "notification_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("channel_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("job_id", sa.Uuid(), nullable=True),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error", sa.String(length=1000), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            f"event_type IN ({_EVENT_TYPES})", name=op.f("ck_notification_events_event_type")
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'sent', 'failed', 'rate_limited')",
            name=op.f("ck_notification_events_status"),
        ),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            ["notification_channels.id"],
            name=op.f("fk_notification_events_channel_id_notification_channels"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["runs.id"],
            name=op.f("fk_notification_events_run_id_runs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["jobs.id"],
            name=op.f("fk_notification_events_job_id_jobs"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notification_events")),
    )
    op.create_index(
        "ix_notification_events_channel_id_created_at",
        "notification_events",
        ["channel_id", "created_at"],
    )
    op.create_index("ix_notification_events_run_id", "notification_events", ["run_id"])
    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("value", sa.JSON(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_app_settings")),
    )


def downgrade() -> None:
    op.drop_table("app_settings")
    op.drop_index("ix_notification_events_run_id", table_name="notification_events")
    op.drop_index("ix_notification_events_channel_id_created_at", table_name="notification_events")
    op.drop_table("notification_events")
    op.drop_table("job_notification_channels")
    op.drop_table("notification_channels")

    with op.batch_alter_table("runs") as batch:
        batch.drop_column("scheduled_for")

    with op.batch_alter_table("jobs") as batch:
        batch.drop_constraint(op.f("ck_jobs_notify_on"), type_="check")
        batch.drop_column("overdue_notified_at")
        batch.drop_column("last_scheduled_fire_at")
        batch.drop_column("heartbeat_secret_enc")
        batch.drop_column("notify_on_recovery")
        batch.drop_column("notify_on")
        batch.drop_column("timezone")

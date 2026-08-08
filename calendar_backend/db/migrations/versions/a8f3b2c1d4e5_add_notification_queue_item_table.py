"""add notification queue item table

Revision ID: a8f3b2c1d4e5
Revises: 4254240455e7
Create Date: 2026-08-08 22:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a8f3b2c1d4e5"
down_revision: str | Sequence[str] | None = "4254240455e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "notification_queue_item",
        sa.Column("notification_id", sa.Uuid(), nullable=False),
        sa.Column(
            "source_kind",
            sa.Enum("TASK", "BLOCK", name="notificationsourcekind", native_enum=False),
            nullable=False,
        ),
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("timer_key", sa.String(), nullable=False),
        sa.Column("window_end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("calendar_entry_id", sa.Uuid(), nullable=True),
        sa.Column("block_calendar_entry_id", sa.Uuid(), nullable=True),
        sa.Column("display_label", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "dismissed_at IS NULL OR dismissed_at >= created_at",
            name="notification_dismissed_after_created",
        ),
        sa.ForeignKeyConstraint(
            ["block_calendar_entry_id"], ["block_calendar_entry.block_calendar_entry_id"]
        ),
        sa.ForeignKeyConstraint(["calendar_entry_id"], ["calendar_entry.calendar_entry_id"]),
        sa.ForeignKeyConstraint(["plan_id"], ["plan.plan_id"]),
        sa.PrimaryKeyConstraint("notification_id"),
    )
    op.create_index(
        "ix_notification_queue_item_timer_key_window_end",
        "notification_queue_item",
        ["timer_key", "window_end_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_notification_queue_item_timer_key_window_end", table_name="notification_queue_item"
    )
    op.drop_table("notification_queue_item")

"""add block plan and block calendar schema

Revision ID: 39ab2bfa6051
Revises: 12d2c5cab97e
Create Date: 2026-07-27 21:07:33.749207

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "39ab2bfa6051"
down_revision: str | Sequence[str] | None = "12d2c5cab97e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "block_calendar_entry",
        sa.Column("block_calendar_entry_id", sa.Uuid(), nullable=False),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_plan_id", sa.Uuid(), nullable=False),
        sa.Column("calendar_run_id", sa.Uuid(), nullable=True),
        sa.Column("display_label", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "start_time < end_time",
            name=op.f("ck_block_calendar_entry_block_calendar_start_before_end"),
        ),
        sa.ForeignKeyConstraint(
            ["calendar_run_id"],
            ["calendar_run.calendar_run_id"],
            name=op.f("fk_block_calendar_entry_calendar_run_id_calendar_run"),
        ),
        sa.ForeignKeyConstraint(
            ["source_plan_id"],
            ["plan.plan_id"],
            name=op.f("fk_block_calendar_entry_source_plan_id_plan"),
        ),
        sa.PrimaryKeyConstraint(
            "block_calendar_entry_id",
            name=op.f("pk_block_calendar_entry"),
        ),
    )
    op.create_table(
        "block_plan",
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("duration_minutes", sa.Integer(), nullable=False),
        sa.Column("divisible", sa.Boolean(), nullable=False),
        sa.Column("minimum_chunk_size_minutes", sa.Integer(), nullable=True),
        sa.Column("user_completed", sa.Boolean(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("block_family", sa.String(), nullable=False),
        sa.Column("immediate_prerequisite_plan_id", sa.Uuid(), nullable=True),
        sa.CheckConstraint(
            "(divisible = 1 AND minimum_chunk_size_minutes IS NOT NULL) "
            "OR (divisible = 0 AND minimum_chunk_size_minutes IS NULL)",
            name=op.f("ck_block_plan_block_chunk_matches_divisibility"),
        ),
        sa.CheckConstraint(
            "duration_minutes > 0",
            name=op.f("ck_block_plan_block_duration_positive"),
        ),
        sa.CheckConstraint(
            "length(trim(block_family)) > 0",
            name=op.f("ck_block_plan_block_family_non_empty"),
        ),
        sa.CheckConstraint(
            "minimum_chunk_size_minutes IS NULL OR minimum_chunk_size_minutes <= duration_minutes",
            name=op.f("ck_block_plan_block_minimum_chunk_lte_duration"),
        ),
        sa.CheckConstraint(
            "minimum_chunk_size_minutes IS NULL OR minimum_chunk_size_minutes > 0",
            name=op.f("ck_block_plan_block_minimum_chunk_positive_when_set"),
        ),
        sa.ForeignKeyConstraint(
            ["immediate_prerequisite_plan_id"],
            ["plan.plan_id"],
            name=op.f("fk_block_plan_immediate_prerequisite_plan_id_plan"),
        ),
        sa.ForeignKeyConstraint(
            ["plan_id"],
            ["plan.plan_id"],
            name=op.f("fk_block_plan_plan_id_plan"),
        ),
        sa.PrimaryKeyConstraint("plan_id", name=op.f("pk_block_plan")),
    )


def downgrade() -> None:
    op.drop_table("block_plan")
    op.drop_table("block_calendar_entry")

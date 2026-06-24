"""add task_plan and repetition_plan enforcement checks

Revision ID: 3fd2ad5a8d31
Revises: 522f4501f06a
Create Date: 2026-06-23 21:54:58.024564

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3fd2ad5a8d31"
down_revision: str | Sequence[str] | None = "522f4501f06a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("task_plan", schema=None) as batch_op:
        batch_op.alter_column(
            "minimum_chunk_size_minutes",
            existing_type=sa.Integer(),
            nullable=True,
        )
        batch_op.create_check_constraint(
            op.f("ck_task_plan_duration_positive"),
            "duration_minutes > 0",
        )
        batch_op.create_check_constraint(
            op.f("ck_task_plan_task_chunk_matches_divisibility"),
            "(divisible = 1 AND minimum_chunk_size_minutes IS NOT NULL) "
            "OR (divisible = 0 AND minimum_chunk_size_minutes IS NULL)",
        )
        batch_op.create_check_constraint(
            op.f("ck_task_plan_minimum_chunk_positive_when_set"),
            "minimum_chunk_size_minutes IS NULL OR minimum_chunk_size_minutes > 0",
        )
        batch_op.create_check_constraint(
            op.f("ck_task_plan_minimum_chunk_lte_duration"),
            "minimum_chunk_size_minutes IS NULL OR minimum_chunk_size_minutes <= duration_minutes",
        )

    with op.batch_alter_table("repetition_plan", schema=None) as batch_op:
        batch_op.create_check_constraint(
            op.f("ck_repetition_plan_manual_count_mode_fields"),
            "repeat_mode != 'MANUAL_COUNT' OR (manual_count IS NOT NULL AND end_time IS NULL)",
        )
        batch_op.create_check_constraint(
            op.f("ck_repetition_plan_date_range_mode_fields"),
            "repeat_mode != 'DATE_RANGE' OR manual_count IS NULL",
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("repetition_plan", schema=None) as batch_op:
        batch_op.drop_constraint(
            op.f("ck_repetition_plan_date_range_mode_fields"),
            type_="check",
        )
        batch_op.drop_constraint(
            op.f("ck_repetition_plan_manual_count_mode_fields"),
            type_="check",
        )

    with op.batch_alter_table("task_plan", schema=None) as batch_op:
        batch_op.drop_constraint(
            op.f("ck_task_plan_minimum_chunk_lte_duration"),
            type_="check",
        )
        batch_op.drop_constraint(
            op.f("ck_task_plan_minimum_chunk_positive_when_set"),
            type_="check",
        )
        batch_op.drop_constraint(
            op.f("ck_task_plan_task_chunk_matches_divisibility"),
            type_="check",
        )
        batch_op.drop_constraint(
            op.f("ck_task_plan_duration_positive"),
            type_="check",
        )
        batch_op.alter_column(
            "minimum_chunk_size_minutes",
            existing_type=sa.Integer(),
            nullable=False,
        )

"""add repetition plan check constraints

Revision ID: e6e01e97df46
Revises: 7369a1e5acb0
Create Date: 2026-06-13 00:57:01.373243

"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e6e01e97df46"
down_revision: str | Sequence[str] | None = "7369a1e5acb0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("repetition_plan", schema=None) as batch_op:
        batch_op.create_check_constraint(
            op.f("ck_repetition_plan_repeat_interval_positive"),
            "repeat_interval_minutes > 0",
        )
        batch_op.create_check_constraint(
            op.f("ck_repetition_plan_end_after_start"),
            "end_time IS NULL OR end_time > start_time",
        )
        batch_op.create_check_constraint(
            op.f("ck_repetition_plan_manual_count_positive_when_set"),
            "manual_count IS NULL OR manual_count > 0",
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("repetition_plan", schema=None) as batch_op:
        batch_op.drop_constraint(
            op.f("ck_repetition_plan_manual_count_positive_when_set"),
            type_="check",
        )
        batch_op.drop_constraint(
            op.f("ck_repetition_plan_end_after_start"),
            type_="check",
        )
        batch_op.drop_constraint(
            op.f("ck_repetition_plan_repeat_interval_positive"),
            type_="check",
        )

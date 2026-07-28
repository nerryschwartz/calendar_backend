"""add plan prerequisite schema

Revision ID: 12d2c5cab97e
Revises: 5210d24989f8
Create Date: 2026-07-27 20:00:24.251372

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "12d2c5cab97e"
down_revision: str | Sequence[str] | None = "5210d24989f8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "plan_prerequisite",
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("prerequisite_plan_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "plan_id != prerequisite_plan_id",
            name=op.f("ck_plan_prerequisite_no_self_prerequisite"),
        ),
        sa.ForeignKeyConstraint(
            ["plan_id"],
            ["plan.plan_id"],
            name=op.f("fk_plan_prerequisite_plan_id_plan"),
        ),
        sa.ForeignKeyConstraint(
            ["prerequisite_plan_id"],
            ["plan.plan_id"],
            name=op.f("fk_plan_prerequisite_prerequisite_plan_id_plan"),
        ),
        sa.PrimaryKeyConstraint(
            "plan_id",
            "prerequisite_plan_id",
            name=op.f("pk_plan_prerequisite"),
        ),
    )

    with op.batch_alter_table("task_plan", schema=None) as batch_op:
        batch_op.add_column(sa.Column("immediate_prerequisite_plan_id", sa.Uuid(), nullable=True))
        batch_op.create_foreign_key(
            op.f("fk_task_plan_immediate_prerequisite_plan_id_plan"),
            "plan",
            ["immediate_prerequisite_plan_id"],
            ["plan_id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("task_plan", schema=None) as batch_op:
        batch_op.drop_constraint(
            op.f("fk_task_plan_immediate_prerequisite_plan_id_plan"),
            type_="foreignkey",
        )
        batch_op.drop_column("immediate_prerequisite_plan_id")

    op.drop_table("plan_prerequisite")

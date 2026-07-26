"""drop goal child chain tables

Revision ID: 5210d24989f8
Revises: c2915f3dc29c
Create Date: 2026-07-25 23:48:02.649072

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5210d24989f8"
down_revision: str | Sequence[str] | None = "c2915f3dc29c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_table("goal_child_chain_item")
    op.drop_table("goal_child_chain")


def downgrade() -> None:
    op.create_table(
        "goal_child_chain",
        sa.Column("goal_child_chain_id", sa.CHAR(length=32), nullable=False),
        sa.Column("parent_goal_id", sa.CHAR(length=32), nullable=False),
        sa.Column("is_critical", sa.BOOLEAN(), nullable=False),
        sa.Column("sort_order", sa.INTEGER(), nullable=False),
        sa.Column("created_at", sa.DATETIME(), nullable=False),
        sa.Column("updated_at", sa.DATETIME(), nullable=False),
        sa.CheckConstraint(
            "sort_order >= 0",
            name=op.f("ck_goal_child_chain_sort_order_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["parent_goal_id"],
            ["goal_plan.plan_id"],
            name=op.f("fk_goal_child_chain_parent_goal_id_goal_plan"),
        ),
        sa.PrimaryKeyConstraint("goal_child_chain_id", name=op.f("pk_goal_child_chain")),
    )
    op.create_table(
        "goal_child_chain_item",
        sa.Column("goal_child_chain_item_id", sa.CHAR(length=32), nullable=False),
        sa.Column("chain_id", sa.CHAR(length=32), nullable=False),
        sa.Column("child_plan_id", sa.CHAR(length=32), nullable=False),
        sa.Column("position", sa.INTEGER(), nullable=False),
        sa.CheckConstraint(
            "position >= 0",
            name=op.f("ck_goal_child_chain_item_position_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["chain_id"],
            ["goal_child_chain.goal_child_chain_id"],
            name=op.f("fk_goal_child_chain_item_chain_id_goal_child_chain"),
        ),
        sa.ForeignKeyConstraint(
            ["child_plan_id"],
            ["plan.plan_id"],
            name=op.f("fk_goal_child_chain_item_child_plan_id_plan"),
        ),
        sa.PrimaryKeyConstraint(
            "goal_child_chain_item_id",
            name=op.f("pk_goal_child_chain_item"),
        ),
        sa.UniqueConstraint(
            "child_plan_id",
            name=op.f("uq_goal_child_chain_item_child_plan_id"),
        ),
    )

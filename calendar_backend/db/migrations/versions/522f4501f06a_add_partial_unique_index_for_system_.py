"""Add partial unique index for SYSTEM_MASTER_HORIZON

Revision ID: 522f4501f06a
Revises: 7e137c1ddfb0
Create Date: 2026-06-15 14:56:26.008073

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "522f4501f06a"
down_revision: str | Sequence[str] | None = "7e137c1ddfb0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_KEEP_ONE_HORIZON_GROUP_SUBQUERY = """
    SELECT MIN(time_constraint_group_id)
    FROM time_constraint_group
    WHERE constraint_kind = 'SYSTEM_MASTER_HORIZON'
    GROUP BY plan_id
"""


def _remove_duplicate_system_master_horizon_groups() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text(
            f"""
            DELETE FROM time_window
            WHERE group_id IN (
                SELECT time_constraint_group_id
                FROM time_constraint_group
                WHERE constraint_kind = 'SYSTEM_MASTER_HORIZON'
                  AND time_constraint_group_id NOT IN ({_KEEP_ONE_HORIZON_GROUP_SUBQUERY})
            )
            """
        )
    )
    connection.execute(
        sa.text(
            f"""
            DELETE FROM time_constraint_group
            WHERE constraint_kind = 'SYSTEM_MASTER_HORIZON'
              AND time_constraint_group_id NOT IN ({_KEEP_ONE_HORIZON_GROUP_SUBQUERY})
            """
        )
    )


def upgrade() -> None:
    """Upgrade schema."""
    _remove_duplicate_system_master_horizon_groups()
    with op.batch_alter_table("time_constraint_group", schema=None) as batch_op:
        batch_op.create_index(
            "uq_time_constraint_group_plan_system_master_horizon",
            ["plan_id"],
            unique=True,
            sqlite_where=sa.text("constraint_kind = 'SYSTEM_MASTER_HORIZON'"),
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("time_constraint_group", schema=None) as batch_op:
        batch_op.drop_index(
            "uq_time_constraint_group_plan_system_master_horizon",
            sqlite_where=sa.text("constraint_kind = 'SYSTEM_MASTER_HORIZON'"),
        )

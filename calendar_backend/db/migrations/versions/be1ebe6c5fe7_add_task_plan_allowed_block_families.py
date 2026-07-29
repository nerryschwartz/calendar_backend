"""add task_plan allowed_block_families

Revision ID: be1ebe6c5fe7
Revises: 39ab2bfa6051
Create Date: 2026-07-28 21:17:51.724407

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "be1ebe6c5fe7"
down_revision: str | Sequence[str] | None = "39ab2bfa6051"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("task_plan", schema=None) as batch_op:
        batch_op.add_column(sa.Column("allowed_block_families", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("task_plan", schema=None) as batch_op:
        batch_op.drop_column("allowed_block_families")

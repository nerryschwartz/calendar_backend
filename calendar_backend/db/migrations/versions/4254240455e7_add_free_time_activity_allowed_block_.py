"""add free_time_activity allowed_block_families

Revision ID: 4254240455e7
Revises: be1ebe6c5fe7
Create Date: 2026-07-29 17:18:05.973311

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4254240455e7"
down_revision: str | Sequence[str] | None = "be1ebe6c5fe7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("free_time_activity", schema=None) as batch_op:
        batch_op.add_column(sa.Column("allowed_block_families", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("free_time_activity", schema=None) as batch_op:
        batch_op.drop_column("allowed_block_families")

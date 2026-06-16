"""remove granularity from app settings

Revision ID: 7e137c1ddfb0
Revises: e6e01e97df46
Create Date: 2026-06-15 13:12:43.429945

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7e137c1ddfb0"
down_revision: str | Sequence[str] | None = "e6e01e97df46"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("app_settings") as batch_op:
        batch_op.drop_column("scheduling_granularity_minutes")


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("app_settings") as batch_op:
        batch_op.add_column(
            sa.Column(
                "scheduling_granularity_minutes",
                sa.INTEGER(),
                nullable=False,
                server_default=sa.text("1"),
            )
        )

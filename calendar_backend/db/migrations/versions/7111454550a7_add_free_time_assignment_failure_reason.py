"""add free time assignment failure reason

Revision ID: 7111454550a7
Revises: 3fd2ad5a8d31
Create Date: 2026-07-10 19:43:45.423252

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7111454550a7"
down_revision: str | Sequence[str] | None = "3fd2ad5a8d31"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LAST_FAILURE_REASON_OLD = sa.Enum(
    "ASSIGNMENT_FAILED",
    "ASSIGNMENT_PRECONDITION_FAILED",
    name="lastfailurereason",
    native_enum=False,
)
_LAST_FAILURE_REASON_NEW = sa.Enum(
    "ASSIGNMENT_FAILED",
    "ASSIGNMENT_PRECONDITION_FAILED",
    "FREE_TIME_ASSIGNMENT_FAILED",
    name="lastfailurereason",
    native_enum=False,
)


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("active_calendar_state", schema=None) as batch_op:
        batch_op.alter_column(
            "last_failure_reason",
            existing_type=_LAST_FAILURE_REASON_OLD,
            type_=_LAST_FAILURE_REASON_NEW,
            existing_nullable=True,
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("active_calendar_state", schema=None) as batch_op:
        batch_op.alter_column(
            "last_failure_reason",
            existing_type=_LAST_FAILURE_REASON_NEW,
            type_=_LAST_FAILURE_REASON_OLD,
            existing_nullable=True,
        )

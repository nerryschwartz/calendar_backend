"""Remember intentionally omitted and deleted repetition occurrences.

Revision ID: d38a761bc924
Revises: c21b4e08d517
"""

import sqlalchemy as sa
from alembic import op

revision = "d38a761bc924"
down_revision = "c21b4e08d517"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "repetition_skipped_occurrence",
        sa.Column(
            "repetition_plan_id",
            sa.Uuid(),
            sa.ForeignKey("repetition_plan.plan_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("instance_index", sa.Integer(), primary_key=True),
        sa.CheckConstraint("instance_index >= 0", name="instance_index_non_negative"),
    )


def downgrade():
    op.drop_table("repetition_skipped_occurrence")

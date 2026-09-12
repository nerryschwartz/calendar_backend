"""Atomic repetition generation receipts.

Revision ID: c21b4e08d517
Revises: b91f6d82a304
"""

import sqlalchemy as sa
from alembic import op

revision = "c21b4e08d517"
down_revision = "b91f6d82a304"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "repetition_generation_receipt",
        sa.Column("generation_key", sa.String(), primary_key=True),
        sa.Column(
            "repetition_plan_id",
            sa.Uuid(),
            sa.ForeignKey("repetition_plan.plan_id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("request_fingerprint", sa.String(), nullable=False),
        sa.Column("reference_map", sa.JSON(), nullable=False),
    )


def downgrade():
    op.drop_table("repetition_generation_receipt")

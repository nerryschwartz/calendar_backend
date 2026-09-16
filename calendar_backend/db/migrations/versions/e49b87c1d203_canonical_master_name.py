"""Give the immutable master plan its canonical display name.

Revision ID: e49b87c1d203
Revises: d38a761bc924
"""

import sqlalchemy as sa
from alembic import op

revision = "e49b87c1d203"
down_revision = "d38a761bc924"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(sa.text("UPDATE plan SET name = 'Master' WHERE is_master = 1"))


def downgrade():
    op.execute(sa.text("UPDATE plan SET name = 'master' WHERE is_master = 1"))

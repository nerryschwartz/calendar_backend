"""Replace elapsed horizon minutes with calendar duration parts."""

import sqlalchemy as sa
from alembic import op

revision = "b91f6d82a304"
down_revision = "a8f3b2c1d4e5"
branch_labels = None
depends_on = None

_UNITS = (("years", 525600), ("months", 43200), ("days", 1440), ("hours", 60), ("minutes", 1))


def upgrade() -> None:
    op.add_column("app_settings", sa.Column("master_horizon_duration", sa.JSON(), nullable=True))
    settings = sa.table(
        "app_settings",
        sa.column("singleton_id", sa.Integer()),
        sa.column("master_horizon_duration_minutes", sa.Integer()),
        sa.column("master_horizon_duration", sa.JSON()),
    )
    connection = op.get_bind()
    for row in connection.execute(sa.select(settings)).mappings():
        remaining = row["master_horizon_duration_minutes"]
        duration = {}
        for name, size in _UNITS:
            duration[name], remaining = divmod(remaining, size)
        connection.execute(
            settings.update()
            .where(settings.c.singleton_id == row["singleton_id"])
            .values(master_horizon_duration=duration)
        )
    with op.batch_alter_table("app_settings") as batch:
        batch.alter_column("master_horizon_duration", existing_type=sa.JSON(), nullable=False)
        batch.drop_column("master_horizon_duration_minutes")


def downgrade() -> None:
    op.add_column(
        "app_settings", sa.Column("master_horizon_duration_minutes", sa.Integer(), nullable=True)
    )
    settings = sa.table(
        "app_settings",
        sa.column("singleton_id", sa.Integer()),
        sa.column("master_horizon_duration_minutes", sa.Integer()),
        sa.column("master_horizon_duration", sa.JSON()),
    )
    connection = op.get_bind()
    for row in connection.execute(sa.select(settings)).mappings():
        minutes = sum(row["master_horizon_duration"][name] * size for name, size in _UNITS)
        connection.execute(
            settings.update()
            .where(settings.c.singleton_id == row["singleton_id"])
            .values(master_horizon_duration_minutes=minutes)
        )
    with op.batch_alter_table("app_settings") as batch:
        batch.alter_column(
            "master_horizon_duration_minutes", existing_type=sa.Integer(), nullable=False
        )
        batch.drop_column("master_horizon_duration")

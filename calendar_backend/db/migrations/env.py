from __future__ import annotations

from logging.config import fileConfig

import calendar_backend.db.session  # noqa: F401  # pyright: ignore[reportUnusedImport]
from alembic import context
from calendar_backend.db.base import Base
from calendar_backend.db.session import DEFAULT_DATABASE_URL, create_engine_for_url
from calendar_backend.models import (
    calendar,  # noqa: F401  # pyright: ignore[reportUnusedImport]
    constraints,  # noqa: F401  # pyright: ignore[reportUnusedImport]
    free_time,  # noqa: F401  # pyright: ignore[reportUnusedImport]
    plans,  # noqa: F401  # pyright: ignore[reportUnusedImport]
    repetitions,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=DEFAULT_DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine_for_url(DEFAULT_DATABASE_URL)

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

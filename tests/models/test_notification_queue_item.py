from __future__ import annotations

import tempfile
from collections.abc import Generator
from pathlib import Path

import calendar_backend.models.notifications  # noqa: F401  # pyright: ignore[reportUnusedImport]
import pytest
from alembic import command
from alembic.config import Config
from calendar_backend.db.base import Base
from calendar_backend.db.session import create_engine_for_url
from sqlalchemy.engine import Engine

NOTIFICATION_TABLE = "notification_queue_item"


@pytest.fixture
def temp_sqlite_url() -> Generator[str]:
    with tempfile.TemporaryDirectory() as tmpdir:
        yield f"sqlite:///{Path(tmpdir) / 'test.sqlite3'}"


@pytest.fixture
def migrated_engine(temp_sqlite_url: str) -> Generator[Engine]:
    engine = create_engine_for_url(temp_sqlite_url)
    Base.metadata.create_all(engine)
    alembic_cfg = Config("alembic.ini")
    alembic_cfg.set_main_option("sqlalchemy.url", temp_sqlite_url)
    command.upgrade(alembic_cfg, "head")
    try:
        yield engine
    finally:
        engine.dispose()


def test_notification_table_exists(migrated_engine: Engine) -> None:
    with migrated_engine.connect() as connection:
        names = migrated_engine.dialect.get_table_names(connection)
    assert NOTIFICATION_TABLE in names

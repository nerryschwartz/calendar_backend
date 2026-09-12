import json
from pathlib import Path

import calendar_backend.db.session as db_session
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


def test_calendar_horizon_migration_preserves_display_units(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = Config("alembic.ini")
    url = f"sqlite:///{tmp_path / 'migration.sqlite3'}"
    config.set_main_option("sqlalchemy.url", url)
    # The repository's Alembic env reads this constant, not the config URL.
    monkeypatch.setattr(db_session, "DEFAULT_DATABASE_URL", url)
    command.upgrade(config, "a8f3b2c1d4e5")
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text("""INSERT INTO app_settings VALUES
                (1, 'UTC', 1051200, 30, 1000, 1, 'MONDAY', '2026-09-12 10:00:00')""")
            )
        command.upgrade(config, "head")
        with engine.connect() as connection:
            duration = json.loads(
                connection.scalar(text("SELECT master_horizon_duration FROM app_settings"))
            )
            assert duration == dict(years=2, months=0, days=0, hours=0, minutes=0)
        assert "master_horizon_duration_minutes" not in {
            column["name"] for column in inspect(engine).get_columns("app_settings")
        }
        command.downgrade(config, "a8f3b2c1d4e5")
        with engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT master_horizon_duration_minutes FROM app_settings"))
                == 1051200
            )
    finally:
        engine.dispose()

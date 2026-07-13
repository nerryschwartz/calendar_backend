"""Shared helpers for the development CLI."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from calendar_backend.db.session import DEFAULT_DATABASE_URL, create_engine_for_url
from sqlalchemy.engine import Engine

DATABASE_URL = DEFAULT_DATABASE_URL


def database_path_from_url(url: str = DATABASE_URL) -> Path:
    return Path(unquote(url.removeprefix("sqlite:///")))


def alembic_config(ini_path: str = "alembic.ini") -> Config:
    path = Path(ini_path)
    if not path.is_file():
        msg = f"{path} not found; run calendar-backend-dev from the repository root"
        raise FileNotFoundError(msg)
    return Config(str(path))


def read_current_revision(engine: Engine) -> str | None:
    with engine.connect() as connection:
        context = MigrationContext.configure(connection)
        return context.get_current_revision()


def upgrade_head(url: str = DATABASE_URL) -> str:
    create_engine_for_url(url)
    config = alembic_config()
    command.upgrade(config, "head")
    engine = create_engine_for_url(url)
    try:
        revision = read_current_revision(engine)
    finally:
        engine.dispose()
    if revision is None:
        msg = "Alembic upgrade completed but no revision recorded"
        raise RuntimeError(msg)
    return revision


def current_revision(url: str = DATABASE_URL) -> str | None:
    engine = create_engine_for_url(url)
    try:
        return read_current_revision(engine)
    finally:
        engine.dispose()


def delete_database_file_if_exists(url: str = DATABASE_URL) -> bool:
    db_path = database_path_from_url(url)
    if db_path.exists():
        db_path.unlink()
        return True
    return False

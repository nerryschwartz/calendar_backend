from __future__ import annotations

import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import unquote

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

DEFAULT_DATABASE_URL = "sqlite:///local_data/calendar_backend.sqlite3"


@event.listens_for(Engine, "connect")
def _set_sqlite_pragma(dbapi_connection: object, connection_record: object) -> None:  # pyright: ignore[reportUnusedFunction]
    del connection_record
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def create_engine_for_url(url: str = DEFAULT_DATABASE_URL) -> Engine:
    db_path = Path(unquote(url.removeprefix("sqlite:///")))
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return create_engine(url)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine)


@contextmanager
def transaction(session: Session) -> Generator[Session]:
    if session.in_transaction():
        with session.begin_nested():
            yield session
    else:
        with session.begin():
            yield session

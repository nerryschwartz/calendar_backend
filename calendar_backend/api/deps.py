"""FastAPI dependency injection."""

from __future__ import annotations

from collections.abc import Generator

from calendar_backend.db.session import (
    DEFAULT_DATABASE_URL,
    create_engine_for_url,
    create_session_factory,
)
from calendar_backend.domain.time import Clock, SystemClock
from fastapi import Request
from sqlalchemy.orm import Session

_engine = create_engine_for_url(DEFAULT_DATABASE_URL)
_session_factory = create_session_factory(_engine)


def get_db_session() -> Generator[Session]:
    session = _session_factory()
    try:
        yield session
    finally:
        session.close()


def get_clock(request: Request) -> Clock:
    override = getattr(request.app.state, "clock", None)
    return override if override is not None else SystemClock()

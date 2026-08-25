"""API test fixtures."""

from __future__ import annotations

import tempfile
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from calendar_backend.api.app import create_app
from calendar_backend.api.deps import get_clock, get_db_session
from calendar_backend.db.base import Base
from calendar_backend.db.session import create_engine_for_url, create_session_factory
from calendar_backend.domain.time import Clock
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session


class ApiTestClock:
    def __init__(self, now: datetime | None = None) -> None:
        self._now = now or datetime(2026, 6, 7, 10, 0, tzinfo=UTC)

    def now_utc(self) -> datetime:
        return self._now


@pytest.fixture
def api_db_engine() -> Generator[Engine]:
    with tempfile.TemporaryDirectory() as tmpdir:
        engine = create_engine_for_url(f"sqlite:///{Path(tmpdir) / 'api_test.sqlite3'}")
        Base.metadata.create_all(engine)
        try:
            yield engine
        finally:
            engine.dispose()


@pytest.fixture
def api_client(api_db_engine: Engine) -> Generator[TestClient]:
    yield from _api_client_for_clock(api_db_engine, ApiTestClock())


@pytest.fixture
def non_minute_api_client(api_db_engine: Engine) -> Generator[TestClient]:
    yield from _api_client_for_clock(
        api_db_engine,
        ApiTestClock(datetime(2026, 6, 7, 10, 0, 37, 123456, tzinfo=UTC)),
    )


def _api_client_for_clock(api_db_engine: Engine, clock: Clock) -> Generator[TestClient]:
    app = create_app()
    session_factory = create_session_factory(api_db_engine)

    def override_get_db_session() -> Generator[Session]:
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    def override_get_clock() -> Clock:
        return clock

    app.dependency_overrides[get_db_session] = override_get_db_session
    app.dependency_overrides[get_clock] = override_get_clock
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()

from __future__ import annotations

import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import calendar_backend.models.calendar  # pyright: ignore[reportUnusedImport]
import calendar_backend.models.chains  # pyright: ignore[reportUnusedImport]
import calendar_backend.models.constraints  # pyright: ignore[reportUnusedImport]
import calendar_backend.models.free_time  # pyright: ignore[reportUnusedImport]
import calendar_backend.models.plans  # pyright: ignore[reportUnusedImport]
import calendar_backend.models.repetitions  # pyright: ignore[reportUnusedImport]
import calendar_backend.models.runs  # pyright: ignore[reportUnusedImport]
import calendar_backend.models.settings  # noqa: F401  # pyright: ignore[reportUnusedImport]
import pytest
from calendar_backend.db.base import Base
from calendar_backend.db.session import create_engine_for_url, create_session_factory, transaction
from calendar_backend.domain.time import Clock
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class FakeClock:
    fixed: datetime

    def now_utc(self) -> datetime:
        return self.fixed


@pytest.fixture
def service_db_url() -> Generator[str]:
    with tempfile.TemporaryDirectory() as tmpdir:
        yield f"sqlite:///{Path(tmpdir) / 'service_test.sqlite3'}"


@pytest.fixture
def service_db_engine(service_db_url: str) -> Generator[Engine]:
    engine = create_engine_for_url(service_db_url)
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def service_db_session(service_db_engine: Engine) -> Generator[Session]:
    session = create_session_factory(service_db_engine)()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def fake_clock() -> Clock:
    return FakeClock(datetime(2026, 6, 7, 12, 0, tzinfo=UTC))


@contextmanager
def service_transaction(session: Session) -> Generator[Session]:
    """Run service-level work inside the db-layer transaction helper."""
    with transaction(session) as txn:
        yield txn

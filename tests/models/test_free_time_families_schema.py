from __future__ import annotations

import json
import tempfile
import uuid
from collections.abc import Generator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from calendar_backend.db.base import Base
from calendar_backend.db.session import create_engine_for_url, create_session_factory, transaction
from calendar_backend.models.free_time import FreeTimeActivity
from sqlalchemy import insert, inspect
from sqlalchemy.engine import Engine


@pytest.fixture
def temp_sqlite_url() -> Generator[str]:
    with tempfile.TemporaryDirectory() as tmpdir:
        yield f"sqlite:///{Path(tmpdir) / 'test.sqlite3'}"


@pytest.fixture
def free_time_families_schema_engine(temp_sqlite_url: str) -> Generator[Engine]:
    engine = create_engine_for_url(temp_sqlite_url)
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()


def _now() -> datetime:
    return datetime.now(UTC)


def _activity_row(
    activity_id: uuid.UUID,
    *,
    allowed_block_families: str | None = None,
) -> dict[str, object]:
    now = _now()
    return {
        "free_time_activity_id": activity_id,
        "name": "reading",
        "enabled": True,
        "real_fraction": Decimal("1"),
        "minimum_block_size_minutes": 30,
        "allowed_block_families": allowed_block_families,
        "created_at": now,
        "updated_at": now,
    }


def test_free_time_activity_allowed_block_families_column_nullable() -> None:
    column = Base.metadata.tables["free_time_activity"].c.allowed_block_families
    assert column.nullable is True


def test_free_time_activity_accepts_null_allowed_block_families(
    free_time_families_schema_engine: Engine,
) -> None:
    activity = Base.metadata.tables["free_time_activity"]
    activity_id = uuid.uuid4()
    engine = free_time_families_schema_engine
    session = create_session_factory(engine)()
    try:
        with transaction(session) as txn:
            txn.execute(insert(activity).values(_activity_row(activity_id)))
    finally:
        session.close()

    session = create_session_factory(engine)()
    try:
        loaded = session.get(FreeTimeActivity, activity_id)
        assert loaded is not None
        assert loaded.allowed_block_families is None
    finally:
        session.close()


def test_free_time_activity_accepts_json_array_allowed_block_families(
    free_time_families_schema_engine: Engine,
) -> None:
    activity = Base.metadata.tables["free_time_activity"]
    activity_id = uuid.uuid4()
    stored = json.dumps(["transit", "free-time"])
    engine = free_time_families_schema_engine
    session = create_session_factory(engine)()
    try:
        with transaction(session) as txn:
            txn.execute(
                insert(activity).values(_activity_row(activity_id, allowed_block_families=stored))
            )
    finally:
        session.close()

    session = create_session_factory(engine)()
    try:
        loaded = session.get(FreeTimeActivity, activity_id)
        assert loaded is not None
        assert loaded.allowed_block_families == stored
    finally:
        session.close()


@pytest.mark.integration
def test_alembic_upgrade_adds_free_time_allowed_block_families_column(
    temp_sqlite_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_create_engine_for_url = create_engine_for_url

    def _engine_for_migration(url: str = temp_sqlite_url) -> Engine:
        del url
        return real_create_engine_for_url(temp_sqlite_url)

    monkeypatch.setattr(
        "calendar_backend.db.session.create_engine_for_url",
        _engine_for_migration,
    )

    command.upgrade(Config("alembic.ini"), "head")

    engine = create_engine_for_url(temp_sqlite_url)
    try:
        columns = {column["name"] for column in inspect(engine).get_columns("free_time_activity")}
    finally:
        engine.dispose()

    assert "allowed_block_families" in columns

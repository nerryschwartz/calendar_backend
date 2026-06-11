from __future__ import annotations

import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest
from calendar_backend.db.session import create_engine_for_url, create_session_factory, transaction
from sqlalchemy import Column, ForeignKey, Integer, MetaData, Table, insert, select, text
from sqlalchemy.exc import IntegrityError


@pytest.fixture
def temp_sqlite_url() -> Generator[str]:
    with tempfile.TemporaryDirectory() as tmpdir:
        yield f"sqlite:///{Path(tmpdir) / 'test.sqlite3'}"


def _items_table(metadata: MetaData) -> Table:
    return Table(
        "items",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("value", Integer, nullable=False),
    )


@pytest.mark.integration
def test_create_engine_for_url_succeeds(temp_sqlite_url: str) -> None:
    engine = create_engine_for_url(temp_sqlite_url)
    with engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT 1").scalar_one() == 1


@pytest.mark.integration
def test_foreign_keys_pragma_enabled(temp_sqlite_url: str) -> None:
    engine = create_engine_for_url(temp_sqlite_url)
    with engine.connect() as connection:
        foreign_keys = connection.execute(text("PRAGMA foreign_keys")).scalar_one()
    assert foreign_keys == 1


@pytest.mark.integration
def test_transaction_commits(temp_sqlite_url: str) -> None:
    metadata = MetaData()
    items = _items_table(metadata)
    engine = create_engine_for_url(temp_sqlite_url)
    metadata.create_all(engine)
    session = create_session_factory(engine)()

    try:
        with transaction(session) as txn:
            txn.execute(insert(items).values(id=1, value=42))
    finally:
        session.close()

    with engine.connect() as connection:
        row = connection.execute(select(items)).mappings().one()
    assert row == {"id": 1, "value": 42}


@pytest.mark.integration
def test_transaction_rolls_back_on_exception(temp_sqlite_url: str) -> None:
    metadata = MetaData()
    items = _items_table(metadata)
    engine = create_engine_for_url(temp_sqlite_url)
    metadata.create_all(engine)
    session = create_session_factory(engine)()

    try:
        with pytest.raises(RuntimeError, match="boom"), transaction(session) as txn:
            txn.execute(insert(items).values(id=1, value=42))
            raise RuntimeError("boom")
    finally:
        session.close()

    with engine.connect() as connection:
        rows = connection.execute(select(items)).mappings().all()
    assert rows == []


@pytest.mark.integration
def test_foreign_key_enforcement(temp_sqlite_url: str) -> None:
    metadata = MetaData()
    Table("parent", metadata, Column("id", Integer, primary_key=True))
    child = Table(
        "child",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("parent_id", Integer, ForeignKey("parent.id"), nullable=False),
    )
    engine = create_engine_for_url(temp_sqlite_url)
    metadata.create_all(engine)
    session = create_session_factory(engine)()

    try:
        with pytest.raises(IntegrityError), transaction(session) as txn:
            txn.execute(insert(child).values(id=1, parent_id=999))
    finally:
        session.close()

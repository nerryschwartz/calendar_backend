from __future__ import annotations

import json
import tempfile
import uuid
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path

import calendar_backend.models.blocks  # pyright: ignore[reportUnusedImport]
import calendar_backend.models.calendar  # pyright: ignore[reportUnusedImport]
import calendar_backend.models.constraints  # pyright: ignore[reportUnusedImport]
import calendar_backend.models.repetitions  # noqa: F401  # pyright: ignore[reportUnusedImport]
import pytest
from alembic import command
from alembic.config import Config
from calendar_backend.db.base import Base
from calendar_backend.db.session import create_engine_for_url, create_session_factory, transaction
from calendar_backend.domain.enums import CloneStatus, PlanKind
from calendar_backend.models.plans import TaskPlan
from sqlalchemy import insert, inspect
from sqlalchemy.engine import Engine


@pytest.fixture
def temp_sqlite_url() -> Generator[str]:
    with tempfile.TemporaryDirectory() as tmpdir:
        yield f"sqlite:///{Path(tmpdir) / 'test.sqlite3'}"


@pytest.fixture
def task_families_schema_engine(temp_sqlite_url: str) -> Generator[Engine]:
    engine = create_engine_for_url(temp_sqlite_url)
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()


def _now() -> datetime:
    return datetime.now(UTC)


def _plan_row(plan_id: uuid.UUID) -> dict[str, object]:
    return {
        "plan_id": plan_id,
        "plan_kind": PlanKind.TASK,
        "name": "task",
        "parent_id": None,
        "is_master": False,
        "cloned_from_id": None,
        "clone_status": CloneStatus.NOT_CLONED,
        "created_at": _now(),
        "updated_at": _now(),
    }


def _task_plan_row(
    plan_id: uuid.UUID,
    *,
    allowed_block_families: str | None = None,
) -> dict[str, object]:
    return {
        "plan_id": plan_id,
        "duration_minutes": 30,
        "divisible": False,
        "minimum_chunk_size_minutes": None,
        "user_completed": False,
        "completed_at": None,
        "immediate_prerequisite_plan_id": None,
        "allowed_block_families": allowed_block_families,
    }


def test_task_plan_allowed_block_families_column_nullable() -> None:
    column = Base.metadata.tables["task_plan"].c.allowed_block_families
    assert column.nullable is True


def test_task_plan_accepts_null_allowed_block_families(
    task_families_schema_engine: Engine,
) -> None:
    plan = Base.metadata.tables["plan"]
    task_plan = Base.metadata.tables["task_plan"]
    task_id = uuid.uuid4()
    engine = task_families_schema_engine
    session = create_session_factory(engine)()
    try:
        with transaction(session) as txn:
            txn.execute(insert(plan).values(_plan_row(task_id)))
            txn.execute(insert(task_plan).values(_task_plan_row(task_id)))
    finally:
        session.close()

    session = create_session_factory(engine)()
    try:
        loaded = session.get(TaskPlan, task_id)
        assert loaded is not None
        assert loaded.allowed_block_families is None
    finally:
        session.close()


def test_task_plan_accepts_json_array_allowed_block_families(
    task_families_schema_engine: Engine,
) -> None:
    plan = Base.metadata.tables["plan"]
    task_plan = Base.metadata.tables["task_plan"]
    task_id = uuid.uuid4()
    stored = json.dumps(["transit", "default"])
    engine = task_families_schema_engine
    session = create_session_factory(engine)()
    try:
        with transaction(session) as txn:
            txn.execute(insert(plan).values(_plan_row(task_id)))
            txn.execute(
                insert(task_plan).values(_task_plan_row(task_id, allowed_block_families=stored))
            )
    finally:
        session.close()

    session = create_session_factory(engine)()
    try:
        loaded = session.get(TaskPlan, task_id)
        assert loaded is not None
        assert loaded.allowed_block_families == stored
    finally:
        session.close()


@pytest.mark.integration
def test_alembic_upgrade_adds_allowed_block_families_column(
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
        task_plan_columns = {column["name"] for column in inspect(engine).get_columns("task_plan")}
    finally:
        engine.dispose()

    assert "allowed_block_families" in task_plan_columns

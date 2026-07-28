from __future__ import annotations

import tempfile
import uuid
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path

import calendar_backend.models.constraints  # pyright: ignore[reportUnusedImport]
import calendar_backend.models.prerequisites  # pyright: ignore[reportUnusedImport]
import calendar_backend.models.repetitions  # noqa: F401  # pyright: ignore[reportUnusedImport]
import pytest
from alembic import command
from alembic.config import Config
from calendar_backend.db.base import Base
from calendar_backend.db.session import create_engine_for_url, create_session_factory, transaction
from calendar_backend.domain.enums import CloneStatus, PlanKind
from calendar_backend.models.plans import Plan, TaskPlan
from calendar_backend.models.prerequisites import PlanPrerequisite
from sqlalchemy import CheckConstraint, PrimaryKeyConstraint, insert, inspect
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

PREREQUISITE_TABLE_NAMES = frozenset({"plan_prerequisite"})


@pytest.fixture
def temp_sqlite_url() -> Generator[str]:
    with tempfile.TemporaryDirectory() as tmpdir:
        yield f"sqlite:///{Path(tmpdir) / 'test.sqlite3'}"


@pytest.fixture
def prerequisite_schema_engine(temp_sqlite_url: str) -> Generator[Engine]:
    engine = create_engine_for_url(temp_sqlite_url)
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()


def _now() -> datetime:
    return datetime.now(UTC)


def _plan_row(
    plan_id: uuid.UUID,
    *,
    plan_kind: PlanKind = PlanKind.TASK,
    parent_id: uuid.UUID | None = None,
) -> dict[str, object]:
    return {
        "plan_id": plan_id,
        "plan_kind": plan_kind,
        "name": "test plan",
        "parent_id": parent_id,
        "is_master": False,
        "cloned_from_id": None,
        "clone_status": CloneStatus.NOT_CLONED,
        "created_at": _now(),
        "updated_at": _now(),
    }


def _task_plan_row(
    plan_id: uuid.UUID,
    *,
    immediate_prerequisite_plan_id: uuid.UUID | None = None,
) -> dict[str, object]:
    return {
        "plan_id": plan_id,
        "duration_minutes": 30,
        "divisible": False,
        "minimum_chunk_size_minutes": None,
        "user_completed": False,
        "completed_at": None,
        "immediate_prerequisite_plan_id": immediate_prerequisite_plan_id,
    }


def test_prerequisites_metadata_includes_plan_prerequisite_table() -> None:
    table_names = set(Base.metadata.tables)
    assert table_names >= PREREQUISITE_TABLE_NAMES


def test_prerequisites_metadata_composite_primary_key() -> None:
    table = Base.metadata.tables["plan_prerequisite"]
    pk = next(
        constraint
        for constraint in table.constraints
        if isinstance(constraint, PrimaryKeyConstraint)
    )
    pk_columns = {column.name for column in pk.columns}
    assert pk_columns == {"plan_id", "prerequisite_plan_id"}


def test_prerequisites_metadata_foreign_keys() -> None:
    expected_fks = {
        ("plan_prerequisite", "plan_id"): "plan.plan_id",
        ("plan_prerequisite", "prerequisite_plan_id"): "plan.plan_id",
        ("task_plan", "immediate_prerequisite_plan_id"): "plan.plan_id",
    }

    for (table_name, column_name), target in expected_fks.items():
        column = Base.metadata.tables[table_name].c[column_name]
        fk_targets = {fk.target_fullname for fk in column.foreign_keys}
        assert fk_targets == {target}


def test_task_plan_immediate_prerequisite_column_nullable() -> None:
    column = Base.metadata.tables["task_plan"].c.immediate_prerequisite_plan_id
    assert column.nullable is True


def test_plan_prerequisite_metadata_self_edge_check() -> None:
    table = Base.metadata.tables["plan_prerequisite"]
    check_names = {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert "ck_plan_prerequisite_no_self_prerequisite" in check_names


@pytest.mark.integration
@pytest.mark.failure_expected
def test_foreign_key_invalid_plan_prerequisite_plan_id_rejected(
    prerequisite_schema_engine: Engine,
) -> None:
    plan_prerequisite = Base.metadata.tables["plan_prerequisite"]
    session = create_session_factory(prerequisite_schema_engine)()

    try:
        with pytest.raises(IntegrityError), transaction(session) as txn:
            txn.execute(
                insert(plan_prerequisite).values(
                    plan_id=uuid.uuid4(),
                    prerequisite_plan_id=uuid.uuid4(),
                )
            )
    finally:
        session.close()


@pytest.mark.integration
@pytest.mark.failure_expected
def test_foreign_key_invalid_immediate_prerequisite_plan_id_rejected(
    prerequisite_schema_engine: Engine,
) -> None:
    plan = Base.metadata.tables["plan"]
    task_plan = Base.metadata.tables["task_plan"]
    session = create_session_factory(prerequisite_schema_engine)()
    task_id = uuid.uuid4()

    try:
        with transaction(session) as txn:
            txn.execute(insert(plan).values(_plan_row(task_id)))

        with pytest.raises(IntegrityError), transaction(session) as txn:
            row = _task_plan_row(task_id, immediate_prerequisite_plan_id=uuid.uuid4())
            txn.execute(insert(task_plan).values(row))
    finally:
        session.close()


@pytest.mark.integration
@pytest.mark.failure_expected
def test_check_plan_prerequisite_rejects_self_edge(
    prerequisite_schema_engine: Engine,
) -> None:
    plan = Base.metadata.tables["plan"]
    plan_prerequisite = Base.metadata.tables["plan_prerequisite"]
    session = create_session_factory(prerequisite_schema_engine)()
    plan_id = uuid.uuid4()

    try:
        with transaction(session) as txn:
            txn.execute(insert(plan).values(_plan_row(plan_id)))

        with pytest.raises(IntegrityError), transaction(session) as txn:
            txn.execute(
                insert(plan_prerequisite).values(
                    plan_id=plan_id,
                    prerequisite_plan_id=plan_id,
                )
            )
    finally:
        session.close()


@pytest.mark.integration
@pytest.mark.failure_expected
def test_plan_prerequisite_accepts_valid_edge(
    prerequisite_schema_engine: Engine,
) -> None:
    plan = Base.metadata.tables["plan"]
    plan_prerequisite = Base.metadata.tables["plan_prerequisite"]
    session = create_session_factory(prerequisite_schema_engine)()
    dependent_id = uuid.uuid4()
    prerequisite_id = uuid.uuid4()

    try:
        with transaction(session) as txn:
            txn.execute(insert(plan).values(_plan_row(dependent_id)))
            txn.execute(insert(plan).values(_plan_row(prerequisite_id)))
            txn.execute(
                insert(plan_prerequisite).values(
                    plan_id=dependent_id,
                    prerequisite_plan_id=prerequisite_id,
                )
            )

        loaded = session.get(PlanPrerequisite, (dependent_id, prerequisite_id))
        assert loaded is not None
        assert loaded.plan_id == dependent_id
        assert loaded.prerequisite_plan_id == prerequisite_id
    finally:
        session.close()


@pytest.mark.integration
@pytest.mark.failure_expected
def test_task_plan_accepts_null_immediate_prerequisite(
    prerequisite_schema_engine: Engine,
) -> None:
    plan = Base.metadata.tables["plan"]
    task_plan = Base.metadata.tables["task_plan"]
    session = create_session_factory(prerequisite_schema_engine)()
    task_id = uuid.uuid4()

    try:
        with transaction(session) as txn:
            txn.execute(insert(plan).values(_plan_row(task_id)))
            txn.execute(insert(task_plan).values(_task_plan_row(task_id)))

        loaded = session.get(TaskPlan, task_id)
        assert loaded is not None
        assert loaded.immediate_prerequisite_plan_id is None
    finally:
        session.close()


@pytest.mark.integration
@pytest.mark.failure_expected
def test_alembic_upgrade_creates_plan_prerequisite_table(
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
        table_names = set(inspect(engine).get_table_names())
        task_columns = {column["name"] for column in inspect(engine).get_columns("task_plan")}
    finally:
        engine.dispose()

    assert "plan_prerequisite" in table_names
    assert "immediate_prerequisite_plan_id" in task_columns


@pytest.mark.integration
@pytest.mark.failure_expected
def test_alembic_upgrade_enforces_plan_prerequisite_self_edge_check(
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
    session = create_session_factory(engine)()
    plan = Base.metadata.tables["plan"]
    plan_prerequisite = Base.metadata.tables["plan_prerequisite"]
    plan_id = uuid.uuid4()

    try:
        with transaction(session) as txn:
            txn.execute(insert(plan).values(_plan_row(plan_id)))

        with pytest.raises(IntegrityError), transaction(session) as txn:
            txn.execute(
                insert(plan_prerequisite).values(
                    plan_id=plan_id,
                    prerequisite_plan_id=plan_id,
                )
            )
    finally:
        session.close()
        engine.dispose()


@pytest.mark.integration
def test_relationships_navigate_plan_prerequisite_edges(
    prerequisite_schema_engine: Engine,
) -> None:
    session = create_session_factory(prerequisite_schema_engine)()
    dependent_id = uuid.uuid4()
    prerequisite_id = uuid.uuid4()
    now = _now()

    try:
        with transaction(session):
            session.add(
                Plan(
                    plan_id=dependent_id,
                    plan_kind=PlanKind.TASK,
                    name="dependent task",
                    parent_id=None,
                    is_master=False,
                    cloned_from_id=None,
                    clone_status=CloneStatus.NOT_CLONED,
                    created_at=now,
                    updated_at=now,
                )
            )
            session.add(
                Plan(
                    plan_id=prerequisite_id,
                    plan_kind=PlanKind.TASK,
                    name="prerequisite task",
                    parent_id=None,
                    is_master=False,
                    cloned_from_id=None,
                    clone_status=CloneStatus.NOT_CLONED,
                    created_at=now,
                    updated_at=now,
                )
            )
            session.add(
                PlanPrerequisite(
                    plan_id=dependent_id,
                    prerequisite_plan_id=prerequisite_id,
                )
            )

        dependent = session.get(Plan, dependent_id)
        assert dependent is not None
        assert len(dependent.prerequisite_edges) == 1
        assert dependent.prerequisite_edges[0].prerequisite_plan_id == prerequisite_id

        prerequisite = session.get(Plan, prerequisite_id)
        assert prerequisite is not None
        assert len(prerequisite.dependent_edges) == 1
        assert prerequisite.dependent_edges[0].plan_id == dependent_id
    finally:
        session.close()

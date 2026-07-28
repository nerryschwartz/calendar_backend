from __future__ import annotations

import tempfile
import uuid
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
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
from calendar_backend.models.blocks import BlockCalendarEntry, BlockPlan
from calendar_backend.models.plans import Plan
from sqlalchemy import CheckConstraint, DateTime, PrimaryKeyConstraint, insert, inspect
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

BLOCK_TABLE_NAMES = frozenset({"block_plan", "block_calendar_entry"})


@pytest.fixture
def temp_sqlite_url() -> Generator[str]:
    with tempfile.TemporaryDirectory() as tmpdir:
        yield f"sqlite:///{Path(tmpdir) / 'test.sqlite3'}"


@pytest.fixture
def block_schema_engine(temp_sqlite_url: str) -> Generator[Engine]:
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
    plan_kind: PlanKind = PlanKind.BLOCK,
    parent_id: uuid.UUID | None = None,
) -> dict[str, object]:
    return {
        "plan_id": plan_id,
        "plan_kind": plan_kind,
        "name": "test block",
        "parent_id": parent_id,
        "is_master": False,
        "cloned_from_id": None,
        "clone_status": CloneStatus.NOT_CLONED,
        "created_at": _now(),
        "updated_at": _now(),
    }


def _block_plan_row(
    plan_id: uuid.UUID,
    *,
    immediate_prerequisite_plan_id: uuid.UUID | None = None,
    block_family: str = "focus",
) -> dict[str, object]:
    return {
        "plan_id": plan_id,
        "duration_minutes": 30,
        "divisible": False,
        "minimum_chunk_size_minutes": None,
        "user_completed": False,
        "completed_at": None,
        "block_family": block_family,
        "immediate_prerequisite_plan_id": immediate_prerequisite_plan_id,
    }


def _block_calendar_entry_row(
    *,
    block_calendar_entry_id: uuid.UUID,
    source_plan_id: uuid.UUID,
    start_time: datetime | None = None,
) -> dict[str, object]:
    start = start_time or _now()
    end = start + timedelta(minutes=30)
    return {
        "block_calendar_entry_id": block_calendar_entry_id,
        "start_time": start,
        "end_time": end,
        "source_plan_id": source_plan_id,
        "calendar_run_id": None,
        "display_label": "focus block",
        "created_at": _now(),
        "updated_at": _now(),
    }


def test_plan_kind_includes_block() -> None:
    assert PlanKind.BLOCK == "BLOCK"


def test_blocks_metadata_includes_block_tables() -> None:
    table_names = set(Base.metadata.tables)
    assert table_names >= BLOCK_TABLE_NAMES


def test_block_plan_metadata_primary_key() -> None:
    table = Base.metadata.tables["block_plan"]
    pk = next(
        constraint
        for constraint in table.constraints
        if isinstance(constraint, PrimaryKeyConstraint)
    )
    pk_columns = {column.name for column in pk.columns}
    assert pk_columns == {"plan_id"}


def test_block_calendar_entry_metadata_primary_key() -> None:
    table = Base.metadata.tables["block_calendar_entry"]
    pk = next(
        constraint
        for constraint in table.constraints
        if isinstance(constraint, PrimaryKeyConstraint)
    )
    pk_columns = {column.name for column in pk.columns}
    assert pk_columns == {"block_calendar_entry_id"}


def test_blocks_metadata_foreign_keys() -> None:
    expected_fks = {
        ("block_plan", "plan_id"): "plan.plan_id",
        ("block_plan", "immediate_prerequisite_plan_id"): "plan.plan_id",
        ("block_calendar_entry", "source_plan_id"): "plan.plan_id",
        ("block_calendar_entry", "calendar_run_id"): "calendar_run.calendar_run_id",
    }

    for (table_name, column_name), target in expected_fks.items():
        column = Base.metadata.tables[table_name].c[column_name]
        fk_targets = {fk.target_fullname for fk in column.foreign_keys}
        assert fk_targets == {target}


def test_block_plan_immediate_prerequisite_column_nullable() -> None:
    column = Base.metadata.tables["block_plan"].c.immediate_prerequisite_plan_id
    assert column.nullable is True


def test_block_plan_metadata_check_constraints() -> None:
    table = Base.metadata.tables["block_plan"]
    check_names = {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert check_names == {
        "ck_block_plan_block_duration_positive",
        "ck_block_plan_block_chunk_matches_divisibility",
        "ck_block_plan_block_minimum_chunk_positive_when_set",
        "ck_block_plan_block_minimum_chunk_lte_duration",
        "ck_block_plan_block_family_non_empty",
    }


def test_block_calendar_entry_metadata_check_constraints() -> None:
    table = Base.metadata.tables["block_calendar_entry"]
    check_names = {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert check_names == {"ck_block_calendar_entry_block_calendar_start_before_end"}


def test_block_metadata_timezone_aware_datetime_columns() -> None:
    timezone_columns = (
        Base.metadata.tables["block_plan"].c.completed_at,
        Base.metadata.tables["block_calendar_entry"].c.start_time,
        Base.metadata.tables["block_calendar_entry"].c.end_time,
        Base.metadata.tables["block_calendar_entry"].c.created_at,
        Base.metadata.tables["block_calendar_entry"].c.updated_at,
    )
    for column in timezone_columns:
        assert isinstance(column.type, DateTime)
        assert column.type.timezone is True


@pytest.mark.integration
def test_foreign_key_invalid_block_plan_id_rejected(block_schema_engine: Engine) -> None:
    block_plan = Base.metadata.tables["block_plan"]
    session = create_session_factory(block_schema_engine)()

    try:
        with pytest.raises(IntegrityError), transaction(session) as txn:
            txn.execute(insert(block_plan).values(_block_plan_row(uuid.uuid4())))
    finally:
        session.close()


@pytest.mark.integration
def test_foreign_key_invalid_immediate_prerequisite_plan_id_rejected(
    block_schema_engine: Engine,
) -> None:
    plan = Base.metadata.tables["plan"]
    block_plan = Base.metadata.tables["block_plan"]
    session = create_session_factory(block_schema_engine)()
    block_id = uuid.uuid4()

    try:
        with transaction(session) as txn:
            txn.execute(insert(plan).values(_plan_row(block_id)))

        with pytest.raises(IntegrityError), transaction(session) as txn:
            row = _block_plan_row(block_id, immediate_prerequisite_plan_id=uuid.uuid4())
            txn.execute(insert(block_plan).values(row))
    finally:
        session.close()


@pytest.mark.integration
def test_check_block_plan_rejects_non_positive_duration(
    block_schema_engine: Engine,
) -> None:
    plan = Base.metadata.tables["plan"]
    block_plan = Base.metadata.tables["block_plan"]
    session = create_session_factory(block_schema_engine)()
    block_id = uuid.uuid4()

    try:
        with transaction(session) as txn:
            txn.execute(insert(plan).values(_plan_row(block_id)))

        with pytest.raises(IntegrityError), transaction(session) as txn:
            row = _block_plan_row(block_id)
            row["duration_minutes"] = 0
            txn.execute(insert(block_plan).values(row))
    finally:
        session.close()


@pytest.mark.integration
def test_check_block_plan_rejects_empty_block_family(
    block_schema_engine: Engine,
) -> None:
    plan = Base.metadata.tables["plan"]
    block_plan = Base.metadata.tables["block_plan"]
    session = create_session_factory(block_schema_engine)()
    block_id = uuid.uuid4()

    try:
        with transaction(session) as txn:
            txn.execute(insert(plan).values(_plan_row(block_id)))

        with pytest.raises(IntegrityError), transaction(session) as txn:
            txn.execute(insert(block_plan).values(_block_plan_row(block_id, block_family="   ")))
    finally:
        session.close()


@pytest.mark.integration
def test_check_block_calendar_entry_rejects_start_after_end(
    block_schema_engine: Engine,
) -> None:
    plan = Base.metadata.tables["plan"]
    block_plan = Base.metadata.tables["block_plan"]
    block_calendar_entry = Base.metadata.tables["block_calendar_entry"]
    session = create_session_factory(block_schema_engine)()
    block_id = uuid.uuid4()
    now = _now()

    try:
        with transaction(session) as txn:
            txn.execute(insert(plan).values(_plan_row(block_id)))
            txn.execute(insert(block_plan).values(_block_plan_row(block_id)))

        with pytest.raises(IntegrityError), transaction(session) as txn:
            row = _block_calendar_entry_row(
                block_calendar_entry_id=uuid.uuid4(),
                source_plan_id=block_id,
                start_time=now + timedelta(minutes=30),
            )
            row["end_time"] = now
            txn.execute(insert(block_calendar_entry).values(row))
    finally:
        session.close()


@pytest.mark.integration
def test_block_plan_accepts_valid_row(block_schema_engine: Engine) -> None:
    plan = Base.metadata.tables["plan"]
    block_plan = Base.metadata.tables["block_plan"]
    session = create_session_factory(block_schema_engine)()
    block_id = uuid.uuid4()

    try:
        with transaction(session) as txn:
            txn.execute(insert(plan).values(_plan_row(block_id)))
            txn.execute(insert(block_plan).values(_block_plan_row(block_id)))

        loaded = session.get(BlockPlan, block_id)
        assert loaded is not None
        assert loaded.block_family == "focus"
        assert loaded.immediate_prerequisite_plan_id is None
    finally:
        session.close()


@pytest.mark.integration
def test_relationships_navigate_plan_block_plan(block_schema_engine: Engine) -> None:
    session = create_session_factory(block_schema_engine)()
    block_id = uuid.uuid4()
    now = _now()

    try:
        with transaction(session):
            session.add(
                Plan(
                    plan_id=block_id,
                    plan_kind=PlanKind.BLOCK,
                    name="focus block",
                    parent_id=None,
                    is_master=False,
                    cloned_from_id=None,
                    clone_status=CloneStatus.NOT_CLONED,
                    created_at=now,
                    updated_at=now,
                )
            )
            session.add(
                BlockPlan(
                    plan_id=block_id,
                    duration_minutes=30,
                    divisible=False,
                    minimum_chunk_size_minutes=None,
                    user_completed=False,
                    completed_at=None,
                    block_family="focus",
                    immediate_prerequisite_plan_id=None,
                )
            )

        loaded = session.get(Plan, block_id)
        assert loaded is not None
        assert loaded.block_plan is not None
        assert loaded.block_plan.block_family == "focus"
    finally:
        session.close()


@pytest.mark.integration
def test_relationships_navigate_block_calendar_entry_source_plan(
    block_schema_engine: Engine,
) -> None:
    session = create_session_factory(block_schema_engine)()
    block_id = uuid.uuid4()
    entry_id = uuid.uuid4()
    now = _now()

    try:
        with transaction(session):
            session.add(
                Plan(
                    plan_id=block_id,
                    plan_kind=PlanKind.BLOCK,
                    name="focus block",
                    parent_id=None,
                    is_master=False,
                    cloned_from_id=None,
                    clone_status=CloneStatus.NOT_CLONED,
                    created_at=now,
                    updated_at=now,
                )
            )
            session.add(
                BlockPlan(
                    plan_id=block_id,
                    duration_minutes=30,
                    divisible=False,
                    minimum_chunk_size_minutes=None,
                    user_completed=False,
                    completed_at=None,
                    block_family="focus",
                    immediate_prerequisite_plan_id=None,
                )
            )
            session.add(
                BlockCalendarEntry(
                    block_calendar_entry_id=entry_id,
                    start_time=now,
                    end_time=now + timedelta(minutes=30),
                    source_plan_id=block_id,
                    calendar_run_id=None,
                    display_label="focus block",
                    created_at=now,
                    updated_at=now,
                )
            )

        loaded = session.get(BlockCalendarEntry, entry_id)
        assert loaded is not None
        assert loaded.source_plan is not None
        assert loaded.source_plan.plan_id == block_id
    finally:
        session.close()


@pytest.mark.integration
def test_alembic_upgrade_creates_block_tables(
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
        block_plan_columns = {
            column["name"] for column in inspect(engine).get_columns("block_plan")
        }
    finally:
        engine.dispose()

    assert table_names >= BLOCK_TABLE_NAMES
    assert "block_family" in block_plan_columns
    assert "immediate_prerequisite_plan_id" in block_plan_columns


@pytest.mark.integration
def test_alembic_upgrade_enforces_block_family_non_empty_check(
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
    block_plan = Base.metadata.tables["block_plan"]
    block_id = uuid.uuid4()

    try:
        with transaction(session) as txn:
            txn.execute(insert(plan).values(_plan_row(block_id)))

        with pytest.raises(IntegrityError), transaction(session) as txn:
            txn.execute(insert(block_plan).values(_block_plan_row(block_id, block_family="   ")))
    finally:
        session.close()
        engine.dispose()

from __future__ import annotations

import tempfile
import uuid
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path

import calendar_backend.models.constraints  # pyright: ignore[reportUnusedImport]
import calendar_backend.models.repetitions  # noqa: F401  # pyright: ignore[reportUnusedImport]
import pytest
from alembic import command
from alembic.config import Config
from calendar_backend.db.base import Base
from calendar_backend.db.session import create_engine_for_url, create_session_factory, transaction
from calendar_backend.domain.enums import CloneStatus, PlanKind, RepeatMode
from calendar_backend.models.plans import GoalPlan, Plan, RepetitionPlan, TaskPlan
from sqlalchemy import CheckConstraint, DateTime, insert, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

PLAN_TABLE_NAMES = frozenset(
    {
        "plan",
        "goal_plan",
        "task_plan",
        "repetition_plan",
    }
)

CHAIN_TABLE_NAMES = frozenset(
    {
        "goal_child_chain",
        "goal_child_chain_item",
    }
)

TIMEZONE_AWARE_COLUMNS = (
    Base.metadata.tables["plan"].c.created_at,
    Base.metadata.tables["plan"].c.updated_at,
    Base.metadata.tables["task_plan"].c.completed_at,
    Base.metadata.tables["repetition_plan"].c.start_time,
    Base.metadata.tables["repetition_plan"].c.end_time,
    Base.metadata.tables["repetition_plan"].c.generated_at,
)


@pytest.fixture
def temp_sqlite_url() -> Generator[str]:
    with tempfile.TemporaryDirectory() as tmpdir:
        yield f"sqlite:///{Path(tmpdir) / 'test.sqlite3'}"


@pytest.fixture
def plan_schema_engine(temp_sqlite_url: str) -> Generator[Engine]:
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
    plan_kind: PlanKind = PlanKind.GOAL,
    is_master: bool = False,
    parent_id: uuid.UUID | None = None,
    cloned_from_id: uuid.UUID | None = None,
    goal_is_critical: bool | None = None,
    goal_sort_order: int | None = None,
) -> dict[str, object]:
    row: dict[str, object] = {
        "plan_id": plan_id,
        "plan_kind": plan_kind,
        "name": "test plan",
        "parent_id": parent_id,
        "is_master": is_master,
        "cloned_from_id": cloned_from_id,
        "clone_status": CloneStatus.NOT_CLONED,
        "created_at": _now(),
        "updated_at": _now(),
    }
    if goal_is_critical is not None or goal_sort_order is not None:
        row["goal_is_critical"] = goal_is_critical
        row["goal_sort_order"] = goal_sort_order
    return row


def _goal_plan_row(plan_id: uuid.UUID) -> dict[str, object]:
    return {"plan_id": plan_id}


def _task_plan_row(plan_id: uuid.UUID) -> dict[str, object]:
    return {
        "plan_id": plan_id,
        "duration_minutes": 30,
        "divisible": False,
        "minimum_chunk_size_minutes": None,
        "user_completed": False,
        "completed_at": None,
    }


def _repetition_plan_row(plan_id: uuid.UUID, template_root_id: uuid.UUID) -> dict[str, object]:
    return {
        "plan_id": plan_id,
        "repeat_mode": RepeatMode.MANUAL_COUNT,
        "start_time": _now(),
        "repeat_interval_minutes": 60,
        "manual_count": 1,
        "end_time": None,
        "template_root_id": template_root_id,
        "default_instance_critical": False,
        "generated_at": None,
    }


def test_plan_metadata_includes_all_plan_tables() -> None:
    table_names = set(Base.metadata.tables)
    assert table_names >= PLAN_TABLE_NAMES
    assert CHAIN_TABLE_NAMES.isdisjoint(table_names)


def test_plan_metadata_key_columns_present() -> None:
    plan = Base.metadata.tables["plan"]
    task_plan = Base.metadata.tables["task_plan"]

    assert "is_master" in plan.c
    assert "goal_is_critical" in plan.c
    assert "goal_sort_order" in plan.c
    assert plan.c.goal_is_critical.nullable is True
    assert plan.c.goal_sort_order.nullable is True
    assert "duration_minutes" in task_plan.c


def test_plan_metadata_foreign_keys() -> None:
    expected_fks = {
        ("plan", "parent_id"): "plan.plan_id",
        ("plan", "cloned_from_id"): "plan.plan_id",
        ("goal_plan", "plan_id"): "plan.plan_id",
        ("task_plan", "plan_id"): "plan.plan_id",
        ("repetition_plan", "plan_id"): "plan.plan_id",
        ("repetition_plan", "template_root_id"): "plan.plan_id",
    }

    for (table_name, column_name), target in expected_fks.items():
        column = Base.metadata.tables[table_name].c[column_name]
        fk_targets = {fk.target_fullname for fk in column.foreign_keys}
        assert fk_targets == {target}


def test_plan_metadata_partial_unique_master_index() -> None:
    plan_table = Base.metadata.tables["plan"]
    master_index = next(idx for idx in plan_table.indexes if idx.name == "uq_plan_is_master")
    assert master_index.unique is True
    sqlite_where = master_index.kwargs.get("sqlite_where")
    assert sqlite_where is not None
    assert str(sqlite_where) == "is_master = 1"


def test_plan_metadata_table_check_constraints() -> None:
    expected = {
        "plan": "ck_plan_master_is_goal",
        "repetition_plan": "ck_repetition_plan_repeat_interval_positive",
        "task_plan": "ck_task_plan_duration_positive",
    }

    for table_name, check_name in expected.items():
        table = Base.metadata.tables[table_name]
        check_names = {
            constraint.name
            for constraint in table.constraints
            if isinstance(constraint, CheckConstraint)
        }
        assert check_name in check_names

    plan_checks = {
        constraint.name
        for constraint in Base.metadata.tables["plan"].constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert "ck_plan_goal_child_ordering_fields_paired" in plan_checks

    repetition_plan_checks = {
        constraint.name
        for constraint in Base.metadata.tables["repetition_plan"].constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert "ck_repetition_plan_end_after_start" in repetition_plan_checks
    assert "ck_repetition_plan_manual_count_positive_when_set" in repetition_plan_checks
    assert "ck_repetition_plan_manual_count_mode_fields" in repetition_plan_checks
    assert "ck_repetition_plan_date_range_mode_fields" in repetition_plan_checks

    task_plan_checks = {
        constraint.name
        for constraint in Base.metadata.tables["task_plan"].constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert "ck_task_plan_task_chunk_matches_divisibility" in task_plan_checks
    assert "ck_task_plan_minimum_chunk_positive_when_set" in task_plan_checks
    assert "ck_task_plan_minimum_chunk_lte_duration" in task_plan_checks


def test_plan_metadata_timezone_aware_datetime_columns() -> None:
    for column in TIMEZONE_AWARE_COLUMNS:
        assert isinstance(column.type, DateTime)
        assert column.type.timezone is True


@pytest.mark.integration
def test_partial_unique_rejects_second_master(plan_schema_engine: Engine) -> None:
    plan = Base.metadata.tables["plan"]
    goal_plan = Base.metadata.tables["goal_plan"]
    session = create_session_factory(plan_schema_engine)()

    master_one_id = uuid.uuid4()
    master_two_id = uuid.uuid4()

    try:
        with transaction(session) as txn:
            txn.execute(insert(plan).values(_plan_row(master_one_id, is_master=True)))
            txn.execute(insert(goal_plan).values(_goal_plan_row(master_one_id)))

        with pytest.raises(IntegrityError), transaction(session) as txn:
            txn.execute(insert(plan).values(_plan_row(master_two_id, is_master=True)))
            txn.execute(insert(goal_plan).values(_goal_plan_row(master_two_id)))
    finally:
        session.close()


@pytest.mark.integration
def test_check_master_must_be_goal(plan_schema_engine: Engine) -> None:
    plan = Base.metadata.tables["plan"]
    session = create_session_factory(plan_schema_engine)()

    try:
        with pytest.raises(IntegrityError), transaction(session) as txn:
            txn.execute(
                insert(plan).values(
                    _plan_row(uuid.uuid4(), plan_kind=PlanKind.TASK, is_master=True)
                )
            )
    finally:
        session.close()


@pytest.mark.integration
def test_foreign_key_invalid_parent_id_rejected(plan_schema_engine: Engine) -> None:
    plan = Base.metadata.tables["plan"]
    session = create_session_factory(plan_schema_engine)()

    try:
        with pytest.raises(IntegrityError), transaction(session) as txn:
            txn.execute(insert(plan).values(_plan_row(uuid.uuid4(), parent_id=uuid.uuid4())))
    finally:
        session.close()


@pytest.mark.integration
def test_foreign_key_invalid_cloned_from_id_rejected(plan_schema_engine: Engine) -> None:
    plan = Base.metadata.tables["plan"]
    session = create_session_factory(plan_schema_engine)()

    try:
        with pytest.raises(IntegrityError), transaction(session) as txn:
            txn.execute(insert(plan).values(_plan_row(uuid.uuid4(), cloned_from_id=uuid.uuid4())))
    finally:
        session.close()


@pytest.mark.integration
def test_foreign_key_invalid_goal_plan_id_rejected(plan_schema_engine: Engine) -> None:
    goal_plan = Base.metadata.tables["goal_plan"]
    session = create_session_factory(plan_schema_engine)()

    try:
        with pytest.raises(IntegrityError), transaction(session) as txn:
            txn.execute(insert(goal_plan).values(_goal_plan_row(uuid.uuid4())))
    finally:
        session.close()


@pytest.mark.integration
def test_foreign_key_invalid_task_plan_id_rejected(plan_schema_engine: Engine) -> None:
    task_plan = Base.metadata.tables["task_plan"]
    session = create_session_factory(plan_schema_engine)()

    try:
        with pytest.raises(IntegrityError), transaction(session) as txn:
            txn.execute(insert(task_plan).values(_task_plan_row(uuid.uuid4())))
    finally:
        session.close()


@pytest.mark.integration
def test_foreign_key_invalid_repetition_plan_id_rejected(plan_schema_engine: Engine) -> None:
    repetition_plan = Base.metadata.tables["repetition_plan"]
    session = create_session_factory(plan_schema_engine)()

    try:
        with pytest.raises(IntegrityError), transaction(session) as txn:
            txn.execute(
                insert(repetition_plan).values(_repetition_plan_row(uuid.uuid4(), uuid.uuid4()))
            )
    finally:
        session.close()


@pytest.mark.integration
def test_foreign_key_invalid_repetition_template_root_id_rejected(
    plan_schema_engine: Engine,
) -> None:
    plan = Base.metadata.tables["plan"]
    repetition_plan = Base.metadata.tables["repetition_plan"]
    session = create_session_factory(plan_schema_engine)()

    repetition_id = uuid.uuid4()

    try:
        with transaction(session) as txn:
            txn.execute(
                insert(plan).values(_plan_row(repetition_id, plan_kind=PlanKind.REPETITION))
            )

        with pytest.raises(IntegrityError), transaction(session) as txn:
            txn.execute(
                insert(repetition_plan).values(_repetition_plan_row(repetition_id, uuid.uuid4()))
            )
    finally:
        session.close()


@pytest.mark.integration
def test_check_repetition_plan_repeat_interval_positive(plan_schema_engine: Engine) -> None:
    plan = Base.metadata.tables["plan"]
    repetition_plan = Base.metadata.tables["repetition_plan"]
    session = create_session_factory(plan_schema_engine)()
    repetition_id = uuid.uuid4()
    template_id = uuid.uuid4()

    try:
        with transaction(session) as txn:
            txn.execute(insert(plan).values(_plan_row(template_id, is_master=True)))
            txn.execute(
                insert(Base.metadata.tables["goal_plan"]).values(_goal_plan_row(template_id))
            )
            txn.execute(
                insert(plan).values(_plan_row(repetition_id, plan_kind=PlanKind.REPETITION))
            )

        with pytest.raises(IntegrityError), transaction(session) as txn:
            row = _repetition_plan_row(repetition_id, template_id)
            row["repeat_interval_minutes"] = 0
            txn.execute(insert(repetition_plan).values(row))
    finally:
        session.close()


@pytest.mark.integration
def test_check_repetition_plan_end_after_start(plan_schema_engine: Engine) -> None:
    plan = Base.metadata.tables["plan"]
    repetition_plan = Base.metadata.tables["repetition_plan"]
    session = create_session_factory(plan_schema_engine)()
    repetition_id = uuid.uuid4()
    template_id = uuid.uuid4()
    now = _now()

    try:
        with transaction(session) as txn:
            txn.execute(insert(plan).values(_plan_row(template_id, is_master=True)))
            txn.execute(
                insert(Base.metadata.tables["goal_plan"]).values(_goal_plan_row(template_id))
            )
            txn.execute(
                insert(plan).values(_plan_row(repetition_id, plan_kind=PlanKind.REPETITION))
            )

        with pytest.raises(IntegrityError), transaction(session) as txn:
            row = _repetition_plan_row(repetition_id, template_id)
            row["start_time"] = now
            row["end_time"] = now
            txn.execute(insert(repetition_plan).values(row))
    finally:
        session.close()


@pytest.mark.integration
def test_check_repetition_plan_manual_count_positive_when_set(
    plan_schema_engine: Engine,
) -> None:
    plan = Base.metadata.tables["plan"]
    repetition_plan = Base.metadata.tables["repetition_plan"]
    session = create_session_factory(plan_schema_engine)()
    repetition_id = uuid.uuid4()
    template_id = uuid.uuid4()

    try:
        with transaction(session) as txn:
            txn.execute(insert(plan).values(_plan_row(template_id, is_master=True)))
            txn.execute(
                insert(Base.metadata.tables["goal_plan"]).values(_goal_plan_row(template_id))
            )
            txn.execute(
                insert(plan).values(_plan_row(repetition_id, plan_kind=PlanKind.REPETITION))
            )

        with pytest.raises(IntegrityError), transaction(session) as txn:
            row = _repetition_plan_row(repetition_id, template_id)
            row["manual_count"] = 0
            txn.execute(insert(repetition_plan).values(row))
    finally:
        session.close()


@pytest.mark.integration
def test_check_task_plan_duration_positive(plan_schema_engine: Engine) -> None:
    plan = Base.metadata.tables["plan"]
    task_plan = Base.metadata.tables["task_plan"]
    session = create_session_factory(plan_schema_engine)()
    task_id = uuid.uuid4()

    try:
        with transaction(session) as txn:
            txn.execute(insert(plan).values(_plan_row(task_id, plan_kind=PlanKind.TASK)))

        with pytest.raises(IntegrityError), transaction(session) as txn:
            row = _task_plan_row(task_id)
            row["duration_minutes"] = 0
            txn.execute(insert(task_plan).values(row))
    finally:
        session.close()


@pytest.mark.integration
def test_check_task_plan_chunk_matches_divisibility(plan_schema_engine: Engine) -> None:
    plan = Base.metadata.tables["plan"]
    task_plan = Base.metadata.tables["task_plan"]
    session = create_session_factory(plan_schema_engine)()
    task_id = uuid.uuid4()

    try:
        with transaction(session) as txn:
            txn.execute(insert(plan).values(_plan_row(task_id, plan_kind=PlanKind.TASK)))

        with pytest.raises(IntegrityError), transaction(session) as txn:
            row = _task_plan_row(task_id)
            row["divisible"] = True
            row["minimum_chunk_size_minutes"] = None
            txn.execute(insert(task_plan).values(row))
    finally:
        session.close()


@pytest.mark.integration
def test_check_task_plan_minimum_chunk_positive_when_set(plan_schema_engine: Engine) -> None:
    plan = Base.metadata.tables["plan"]
    task_plan = Base.metadata.tables["task_plan"]
    session = create_session_factory(plan_schema_engine)()
    task_id = uuid.uuid4()

    try:
        with transaction(session) as txn:
            txn.execute(insert(plan).values(_plan_row(task_id, plan_kind=PlanKind.TASK)))

        with pytest.raises(IntegrityError), transaction(session) as txn:
            row = _task_plan_row(task_id)
            row["divisible"] = True
            row["minimum_chunk_size_minutes"] = 0
            txn.execute(insert(task_plan).values(row))
    finally:
        session.close()


@pytest.mark.integration
def test_check_task_plan_minimum_chunk_lte_duration(plan_schema_engine: Engine) -> None:
    plan = Base.metadata.tables["plan"]
    task_plan = Base.metadata.tables["task_plan"]
    session = create_session_factory(plan_schema_engine)()
    task_id = uuid.uuid4()

    try:
        with transaction(session) as txn:
            txn.execute(insert(plan).values(_plan_row(task_id, plan_kind=PlanKind.TASK)))

        with pytest.raises(IntegrityError), transaction(session) as txn:
            row = _task_plan_row(task_id)
            row["divisible"] = True
            row["minimum_chunk_size_minutes"] = 60
            txn.execute(insert(task_plan).values(row))
    finally:
        session.close()


@pytest.mark.integration
def test_plan_goal_ordering_fields_null_by_default(plan_schema_engine: Engine) -> None:
    plan = Base.metadata.tables["plan"]
    session = create_session_factory(plan_schema_engine)()
    plan_id = uuid.uuid4()

    try:
        with transaction(session) as txn:
            txn.execute(insert(plan).values(_plan_row(plan_id, is_master=True)))
            txn.execute(insert(Base.metadata.tables["goal_plan"]).values(_goal_plan_row(plan_id)))

        loaded = session.get(Plan, plan_id)
        assert loaded is not None
        assert loaded.goal_is_critical is None
        assert loaded.goal_sort_order is None
    finally:
        session.close()


@pytest.mark.integration
def test_plan_goal_ordering_fields_accepts_paired_values(plan_schema_engine: Engine) -> None:
    plan = Base.metadata.tables["plan"]
    session = create_session_factory(plan_schema_engine)()
    master_id = uuid.uuid4()
    child_id = uuid.uuid4()

    try:
        with transaction(session) as txn:
            txn.execute(insert(plan).values(_plan_row(master_id, is_master=True)))
            txn.execute(insert(Base.metadata.tables["goal_plan"]).values(_goal_plan_row(master_id)))
            txn.execute(
                insert(plan).values(
                    _plan_row(
                        child_id,
                        plan_kind=PlanKind.TASK,
                        parent_id=master_id,
                        goal_is_critical=False,
                        goal_sort_order=0,
                    )
                )
            )

        loaded = session.get(Plan, child_id)
        assert loaded is not None
        assert loaded.goal_is_critical is False
        assert loaded.goal_sort_order == 0
    finally:
        session.close()


@pytest.mark.integration
def test_check_goal_ordering_fields_must_be_paired(plan_schema_engine: Engine) -> None:
    plan = Base.metadata.tables["plan"]
    session = create_session_factory(plan_schema_engine)()

    try:
        with pytest.raises(IntegrityError), transaction(session) as txn:
            row = _plan_row(uuid.uuid4(), is_master=True)
            row["goal_is_critical"] = True
            row["goal_sort_order"] = None
            txn.execute(insert(plan).values(row))
    finally:
        session.close()


@pytest.mark.integration
def test_check_goal_sort_order_non_negative(plan_schema_engine: Engine) -> None:
    plan = Base.metadata.tables["plan"]
    session = create_session_factory(plan_schema_engine)()

    try:
        with pytest.raises(IntegrityError), transaction(session) as txn:
            row = _plan_row(uuid.uuid4(), is_master=True)
            row["goal_is_critical"] = False
            row["goal_sort_order"] = -1
            txn.execute(insert(plan).values(row))
    finally:
        session.close()


@pytest.mark.integration
def test_alembic_upgrade_plan_has_goal_ordering_columns(
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
        columns = {column["name"] for column in inspect(engine).get_columns("plan")}
    finally:
        engine.dispose()

    assert "goal_is_critical" in columns
    assert "goal_sort_order" in columns


@pytest.mark.integration
def test_alembic_upgrade_enforces_goal_ordering_fields_paired(
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

    try:
        with pytest.raises(IntegrityError), transaction(session) as txn:
            row = _plan_row(uuid.uuid4(), is_master=True)
            row["goal_is_critical"] = True
            row["goal_sort_order"] = None
            txn.execute(insert(plan).values(row))
    finally:
        session.close()
        engine.dispose()


@pytest.mark.integration
def test_alembic_upgrade_copies_chain_ordering_to_flat_fields(
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

    command.upgrade(Config("alembic.ini"), "7111454550a7")

    engine = create_engine_for_url(temp_sqlite_url)
    session = create_session_factory(engine)()
    plan = Base.metadata.tables["plan"]
    goal_plan = Base.metadata.tables["goal_plan"]
    task_plan = Base.metadata.tables["task_plan"]
    master_id = uuid.uuid4()
    child_id = uuid.uuid4()
    chain_id = uuid.uuid4()
    item_id = uuid.uuid4()
    now = _now()

    try:
        with transaction(session) as txn:
            txn.execute(insert(plan).values(_plan_row(master_id, is_master=True)))
            txn.execute(insert(goal_plan).values(_goal_plan_row(master_id)))
            txn.execute(
                insert(plan).values(
                    _plan_row(child_id, plan_kind=PlanKind.TASK, parent_id=master_id)
                )
            )
            txn.execute(insert(task_plan).values(_task_plan_row(child_id)))
            txn.execute(
                text(
                    """
                    INSERT INTO goal_child_chain (
                        goal_child_chain_id,
                        parent_goal_id,
                        is_critical,
                        sort_order,
                        created_at,
                        updated_at
                    ) VALUES (
                        :chain_id,
                        :parent_goal_id,
                        :is_critical,
                        :sort_order,
                        :created_at,
                        :updated_at
                    )
                    """
                ),
                {
                    "chain_id": chain_id.hex,
                    "parent_goal_id": master_id.hex,
                    "is_critical": False,
                    "sort_order": 0,
                    "created_at": now,
                    "updated_at": now,
                },
            )
            txn.execute(
                text(
                    """
                    INSERT INTO goal_child_chain_item (
                        goal_child_chain_item_id,
                        chain_id,
                        child_plan_id,
                        position
                    ) VALUES (
                        :item_id,
                        :chain_id,
                        :child_plan_id,
                        :position
                    )
                    """
                ),
                {
                    "item_id": item_id.hex,
                    "chain_id": chain_id.hex,
                    "child_plan_id": child_id.hex,
                    "position": 0,
                },
            )
    finally:
        session.close()
        engine.dispose()

    command.upgrade(Config("alembic.ini"), "head")

    engine = create_engine_for_url(temp_sqlite_url)
    session = create_session_factory(engine)()
    try:
        loaded = session.get(Plan, child_id)
        assert loaded is not None
        assert loaded.goal_is_critical is False
        assert loaded.goal_sort_order == 0
    finally:
        session.close()
        engine.dispose()


@pytest.mark.integration
def test_check_repetition_plan_manual_count_mode_fields(plan_schema_engine: Engine) -> None:
    plan = Base.metadata.tables["plan"]
    repetition_plan = Base.metadata.tables["repetition_plan"]
    session = create_session_factory(plan_schema_engine)()
    repetition_id = uuid.uuid4()
    template_id = uuid.uuid4()

    try:
        with transaction(session) as txn:
            txn.execute(insert(plan).values(_plan_row(template_id, is_master=True)))
            txn.execute(
                insert(Base.metadata.tables["goal_plan"]).values(_goal_plan_row(template_id))
            )
            txn.execute(
                insert(plan).values(_plan_row(repetition_id, plan_kind=PlanKind.REPETITION))
            )

        with pytest.raises(IntegrityError), transaction(session) as txn:
            row = _repetition_plan_row(repetition_id, template_id)
            row["end_time"] = _now()
            txn.execute(insert(repetition_plan).values(row))
    finally:
        session.close()


@pytest.mark.integration
def test_check_repetition_plan_date_range_mode_fields(plan_schema_engine: Engine) -> None:
    plan = Base.metadata.tables["plan"]
    repetition_plan = Base.metadata.tables["repetition_plan"]
    session = create_session_factory(plan_schema_engine)()
    repetition_id = uuid.uuid4()
    template_id = uuid.uuid4()

    try:
        with transaction(session) as txn:
            txn.execute(insert(plan).values(_plan_row(template_id, is_master=True)))
            txn.execute(
                insert(Base.metadata.tables["goal_plan"]).values(_goal_plan_row(template_id))
            )
            txn.execute(
                insert(plan).values(_plan_row(repetition_id, plan_kind=PlanKind.REPETITION))
            )

        with pytest.raises(IntegrityError), transaction(session) as txn:
            row = _repetition_plan_row(repetition_id, template_id)
            row["repeat_mode"] = RepeatMode.DATE_RANGE
            row["manual_count"] = 1
            txn.execute(insert(repetition_plan).values(row))
    finally:
        session.close()


@pytest.mark.integration
def test_relationships_navigate_plan_cloned_from(plan_schema_engine: Engine) -> None:
    session = create_session_factory(plan_schema_engine)()
    template_id = uuid.uuid4()
    clone_id = uuid.uuid4()
    now = _now()

    try:
        with transaction(session):
            session.add(
                Plan(
                    plan_id=template_id,
                    plan_kind=PlanKind.TASK,
                    name="template task",
                    parent_id=None,
                    is_master=False,
                    cloned_from_id=None,
                    clone_status=CloneStatus.TEMPLATE,
                    created_at=now,
                    updated_at=now,
                )
            )
            session.add(
                TaskPlan(
                    plan_id=template_id,
                    duration_minutes=30,
                    divisible=False,
                    minimum_chunk_size_minutes=None,
                    user_completed=False,
                    completed_at=None,
                )
            )
            session.add(
                Plan(
                    plan_id=clone_id,
                    plan_kind=PlanKind.TASK,
                    name="linked clone",
                    parent_id=None,
                    is_master=False,
                    cloned_from_id=template_id,
                    clone_status=CloneStatus.LINKED,
                    created_at=now,
                    updated_at=now,
                )
            )
            session.add(
                TaskPlan(
                    plan_id=clone_id,
                    duration_minutes=30,
                    divisible=False,
                    minimum_chunk_size_minutes=None,
                    user_completed=False,
                    completed_at=None,
                )
            )

        loaded = session.get(Plan, clone_id)
        assert loaded is not None
        assert loaded.cloned_from is not None
        assert loaded.cloned_from.plan_id == template_id
        assert loaded.cloned_from.name == "template task"
        assert loaded.cloned_from.clone_status == CloneStatus.TEMPLATE
    finally:
        session.close()


@pytest.mark.integration
def test_relationships_navigate_repetition_plan_template_root(
    plan_schema_engine: Engine,
) -> None:
    session = create_session_factory(plan_schema_engine)()
    template_id = uuid.uuid4()
    repetition_id = uuid.uuid4()
    now = _now()

    try:
        with transaction(session):
            session.add(
                Plan(
                    plan_id=template_id,
                    plan_kind=PlanKind.GOAL,
                    name="template root",
                    parent_id=None,
                    is_master=True,
                    cloned_from_id=None,
                    clone_status=CloneStatus.NOT_CLONED,
                    created_at=now,
                    updated_at=now,
                )
            )
            session.add(GoalPlan(plan_id=template_id))
            session.add(
                Plan(
                    plan_id=repetition_id,
                    plan_kind=PlanKind.REPETITION,
                    name="repetition shell",
                    parent_id=template_id,
                    is_master=False,
                    cloned_from_id=None,
                    clone_status=CloneStatus.NOT_CLONED,
                    created_at=now,
                    updated_at=now,
                )
            )
            session.add(
                RepetitionPlan(
                    plan_id=repetition_id,
                    repeat_mode=RepeatMode.MANUAL_COUNT,
                    start_time=now,
                    repeat_interval_minutes=60,
                    manual_count=1,
                    end_time=None,
                    template_root_id=template_id,
                    default_instance_critical=False,
                    generated_at=None,
                )
            )

        loaded = session.get(RepetitionPlan, repetition_id)
        assert loaded is not None
        assert loaded.template_root.plan_id == template_id
        assert loaded.template_root.name == "template root"
        assert loaded.template_root.is_master is True
        assert loaded.plan.plan_kind == PlanKind.REPETITION
    finally:
        session.close()


@pytest.mark.integration
def test_alembic_upgrade_creates_plan_tables(
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
    finally:
        engine.dispose()

    assert table_names >= PLAN_TABLE_NAMES


@pytest.mark.integration
@pytest.mark.failure_expected
def test_goal_child_chain_tables_absent_after_upgrade(
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
    finally:
        engine.dispose()

    assert CHAIN_TABLE_NAMES.isdisjoint(table_names)


@pytest.mark.integration
def test_alembic_upgrade_enforces_repetition_plan_repeat_interval_check(
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
    goal_plan = Base.metadata.tables["goal_plan"]
    repetition_plan = Base.metadata.tables["repetition_plan"]
    repetition_id = uuid.uuid4()
    template_id = uuid.uuid4()

    try:
        with transaction(session) as txn:
            txn.execute(insert(plan).values(_plan_row(template_id, is_master=True)))
            txn.execute(insert(goal_plan).values(_goal_plan_row(template_id)))
            txn.execute(
                insert(plan).values(_plan_row(repetition_id, plan_kind=PlanKind.REPETITION))
            )

        with pytest.raises(IntegrityError), transaction(session) as txn:
            row = _repetition_plan_row(repetition_id, template_id)
            row["repeat_interval_minutes"] = 0
            txn.execute(insert(repetition_plan).values(row))
    finally:
        session.close()
        engine.dispose()


@pytest.mark.integration
def test_alembic_upgrade_enforces_task_plan_duration_positive(
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
    task_plan = Base.metadata.tables["task_plan"]
    task_id = uuid.uuid4()

    try:
        with transaction(session) as txn:
            txn.execute(insert(plan).values(_plan_row(task_id, plan_kind=PlanKind.TASK)))

        with pytest.raises(IntegrityError), transaction(session) as txn:
            row = _task_plan_row(task_id)
            row["duration_minutes"] = 0
            txn.execute(insert(task_plan).values(row))
    finally:
        session.close()
        engine.dispose()

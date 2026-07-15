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
from calendar_backend.models.chains import GoalChildChain, GoalChildChainItem
from calendar_backend.models.plans import GoalPlan, Plan, RepetitionPlan, TaskPlan
from sqlalchemy import CheckConstraint, DateTime, UniqueConstraint, insert, inspect
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

PLAN_TABLE_NAMES = frozenset(
    {
        "plan",
        "goal_plan",
        "task_plan",
        "repetition_plan",
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
    Base.metadata.tables["goal_child_chain"].c.created_at,
    Base.metadata.tables["goal_child_chain"].c.updated_at,
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
) -> dict[str, object]:
    return {
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


def _goal_child_chain_row(
    chain_id: uuid.UUID,
    parent_goal_id: uuid.UUID,
    *,
    sort_order: int = 0,
) -> dict[str, object]:
    return {
        "goal_child_chain_id": chain_id,
        "parent_goal_id": parent_goal_id,
        "is_critical": False,
        "sort_order": sort_order,
        "created_at": _now(),
        "updated_at": _now(),
    }


def _goal_child_chain_item_row(
    item_id: uuid.UUID,
    chain_id: uuid.UUID,
    child_plan_id: uuid.UUID,
    *,
    position: int = 0,
) -> dict[str, object]:
    return {
        "goal_child_chain_item_id": item_id,
        "chain_id": chain_id,
        "child_plan_id": child_plan_id,
        "position": position,
    }


def _insert_master_goal(txn: Session, plan_id: uuid.UUID | None = None) -> uuid.UUID:
    plan_id = plan_id or uuid.uuid4()
    plan = Base.metadata.tables["plan"]
    goal_plan = Base.metadata.tables["goal_plan"]
    txn.execute(insert(plan).values(_plan_row(plan_id, is_master=True)))
    txn.execute(insert(goal_plan).values(_goal_plan_row(plan_id)))
    return plan_id


def test_plan_metadata_includes_all_six_tables() -> None:
    table_names = set(Base.metadata.tables)
    assert table_names >= PLAN_TABLE_NAMES


def test_plan_metadata_key_columns_present() -> None:
    plan = Base.metadata.tables["plan"]
    task_plan = Base.metadata.tables["task_plan"]
    chain_item = Base.metadata.tables["goal_child_chain_item"]

    assert "is_master" in plan.c
    assert "duration_minutes" in task_plan.c
    assert "child_plan_id" in chain_item.c


def test_plan_metadata_foreign_keys() -> None:
    expected_fks = {
        ("plan", "parent_id"): "plan.plan_id",
        ("plan", "cloned_from_id"): "plan.plan_id",
        ("goal_plan", "plan_id"): "plan.plan_id",
        ("task_plan", "plan_id"): "plan.plan_id",
        ("repetition_plan", "plan_id"): "plan.plan_id",
        ("repetition_plan", "template_root_id"): "plan.plan_id",
        ("goal_child_chain", "parent_goal_id"): "goal_plan.plan_id",
        ("goal_child_chain_item", "chain_id"): "goal_child_chain.goal_child_chain_id",
        ("goal_child_chain_item", "child_plan_id"): "plan.plan_id",
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
        "goal_child_chain": "ck_goal_child_chain_sort_order_non_negative",
        "goal_child_chain_item": "ck_goal_child_chain_item_position_non_negative",
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


def test_plan_metadata_unique_child_plan_id_constraint() -> None:
    table = Base.metadata.tables["goal_child_chain_item"]
    child_plan_uniques = [
        constraint
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
        and {column.name for column in constraint.columns} == {"child_plan_id"}
    ]
    assert len(child_plan_uniques) == 1


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
def test_check_sort_order_non_negative(plan_schema_engine: Engine) -> None:
    chain = Base.metadata.tables["goal_child_chain"]
    session = create_session_factory(plan_schema_engine)()

    try:
        with transaction(session) as txn:
            parent_id = _insert_master_goal(txn)

        with pytest.raises(IntegrityError), transaction(session) as txn:
            txn.execute(
                insert(chain).values(_goal_child_chain_row(uuid.uuid4(), parent_id, sort_order=-1))
            )
    finally:
        session.close()


@pytest.mark.integration
def test_check_position_non_negative(plan_schema_engine: Engine) -> None:
    plan = Base.metadata.tables["plan"]
    chain = Base.metadata.tables["goal_child_chain"]
    chain_item = Base.metadata.tables["goal_child_chain_item"]
    session = create_session_factory(plan_schema_engine)()

    parent_id = uuid.uuid4()
    child_id = uuid.uuid4()
    chain_id = uuid.uuid4()

    try:
        with transaction(session) as txn:
            txn.execute(insert(plan).values(_plan_row(parent_id, is_master=True)))
            txn.execute(insert(Base.metadata.tables["goal_plan"]).values(_goal_plan_row(parent_id)))
            txn.execute(insert(plan).values(_plan_row(child_id, plan_kind=PlanKind.TASK)))
            txn.execute(insert(chain).values(_goal_child_chain_row(chain_id, parent_id)))

        with pytest.raises(IntegrityError), transaction(session) as txn:
            txn.execute(
                insert(chain_item).values(
                    _goal_child_chain_item_row(uuid.uuid4(), chain_id, child_id, position=-1)
                )
            )
    finally:
        session.close()


@pytest.mark.integration
def test_unique_child_plan_id_in_chain_items(plan_schema_engine: Engine) -> None:
    plan = Base.metadata.tables["plan"]
    goal_plan = Base.metadata.tables["goal_plan"]
    chain = Base.metadata.tables["goal_child_chain"]
    chain_item = Base.metadata.tables["goal_child_chain_item"]
    session = create_session_factory(plan_schema_engine)()

    parent_id = uuid.uuid4()
    child_id = uuid.uuid4()
    chain_id = uuid.uuid4()

    try:
        with transaction(session) as txn:
            txn.execute(insert(plan).values(_plan_row(parent_id, is_master=True)))
            txn.execute(insert(goal_plan).values(_goal_plan_row(parent_id)))
            txn.execute(insert(plan).values(_plan_row(child_id, plan_kind=PlanKind.TASK)))
            txn.execute(insert(chain).values(_goal_child_chain_row(chain_id, parent_id)))
            txn.execute(
                insert(chain_item).values(
                    _goal_child_chain_item_row(uuid.uuid4(), chain_id, child_id)
                )
            )

        with pytest.raises(IntegrityError), transaction(session) as txn:
            txn.execute(
                insert(chain_item).values(
                    _goal_child_chain_item_row(uuid.uuid4(), chain_id, child_id, position=1)
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
def test_foreign_key_invalid_chain_parent_goal_id_rejected(plan_schema_engine: Engine) -> None:
    chain = Base.metadata.tables["goal_child_chain"]
    session = create_session_factory(plan_schema_engine)()

    try:
        with pytest.raises(IntegrityError), transaction(session) as txn:
            txn.execute(insert(chain).values(_goal_child_chain_row(uuid.uuid4(), uuid.uuid4())))
    finally:
        session.close()


@pytest.mark.integration
def test_foreign_key_invalid_chain_item_chain_id_rejected(plan_schema_engine: Engine) -> None:
    chain_item = Base.metadata.tables["goal_child_chain_item"]
    session = create_session_factory(plan_schema_engine)()

    try:
        with pytest.raises(IntegrityError), transaction(session) as txn:
            txn.execute(
                insert(chain_item).values(
                    _goal_child_chain_item_row(uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
                )
            )
    finally:
        session.close()


@pytest.mark.integration
def test_foreign_key_invalid_chain_item_child_plan_id_rejected(
    plan_schema_engine: Engine,
) -> None:
    chain = Base.metadata.tables["goal_child_chain"]
    chain_item = Base.metadata.tables["goal_child_chain_item"]
    session = create_session_factory(plan_schema_engine)()

    chain_id = uuid.uuid4()

    try:
        with transaction(session) as txn:
            parent_id = _insert_master_goal(txn)
            txn.execute(insert(chain).values(_goal_child_chain_row(chain_id, parent_id)))

        with pytest.raises(IntegrityError), transaction(session) as txn:
            txn.execute(
                insert(chain_item).values(
                    _goal_child_chain_item_row(uuid.uuid4(), chain_id, uuid.uuid4())
                )
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
def test_relationships_navigate_goal_to_chain_item(plan_schema_engine: Engine) -> None:
    session = create_session_factory(plan_schema_engine)()
    master_id = uuid.uuid4()
    child_id = uuid.uuid4()
    chain_id = uuid.uuid4()
    item_id = uuid.uuid4()
    now = _now()

    try:
        with transaction(session):
            session.add(
                Plan(
                    plan_id=master_id,
                    plan_kind=PlanKind.GOAL,
                    name="master",
                    parent_id=None,
                    is_master=True,
                    cloned_from_id=None,
                    clone_status=CloneStatus.NOT_CLONED,
                    created_at=now,
                    updated_at=now,
                )
            )
            session.add(GoalPlan(plan_id=master_id))
            session.add(
                Plan(
                    plan_id=child_id,
                    plan_kind=PlanKind.TASK,
                    name="child task",
                    parent_id=master_id,
                    is_master=False,
                    cloned_from_id=None,
                    clone_status=CloneStatus.NOT_CLONED,
                    created_at=now,
                    updated_at=now,
                )
            )
            session.add(
                TaskPlan(
                    plan_id=child_id,
                    duration_minutes=30,
                    divisible=False,
                    minimum_chunk_size_minutes=None,
                    user_completed=False,
                    completed_at=None,
                )
            )
            session.add(
                GoalChildChain(
                    goal_child_chain_id=chain_id,
                    parent_goal_id=master_id,
                    is_critical=False,
                    sort_order=0,
                    created_at=now,
                    updated_at=now,
                )
            )
            session.add(
                GoalChildChainItem(
                    goal_child_chain_item_id=item_id,
                    chain_id=chain_id,
                    child_plan_id=child_id,
                    position=0,
                )
            )

        loaded = session.get(GoalPlan, master_id)
        assert loaded is not None
        assert len(loaded.chains) == 1
        assert loaded.chains[0].goal_child_chain_id == chain_id
        assert len(loaded.chains[0].items) == 1
        assert loaded.chains[0].items[0].child_plan.plan_kind == PlanKind.TASK
        assert loaded.plan.is_master is True
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

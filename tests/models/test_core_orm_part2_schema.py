from __future__ import annotations

import tempfile
import uuid
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
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
from alembic import command
from alembic.config import Config
from calendar_backend.db.base import Base
from calendar_backend.db.session import create_engine_for_url, create_session_factory, transaction
from calendar_backend.domain.enums import (
    CalendarEntryType,
    CalendarRunStatus,
    CloneStatus,
    ConstraintKind,
    FreeTimeWeekStartDay,
    PlanKind,
    RepeatMode,
    SolverStatus,
)
from calendar_backend.models.calendar import CalendarEntry
from calendar_backend.models.constraints import TimeConstraintGroup, TimeWindow
from calendar_backend.models.free_time import FreeTimeActivity, FreeTimeActivityPrerequisite
from calendar_backend.models.plans import GoalPlan, Plan, RepetitionPlan
from calendar_backend.models.repetitions import RepetitionInstance
from calendar_backend.models.runs import ActiveCalendarState, CalendarRun
from sqlalchemy import CheckConstraint, DateTime, Enum, insert, inspect
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

PART2_TABLE_NAMES = frozenset(
    {
        "time_constraint_group",
        "time_window",
        "repetition_instance",
        "calendar_entry",
        "free_time_activity",
        "free_time_activity_prerequisite",
        "calendar_run",
        "active_calendar_state",
        "app_settings",
    }
)

PART2_FOREIGN_KEYS: dict[tuple[str, str], str] = {
    ("time_constraint_group", "plan_id"): "plan.plan_id",
    ("time_window", "group_id"): "time_constraint_group.time_constraint_group_id",
    ("repetition_instance", "repetition_plan_id"): "repetition_plan.plan_id",
    ("repetition_instance", "root_clone_id"): "plan.plan_id",
    ("calendar_entry", "source_plan_id"): "plan.plan_id",
    ("calendar_entry", "source_free_time_activity_id"): "free_time_activity.free_time_activity_id",
    ("calendar_entry", "calendar_run_id"): "calendar_run.calendar_run_id",
    ("free_time_activity_prerequisite", "free_time_activity_id"): (
        "free_time_activity.free_time_activity_id"
    ),
    ("free_time_activity_prerequisite", "source_plan_id"): "plan.plan_id",
    ("active_calendar_state", "active_calendar_run_id"): "calendar_run.calendar_run_id",
}

PART2_CHECK_CONSTRAINTS: dict[str, str] = {
    "time_window": "ck_time_window_start_before_end",
    "calendar_entry": "ck_calendar_entry_start_before_end",
    "free_time_activity": "ck_free_time_activity_minimum_block_size_non_negative",
    "repetition_instance": "ck_repetition_instance_instance_index_non_negative",
    "active_calendar_state": "ck_active_calendar_state_active_calendar_state_singleton_id_is_one",
    "app_settings": "ck_app_settings_app_settings_singleton_id_is_one",
}


PART2_ENUM_COLUMNS: dict[tuple[str, str], set[str]] = {
    ("time_constraint_group", "constraint_kind"): {
        "USER",
        "SYSTEM_REPETITION_WINDOW",
        "SYSTEM_MASTER_HORIZON",
    },
    ("calendar_entry", "entry_type"): {"TASK", "FREE_TIME"},
    ("calendar_run", "status"): {"SUCCESS", "FAILED"},
    ("calendar_run", "solver_status"): {"OPTIMAL", "FEASIBLE", "INFEASIBLE"},
    ("active_calendar_state", "last_failure_reason"): {
        "ASSIGNMENT_FAILED",
        "ASSIGNMENT_PRECONDITION_FAILED",
    },
    ("app_settings", "free_time_week_start_day"): {
        "MONDAY",
        "TUESDAY",
        "WEDNESDAY",
        "THURSDAY",
        "FRIDAY",
        "SATURDAY",
        "SUNDAY",
    },
}


@pytest.fixture
def temp_sqlite_url() -> Generator[str]:
    with tempfile.TemporaryDirectory() as tmpdir:
        yield f"sqlite:///{Path(tmpdir) / 'test.sqlite3'}"


@pytest.fixture
def part2_schema_engine(temp_sqlite_url: str) -> Generator[Engine]:
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
) -> dict[str, object]:
    return {
        "plan_id": plan_id,
        "plan_kind": plan_kind,
        "name": "test plan",
        "parent_id": parent_id,
        "is_master": is_master,
        "cloned_from_id": None,
        "clone_status": CloneStatus.NOT_CLONED,
        "created_at": _now(),
        "updated_at": _now(),
    }


def _goal_plan_row(plan_id: uuid.UUID) -> dict[str, object]:
    return {"plan_id": plan_id}


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


def _insert_master_goal(txn: Session, plan_id: uuid.UUID | None = None) -> uuid.UUID:
    plan_id = plan_id or uuid.uuid4()
    txn.execute(insert(Base.metadata.tables["plan"]).values(_plan_row(plan_id, is_master=True)))
    txn.execute(insert(Base.metadata.tables["goal_plan"]).values(_goal_plan_row(plan_id)))
    return plan_id


def _insert_repetition_plan(
    txn: Session, repetition_id: uuid.UUID, template_root_id: uuid.UUID
) -> None:
    txn.execute(
        insert(Base.metadata.tables["plan"]).values(
            _plan_row(repetition_id, plan_kind=PlanKind.REPETITION)
        )
    )
    txn.execute(
        insert(Base.metadata.tables["repetition_plan"]).values(
            _repetition_plan_row(repetition_id, template_root_id)
        )
    )


def _free_time_activity_row(
    activity_id: uuid.UUID, *, minimum_block_size_minutes: int = 0
) -> dict[str, object]:
    now = _now()
    return {
        "free_time_activity_id": activity_id,
        "name": "reading",
        "enabled": True,
        "real_fraction": Decimal("0.5"),
        "minimum_block_size_minutes": minimum_block_size_minutes,
        "created_at": now,
        "updated_at": now,
    }


def test_part2_metadata_includes_all_nine_tables() -> None:
    assert set(Base.metadata.tables) >= PART2_TABLE_NAMES


def test_part2_metadata_key_columns_present() -> None:
    assert "constraint_kind" in Base.metadata.tables["time_constraint_group"].c
    assert "instance_index" in Base.metadata.tables["repetition_instance"].c
    assert "display_label" in Base.metadata.tables["calendar_entry"].c
    assert "real_fraction" in Base.metadata.tables["free_time_activity"].c
    assert "solver_status" in Base.metadata.tables["calendar_run"].c
    assert "local_timezone" in Base.metadata.tables["app_settings"].c


def test_part2_metadata_foreign_keys() -> None:
    for (table_name, column_name), target in PART2_FOREIGN_KEYS.items():
        column = Base.metadata.tables[table_name].c[column_name]
        fk_targets = {fk.target_fullname for fk in column.foreign_keys}
        assert fk_targets == {target}


def test_part2_metadata_check_constraints() -> None:
    for table_name, check_name in PART2_CHECK_CONSTRAINTS.items():
        table = Base.metadata.tables[table_name]
        check_names = {
            constraint.name
            for constraint in table.constraints
            if isinstance(constraint, CheckConstraint)
        }
        assert check_name in check_names

    repetition = Base.metadata.tables["repetition_instance"]
    repetition_checks = {
        constraint.name
        for constraint in repetition.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert "ck_repetition_instance_sort_order_non_negative" in repetition_checks


def test_part2_metadata_timezone_aware_datetime_columns() -> None:
    for table_name in PART2_TABLE_NAMES:
        for column in Base.metadata.tables[table_name].columns:
            if isinstance(column.type, DateTime):
                assert column.type.timezone is True


def test_part2_metadata_enum_columns() -> None:
    for (table_name, column_name), expected_values in PART2_ENUM_COLUMNS.items():
        column = Base.metadata.tables[table_name].c[column_name]
        assert isinstance(column.type, Enum)
        assert set(column.type.enums) == expected_values


@pytest.mark.integration
def test_check_time_window_start_before_end(part2_schema_engine: Engine) -> None:
    time_window = Base.metadata.tables["time_window"]
    session = create_session_factory(part2_schema_engine)()
    group_id = uuid.uuid4()
    plan_id = uuid.uuid4()
    now = _now()

    try:
        with transaction(session) as txn:
            _insert_master_goal(txn, plan_id)
            txn.execute(
                insert(Base.metadata.tables["time_constraint_group"]).values(
                    {
                        "time_constraint_group_id": group_id,
                        "plan_id": plan_id,
                        "constraint_kind": ConstraintKind.USER,
                    }
                )
            )

        with pytest.raises(IntegrityError), transaction(session) as txn:
            txn.execute(
                insert(time_window).values(
                    {
                        "time_window_id": uuid.uuid4(),
                        "group_id": group_id,
                        "start_time": now + timedelta(hours=1),
                        "end_time": now,
                    }
                )
            )
    finally:
        session.close()


@pytest.mark.integration
def test_check_calendar_entry_start_before_end(part2_schema_engine: Engine) -> None:
    calendar_entry = Base.metadata.tables["calendar_entry"]
    session = create_session_factory(part2_schema_engine)()
    now = _now()

    try:
        with pytest.raises(IntegrityError), transaction(session) as txn:
            txn.execute(
                insert(calendar_entry).values(
                    {
                        "calendar_entry_id": uuid.uuid4(),
                        "entry_type": CalendarEntryType.TASK,
                        "start_time": now + timedelta(hours=1),
                        "end_time": now,
                        "source_plan_id": None,
                        "source_free_time_activity_id": None,
                        "calendar_run_id": None,
                        "display_label": "task",
                        "created_at": now,
                        "updated_at": now,
                    }
                )
            )
    finally:
        session.close()


@pytest.mark.integration
def test_check_free_time_minimum_block_size_non_negative(part2_schema_engine: Engine) -> None:
    activity = Base.metadata.tables["free_time_activity"]
    session = create_session_factory(part2_schema_engine)()

    try:
        with pytest.raises(IntegrityError), transaction(session) as txn:
            txn.execute(
                insert(activity).values(
                    _free_time_activity_row(uuid.uuid4(), minimum_block_size_minutes=-1)
                )
            )
    finally:
        session.close()


@pytest.mark.integration
def test_check_repetition_instance_index_non_negative(part2_schema_engine: Engine) -> None:
    repetition_instance = Base.metadata.tables["repetition_instance"]
    session = create_session_factory(part2_schema_engine)()
    repetition_id = uuid.uuid4()
    template_id = uuid.uuid4()
    clone_id = uuid.uuid4()

    try:
        with transaction(session) as txn:
            _insert_master_goal(txn, template_id)
            _insert_repetition_plan(txn, repetition_id, template_id)
            txn.execute(
                insert(Base.metadata.tables["plan"]).values(
                    _plan_row(clone_id, plan_kind=PlanKind.TASK)
                )
            )

        with pytest.raises(IntegrityError), transaction(session) as txn:
            txn.execute(
                insert(repetition_instance).values(
                    {
                        "repetition_instance_id": uuid.uuid4(),
                        "repetition_plan_id": repetition_id,
                        "instance_index": -1,
                        "root_clone_id": clone_id,
                        "instance_start_time": _now(),
                        "is_critical": False,
                        "sort_order": 0,
                    }
                )
            )
    finally:
        session.close()


@pytest.mark.integration
def test_check_repetition_instance_sort_order_non_negative(part2_schema_engine: Engine) -> None:
    repetition_instance = Base.metadata.tables["repetition_instance"]
    session = create_session_factory(part2_schema_engine)()
    repetition_id = uuid.uuid4()
    template_id = uuid.uuid4()
    clone_id = uuid.uuid4()

    try:
        with transaction(session) as txn:
            _insert_master_goal(txn, template_id)
            _insert_repetition_plan(txn, repetition_id, template_id)
            txn.execute(
                insert(Base.metadata.tables["plan"]).values(
                    _plan_row(clone_id, plan_kind=PlanKind.TASK)
                )
            )

        with pytest.raises(IntegrityError), transaction(session) as txn:
            txn.execute(
                insert(repetition_instance).values(
                    {
                        "repetition_instance_id": uuid.uuid4(),
                        "repetition_plan_id": repetition_id,
                        "instance_index": 0,
                        "root_clone_id": clone_id,
                        "instance_start_time": _now(),
                        "is_critical": False,
                        "sort_order": -1,
                    }
                )
            )
    finally:
        session.close()


@pytest.mark.integration
def test_check_active_calendar_state_singleton_id(part2_schema_engine: Engine) -> None:
    active_state = Base.metadata.tables["active_calendar_state"]
    session = create_session_factory(part2_schema_engine)()

    try:
        with pytest.raises(IntegrityError), transaction(session) as txn:
            txn.execute(
                insert(active_state).values(
                    {
                        "singleton_id": 2,
                        "active_calendar_run_id": None,
                        "last_refresh_failed": False,
                        "last_failure_at": None,
                        "last_failure_reason": None,
                        "updated_at": _now(),
                    }
                )
            )
    finally:
        session.close()


@pytest.mark.integration
def test_check_app_settings_singleton_id(part2_schema_engine: Engine) -> None:
    app_settings = Base.metadata.tables["app_settings"]
    session = create_session_factory(part2_schema_engine)()

    try:
        with pytest.raises(IntegrityError), transaction(session) as txn:
            txn.execute(
                insert(app_settings).values(
                    {
                        "singleton_id": 2,
                        "local_timezone": "UTC",
                        "master_horizon_duration_minutes": 60,
                        "exact_solver_time_limit_seconds": 30,
                        "exact_solver_model_size_limit": 1000,
                        "heuristic_enabled": True,
                        "free_time_week_start_day": FreeTimeWeekStartDay.MONDAY,
                        "updated_at": _now(),
                    }
                )
            )
    finally:
        session.close()


@pytest.mark.integration
@pytest.mark.parametrize(
    ("table_name", "column_name"),
    list(PART2_FOREIGN_KEYS),
)
def test_foreign_key_rejects_invalid_parent(
    part2_schema_engine: Engine,
    table_name: str,
    column_name: str,
) -> None:
    table = Base.metadata.tables[table_name]
    session = create_session_factory(part2_schema_engine)()
    row: dict[str, object] = {}

    if table_name == "time_constraint_group":
        row = {
            "time_constraint_group_id": uuid.uuid4(),
            "plan_id": uuid.uuid4(),
            "constraint_kind": ConstraintKind.USER,
        }
    elif table_name == "time_window":
        row = {
            "time_window_id": uuid.uuid4(),
            "group_id": uuid.uuid4(),
            "start_time": _now(),
            "end_time": _now() + timedelta(hours=1),
        }
    elif table_name == "repetition_instance":
        row = {
            "repetition_instance_id": uuid.uuid4(),
            "repetition_plan_id": uuid.uuid4(),
            "instance_index": 0,
            "root_clone_id": uuid.uuid4(),
            "instance_start_time": _now(),
            "is_critical": False,
            "sort_order": 0,
        }
    elif table_name == "calendar_entry":
        now = _now()
        row = {
            "calendar_entry_id": uuid.uuid4(),
            "entry_type": CalendarEntryType.TASK,
            "start_time": now,
            "end_time": now + timedelta(hours=1),
            "source_plan_id": None,
            "source_free_time_activity_id": None,
            "calendar_run_id": None,
            "display_label": "task",
            "created_at": now,
            "updated_at": now,
        }
    elif table_name == "free_time_activity_prerequisite":
        row = {
            "prerequisite_id": uuid.uuid4(),
            "free_time_activity_id": uuid.uuid4(),
            "source_plan_id": uuid.uuid4(),
        }
    elif table_name == "active_calendar_state":
        row = {
            "singleton_id": 1,
            "active_calendar_run_id": uuid.uuid4(),
            "last_refresh_failed": False,
            "last_failure_at": None,
            "last_failure_reason": None,
            "updated_at": _now(),
        }

    row[column_name] = uuid.uuid4()

    try:
        with pytest.raises(IntegrityError), transaction(session) as txn:
            txn.execute(insert(table).values(row))
    finally:
        session.close()


@pytest.mark.integration
def test_relationships_navigate_plan_to_constraint_windows(part2_schema_engine: Engine) -> None:
    session = create_session_factory(part2_schema_engine)()
    plan_id = uuid.uuid4()
    group_id = uuid.uuid4()
    window_id = uuid.uuid4()
    now = _now()

    try:
        with transaction(session):
            session.add(
                Plan(
                    plan_id=plan_id,
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
            session.add(GoalPlan(plan_id=plan_id))
            session.add(
                TimeConstraintGroup(
                    time_constraint_group_id=group_id,
                    plan_id=plan_id,
                    constraint_kind=ConstraintKind.USER,
                )
            )
            session.add(
                TimeWindow(
                    time_window_id=window_id,
                    group_id=group_id,
                    start_time=now,
                    end_time=now + timedelta(hours=1),
                )
            )

        loaded = session.get(Plan, plan_id)
        assert loaded is not None
        assert len(loaded.constraint_groups) == 1
        assert loaded.constraint_groups[0].windows[0].time_window_id == window_id
    finally:
        session.close()


@pytest.mark.integration
def test_relationships_navigate_repetition_plan_to_instances(part2_schema_engine: Engine) -> None:
    session = create_session_factory(part2_schema_engine)()
    template_id = uuid.uuid4()
    repetition_id = uuid.uuid4()
    clone_id = uuid.uuid4()
    instance_id = uuid.uuid4()
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
                    name="repeat",
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
            session.add(
                Plan(
                    plan_id=clone_id,
                    plan_kind=PlanKind.TASK,
                    name="clone",
                    parent_id=repetition_id,
                    is_master=False,
                    cloned_from_id=None,
                    clone_status=CloneStatus.LINKED,
                    created_at=now,
                    updated_at=now,
                )
            )
            session.add(
                RepetitionInstance(
                    repetition_instance_id=instance_id,
                    repetition_plan_id=repetition_id,
                    instance_index=0,
                    root_clone_id=clone_id,
                    instance_start_time=now,
                    is_critical=False,
                    sort_order=0,
                )
            )

        loaded = session.get(RepetitionPlan, repetition_id)
        assert loaded is not None
        assert len(loaded.instances) == 1
        assert loaded.instances[0].root_clone.plan_kind == PlanKind.TASK
    finally:
        session.close()


@pytest.mark.integration
def test_relationships_navigate_calendar_entry_sources(part2_schema_engine: Engine) -> None:
    session = create_session_factory(part2_schema_engine)()
    plan_id = uuid.uuid4()
    activity_id = uuid.uuid4()
    run_id = uuid.uuid4()
    entry_id = uuid.uuid4()
    now = _now()

    try:
        with transaction(session):
            session.add(
                Plan(
                    plan_id=plan_id,
                    plan_kind=PlanKind.TASK,
                    name="task",
                    parent_id=None,
                    is_master=False,
                    cloned_from_id=None,
                    clone_status=CloneStatus.NOT_CLONED,
                    created_at=now,
                    updated_at=now,
                )
            )
            session.add(
                FreeTimeActivity(
                    free_time_activity_id=activity_id,
                    name="reading",
                    enabled=True,
                    real_fraction=Decimal("1"),
                    minimum_block_size_minutes=0,
                    created_at=now,
                    updated_at=now,
                )
            )
            session.add(
                CalendarRun(
                    calendar_run_id=run_id,
                    run_started_at=now,
                    run_finished_at=None,
                    status=CalendarRunStatus.SUCCESS,
                    solver_status=SolverStatus.FEASIBLE,
                    conflict_count=0,
                    warning_count=0,
                    runtime_ms=5,
                    created_at=now,
                )
            )
            session.add(
                CalendarEntry(
                    calendar_entry_id=entry_id,
                    entry_type=CalendarEntryType.TASK,
                    start_time=now,
                    end_time=now + timedelta(hours=1),
                    source_plan_id=plan_id,
                    source_free_time_activity_id=None,
                    calendar_run_id=run_id,
                    display_label="task block",
                    created_at=now,
                    updated_at=now,
                )
            )

        loaded = session.get(CalendarEntry, entry_id)
        assert loaded is not None
        assert loaded.source_plan is not None
        assert loaded.source_plan.name == "task"
        assert loaded.calendar_run is not None
        assert loaded.calendar_run.status == CalendarRunStatus.SUCCESS
    finally:
        session.close()


@pytest.mark.integration
def test_relationships_navigate_free_time_prerequisites(part2_schema_engine: Engine) -> None:
    session = create_session_factory(part2_schema_engine)()
    activity_id = uuid.uuid4()
    prerequisite_id = uuid.uuid4()
    source_plan_id = uuid.uuid4()
    now = _now()

    try:
        with transaction(session):
            session.add(
                Plan(
                    plan_id=source_plan_id,
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
                FreeTimeActivity(
                    free_time_activity_id=activity_id,
                    name="reading",
                    enabled=True,
                    real_fraction=Decimal("1"),
                    minimum_block_size_minutes=0,
                    created_at=now,
                    updated_at=now,
                )
            )
            session.add(
                FreeTimeActivityPrerequisite(
                    prerequisite_id=prerequisite_id,
                    free_time_activity_id=activity_id,
                    source_plan_id=source_plan_id,
                )
            )

        loaded = session.get(FreeTimeActivity, activity_id)
        assert loaded is not None
        assert len(loaded.prerequisites) == 1
        assert loaded.prerequisites[0].source_plan.name == "prerequisite task"
    finally:
        session.close()


@pytest.mark.integration
def test_relationships_navigate_active_calendar_state_to_run(part2_schema_engine: Engine) -> None:
    session = create_session_factory(part2_schema_engine)()
    run_id = uuid.uuid4()
    now = _now()

    try:
        with transaction(session):
            session.add(
                CalendarRun(
                    calendar_run_id=run_id,
                    run_started_at=now,
                    run_finished_at=None,
                    status=CalendarRunStatus.SUCCESS,
                    solver_status=None,
                    conflict_count=0,
                    warning_count=0,
                    runtime_ms=3,
                    created_at=now,
                )
            )
            session.add(
                ActiveCalendarState(
                    singleton_id=1,
                    active_calendar_run_id=run_id,
                    last_refresh_failed=False,
                    last_failure_at=None,
                    last_failure_reason=None,
                    updated_at=now,
                )
            )

        loaded = session.get(ActiveCalendarState, 1)
        assert loaded is not None
        assert loaded.active_calendar_run is not None
        assert loaded.active_calendar_run.calendar_run_id == run_id
    finally:
        session.close()


@pytest.mark.integration
def test_alembic_upgrade_creates_part2_tables(
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

    assert table_names >= PART2_TABLE_NAMES

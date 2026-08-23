from __future__ import annotations

from datetime import UTC, datetime

from calendar_backend.domain.enums import ConstraintKind, PlanKind
from calendar_backend.domain.plan_create import GoalCreatePayload
from calendar_backend.domain.time import Clock
from calendar_backend.models.constraints import TimeConstraintGroup, TimeWindow
from calendar_backend.services.goal import GoalService
from calendar_backend.services.master_plan import MasterPlanService
from calendar_backend.services.plan_tree_invariant import PlanTreeInvariantService
from calendar_backend.services.plan_tree_read import PlanTreeReadService
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .conftest import FakeClock

RUN_AT = datetime(2026, 6, 7, 10, 0, tzinfo=UTC)


def _horizon_group_count(session: Session) -> int:
    return (
        session.scalar(
            select(func.count())
            .select_from(TimeConstraintGroup)
            .where(TimeConstraintGroup.constraint_kind == ConstraintKind.SYSTEM_MASTER_HORIZON)
        )
        or 0
    )


def _horizon_window_count(session: Session) -> int:
    return (
        session.scalar(
            select(func.count())
            .select_from(TimeWindow)
            .join(TimeConstraintGroup)
            .where(TimeConstraintGroup.constraint_kind == ConstraintKind.SYSTEM_MASTER_HORIZON)
        )
        or 0
    )


def test_plan_tree_read_master_and_detail(service_db_session: Session, fake_clock: Clock) -> None:
    master = MasterPlanService(service_db_session, fake_clock).ensure_master_exists()
    assert master.success
    assert master.value is not None

    read = PlanTreeReadService(service_db_session, fake_clock)
    master_id = read.ensure_master_and_get_id()
    assert master_id.success

    detail = read.get_plan_detail(master.value.plan_id)
    assert detail.success
    assert detail.value is not None
    assert detail.value.is_master is True
    assert detail.value.ancestry[0].plan_id == master.value.plan_id


def test_plan_tree_search(service_db_session: Session, fake_clock: Clock) -> None:
    master = MasterPlanService(service_db_session, fake_clock).ensure_master_exists()
    assert master.success and master.value is not None

    read = PlanTreeReadService(service_db_session, fake_clock)
    results = read.search_plans(master.value.name[:3].lower())
    assert results.success
    assert results.value is not None
    assert len(results.value) >= 1


def test_ensure_master_and_get_id_bootstraps_horizon(service_db_session: Session) -> None:
    read = PlanTreeReadService(service_db_session, FakeClock(RUN_AT))

    result = read.ensure_master_and_get_id()

    assert result.success and result.value is not None
    assert _horizon_group_count(service_db_session) == 1
    assert _horizon_window_count(service_db_session) == 1


def test_ensure_master_and_get_id_is_horizon_idempotent(service_db_session: Session) -> None:
    read = PlanTreeReadService(service_db_session, FakeClock(RUN_AT))

    first = read.ensure_master_and_get_id()
    second = read.ensure_master_and_get_id()

    assert first.success and first.value is not None
    assert second.success and second.value == first.value
    assert _horizon_group_count(service_db_session) == 1
    assert _horizon_window_count(service_db_session) == 1


def test_ensure_master_and_get_id_validates_without_schedule_refresh(
    service_db_session: Session,
) -> None:
    read = PlanTreeReadService(service_db_session, FakeClock(RUN_AT))
    result = read.ensure_master_and_get_id()
    assert result.success and result.value is not None

    validation = PlanTreeInvariantService(service_db_session).validate_master_tree()

    assert validation.success


def test_non_critical_goal_child_under_bootstrapped_master_validates(
    service_db_session: Session,
) -> None:
    clock = FakeClock(RUN_AT)
    master = PlanTreeReadService(service_db_session, clock).ensure_master_and_get_id()
    assert master.success and master.value is not None
    child = GoalService(service_db_session, clock).create_child(
        master.value,
        PlanKind.GOAL,
        GoalCreatePayload(name="generic goal"),
        is_critical=False,
    )
    assert child.success

    validation = PlanTreeInvariantService(service_db_session).validate_master_tree()

    assert validation.success


def test_ensure_master_and_get_id_truncates_clock_for_horizon(
    service_db_session: Session,
) -> None:
    run_at = datetime(2026, 6, 7, 10, 0, 42, tzinfo=UTC)
    read = PlanTreeReadService(service_db_session, FakeClock(run_at))

    result = read.ensure_master_and_get_id()

    assert result.success and result.value is not None
    window = service_db_session.scalar(
        select(TimeWindow)
        .join(TimeConstraintGroup)
        .where(TimeConstraintGroup.constraint_kind == ConstraintKind.SYSTEM_MASTER_HORIZON)
    )
    assert window is not None
    assert window.start_time == RUN_AT.replace(tzinfo=None)

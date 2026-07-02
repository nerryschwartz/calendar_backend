"""Integration tests for PlanTreeService rename, preview_delete, and delete_plan."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from calendar_backend.db.session import transaction
from calendar_backend.domain.enums import CalendarEntryType, PlanKind
from calendar_backend.domain.errors import MessageCode
from calendar_backend.domain.ids import CalendarEntryID, PlanID
from calendar_backend.domain.plan_create import GoalCreatePayload, TaskCreatePayload
from calendar_backend.models.calendar import CalendarEntry
from calendar_backend.models.chains import GoalChildChainItem
from calendar_backend.models.plans import Plan
from calendar_backend.services.app_settings import AppSettingsService
from calendar_backend.services.goal import GoalService
from calendar_backend.services.master_horizon import MasterHorizonService
from calendar_backend.services.master_plan import MasterPlanService
from calendar_backend.services.plan_tree import PlanTreeService
from calendar_backend.services.plan_tree_invariant import PlanTreeInvariantService
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .conftest import FakeClock

RUN_AT = datetime(2026, 6, 7, 10, 0, tzinfo=UTC)


def _bootstrap_master_with_horizon(session: Session) -> PlanID:
    clock = FakeClock(RUN_AT)
    master = MasterPlanService(session, clock).ensure_master_exists()
    assert master.success and master.value is not None
    AppSettingsService(session, clock).get_settings()
    MasterHorizonService(session, clock).refresh_master_horizon(RUN_AT)
    return master.value.plan_id


@pytest.fixture
def master_plan_id(service_db_session: Session) -> PlanID:
    return _bootstrap_master_with_horizon(service_db_session)


def _goal_service(session: Session) -> GoalService:
    return GoalService(session, FakeClock(RUN_AT))


def _plan_tree_service(session: Session) -> PlanTreeService:
    return PlanTreeService(session, FakeClock(RUN_AT))


def _assert_tree_invariant(session: Session) -> None:
    result = PlanTreeInvariantService(session).validate_master_tree()
    assert result.success, result.errors


def _add_calendar_entry(session: Session, *, source_plan_id: PlanID) -> uuid.UUID:
    entry_id = uuid.uuid4()
    with transaction(session) as txn:
        txn.add(
            CalendarEntry(
                calendar_entry_id=entry_id,
                entry_type=CalendarEntryType.TASK,
                start_time=RUN_AT,
                end_time=RUN_AT + timedelta(hours=1),
                source_plan_id=source_plan_id,
                source_free_time_activity_id=None,
                calendar_run_id=None,
                display_label="task block",
                created_at=RUN_AT,
                updated_at=RUN_AT,
            )
        )
        txn.flush()
    return entry_id


def _two_tasks_same_chain(session: Session, master_plan_id: PlanID) -> tuple[PlanID, PlanID]:
    goal = _goal_service(session)
    first = goal.create_child(
        master_plan_id,
        PlanKind.TASK,
        TaskCreatePayload("first", 30, False, None),
        is_critical=False,
    )
    second = goal.create_child(
        master_plan_id,
        PlanKind.TASK,
        TaskCreatePayload("second", 30, False, None),
        is_critical=False,
    )
    assert first.success and first.value is not None
    assert second.success and second.value is not None
    assert goal.move_plan(second.value.plan_id, 0, 0).success
    return first.value.plan_id, second.value.plan_id


@pytest.mark.integration
def test_rename_plan_persists_name(service_db_session: Session, master_plan_id: PlanID) -> None:
    created = _goal_service(service_db_session).create_child(
        master_plan_id,
        PlanKind.TASK,
        TaskCreatePayload("old", 30, False, None),
        is_critical=False,
    )
    assert created.success and created.value is not None

    result = _plan_tree_service(service_db_session).rename_plan(created.value.plan_id, "new name")
    assert result.success

    plan = service_db_session.get(Plan, created.value.plan_id)
    assert plan is not None
    assert plan.name == "new name"
    _assert_tree_invariant(service_db_session)


@pytest.mark.integration
def test_rename_plan_master_forbidden(service_db_session: Session, master_plan_id: PlanID) -> None:
    result = _plan_tree_service(service_db_session).rename_plan(master_plan_id, "renamed")
    assert not result.success
    assert any(error.code == MessageCode.MASTER_MUTATION_FORBIDDEN for error in result.errors)


@pytest.mark.integration
def test_preview_delete_leaf_plan(service_db_session: Session, master_plan_id: PlanID) -> None:
    created = _goal_service(service_db_session).create_child(
        master_plan_id,
        PlanKind.TASK,
        TaskCreatePayload("leaf", 30, False, None),
        is_critical=False,
    )
    assert created.success and created.value is not None
    plan_id = created.value.plan_id

    preview = _plan_tree_service(service_db_session).preview_delete(plan_id)
    assert preview.success and preview.value is not None
    assert preview.value.affected_plan_ids == (plan_id,)
    assert preview.value.affected_calendar_entry_ids == ()


@pytest.mark.integration
def test_preview_delete_descendant_cascade(
    service_db_session: Session, master_plan_id: PlanID
) -> None:
    goal = _goal_service(service_db_session)
    parent = goal.create_child(
        master_plan_id,
        PlanKind.GOAL,
        GoalCreatePayload(name="parent"),
        is_critical=False,
    )
    assert parent.success and parent.value is not None
    child = goal.create_child(
        parent.value.plan_id,
        PlanKind.TASK,
        TaskCreatePayload("child", 30, False, None),
        is_critical=False,
    )
    assert child.success and child.value is not None

    preview = _plan_tree_service(service_db_session).preview_delete(parent.value.plan_id)
    assert preview.success and preview.value is not None
    assert set(preview.value.affected_plan_ids) == {
        parent.value.plan_id,
        child.value.plan_id,
    }


@pytest.mark.integration
def test_preview_delete_expands_whole_chain(
    service_db_session: Session, master_plan_id: PlanID
) -> None:
    first_id, second_id = _two_tasks_same_chain(service_db_session, master_plan_id)

    preview = _plan_tree_service(service_db_session).preview_delete(first_id)
    assert preview.success and preview.value is not None
    assert set(preview.value.affected_plan_ids) == {first_id, second_id}


@pytest.mark.integration
def test_preview_delete_critical_chain_includes_parent_goal(
    service_db_session: Session,
    master_plan_id: PlanID,
) -> None:
    goal = _goal_service(service_db_session)
    parent = goal.create_child(
        master_plan_id,
        PlanKind.GOAL,
        GoalCreatePayload(name="parent"),
        is_critical=False,
    )
    assert parent.success and parent.value is not None
    first = goal.create_child(
        parent.value.plan_id,
        PlanKind.TASK,
        TaskCreatePayload("first", 30, False, None),
        is_critical=True,
    )
    second = goal.create_child(
        parent.value.plan_id,
        PlanKind.TASK,
        TaskCreatePayload("second", 30, False, None),
        is_critical=True,
    )
    assert first.success and first.value is not None
    assert second.success and second.value is not None
    assert goal.move_plan(second.value.plan_id, 0, 0).success

    preview = _plan_tree_service(service_db_session).preview_delete(first.value.plan_id)
    assert preview.success and preview.value is not None
    assert set(preview.value.affected_plan_ids) == {
        parent.value.plan_id,
        first.value.plan_id,
        second.value.plan_id,
    }


@pytest.mark.integration
def test_preview_delete_collects_calendar_entries(
    service_db_session: Session,
    master_plan_id: PlanID,
) -> None:
    created = _goal_service(service_db_session).create_child(
        master_plan_id,
        PlanKind.TASK,
        TaskCreatePayload("task", 30, False, None),
        is_critical=False,
    )
    assert created.success and created.value is not None
    entry_id = _add_calendar_entry(service_db_session, source_plan_id=created.value.plan_id)

    preview = _plan_tree_service(service_db_session).preview_delete(created.value.plan_id)
    assert preview.success and preview.value is not None
    assert preview.value.affected_calendar_entry_ids == (CalendarEntryID(entry_id),)


@pytest.mark.integration
def test_preview_delete_master_forbidden(
    service_db_session: Session, master_plan_id: PlanID
) -> None:
    result = _plan_tree_service(service_db_session).preview_delete(master_plan_id)
    assert not result.success
    assert any(error.code == MessageCode.MASTER_DELETE_FORBIDDEN for error in result.errors)


@pytest.mark.integration
def test_preview_delete_plan_not_found(service_db_session: Session, master_plan_id: PlanID) -> None:
    result = _plan_tree_service(service_db_session).preview_delete(PlanID(uuid.uuid4()))
    assert not result.success
    assert any(error.code == MessageCode.PLAN_NOT_FOUND for error in result.errors)


@pytest.mark.integration
def test_delete_plan_matches_preview_and_removes_rows(
    service_db_session: Session,
    master_plan_id: PlanID,
) -> None:
    first_id, _second_id = _two_tasks_same_chain(service_db_session, master_plan_id)
    entry_id = _add_calendar_entry(service_db_session, source_plan_id=first_id)
    plan_tree = _plan_tree_service(service_db_session)

    preview = plan_tree.preview_delete(first_id)
    assert preview.success and preview.value is not None
    affected_plans = set(preview.value.affected_plan_ids)
    affected_entries = set(preview.value.affected_calendar_entry_ids)
    assert CalendarEntryID(entry_id) in affected_entries

    delete = plan_tree.delete_plan(first_id)
    assert delete.success

    for plan_id in affected_plans:
        assert service_db_session.get(Plan, plan_id) is None
    for calendar_entry_id in affected_entries:
        assert service_db_session.get(CalendarEntry, calendar_entry_id) is None
    assert service_db_session.get(Plan, master_plan_id) is not None
    assert service_db_session.scalar(select(func.count()).select_from(GoalChildChainItem)) == 0
    _assert_tree_invariant(service_db_session)


@pytest.mark.integration
def test_delete_plan_master_forbidden(service_db_session: Session, master_plan_id: PlanID) -> None:
    result = _plan_tree_service(service_db_session).delete_plan(master_plan_id)
    assert not result.success
    assert any(error.code == MessageCode.MASTER_DELETE_FORBIDDEN for error in result.errors)


@pytest.mark.integration
def test_delete_plan_subtree_removes_descendants(
    service_db_session: Session,
    master_plan_id: PlanID,
) -> None:
    goal = _goal_service(service_db_session)
    parent = goal.create_child(
        master_plan_id,
        PlanKind.GOAL,
        GoalCreatePayload(name="parent"),
        is_critical=False,
    )
    assert parent.success and parent.value is not None
    child = goal.create_child(
        parent.value.plan_id,
        PlanKind.TASK,
        TaskCreatePayload("child", 30, False, None),
        is_critical=False,
    )
    assert child.success and child.value is not None

    result = _plan_tree_service(service_db_session).delete_plan(parent.value.plan_id)
    assert result.success
    assert service_db_session.get(Plan, parent.value.plan_id) is None
    assert service_db_session.get(Plan, child.value.plan_id) is None
    _assert_tree_invariant(service_db_session)

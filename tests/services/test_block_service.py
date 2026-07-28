"""Integration tests for BlockService CRUD and immediate prerequisites."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from calendar_backend.domain.enums import PlanKind
from calendar_backend.domain.errors import MessageCode
from calendar_backend.domain.ids import PlanID
from calendar_backend.domain.plan_create import (
    BlockCreatePayload,
    GoalCreatePayload,
    TaskCreatePayload,
)
from calendar_backend.models.blocks import BlockCalendarEntry, BlockPlan
from calendar_backend.models.plans import TaskPlan
from calendar_backend.services.app_settings import AppSettingsService
from calendar_backend.services.block import BlockService
from calendar_backend.services.goal import GoalService
from calendar_backend.services.master_horizon import MasterHorizonService
from calendar_backend.services.master_plan import MasterPlanService
from calendar_backend.services.plan_tree import PlanTreeService
from calendar_backend.services.plan_tree_invariant import PlanTreeInvariantService
from calendar_backend.services.task import TaskService
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


def _block_service(session: Session) -> BlockService:
    return BlockService(session, FakeClock(RUN_AT))


def _task_service(session: Session) -> TaskService:
    return TaskService(session, FakeClock(RUN_AT))


def _create_block(session: Session, parent_id: PlanID, *, name: str = "block") -> PlanID:
    result = _goal_service(session).create_child(
        parent_id,
        PlanKind.BLOCK,
        BlockCreatePayload(name, 30, False, None, "focus"),
        is_critical=False,
    )
    assert result.success and result.value is not None
    return result.value.plan_id


def _create_task(session: Session, parent_id: PlanID, *, name: str = "task") -> PlanID:
    result = _goal_service(session).create_child(
        parent_id,
        PlanKind.TASK,
        TaskCreatePayload(name, 30, False, None),
        is_critical=False,
    )
    assert result.success and result.value is not None
    return result.value.plan_id


def _assert_tree_invariant(session: Session) -> None:
    result = PlanTreeInvariantService(session).validate_master_tree()
    assert result.success, result.errors


@pytest.mark.integration
def test_block_service_update_scheduling_fields(
    service_db_session: Session, master_plan_id: PlanID
) -> None:
    block_id = _create_block(service_db_session, master_plan_id)
    result = _block_service(service_db_session).update_scheduling_fields(
        block_id,
        duration_minutes=60,
        divisible=True,
        minimum_chunk_size_minutes=30,
        block_family="deep-work",
    )

    assert result.success and result.value is not None
    block_plan = service_db_session.get(BlockPlan, block_id)
    assert block_plan is not None
    assert block_plan.duration_minutes == 60
    assert block_plan.block_family == "deep-work"
    _assert_tree_invariant(service_db_session)


@pytest.mark.integration
def test_block_service_mark_complete_and_reopen(
    service_db_session: Session, master_plan_id: PlanID
) -> None:
    block_id = _create_block(service_db_session, master_plan_id)
    service = _block_service(service_db_session)

    complete = service.mark_complete(block_id)
    assert complete.success and complete.value is not None
    assert complete.value.user_completed is True

    reopen = service.reopen(block_id)
    assert reopen.success and reopen.value is not None
    assert reopen.value.user_completed is False
    _assert_tree_invariant(service_db_session)


@pytest.mark.integration
def test_block_immediate_prerequisite_task_to_block(
    service_db_session: Session, master_plan_id: PlanID
) -> None:
    task_id = _create_task(service_db_session, master_plan_id, name="first")
    block_id = _create_block(service_db_session, master_plan_id, name="second")

    result = _block_service(service_db_session).set_immediate_prerequisite(block_id, task_id)
    assert result.success and result.value is not None

    block_plan = service_db_session.get(BlockPlan, block_id)
    assert block_plan is not None
    assert block_plan.immediate_prerequisite_plan_id == task_id
    _assert_tree_invariant(service_db_session)


@pytest.mark.integration
def test_task_immediate_prerequisite_block_to_task(
    service_db_session: Session, master_plan_id: PlanID
) -> None:
    block_id = _create_block(service_db_session, master_plan_id, name="first")
    task_id = _create_task(service_db_session, master_plan_id, name="second")

    result = _task_service(service_db_session).set_immediate_prerequisite(task_id, block_id)
    assert result.success and result.value is not None

    task_plan = service_db_session.get(TaskPlan, task_id)
    assert task_plan is not None
    assert task_plan.immediate_prerequisite_plan_id == block_id
    _assert_tree_invariant(service_db_session)


@pytest.mark.integration
def test_delete_block_plan_with_calendar_entry(
    service_db_session: Session, master_plan_id: PlanID
) -> None:
    block_id = _create_block(service_db_session, master_plan_id)
    now = RUN_AT
    service_db_session.add(
        BlockCalendarEntry(
            block_calendar_entry_id=uuid.uuid4(),
            start_time=now,
            end_time=now + timedelta(minutes=30),
            source_plan_id=block_id,
            calendar_run_id=None,
            display_label="focus",
            created_at=now,
            updated_at=now,
        )
    )
    service_db_session.flush()

    result = PlanTreeService(service_db_session, FakeClock(RUN_AT)).delete_plan(block_id)
    assert result.success
    assert service_db_session.get(BlockPlan, block_id) is None
    _assert_tree_invariant(service_db_session)


@pytest.mark.integration
def test_block_immediate_prerequisite_rejects_goal_predecessor(
    service_db_session: Session, master_plan_id: PlanID
) -> None:
    nested_goal = _goal_service(service_db_session).create_child(
        master_plan_id,
        PlanKind.GOAL,
        GoalCreatePayload("nested"),
        is_critical=False,
    )
    assert nested_goal.success and nested_goal.value is not None
    block_id = _create_block(service_db_session, master_plan_id)

    result = _block_service(service_db_session).set_immediate_prerequisite(
        block_id,
        nested_goal.value.plan_id,
    )
    assert not result.success
    assert result.errors[0].code == MessageCode.IMMEDIATE_PREREQUISITE_NOT_TASK

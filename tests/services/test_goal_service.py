"""Integration tests for GoalService create_child and move_plan."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from calendar_backend.db.session import transaction
from calendar_backend.domain.enums import CloneStatus, PlanKind, RepeatMode
from calendar_backend.domain.errors import MessageCode
from calendar_backend.domain.ids import PlanID
from calendar_backend.domain.plan_create import (
    GoalCreatePayload,
    RepetitionCreatePayload,
    TaskCreatePayload,
)
from calendar_backend.models.chains import GoalChildChain, GoalChildChainItem
from calendar_backend.models.plans import Plan, RepetitionPlan, TaskPlan
from calendar_backend.models.repetitions import RepetitionInstance
from calendar_backend.services.app_settings import AppSettingsService
from calendar_backend.services.goal import GoalService
from calendar_backend.services.master_horizon import MasterHorizonService
from calendar_backend.services.master_plan import MasterPlanService
from calendar_backend.services.plan_tree_invariant import PlanTreeInvariantService
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .conftest import FakeClock

RUN_AT = datetime(2026, 6, 7, 10, 0, tzinfo=UTC)
_START = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)


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


def _repetition_payload() -> RepetitionCreatePayload:
    return RepetitionCreatePayload(
        name="weekly",
        repeat_mode=RepeatMode.MANUAL_COUNT,
        start_time=_START,
        repeat_interval_minutes=60,
        manual_count=3,
        end_time=None,
        default_instance_critical=False,
        template_type=PlanKind.GOAL,
        template_payload=GoalCreatePayload(name="template"),
    )


def _assert_tree_invariant(session: Session) -> None:
    result = PlanTreeInvariantService(session).validate_master_tree()
    assert result.success, result.errors


def _chain_sort_orders(session: Session, parent_goal_id: PlanID) -> list[int]:
    return list(
        session.scalars(
            select(GoalChildChain.sort_order)
            .where(GoalChildChain.parent_goal_id == parent_goal_id)
            .order_by(GoalChildChain.sort_order)
        ).all()
    )


def _chain_positions(session: Session, chain_id: uuid.UUID) -> list[int]:
    return list(
        session.scalars(
            select(GoalChildChainItem.position)
            .where(GoalChildChainItem.chain_id == chain_id)
            .order_by(GoalChildChainItem.position)
        ).all()
    )


def _child_plan_ids_in_chain_order(session: Session, chain_id: uuid.UUID) -> list[uuid.UUID]:
    return list(
        session.scalars(
            select(GoalChildChainItem.child_plan_id)
            .where(GoalChildChainItem.chain_id == chain_id)
            .order_by(GoalChildChainItem.position)
        ).all()
    )


@pytest.mark.integration
def test_create_child_goal_under_master_persists_chain_and_parent(
    service_db_session: Session,
    master_plan_id: PlanID,
) -> None:
    service = _goal_service(service_db_session)
    result = service.create_child(
        master_plan_id,
        PlanKind.GOAL,
        GoalCreatePayload(name="child goal"),
        is_critical=False,
    )

    assert result.success and result.value is not None
    child_id = result.value.plan_id
    plan = service_db_session.get(Plan, child_id)
    assert plan is not None
    assert plan.parent_id == master_plan_id
    assert plan.plan_kind == PlanKind.GOAL

    chain = service_db_session.scalar(
        select(GoalChildChain).where(GoalChildChain.parent_goal_id == master_plan_id)
    )
    assert chain is not None
    assert chain.sort_order == 0
    item = service_db_session.scalar(
        select(GoalChildChainItem).where(GoalChildChainItem.child_plan_id == child_id)
    )
    assert item is not None
    assert item.position == 0
    _assert_tree_invariant(service_db_session)


@pytest.mark.integration
def test_create_child_task_under_master(
    service_db_session: Session, master_plan_id: PlanID
) -> None:
    service = _goal_service(service_db_session)
    result = service.create_child(
        master_plan_id,
        PlanKind.TASK,
        TaskCreatePayload("task", 30, False, None),
        is_critical=False,
    )

    assert result.success and result.value is not None
    assert service_db_session.get(TaskPlan, result.value.plan_id) is not None
    _assert_tree_invariant(service_db_session)


@pytest.mark.integration
def test_create_child_repetition_under_master_persists_template_subtree(
    service_db_session: Session,
    master_plan_id: PlanID,
) -> None:
    service = _goal_service(service_db_session)
    result = service.create_child(
        master_plan_id,
        PlanKind.REPETITION,
        _repetition_payload(),
        is_critical=False,
    )

    assert result.success and result.value is not None
    repetition_id = result.value.plan_id
    repetition = service_db_session.get(RepetitionPlan, repetition_id)
    assert repetition is not None
    assert repetition.generated_at is None
    assert (
        service_db_session.scalar(
            select(func.count())
            .select_from(RepetitionInstance)
            .where(RepetitionInstance.repetition_plan_id == repetition_id)
        )
        == 0
    )
    template = service_db_session.get(Plan, repetition.template_root_id)
    assert template is not None
    assert template.parent_id == repetition_id
    assert template.clone_status == CloneStatus.TEMPLATE
    _assert_tree_invariant(service_db_session)


@pytest.mark.integration
def test_create_child_under_nested_goal(
    service_db_session: Session, master_plan_id: PlanID
) -> None:
    service = _goal_service(service_db_session)
    parent = service.create_child(
        master_plan_id,
        PlanKind.GOAL,
        GoalCreatePayload(name="nested"),
        is_critical=False,
    )
    assert parent.success and parent.value is not None

    child = service.create_child(
        parent.value.plan_id,
        PlanKind.TASK,
        TaskCreatePayload("nested task", 45, True, 15),
        is_critical=False,
    )
    assert child.success and child.value is not None
    plan = service_db_session.get(Plan, child.value.plan_id)
    assert plan is not None
    assert plan.parent_id == parent.value.plan_id
    _assert_tree_invariant(service_db_session)


@pytest.mark.integration
def test_sequential_creates_increment_chain_sort_order(
    service_db_session: Session,
    master_plan_id: PlanID,
) -> None:
    service = _goal_service(service_db_session)
    for index in range(3):
        result = service.create_child(
            master_plan_id,
            PlanKind.TASK,
            TaskCreatePayload(f"task-{index}", 30, False, None),
            is_critical=False,
        )
        assert result.success

    assert _chain_sort_orders(service_db_session, master_plan_id) == [0, 1, 2]
    _assert_tree_invariant(service_db_session)


@pytest.mark.integration
def test_create_child_critical_under_master_rejected(
    service_db_session: Session,
    master_plan_id: PlanID,
) -> None:
    service = _goal_service(service_db_session)
    result = service.create_child(
        master_plan_id,
        PlanKind.TASK,
        TaskCreatePayload("task", 30, False, None),
        is_critical=True,
    )

    assert not result.success
    assert any(
        error.code == MessageCode.MASTER_CHILD_MUST_BE_NON_CRITICAL for error in result.errors
    )


@pytest.mark.integration
def test_create_child_invalid_parent_not_goal(
    service_db_session: Session,
    master_plan_id: PlanID,
) -> None:
    service = _goal_service(service_db_session)
    task = service.create_child(
        master_plan_id,
        PlanKind.TASK,
        TaskCreatePayload("task", 30, False, None),
        is_critical=False,
    )
    assert task.success and task.value is not None

    result = service.create_child(
        task.value.plan_id,
        PlanKind.GOAL,
        GoalCreatePayload(name="orphan attempt"),
        is_critical=False,
    )

    assert not result.success
    assert any(error.code == MessageCode.INVALID_PARENT for error in result.errors)


@pytest.mark.integration
def test_create_child_parent_not_found(service_db_session: Session, master_plan_id: PlanID) -> None:
    service = _goal_service(service_db_session)
    missing = PlanID(uuid.uuid4())
    result = service.create_child(
        missing,
        PlanKind.TASK,
        TaskCreatePayload("task", 30, False, None),
        is_critical=False,
    )

    assert not result.success
    assert any(error.code == MessageCode.PLAN_NOT_FOUND for error in result.errors)


@pytest.mark.integration
def test_create_child_invalid_task_fields_rejected(
    service_db_session: Session,
    master_plan_id: PlanID,
) -> None:
    service = _goal_service(service_db_session)
    result = service.create_child(
        master_plan_id,
        PlanKind.TASK,
        TaskCreatePayload("task", 0, True, 15),
        is_critical=False,
    )

    assert not result.success
    assert any(error.code == MessageCode.INVALID_DURATION for error in result.errors)


@pytest.mark.integration
def test_create_child_kind_payload_mismatch_rejected(
    service_db_session: Session,
    master_plan_id: PlanID,
) -> None:
    service = _goal_service(service_db_session)
    result = service.create_child(  # pyright: ignore[reportCallIssue]
        master_plan_id,
        PlanKind.GOAL,
        TaskCreatePayload("task", 30, False, None),  # pyright: ignore[reportArgumentType]
        is_critical=False,
    )

    assert not result.success
    assert any(error.code == MessageCode.INVALID_CREATE_PAYLOAD for error in result.errors)


@pytest.mark.integration
def test_move_plan_reorders_within_chain(
    service_db_session: Session, master_plan_id: PlanID
) -> None:
    service = _goal_service(service_db_session)
    first = service.create_child(
        master_plan_id,
        PlanKind.TASK,
        TaskCreatePayload("first", 30, False, None),
        is_critical=False,
    )
    second = service.create_child(
        master_plan_id,
        PlanKind.TASK,
        TaskCreatePayload("second", 30, False, None),
        is_critical=False,
    )
    assert first.success and first.value is not None
    assert second.success and second.value is not None

    move_into_chain = service.move_plan(second.value.plan_id, 0, 0)
    assert move_into_chain.success

    chain_id = service_db_session.scalar(
        select(GoalChildChainItem.chain_id).where(
            GoalChildChainItem.child_plan_id == first.value.plan_id
        )
    )
    assert chain_id is not None
    assert _child_plan_ids_in_chain_order(service_db_session, chain_id) == [
        second.value.plan_id,
        first.value.plan_id,
    ]

    reorder = service.move_plan(second.value.plan_id, 1)
    assert reorder.success
    assert _child_plan_ids_in_chain_order(service_db_session, chain_id) == [
        first.value.plan_id,
        second.value.plan_id,
    ]
    _assert_tree_invariant(service_db_session)


@pytest.mark.integration
def test_move_plan_cross_chain_moves_item(
    service_db_session: Session, master_plan_id: PlanID
) -> None:
    service = _goal_service(service_db_session)
    left = service.create_child(
        master_plan_id,
        PlanKind.TASK,
        TaskCreatePayload("left", 30, False, None),
        is_critical=False,
    )
    right = service.create_child(
        master_plan_id,
        PlanKind.TASK,
        TaskCreatePayload("right", 30, False, None),
        is_critical=False,
    )
    assert left.success and left.value is not None
    assert right.success and right.value is not None

    result = service.move_plan(right.value.plan_id, 0, 0)
    assert result.success

    chain_id = service_db_session.scalar(
        select(GoalChildChainItem.chain_id).where(
            GoalChildChainItem.child_plan_id == left.value.plan_id
        )
    )
    assert chain_id is not None
    assert _child_plan_ids_in_chain_order(service_db_session, chain_id) == [
        right.value.plan_id,
        left.value.plan_id,
    ]
    _assert_tree_invariant(service_db_session)


@pytest.mark.integration
def test_move_plan_position_minus_one_appends_in_chain(
    service_db_session: Session,
    master_plan_id: PlanID,
) -> None:
    service = _goal_service(service_db_session)
    first = service.create_child(
        master_plan_id,
        PlanKind.TASK,
        TaskCreatePayload("first", 30, False, None),
        is_critical=False,
    )
    second = service.create_child(
        master_plan_id,
        PlanKind.TASK,
        TaskCreatePayload("second", 30, False, None),
        is_critical=False,
    )
    assert first.success and first.value is not None
    assert second.success and second.value is not None
    assert service.move_plan(second.value.plan_id, 0, 0).success

    result = service.move_plan(first.value.plan_id, -1)
    assert result.success

    chain_id = service_db_session.scalar(
        select(GoalChildChainItem.chain_id).where(
            GoalChildChainItem.child_plan_id == first.value.plan_id
        )
    )
    assert chain_id is not None
    assert _child_plan_ids_in_chain_order(service_db_session, chain_id) == [
        second.value.plan_id,
        first.value.plan_id,
    ]
    _assert_tree_invariant(service_db_session)


@pytest.mark.integration
def test_move_plan_chain_index_minus_one_creates_new_chain(
    service_db_session: Session,
    master_plan_id: PlanID,
) -> None:
    service = _goal_service(service_db_session)
    nested_goal = service.create_child(
        master_plan_id,
        PlanKind.GOAL,
        GoalCreatePayload(name="nested"),
        is_critical=False,
    )
    assert nested_goal.success and nested_goal.value is not None
    parent_id = nested_goal.value.plan_id

    first = service.create_child(
        parent_id,
        PlanKind.TASK,
        TaskCreatePayload("first", 30, False, None),
        is_critical=True,
    )
    second = service.create_child(
        parent_id,
        PlanKind.TASK,
        TaskCreatePayload("second", 30, False, None),
        is_critical=True,
    )
    assert first.success and first.value is not None
    assert second.success and second.value is not None
    assert service.move_plan(second.value.plan_id, 0, 0).success

    result = service.move_plan(first.value.plan_id, -1, 0)
    assert result.success

    chains = list(
        service_db_session.scalars(
            select(GoalChildChain)
            .where(GoalChildChain.parent_goal_id == parent_id)
            .order_by(GoalChildChain.sort_order)
        ).all()
    )
    assert len(chains) == 2
    assert chains[0].is_critical is True
    assert chains[1].is_critical is True
    assert _chain_positions(service_db_session, chains[1].goal_child_chain_id) == [0]
    _assert_tree_invariant(service_db_session)


@pytest.mark.integration
def test_move_plan_deletes_empty_source_chain(
    service_db_session: Session,
    master_plan_id: PlanID,
) -> None:
    service = _goal_service(service_db_session)
    left = service.create_child(
        master_plan_id,
        PlanKind.TASK,
        TaskCreatePayload("left", 30, False, None),
        is_critical=False,
    )
    right = service.create_child(
        master_plan_id,
        PlanKind.TASK,
        TaskCreatePayload("right", 30, False, None),
        is_critical=False,
    )
    assert left.success and left.value is not None
    assert right.success and right.value is not None

    source_chain_id = service_db_session.scalar(
        select(GoalChildChainItem.chain_id).where(
            GoalChildChainItem.child_plan_id == right.value.plan_id
        )
    )
    assert source_chain_id is not None
    assert service.move_plan(right.value.plan_id, 0, -1).success

    assert service_db_session.get(GoalChildChain, source_chain_id) is None
    _assert_tree_invariant(service_db_session)


@pytest.mark.integration
def test_move_plan_master_forbidden(service_db_session: Session, master_plan_id: PlanID) -> None:
    service = _goal_service(service_db_session)
    result = service.move_plan(master_plan_id, 0)
    assert not result.success
    assert any(error.code == MessageCode.MASTER_MUTATION_FORBIDDEN for error in result.errors)


@pytest.mark.integration
def test_move_plan_not_in_chain(service_db_session: Session, master_plan_id: PlanID) -> None:
    orphan_id = uuid.uuid4()
    with transaction(service_db_session) as txn:
        txn.add(
            Plan(
                plan_id=orphan_id,
                plan_kind=PlanKind.TASK,
                name="orphan",
                parent_id=master_plan_id,
                is_master=False,
                cloned_from_id=None,
                clone_status=CloneStatus.NOT_CLONED,
                created_at=RUN_AT,
                updated_at=RUN_AT,
            )
        )
        txn.add(
            TaskPlan(
                plan_id=orphan_id,
                duration_minutes=30,
                divisible=False,
                minimum_chunk_size_minutes=None,
                user_completed=False,
                completed_at=None,
            )
        )
        txn.flush()

    result = _goal_service(service_db_session).move_plan(PlanID(orphan_id), 0)
    assert not result.success
    assert any(error.code == MessageCode.PLAN_NOT_IN_CHAIN for error in result.errors)

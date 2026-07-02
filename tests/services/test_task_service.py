"""Integration tests for TaskService scheduling, completion, and clone detachment."""

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
from calendar_backend.models.plans import GoalPlan, Plan, RepetitionPlan, TaskPlan
from calendar_backend.services.app_settings import AppSettingsService
from calendar_backend.services.goal import GoalService
from calendar_backend.services.master_horizon import MasterHorizonService
from calendar_backend.services.master_plan import MasterPlanService
from calendar_backend.services.plan_tree_invariant import PlanTreeInvariantService
from calendar_backend.services.task import TaskService
from sqlalchemy.orm import Session

from .conftest import FakeClock

RUN_AT = datetime(2026, 6, 7, 10, 0, tzinfo=UTC)
_START = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
_STORED_RUN_AT = RUN_AT.replace(tzinfo=None)


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


def _task_service(session: Session) -> TaskService:
    return TaskService(session, FakeClock(RUN_AT))


def _goal_service(session: Session) -> GoalService:
    return GoalService(session, FakeClock(RUN_AT))


def _create_task(
    session: Session,
    parent_id: PlanID,
    *,
    name: str = "task",
    duration_minutes: int = 30,
    divisible: bool = False,
    minimum_chunk_size_minutes: int | None = None,
) -> PlanID:
    result = _goal_service(session).create_child(
        parent_id,
        PlanKind.TASK,
        TaskCreatePayload(name, duration_minutes, divisible, minimum_chunk_size_minutes),
        is_critical=False,
    )
    assert result.success and result.value is not None
    return result.value.plan_id


def _clone_status(session: Session, plan_id: PlanID) -> CloneStatus:
    plan = session.get(Plan, plan_id)
    assert plan is not None
    return plan.clone_status


def _assert_tree_invariant(session: Session) -> None:
    result = PlanTreeInvariantService(session).validate_master_tree()
    assert result.success, result.errors


def _seed_linked_task_detach_subtree(
    session: Session,
    master_id: PlanID,
) -> dict[str, PlanID]:
    repetition_result = _goal_service(session).create_child(
        master_id,
        PlanKind.REPETITION,
        RepetitionCreatePayload(
            name="weekly",
            repeat_mode=RepeatMode.MANUAL_COUNT,
            start_time=_START,
            repeat_interval_minutes=60,
            manual_count=1,
            end_time=None,
            default_instance_critical=False,
            template_type=PlanKind.GOAL,
            template_payload=GoalCreatePayload(name="template"),
        ),
        is_critical=False,
    )
    assert repetition_result.success and repetition_result.value is not None
    repetition_id = repetition_result.value.plan_id
    repetition = session.get(RepetitionPlan, repetition_id)
    assert repetition is not None
    template_id = PlanID(repetition.template_root_id)

    clone_goal_id = PlanID(uuid.uuid4())
    sibling_clone_id = PlanID(uuid.uuid4())
    target_task_id = PlanID(uuid.uuid4())
    nested_task_id = PlanID(uuid.uuid4())
    sibling_task_id = PlanID(uuid.uuid4())

    with transaction(session) as txn:
        for goal_id, name in (
            (clone_goal_id, "clone goal"),
            (sibling_clone_id, "sibling clone"),
        ):
            txn.add(
                Plan(
                    plan_id=goal_id,
                    plan_kind=PlanKind.GOAL,
                    name=name,
                    parent_id=repetition_id,
                    is_master=False,
                    cloned_from_id=template_id,
                    clone_status=CloneStatus.LINKED,
                    created_at=RUN_AT,
                    updated_at=RUN_AT,
                )
            )
            txn.add(GoalPlan(plan_id=goal_id))

        for task_id, parent_id, name in (
            (target_task_id, clone_goal_id, "target task"),
            (nested_task_id, target_task_id, "nested task"),
            (sibling_task_id, sibling_clone_id, "sibling task"),
        ):
            txn.add(
                Plan(
                    plan_id=task_id,
                    plan_kind=PlanKind.TASK,
                    name=name,
                    parent_id=parent_id,
                    is_master=False,
                    cloned_from_id=template_id,
                    clone_status=CloneStatus.LINKED,
                    created_at=RUN_AT,
                    updated_at=RUN_AT,
                )
            )
            txn.add(
                TaskPlan(
                    plan_id=task_id,
                    duration_minutes=30,
                    divisible=False,
                    minimum_chunk_size_minutes=None,
                    user_completed=False,
                    completed_at=None,
                )
            )
        txn.flush()

    return {
        "repetition_id": repetition_id,
        "template_id": template_id,
        "clone_goal_id": clone_goal_id,
        "sibling_clone_id": sibling_clone_id,
        "target_task_id": target_task_id,
        "nested_task_id": nested_task_id,
        "sibling_task_id": sibling_task_id,
    }


@pytest.mark.integration
def test_update_scheduling_fields_persists_valid_change(
    service_db_session: Session,
    master_plan_id: PlanID,
) -> None:
    task_id = _create_task(service_db_session, master_plan_id)
    service = _task_service(service_db_session)

    result = service.update_scheduling_fields(task_id, 45, True, 15)

    assert result.success and result.value is not None
    assert result.value.duration_minutes == 45
    assert result.value.divisible is True
    assert result.value.minimum_chunk_size_minutes == 15
    task_plan = service_db_session.get(TaskPlan, task_id)
    assert task_plan is not None
    assert task_plan.duration_minutes == 45
    plan = service_db_session.get(Plan, task_id)
    assert plan is not None
    assert plan.updated_at == _STORED_RUN_AT
    _assert_tree_invariant(service_db_session)


@pytest.mark.integration
def test_update_scheduling_fields_rejects_invalid_divisible_pairing(
    service_db_session: Session,
    master_plan_id: PlanID,
) -> None:
    task_id = _create_task(service_db_session, master_plan_id)

    result = _task_service(service_db_session).update_scheduling_fields(task_id, 30, True, None)

    assert not result.success
    assert any(error.code == MessageCode.INVALID_TASK_SCHEDULING_FIELDS for error in result.errors)
    task_plan = service_db_session.get(TaskPlan, task_id)
    assert task_plan is not None
    assert task_plan.duration_minutes == 30
    assert task_plan.divisible is False


@pytest.mark.integration
def test_update_scheduling_fields_rejects_non_positive_duration(
    service_db_session: Session,
    master_plan_id: PlanID,
) -> None:
    task_id = _create_task(service_db_session, master_plan_id)

    result = _task_service(service_db_session).update_scheduling_fields(task_id, 0, False, None)

    assert not result.success
    assert any(error.code == MessageCode.INVALID_DURATION for error in result.errors)


@pytest.mark.integration
def test_update_scheduling_fields_rejects_chunk_exceeding_duration(
    service_db_session: Session,
    master_plan_id: PlanID,
) -> None:
    task_id = _create_task(service_db_session, master_plan_id)

    result = _task_service(service_db_session).update_scheduling_fields(task_id, 30, True, 31)

    assert not result.success
    assert any(error.code == MessageCode.MINIMUM_CHUNK_SIZE_IMPOSSIBLE for error in result.errors)


@pytest.mark.integration
def test_update_scheduling_fields_plan_not_found(service_db_session: Session) -> None:
    missing_id = PlanID(uuid.uuid4())

    result = _task_service(service_db_session).update_scheduling_fields(missing_id, 30, False, None)

    assert not result.success
    assert any(error.code == MessageCode.PLAN_NOT_FOUND for error in result.errors)


@pytest.mark.integration
def test_update_scheduling_fields_rejects_non_task_plan(
    service_db_session: Session,
    master_plan_id: PlanID,
) -> None:
    goal_result = _goal_service(service_db_session).create_child(
        master_plan_id,
        PlanKind.GOAL,
        GoalCreatePayload(name="goal"),
        is_critical=False,
    )
    assert goal_result.success and goal_result.value is not None

    result = _task_service(service_db_session).update_scheduling_fields(
        goal_result.value.plan_id, 30, False, None
    )

    assert not result.success
    assert any(error.code == MessageCode.PLAN_SUBTYPE_MISMATCH for error in result.errors)


@pytest.mark.integration
def test_update_scheduling_fields_rejects_missing_task_plan_row(
    service_db_session: Session,
    master_plan_id: PlanID,
) -> None:
    task_shell_id = uuid.uuid4()
    with transaction(service_db_session) as txn:
        txn.add(
            Plan(
                plan_id=task_shell_id,
                plan_kind=PlanKind.TASK,
                name="task shell",
                parent_id=master_plan_id,
                is_master=False,
                cloned_from_id=None,
                clone_status=CloneStatus.NOT_CLONED,
                created_at=RUN_AT,
                updated_at=RUN_AT,
            )
        )
        txn.flush()

    result = _task_service(service_db_session).update_scheduling_fields(
        PlanID(task_shell_id), 30, False, None
    )

    assert not result.success
    assert any(error.code == MessageCode.PLAN_SUBTYPE_MISMATCH for error in result.errors)


@pytest.mark.integration
def test_update_scheduling_fields_while_completed(
    service_db_session: Session,
    master_plan_id: PlanID,
) -> None:
    task_id = _create_task(service_db_session, master_plan_id)
    service = _task_service(service_db_session)
    assert service.mark_complete(task_id).success

    result = service.update_scheduling_fields(task_id, 60, True, 20)

    assert result.success and result.value is not None
    assert result.value.user_completed is True
    assert result.value.completed_at is not None
    assert result.value.completed_at.replace(tzinfo=UTC) == RUN_AT
    assert result.value.duration_minutes == 60


@pytest.mark.integration
def test_update_scheduling_fields_no_op_same_values_succeeds(
    service_db_session: Session,
    master_plan_id: PlanID,
) -> None:
    task_id = _create_task(service_db_session, master_plan_id)

    result = _task_service(service_db_session).update_scheduling_fields(task_id, 30, False, None)

    assert result.success and result.value is not None
    assert result.value.duration_minutes == 30


@pytest.mark.integration
def test_mark_complete_sets_completion_fields(
    service_db_session: Session,
    master_plan_id: PlanID,
) -> None:
    task_id = _create_task(service_db_session, master_plan_id)

    result = _task_service(service_db_session).mark_complete(task_id)

    assert result.success and result.value is not None
    assert result.value.user_completed is True
    assert result.value.completed_at is not None
    assert result.value.completed_at.replace(tzinfo=UTC) == RUN_AT
    plan = service_db_session.get(Plan, task_id)
    assert plan is not None
    assert plan.updated_at == _STORED_RUN_AT


@pytest.mark.integration
def test_mark_complete_rejects_already_completed(
    service_db_session: Session,
    master_plan_id: PlanID,
) -> None:
    task_id = _create_task(service_db_session, master_plan_id)
    service = _task_service(service_db_session)
    assert service.mark_complete(task_id).success

    result = service.mark_complete(task_id)

    assert not result.success
    assert any(error.code == MessageCode.TASK_ALREADY_COMPLETED for error in result.errors)


@pytest.mark.integration
def test_reopen_clears_completion_fields(
    service_db_session: Session,
    master_plan_id: PlanID,
) -> None:
    task_id = _create_task(service_db_session, master_plan_id)
    service = _task_service(service_db_session)
    assert service.mark_complete(task_id).success

    result = service.reopen(task_id)

    assert result.success and result.value is not None
    assert result.value.user_completed is False
    assert result.value.completed_at is None


@pytest.mark.integration
def test_reopen_idempotent_when_already_open(
    service_db_session: Session,
    master_plan_id: PlanID,
) -> None:
    task_id = _create_task(service_db_session, master_plan_id)

    result = _task_service(service_db_session).reopen(task_id)

    assert result.success and result.value is not None
    assert result.value.user_completed is False
    assert result.value.completed_at is None


@pytest.mark.integration
def test_reopen_preserves_scheduling_fields(
    service_db_session: Session,
    master_plan_id: PlanID,
) -> None:
    task_id = _create_task(
        service_db_session,
        master_plan_id,
        duration_minutes=45,
        divisible=True,
        minimum_chunk_size_minutes=15,
    )
    service = _task_service(service_db_session)
    assert service.mark_complete(task_id).success

    result = service.reopen(task_id)

    assert result.success and result.value is not None
    assert result.value.duration_minutes == 45
    assert result.value.divisible is True
    assert result.value.minimum_chunk_size_minutes == 15


@pytest.mark.integration
def test_update_scheduling_fields_detaches_linked_self_and_descendants(
    service_db_session: Session,
    master_plan_id: PlanID,
) -> None:
    seeded = _seed_linked_task_detach_subtree(service_db_session, master_plan_id)
    target_task_id = seeded["target_task_id"]

    result = _task_service(service_db_session).update_scheduling_fields(
        target_task_id, 45, False, None
    )

    assert result.success
    assert _clone_status(service_db_session, target_task_id) == CloneStatus.DETACHED
    assert _clone_status(service_db_session, seeded["nested_task_id"]) == CloneStatus.DETACHED
    assert _clone_status(service_db_session, seeded["clone_goal_id"]) == CloneStatus.LINKED
    assert _clone_status(service_db_session, seeded["sibling_clone_id"]) == CloneStatus.LINKED
    assert _clone_status(service_db_session, seeded["sibling_task_id"]) == CloneStatus.LINKED
    assert _clone_status(service_db_session, seeded["template_id"]) == CloneStatus.TEMPLATE


@pytest.mark.integration
def test_mark_complete_detaches_linked_self_and_descendants(
    service_db_session: Session,
    master_plan_id: PlanID,
) -> None:
    seeded = _seed_linked_task_detach_subtree(service_db_session, master_plan_id)
    target_task_id = seeded["target_task_id"]

    result = _task_service(service_db_session).mark_complete(target_task_id)

    assert result.success
    assert _clone_status(service_db_session, target_task_id) == CloneStatus.DETACHED
    assert _clone_status(service_db_session, seeded["nested_task_id"]) == CloneStatus.DETACHED
    assert _clone_status(service_db_session, seeded["clone_goal_id"]) == CloneStatus.LINKED
    assert _clone_status(service_db_session, seeded["sibling_task_id"]) == CloneStatus.LINKED


@pytest.mark.integration
def test_update_scheduling_fields_leaves_not_cloned_task_unchanged(
    service_db_session: Session,
    master_plan_id: PlanID,
) -> None:
    task_id = _create_task(service_db_session, master_plan_id)

    result = _task_service(service_db_session).update_scheduling_fields(task_id, 45, False, None)

    assert result.success
    assert _clone_status(service_db_session, task_id) == CloneStatus.NOT_CLONED


@pytest.mark.integration
def test_mark_complete_on_detached_task_does_not_change_sibling_linked_clone(
    service_db_session: Session,
    master_plan_id: PlanID,
) -> None:
    seeded = _seed_linked_task_detach_subtree(service_db_session, master_plan_id)
    target_task_id = seeded["target_task_id"]
    with transaction(service_db_session) as txn:
        target = txn.get(Plan, target_task_id)
        assert target is not None
        target.clone_status = CloneStatus.DETACHED
        txn.flush()

    result = _task_service(service_db_session).mark_complete(target_task_id)

    assert result.success
    assert _clone_status(service_db_session, target_task_id) == CloneStatus.DETACHED
    assert _clone_status(service_db_session, seeded["sibling_task_id"]) == CloneStatus.LINKED

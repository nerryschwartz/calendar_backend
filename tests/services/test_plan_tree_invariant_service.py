"""Integration tests for PlanTreeInvariantService."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from calendar_backend.db.session import transaction
from calendar_backend.domain.enums import CloneStatus, ConstraintKind, PlanKind, RepeatMode
from calendar_backend.domain.errors import MessageCode
from calendar_backend.domain.ids import PlanID
from calendar_backend.domain.plan_create import GoalCreatePayload, RepetitionCreatePayload
from calendar_backend.models.chains import GoalChildChain, GoalChildChainItem
from calendar_backend.models.constraints import TimeConstraintGroup
from calendar_backend.models.plans import GoalPlan, Plan, RepetitionPlan, TaskPlan
from calendar_backend.models.repetitions import RepetitionInstance
from calendar_backend.services.app_settings import AppSettingsService
from calendar_backend.services.goal import GoalService
from calendar_backend.services.master_horizon import MasterHorizonService
from calendar_backend.services.master_plan import MasterPlanService
from calendar_backend.services.plan_tree_invariant import PlanTreeInvariantService
from calendar_backend.services.repetition import RepetitionService
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


def _add_chain_item(
    txn: Session,
    *,
    parent_goal_id: uuid.UUID,
    child_plan_id: uuid.UUID,
    chain_id: uuid.UUID | None = None,
    position: int = 0,
) -> uuid.UUID:
    resolved_chain_id = chain_id or uuid.uuid4()
    txn.add(
        GoalChildChain(
            goal_child_chain_id=resolved_chain_id,
            parent_goal_id=parent_goal_id,
            is_critical=False,
            sort_order=0,
            created_at=RUN_AT,
            updated_at=RUN_AT,
        )
    )
    txn.add(
        GoalChildChainItem(
            goal_child_chain_item_id=uuid.uuid4(),
            chain_id=resolved_chain_id,
            child_plan_id=child_plan_id,
            position=position,
        )
    )
    return resolved_chain_id


def _seed_valid_repetition_create_shape(
    txn: Session,
    master_id: PlanID,
) -> uuid.UUID:
    goal_id = uuid.uuid4()
    template_id = uuid.uuid4()
    repetition_id = uuid.uuid4()

    txn.add(
        Plan(
            plan_id=goal_id,
            plan_kind=PlanKind.GOAL,
            name="goal",
            parent_id=master_id,
            is_master=False,
            cloned_from_id=None,
            clone_status=CloneStatus.NOT_CLONED,
            created_at=RUN_AT,
            updated_at=RUN_AT,
        )
    )
    txn.add(GoalPlan(plan_id=goal_id))
    _add_chain_item(txn, parent_goal_id=master_id, child_plan_id=goal_id)

    txn.add(
        Plan(
            plan_id=repetition_id,
            plan_kind=PlanKind.REPETITION,
            name="repetition",
            parent_id=goal_id,
            is_master=False,
            cloned_from_id=None,
            clone_status=CloneStatus.NOT_CLONED,
            created_at=RUN_AT,
            updated_at=RUN_AT,
        )
    )
    txn.add(
        Plan(
            plan_id=template_id,
            plan_kind=PlanKind.GOAL,
            name="template",
            parent_id=repetition_id,
            is_master=False,
            cloned_from_id=None,
            clone_status=CloneStatus.TEMPLATE,
            created_at=RUN_AT,
            updated_at=RUN_AT,
        )
    )
    txn.add(GoalPlan(plan_id=template_id))
    txn.add(
        RepetitionPlan(
            plan_id=repetition_id,
            repeat_mode=RepeatMode.MANUAL_COUNT,
            start_time=RUN_AT,
            repeat_interval_minutes=60,
            manual_count=1,
            end_time=None,
            template_root_id=template_id,
            default_instance_critical=False,
            generated_at=None,
        )
    )
    _add_chain_item(txn, parent_goal_id=goal_id, child_plan_id=repetition_id)

    txn.flush()
    return repetition_id


def _seed_valid_repetition_instance(
    txn: Session,
    master_id: PlanID,
) -> tuple[uuid.UUID, uuid.UUID]:
    repetition_id = _seed_valid_repetition_create_shape(txn, master_id)
    repetition_plan = txn.get(RepetitionPlan, repetition_id)
    assert repetition_plan is not None
    template_root_id = repetition_plan.template_root_id
    clone_id = uuid.uuid4()
    txn.add(
        Plan(
            plan_id=clone_id,
            plan_kind=PlanKind.GOAL,
            name="clone",
            parent_id=repetition_id,
            is_master=False,
            cloned_from_id=template_root_id,
            clone_status=CloneStatus.LINKED,
            created_at=RUN_AT,
            updated_at=RUN_AT,
        )
    )
    txn.add(GoalPlan(plan_id=clone_id))
    txn.add(
        RepetitionInstance(
            repetition_instance_id=uuid.uuid4(),
            repetition_plan_id=repetition_id,
            instance_index=0,
            root_clone_id=clone_id,
            instance_start_time=RUN_AT,
            is_critical=False,
            sort_order=0,
        )
    )
    txn.flush()
    return repetition_id, clone_id


@pytest.mark.integration
def test_validate_master_tree_passes_after_bootstrap(service_db_session: Session) -> None:
    _bootstrap_master_with_horizon(service_db_session)

    result = PlanTreeInvariantService(service_db_session).validate_master_tree()

    assert result.success


@pytest.mark.integration
def test_validate_master_tree_reports_orphan_plan(service_db_session: Session) -> None:
    _bootstrap_master_with_horizon(service_db_session)
    orphan_id = uuid.uuid4()
    with transaction(service_db_session) as txn:
        txn.add(
            Plan(
                plan_id=orphan_id,
                plan_kind=PlanKind.TASK,
                name="orphan",
                parent_id=None,
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

    result = PlanTreeInvariantService(service_db_session).validate_master_tree()

    assert not result.success
    assert any(error.code == MessageCode.ORPHAN_PLAN for error in result.errors)


@pytest.mark.integration
def test_validate_master_tree_reports_subtype_mismatch(service_db_session: Session) -> None:
    master_id = _bootstrap_master_with_horizon(service_db_session)
    task_id = uuid.uuid4()
    with transaction(service_db_session) as txn:
        txn.add(
            Plan(
                plan_id=task_id,
                plan_kind=PlanKind.TASK,
                name="task",
                parent_id=master_id,
                is_master=False,
                cloned_from_id=None,
                clone_status=CloneStatus.NOT_CLONED,
                created_at=RUN_AT,
                updated_at=RUN_AT,
            )
        )
        txn.flush()

    result = PlanTreeInvariantService(service_db_session).validate_master_tree()

    assert not result.success
    assert any(error.code == MessageCode.PLAN_SUBTYPE_MISMATCH for error in result.errors)


@pytest.mark.integration
def test_validate_master_tree_reports_empty_user_group(service_db_session: Session) -> None:
    master_id = _bootstrap_master_with_horizon(service_db_session)
    with transaction(service_db_session) as txn:
        txn.add(
            TimeConstraintGroup(
                time_constraint_group_id=uuid.uuid4(),
                plan_id=master_id,
                constraint_kind=ConstraintKind.USER,
            )
        )
        txn.flush()

    result = PlanTreeInvariantService(service_db_session).validate_master_tree()

    assert not result.success
    assert any(
        error.code == MessageCode.CONSTRAINT_INVARIANT_VIOLATION
        and "USER constraint group" in error.message
        for error in result.errors
    )


@pytest.mark.integration
def test_validate_master_tree_reports_misaligned_chain_child(
    service_db_session: Session,
) -> None:
    master_id = _bootstrap_master_with_horizon(service_db_session)
    goal_id = uuid.uuid4()
    child_id = uuid.uuid4()
    chain_id = uuid.uuid4()
    with transaction(service_db_session) as txn:
        txn.add(
            Plan(
                plan_id=goal_id,
                plan_kind=PlanKind.GOAL,
                name="goal",
                parent_id=master_id,
                is_master=False,
                cloned_from_id=None,
                clone_status=CloneStatus.NOT_CLONED,
                created_at=RUN_AT,
                updated_at=RUN_AT,
            )
        )
        txn.add(GoalPlan(plan_id=goal_id))
        txn.add(
            Plan(
                plan_id=child_id,
                plan_kind=PlanKind.TASK,
                name="child",
                parent_id=master_id,
                is_master=False,
                cloned_from_id=None,
                clone_status=CloneStatus.NOT_CLONED,
                created_at=RUN_AT,
                updated_at=RUN_AT,
            )
        )
        txn.add(
            TaskPlan(
                plan_id=child_id,
                duration_minutes=30,
                divisible=False,
                minimum_chunk_size_minutes=None,
                user_completed=False,
                completed_at=None,
            )
        )
        txn.add(
            GoalChildChain(
                goal_child_chain_id=chain_id,
                parent_goal_id=goal_id,
                is_critical=False,
                sort_order=0,
                created_at=RUN_AT,
                updated_at=RUN_AT,
            )
        )
        txn.add(
            GoalChildChainItem(
                goal_child_chain_item_id=uuid.uuid4(),
                chain_id=chain_id,
                child_plan_id=child_id,
                position=0,
            )
        )
        txn.flush()

    result = PlanTreeInvariantService(service_db_session).validate_master_tree()

    assert not result.success
    assert any(
        error.code == MessageCode.CHAIN_INVARIANT_VIOLATION
        and "direct child of the parent goal" in error.message
        for error in result.errors
    )


@pytest.mark.integration
def test_validate_master_tree_reports_non_dense_chain_position(
    service_db_session: Session,
) -> None:
    master_id = _bootstrap_master_with_horizon(service_db_session)
    goal_id = uuid.uuid4()
    child_a_id = uuid.uuid4()
    child_b_id = uuid.uuid4()
    chain_id = uuid.uuid4()
    with transaction(service_db_session) as txn:
        txn.add(
            Plan(
                plan_id=goal_id,
                plan_kind=PlanKind.GOAL,
                name="goal",
                parent_id=master_id,
                is_master=False,
                cloned_from_id=None,
                clone_status=CloneStatus.NOT_CLONED,
                created_at=RUN_AT,
                updated_at=RUN_AT,
            )
        )
        txn.add(GoalPlan(plan_id=goal_id))
        for child_id in (child_a_id, child_b_id):
            txn.add(
                Plan(
                    plan_id=child_id,
                    plan_kind=PlanKind.TASK,
                    name="child",
                    parent_id=goal_id,
                    is_master=False,
                    cloned_from_id=None,
                    clone_status=CloneStatus.NOT_CLONED,
                    created_at=RUN_AT,
                    updated_at=RUN_AT,
                )
            )
            txn.add(
                TaskPlan(
                    plan_id=child_id,
                    duration_minutes=30,
                    divisible=False,
                    minimum_chunk_size_minutes=None,
                    user_completed=False,
                    completed_at=None,
                )
            )
        txn.add(
            GoalChildChain(
                goal_child_chain_id=chain_id,
                parent_goal_id=goal_id,
                is_critical=False,
                sort_order=0,
                created_at=RUN_AT,
                updated_at=RUN_AT,
            )
        )
        txn.add(
            GoalChildChainItem(
                goal_child_chain_item_id=uuid.uuid4(),
                chain_id=chain_id,
                child_plan_id=child_a_id,
                position=0,
            )
        )
        txn.add(
            GoalChildChainItem(
                goal_child_chain_item_id=uuid.uuid4(),
                chain_id=chain_id,
                child_plan_id=child_b_id,
                position=2,
            )
        )
        txn.flush()

    result = PlanTreeInvariantService(service_db_session).validate_master_tree()

    assert not result.success
    assert any(
        error.code == MessageCode.CHAIN_INVARIANT_VIOLATION
        and "positions must be dense" in error.message
        for error in result.errors
    )


@pytest.mark.integration
def test_validate_master_tree_passes_with_valid_repetition_create_shape(
    service_db_session: Session,
) -> None:
    master_id = _bootstrap_master_with_horizon(service_db_session)
    with transaction(service_db_session) as txn:
        _seed_valid_repetition_create_shape(txn, master_id)

    result = PlanTreeInvariantService(service_db_session).validate_master_tree()

    assert result.success


@pytest.mark.integration
def test_validate_master_tree_passes_with_instances_before_generated_at(
    service_db_session: Session,
) -> None:
    master_id = _bootstrap_master_with_horizon(service_db_session)
    with transaction(service_db_session) as txn:
        _seed_valid_repetition_instance(txn, master_id)

    result = PlanTreeInvariantService(service_db_session).validate_master_tree()

    assert result.success


@pytest.mark.integration
def test_validate_master_tree_passes_after_repetition_generate_and_refresh(
    service_db_session: Session,
) -> None:
    master_id = _bootstrap_master_with_horizon(service_db_session)
    clock = FakeClock(RUN_AT)
    goal_service = GoalService(service_db_session, clock)
    repetition_service = RepetitionService(service_db_session, clock)
    payload = RepetitionCreatePayload(
        name="weekly",
        repeat_mode=RepeatMode.MANUAL_COUNT,
        start_time=RUN_AT,
        repeat_interval_minutes=60,
        manual_count=1,
        end_time=None,
        default_instance_critical=False,
        template_type=PlanKind.GOAL,
        template_payload=GoalCreatePayload(name="template"),
    )
    create = goal_service.create_child(master_id, PlanKind.REPETITION, payload, is_critical=False)
    assert create.success and create.value is not None
    repetition_id = create.value.plan_id

    generate = repetition_service.generate_instances(repetition_id, RUN_AT)
    assert generate.success

    refresh = repetition_service.refresh_all_repetitions(RUN_AT)
    assert refresh.success

    result = PlanTreeInvariantService(service_db_session).validate_master_tree()

    assert result.success


@pytest.mark.integration
def test_validate_master_tree_reports_repetition_clone_wrong_parent(
    service_db_session: Session,
) -> None:
    master_id = _bootstrap_master_with_horizon(service_db_session)
    with transaction(service_db_session) as txn:
        repetition_id, clone_id = _seed_valid_repetition_instance(txn, master_id)
        clone = txn.get(Plan, clone_id)
        assert clone is not None
        clone.parent_id = master_id
        txn.flush()

    result = PlanTreeInvariantService(service_db_session).validate_master_tree()

    assert not result.success
    assert any(
        error.code == MessageCode.CHAIN_INVARIANT_VIOLATION
        and "Repetition root clone must be child of repetition plan" in error.message
        and error.details.get("repetition_plan_id") == str(repetition_id)
        for error in result.errors
    )

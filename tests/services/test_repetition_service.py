"""Integration tests for RepetitionService."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
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
from calendar_backend.services.app_settings import (
    DEFAULT_MASTER_HORIZON_DURATION_MINUTES,
    AppSettingsService,
)
from calendar_backend.services.goal import GoalService
from calendar_backend.services.master_horizon import MasterHorizonService
from calendar_backend.services.master_plan import MasterPlanService
from calendar_backend.services.plan_tree_invariant import PlanTreeInvariantService
from calendar_backend.services.repetition import RepetitionService
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


def _repetition_service(session: Session) -> RepetitionService:
    return RepetitionService(session, FakeClock(RUN_AT))


def _repetition_payload(
    *,
    manual_count: int = 3,
    template_type: PlanKind = PlanKind.GOAL,
    template_payload: GoalCreatePayload | TaskCreatePayload | RepetitionCreatePayload | None = None,
) -> RepetitionCreatePayload:
    if template_payload is None:
        template_payload = GoalCreatePayload(name="template")
    return RepetitionCreatePayload(
        name="weekly",
        repeat_mode=RepeatMode.MANUAL_COUNT,
        start_time=_START,
        repeat_interval_minutes=60,
        manual_count=manual_count,
        end_time=None,
        default_instance_critical=False,
        template_type=template_type,
        template_payload=template_payload,
    )


def _create_repetition(
    session: Session, master_plan_id: PlanID, payload: RepetitionCreatePayload
) -> PlanID:
    result = _goal_service(session).create_child(
        master_plan_id,
        PlanKind.REPETITION,
        payload,
        is_critical=False,
    )
    assert result.success and result.value is not None
    return result.value.plan_id


def _assert_tree_invariant(session: Session) -> None:
    result = PlanTreeInvariantService(session).validate_master_tree()
    assert result.success, result.errors


@pytest.mark.integration
def test_generate_instances_manual_count_goal_template(
    service_db_session: Session,
    master_plan_id: PlanID,
) -> None:
    repetition_id = _create_repetition(service_db_session, master_plan_id, _repetition_payload())
    service = _repetition_service(service_db_session)

    result = service.generate_instances(repetition_id, RUN_AT)

    assert result.success and result.value is not None
    assert result.value.generated_at == RUN_AT
    repetition = service_db_session.get(RepetitionPlan, repetition_id)
    assert repetition is not None
    template = service_db_session.get(Plan, repetition.template_root_id)
    assert template is not None
    assert template.clone_status == CloneStatus.TEMPLATE

    instances = service_db_session.scalars(
        select(RepetitionInstance)
        .where(RepetitionInstance.repetition_plan_id == repetition_id)
        .order_by(RepetitionInstance.instance_index)
    ).all()
    assert len(instances) == 3
    assert [instance.instance_index for instance in instances] == [0, 1, 2]
    assert [instance.sort_order for instance in instances] == [0, 1, 2]

    for index, instance in enumerate(instances):
        root_clone = service_db_session.get(Plan, instance.root_clone_id)
        assert root_clone is not None
        assert root_clone.parent_id == repetition_id
        assert root_clone.cloned_from_id == repetition.template_root_id
        assert root_clone.clone_status == CloneStatus.LINKED
        assert instance.instance_start_time.replace(tzinfo=UTC) == _START + timedelta(
            minutes=60 * index
        )

    _assert_tree_invariant(service_db_session)


@pytest.mark.integration
def test_generate_instances_task_template_root(
    service_db_session: Session,
    master_plan_id: PlanID,
) -> None:
    repetition_id = _create_repetition(
        service_db_session,
        master_plan_id,
        _repetition_payload(
            manual_count=2,
            template_type=PlanKind.TASK,
            template_payload=TaskCreatePayload("template task", 30, False, None),
        ),
    )

    result = _repetition_service(service_db_session).generate_instances(repetition_id, RUN_AT)

    assert result.success and result.value is not None
    repetition = service_db_session.get(RepetitionPlan, repetition_id)
    assert repetition is not None
    template = service_db_session.get(Plan, repetition.template_root_id)
    assert template is not None
    assert template.plan_kind == PlanKind.TASK
    assert template.clone_status == CloneStatus.TEMPLATE

    instances = service_db_session.scalars(
        select(RepetitionInstance).where(RepetitionInstance.repetition_plan_id == repetition_id)
    ).all()
    assert len(instances) == 2
    for instance in instances:
        root_clone = service_db_session.get(Plan, instance.root_clone_id)
        assert root_clone is not None
        assert root_clone.plan_kind == PlanKind.TASK
        assert root_clone.clone_status == CloneStatus.LINKED
        assert service_db_session.get(TaskPlan, root_clone.plan_id) is not None

    _assert_tree_invariant(service_db_session)


@pytest.mark.integration
def test_generate_instances_clones_template_goal_child_chain(
    service_db_session: Session,
    master_plan_id: PlanID,
) -> None:
    repetition_id = _create_repetition(
        service_db_session, master_plan_id, _repetition_payload(manual_count=1)
    )
    repetition = service_db_session.get(RepetitionPlan, repetition_id)
    assert repetition is not None
    template_goal_id = PlanID(repetition.template_root_id)

    child_result = _goal_service(service_db_session).create_child(
        template_goal_id,
        PlanKind.TASK,
        TaskCreatePayload("template child", 45, True, 15),
        is_critical=True,
    )
    assert child_result.success and child_result.value is not None
    template_child_id = child_result.value.plan_id

    result = _repetition_service(service_db_session).generate_instances(repetition_id, RUN_AT)
    assert result.success

    instance = service_db_session.scalar(
        select(RepetitionInstance).where(RepetitionInstance.repetition_plan_id == repetition_id)
    )
    assert instance is not None
    root_clone = service_db_session.get(Plan, instance.root_clone_id)
    assert root_clone is not None

    clone_child = service_db_session.scalar(
        select(Plan).where(
            Plan.parent_id == root_clone.plan_id,
            Plan.cloned_from_id == template_child_id,
        )
    )
    assert clone_child is not None
    assert clone_child.clone_status == CloneStatus.LINKED

    clone_chain = service_db_session.scalar(
        select(GoalChildChain).where(GoalChildChain.parent_goal_id == root_clone.plan_id)
    )
    assert clone_chain is not None
    clone_item = service_db_session.scalar(
        select(GoalChildChainItem).where(
            GoalChildChainItem.chain_id == clone_chain.goal_child_chain_id
        )
    )
    assert clone_item is not None
    assert clone_item.child_plan_id == clone_child.plan_id

    _assert_tree_invariant(service_db_session)


@pytest.mark.integration
def test_generate_instances_rejects_double_generation(
    service_db_session: Session,
    master_plan_id: PlanID,
) -> None:
    repetition_id = _create_repetition(
        service_db_session, master_plan_id, _repetition_payload(manual_count=1)
    )
    service = _repetition_service(service_db_session)

    first = service.generate_instances(repetition_id, RUN_AT)
    second = service.generate_instances(repetition_id, RUN_AT)

    assert first.success
    assert not second.success
    assert any(error.code == MessageCode.REPETITION_ALREADY_GENERATED for error in second.errors)
    assert (
        service_db_session.scalar(
            select(func.count())
            .select_from(RepetitionInstance)
            .where(RepetitionInstance.repetition_plan_id == repetition_id)
        )
        == 1
    )


@pytest.mark.integration
def test_generate_instances_date_range_with_explicit_end(
    service_db_session: Session,
    master_plan_id: PlanID,
) -> None:
    end_time = _START + timedelta(hours=3)
    payload = RepetitionCreatePayload(
        name="daily",
        repeat_mode=RepeatMode.DATE_RANGE,
        start_time=_START,
        repeat_interval_minutes=60,
        manual_count=None,
        end_time=end_time,
        default_instance_critical=False,
        template_type=PlanKind.GOAL,
        template_payload=GoalCreatePayload(name="template"),
    )
    repetition_id = _create_repetition(service_db_session, master_plan_id, payload)

    result = _repetition_service(service_db_session).generate_instances(repetition_id, RUN_AT)

    assert result.success
    instances = service_db_session.scalars(
        select(RepetitionInstance)
        .where(RepetitionInstance.repetition_plan_id == repetition_id)
        .order_by(RepetitionInstance.instance_index)
    ).all()
    assert len(instances) == 3
    assert instances[-1].instance_start_time.replace(tzinfo=UTC) == _START + timedelta(hours=2)
    _assert_tree_invariant(service_db_session)


_WEEKLY_INTERVAL_MINUTES = 10_080


@pytest.mark.integration
def test_generate_instances_repetition_template_root(
    service_db_session: Session,
    master_plan_id: PlanID,
) -> None:
    inner_repetition = RepetitionCreatePayload(
        name="inner repetition",
        repeat_mode=RepeatMode.MANUAL_COUNT,
        start_time=_START,
        repeat_interval_minutes=60,
        manual_count=1,
        end_time=None,
        default_instance_critical=False,
        template_type=PlanKind.GOAL,
        template_payload=GoalCreatePayload(name="inner template"),
    )
    repetition_id = _create_repetition(
        service_db_session,
        master_plan_id,
        _repetition_payload(
            manual_count=1,
            template_type=PlanKind.REPETITION,
            template_payload=inner_repetition,
        ),
    )

    result = _repetition_service(service_db_session).generate_instances(repetition_id, RUN_AT)
    assert result.success

    outer_repetition = service_db_session.get(RepetitionPlan, repetition_id)
    assert outer_repetition is not None
    blueprint_inner_repetition_id = PlanID(outer_repetition.template_root_id)
    blueprint_inner_repetition = service_db_session.get(
        RepetitionPlan, blueprint_inner_repetition_id
    )
    assert blueprint_inner_repetition is not None
    blueprint_inner_goal_id = PlanID(blueprint_inner_repetition.template_root_id)

    blueprint_inner_repetition_plan = service_db_session.get(Plan, blueprint_inner_repetition_id)
    assert blueprint_inner_repetition_plan is not None
    assert blueprint_inner_repetition_plan.clone_status == CloneStatus.TEMPLATE

    instance = service_db_session.scalar(
        select(RepetitionInstance).where(RepetitionInstance.repetition_plan_id == repetition_id)
    )
    assert instance is not None
    root_clone = service_db_session.get(Plan, instance.root_clone_id)
    assert root_clone is not None
    assert root_clone.plan_kind == PlanKind.REPETITION
    assert root_clone.clone_status == CloneStatus.LINKED
    assert root_clone.parent_id == repetition_id
    assert root_clone.cloned_from_id == blueprint_inner_repetition_id

    clone_inner_repetition = service_db_session.get(RepetitionPlan, root_clone.plan_id)
    assert clone_inner_repetition is not None
    assert clone_inner_repetition.template_root_id != blueprint_inner_goal_id

    cloned_inner_goal = service_db_session.get(Plan, clone_inner_repetition.template_root_id)
    assert cloned_inner_goal is not None
    assert cloned_inner_goal.clone_status == CloneStatus.LINKED
    assert cloned_inner_goal.cloned_from_id == blueprint_inner_goal_id

    _assert_tree_invariant(service_db_session)


@pytest.mark.integration
def test_generate_instances_date_range_open_end_uses_master_horizon(
    service_db_session: Session,
    master_plan_id: PlanID,
) -> None:
    payload = RepetitionCreatePayload(
        name="horizon bound",
        repeat_mode=RepeatMode.DATE_RANGE,
        start_time=RUN_AT,
        repeat_interval_minutes=_WEEKLY_INTERVAL_MINUTES,
        manual_count=None,
        end_time=None,
        default_instance_critical=False,
        template_type=PlanKind.GOAL,
        template_payload=GoalCreatePayload(name="template"),
    )
    repetition_id = _create_repetition(service_db_session, master_plan_id, payload)

    result = _repetition_service(service_db_session).generate_instances(repetition_id, RUN_AT)

    assert result.success
    horizon_end = RUN_AT + timedelta(minutes=DEFAULT_MASTER_HORIZON_DURATION_MINUTES)
    expected_count = 0
    while RUN_AT + timedelta(minutes=_WEEKLY_INTERVAL_MINUTES * expected_count) < horizon_end:
        expected_count += 1
    actual_count = service_db_session.scalar(
        select(func.count())
        .select_from(RepetitionInstance)
        .where(RepetitionInstance.repetition_plan_id == repetition_id)
    )
    assert actual_count == expected_count
    _assert_tree_invariant(service_db_session)

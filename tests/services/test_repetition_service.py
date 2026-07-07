"""Integration tests for RepetitionService."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from calendar_backend.domain.enums import CloneStatus, ConstraintKind, PlanKind, RepeatMode
from calendar_backend.domain.errors import MessageCode
from calendar_backend.domain.ids import PlanID
from calendar_backend.domain.plan_create import (
    GoalCreatePayload,
    RepetitionCreatePayload,
    TaskCreatePayload,
)
from calendar_backend.models.chains import GoalChildChain, GoalChildChainItem
from calendar_backend.models.constraints import TimeConstraintGroup, TimeWindow
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
from calendar_backend.services.task import TaskService
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


def _task_service(session: Session) -> TaskService:
    return TaskService(session, FakeClock(RUN_AT))


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


def _assert_repetition_window_on_instance_root(
    session: Session,
    *,
    root_clone_id: PlanID,
    expected_start: datetime,
    repeat_interval_minutes: int,
) -> None:
    group = session.scalar(
        select(TimeConstraintGroup)
        .where(TimeConstraintGroup.plan_id == root_clone_id)
        .where(TimeConstraintGroup.constraint_kind == ConstraintKind.SYSTEM_REPETITION_WINDOW)
    )
    assert group is not None
    window = session.scalar(
        select(TimeWindow).where(TimeWindow.group_id == group.time_constraint_group_id)
    )
    assert window is not None
    assert window.start_time.replace(tzinfo=UTC) == expected_start
    assert window.end_time.replace(tzinfo=UTC) == expected_start + timedelta(
        minutes=repeat_interval_minutes
    )


def _assert_no_repetition_window(session: Session, plan_id: PlanID) -> None:
    group = session.scalar(
        select(TimeConstraintGroup)
        .where(TimeConstraintGroup.plan_id == plan_id)
        .where(TimeConstraintGroup.constraint_kind == ConstraintKind.SYSTEM_REPETITION_WINDOW)
    )
    assert group is None


def _setup_goal_repetition_with_task_child(
    session: Session,
    master_plan_id: PlanID,
    *,
    manual_count: int = 1,
) -> tuple[PlanID, PlanID, PlanID]:
    repetition_id = _create_repetition(
        session, master_plan_id, _repetition_payload(manual_count=manual_count)
    )
    repetition = session.get(RepetitionPlan, repetition_id)
    assert repetition is not None
    template_goal_id = PlanID(repetition.template_root_id)
    child_result = _goal_service(session).create_child(
        template_goal_id,
        PlanKind.TASK,
        TaskCreatePayload("template task", 30, False, None),
        is_critical=False,
    )
    assert child_result.success and child_result.value is not None
    return repetition_id, template_goal_id, child_result.value.plan_id


def _instance_root_clone_id(
    session: Session,
    repetition_id: PlanID,
    instance_index: int,
) -> PlanID:
    instance = session.scalar(
        select(RepetitionInstance)
        .where(RepetitionInstance.repetition_plan_id == repetition_id)
        .where(RepetitionInstance.instance_index == instance_index)
    )
    assert instance is not None
    return PlanID(instance.root_clone_id)


def _clone_for_template(
    session: Session,
    *,
    parent_clone_id: PlanID,
    template_plan_id: PlanID,
) -> PlanID:
    clone = session.scalar(
        select(Plan).where(
            Plan.parent_id == parent_clone_id,
            Plan.cloned_from_id == template_plan_id,
        )
    )
    assert clone is not None
    return PlanID(clone.plan_id)


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
        _assert_repetition_window_on_instance_root(
            service_db_session,
            root_clone_id=PlanID(root_clone.plan_id),
            expected_start=instance.instance_start_time.replace(tzinfo=UTC),
            repeat_interval_minutes=repetition.repeat_interval_minutes,
        )

    _assert_no_repetition_window(service_db_session, PlanID(repetition.template_root_id))
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
        _assert_repetition_window_on_instance_root(
            service_db_session,
            root_clone_id=PlanID(root_clone.plan_id),
            expected_start=instance.instance_start_time.replace(tzinfo=UTC),
            repeat_interval_minutes=repetition.repeat_interval_minutes,
        )

    _assert_no_repetition_window(service_db_session, PlanID(repetition.template_root_id))
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

    _assert_repetition_window_on_instance_root(
        service_db_session,
        root_clone_id=PlanID(root_clone.plan_id),
        expected_start=instance.instance_start_time.replace(tzinfo=UTC),
        repeat_interval_minutes=repetition.repeat_interval_minutes,
    )
    _assert_no_repetition_window(service_db_session, PlanID(repetition.template_root_id))
    _assert_no_repetition_window(service_db_session, PlanID(clone_child.plan_id))
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
    repetition_plan = service_db_session.get(RepetitionPlan, repetition_id)
    assert repetition_plan is not None
    instances = service_db_session.scalars(
        select(RepetitionInstance)
        .where(RepetitionInstance.repetition_plan_id == repetition_id)
        .order_by(RepetitionInstance.instance_index)
    ).all()
    assert len(instances) == 3
    assert instances[-1].instance_start_time.replace(tzinfo=UTC) == _START + timedelta(hours=2)
    for instance in instances:
        _assert_repetition_window_on_instance_root(
            service_db_session,
            root_clone_id=PlanID(instance.root_clone_id),
            expected_start=instance.instance_start_time.replace(tzinfo=UTC),
            repeat_interval_minutes=repetition_plan.repeat_interval_minutes,
        )
    _assert_no_repetition_window(service_db_session, PlanID(repetition_plan.template_root_id))
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

    _assert_repetition_window_on_instance_root(
        service_db_session,
        root_clone_id=PlanID(root_clone.plan_id),
        expected_start=instance.instance_start_time.replace(tzinfo=UTC),
        repeat_interval_minutes=outer_repetition.repeat_interval_minutes,
    )
    _assert_no_repetition_window(service_db_session, blueprint_inner_repetition_id)
    _assert_no_repetition_window(service_db_session, PlanID(cloned_inner_goal.plan_id))
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
    repetition_plan = service_db_session.get(RepetitionPlan, repetition_id)
    assert repetition_plan is not None
    instances = service_db_session.scalars(
        select(RepetitionInstance).where(RepetitionInstance.repetition_plan_id == repetition_id)
    ).all()
    for instance in instances:
        _assert_repetition_window_on_instance_root(
            service_db_session,
            root_clone_id=PlanID(instance.root_clone_id),
            expected_start=instance.instance_start_time.replace(tzinfo=UTC),
            repeat_interval_minutes=repetition_plan.repeat_interval_minutes,
        )
    _assert_no_repetition_window(service_db_session, PlanID(repetition_plan.template_root_id))
    _assert_tree_invariant(service_db_session)


@pytest.mark.integration
def test_refresh_rejects_before_generation(
    service_db_session: Session,
    master_plan_id: PlanID,
) -> None:
    repetition_id, _, _ = _setup_goal_repetition_with_task_child(service_db_session, master_plan_id)

    result = _repetition_service(service_db_session).refresh_repetition(repetition_id, RUN_AT)

    assert not result.success
    assert any(error.code == MessageCode.REPETITION_NOT_GENERATED for error in result.errors)


@pytest.mark.integration
def test_refresh_adds_instances_when_manual_count_increases(
    service_db_session: Session,
    master_plan_id: PlanID,
) -> None:
    repetition_id, _, _ = _setup_goal_repetition_with_task_child(
        service_db_session, master_plan_id, manual_count=1
    )
    service = _repetition_service(service_db_session)

    assert service.generate_instances(repetition_id, RUN_AT).success
    assert service.update_settings(repetition_id, manual_count=3).success
    assert service.refresh_repetition(repetition_id, RUN_AT).success

    repetition = service_db_session.get(RepetitionPlan, repetition_id)
    assert repetition is not None
    instances = service_db_session.scalars(
        select(RepetitionInstance)
        .where(RepetitionInstance.repetition_plan_id == repetition_id)
        .order_by(RepetitionInstance.instance_index)
    ).all()
    assert len(instances) == 3
    assert [instance.instance_index for instance in instances] == [0, 1, 2]
    for instance in instances:
        _assert_repetition_window_on_instance_root(
            service_db_session,
            root_clone_id=PlanID(instance.root_clone_id),
            expected_start=instance.instance_start_time.replace(tzinfo=UTC),
            repeat_interval_minutes=repetition.repeat_interval_minutes,
        )
    _assert_tree_invariant(service_db_session)


@pytest.mark.integration
def test_refresh_all_repetitions_smoke(
    service_db_session: Session,
    master_plan_id: PlanID,
) -> None:
    repetition_ids = [
        _setup_goal_repetition_with_task_child(service_db_session, master_plan_id)[0]
        for _ in range(2)
    ]
    service = _repetition_service(service_db_session)
    for repetition_id in repetition_ids:
        assert service.generate_instances(repetition_id, RUN_AT).success

    result = service.refresh_all_repetitions(RUN_AT)

    assert result.success
    _assert_tree_invariant(service_db_session)


@pytest.mark.integration
def test_refresh_skips_detached_subtree_after_task_detach(
    service_db_session: Session,
    master_plan_id: PlanID,
) -> None:
    repetition_id, _, template_task_id = _setup_goal_repetition_with_task_child(
        service_db_session, master_plan_id
    )
    service = _repetition_service(service_db_session)
    assert service.generate_instances(repetition_id, RUN_AT).success

    root_clone_id = _instance_root_clone_id(service_db_session, repetition_id, 0)
    task_clone_id = _clone_for_template(
        service_db_session,
        parent_clone_id=root_clone_id,
        template_plan_id=template_task_id,
    )
    assert (
        _task_service(service_db_session)
        .update_scheduling_fields(task_clone_id, 45, False, None)
        .success
    )
    detached_plan = service_db_session.get(Plan, task_clone_id)
    assert detached_plan is not None
    assert detached_plan.clone_status == CloneStatus.DETACHED

    assert (
        _task_service(service_db_session)
        .update_scheduling_fields(template_task_id, 50, False, None)
        .success
    )
    assert service.refresh_repetition(repetition_id, RUN_AT).success

    detached_task = service_db_session.get(TaskPlan, task_clone_id)
    assert detached_task is not None
    assert detached_task.duration_minutes == 45
    _assert_tree_invariant(service_db_session)


@pytest.mark.integration
def test_refresh_propagates_to_sibling_instance_when_other_detached(
    service_db_session: Session,
    master_plan_id: PlanID,
) -> None:
    repetition_id, _, template_task_id = _setup_goal_repetition_with_task_child(
        service_db_session, master_plan_id, manual_count=2
    )
    service = _repetition_service(service_db_session)
    assert service.generate_instances(repetition_id, RUN_AT).success

    root_clone_0 = _instance_root_clone_id(service_db_session, repetition_id, 0)
    root_clone_1 = _instance_root_clone_id(service_db_session, repetition_id, 1)
    task_clone_0 = _clone_for_template(
        service_db_session,
        parent_clone_id=root_clone_0,
        template_plan_id=template_task_id,
    )
    task_clone_1 = _clone_for_template(
        service_db_session,
        parent_clone_id=root_clone_1,
        template_plan_id=template_task_id,
    )

    assert (
        _task_service(service_db_session)
        .update_scheduling_fields(task_clone_0, 45, False, None)
        .success
    )
    assert (
        _task_service(service_db_session)
        .update_scheduling_fields(template_task_id, 60, False, None)
        .success
    )
    assert service.refresh_repetition(repetition_id, RUN_AT).success

    detached_task = service_db_session.get(TaskPlan, task_clone_0)
    linked_task = service_db_session.get(TaskPlan, task_clone_1)
    assert detached_task is not None
    assert linked_task is not None
    assert detached_task.duration_minutes == 45
    assert linked_task.duration_minutes == 60
    _assert_tree_invariant(service_db_session)


@pytest.mark.integration
def test_refresh_materializes_new_template_goal_child_on_linked_instances(
    service_db_session: Session,
    master_plan_id: PlanID,
) -> None:
    repetition_id, template_goal_id, _ = _setup_goal_repetition_with_task_child(
        service_db_session, master_plan_id, manual_count=2
    )
    service = _repetition_service(service_db_session)
    assert service.generate_instances(repetition_id, RUN_AT).success

    new_child_result = _goal_service(service_db_session).create_child(
        template_goal_id,
        PlanKind.TASK,
        TaskCreatePayload("new template child", 20, False, None),
        is_critical=False,
    )
    assert new_child_result.success and new_child_result.value is not None
    new_template_child_id = new_child_result.value.plan_id

    assert service.refresh_repetition(repetition_id, RUN_AT).success

    for instance_index in (0, 1):
        root_clone_id = _instance_root_clone_id(service_db_session, repetition_id, instance_index)
        clone_child = service_db_session.scalar(
            select(Plan).where(
                Plan.parent_id == root_clone_id,
                Plan.cloned_from_id == new_template_child_id,
            )
        )
        assert clone_child is not None
        assert clone_child.clone_status == CloneStatus.LINKED
        assert service_db_session.get(TaskPlan, clone_child.plan_id) is not None

        clone_item = service_db_session.scalar(
            select(GoalChildChainItem).where(
                GoalChildChainItem.child_plan_id == clone_child.plan_id,
            )
        )
        assert clone_item is not None
        clone_chain = service_db_session.get(GoalChildChain, clone_item.chain_id)
        assert clone_chain is not None
        assert clone_chain.parent_goal_id == root_clone_id

    _assert_tree_invariant(service_db_session)

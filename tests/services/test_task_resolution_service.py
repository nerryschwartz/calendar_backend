"""Integration tests for TaskResolutionService."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from calendar_backend.db.session import transaction
from calendar_backend.domain.enums import CloneStatus, ConstraintKind, PlanKind, RepeatMode
from calendar_backend.domain.errors import MessageCode
from calendar_backend.domain.ids import PlanID
from calendar_backend.domain.plan_create import (
    GoalCreatePayload,
    RepetitionCreatePayload,
    TaskCreatePayload,
)
from calendar_backend.domain.resolution import (
    ResolvedTask,
    ResolveTasksResult,
    is_invalid_incomplete_task,
)
from calendar_backend.domain.time import TimeWindow
from calendar_backend.models.constraints import TimeConstraintGroup
from calendar_backend.models.constraints import TimeWindow as OrmTimeWindow
from calendar_backend.models.plans import Plan, RepetitionPlan
from calendar_backend.models.repetitions import RepetitionInstance
from calendar_backend.services.app_settings import AppSettingsService
from calendar_backend.services.goal import GoalService
from calendar_backend.services.master_plan import MasterPlanService
from calendar_backend.services.repetition import RepetitionService
from calendar_backend.services.task import TaskService
from calendar_backend.services.task_resolution import (
    TaskResolutionService,
    _resolve_from_current_tree,  # pyright: ignore[reportPrivateUsage]
    load_plan_graph,
)
from calendar_backend.services.time_constraint import TimeConstraintService
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .conftest import FakeClock

RUN_AT = datetime(2026, 6, 7, 10, 0, tzinfo=UTC)
_START = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)


def _bootstrap_master(session: Session) -> PlanID:
    clock = FakeClock(RUN_AT)
    master = MasterPlanService(session, clock).ensure_master_exists()
    assert master.success and master.value is not None
    AppSettingsService(session, clock).get_settings()
    return master.value.plan_id


def _goal_service(session: Session) -> GoalService:
    return GoalService(session, FakeClock(RUN_AT))


def _task_service(session: Session) -> TaskService:
    return TaskService(session, FakeClock(RUN_AT))


def _repetition_service(session: Session) -> RepetitionService:
    return RepetitionService(session, FakeClock(RUN_AT))


def _resolution_service(session: Session) -> TaskResolutionService:
    return TaskResolutionService(session, FakeClock(RUN_AT))


def _create_task(session: Session, parent_id: PlanID, *, name: str = "task") -> PlanID:
    result = _goal_service(session).create_child(
        parent_id,
        PlanKind.TASK,
        TaskCreatePayload(name, 30, False, None),
        is_critical=False,
    )
    assert result.success and result.value is not None
    return result.value.plan_id


def _create_repetition(
    session: Session,
    master_plan_id: PlanID,
    *,
    manual_count: int = 2,
    start_time: datetime = _START,
) -> PlanID:
    result = _goal_service(session).create_child(
        master_plan_id,
        PlanKind.REPETITION,
        RepetitionCreatePayload(
            name="weekly",
            repeat_mode=RepeatMode.MANUAL_COUNT,
            start_time=start_time,
            repeat_interval_minutes=60,
            manual_count=manual_count,
            end_time=None,
            default_instance_critical=False,
            template_type=PlanKind.TASK,
            template_payload=TaskCreatePayload("template task", 30, False, None),
        ),
        is_critical=False,
    )
    assert result.success and result.value is not None
    return result.value.plan_id


def _create_goal_template_repetition_with_task_child(
    session: Session,
    master_plan_id: PlanID,
    *,
    manual_count: int = 1,
) -> tuple[PlanID, PlanID, PlanID]:
    repetition_result = _goal_service(session).create_child(
        master_plan_id,
        PlanKind.REPETITION,
        RepetitionCreatePayload(
            name="weekly",
            repeat_mode=RepeatMode.MANUAL_COUNT,
            start_time=RUN_AT,
            repeat_interval_minutes=60,
            manual_count=manual_count,
            end_time=None,
            default_instance_critical=False,
            template_type=PlanKind.GOAL,
            template_payload=GoalCreatePayload(name="template goal"),
        ),
        is_critical=False,
    )
    assert repetition_result.success and repetition_result.value is not None
    repetition = session.get(RepetitionPlan, repetition_result.value.plan_id)
    assert repetition is not None
    template_goal_id = PlanID(repetition.template_root_id)
    child_result = _goal_service(session).create_child(
        template_goal_id,
        PlanKind.TASK,
        TaskCreatePayload("template child", 30, False, None),
        is_critical=False,
    )
    assert child_result.success and child_result.value is not None
    return (
        repetition_result.value.plan_id,
        template_goal_id,
        child_result.value.plan_id,
    )


def _three_tasks_in_master_chain(
    session: Session, master_plan_id: PlanID
) -> tuple[PlanID, PlanID, PlanID]:
    first_id = _create_task(session, master_plan_id, name="first")
    second_id = _create_task(session, master_plan_id, name="second")
    third_id = _create_task(session, master_plan_id, name="third")
    goal_service = _goal_service(session)
    assert goal_service.move_plan(second_id, 0, 1).success
    assert goal_service.move_plan(third_id, 0, 2).success
    return first_id, second_id, third_id


def _instance_root_clone_goal_id(
    session: Session,
    repetition_id: PlanID,
    *,
    instance_index: int = 0,
) -> PlanID:
    instance = session.scalar(
        select(RepetitionInstance)
        .where(RepetitionInstance.repetition_plan_id == repetition_id)
        .where(RepetitionInstance.instance_index == instance_index)
    )
    assert instance is not None
    return PlanID(instance.root_clone_id)


def _attach_user_group(session: Session, plan_id: PlanID) -> None:
    clock = FakeClock(RUN_AT)
    assert (
        TimeConstraintService(session, clock)
        .add_user_group(
            plan_id,
            (TimeWindow(start_time=RUN_AT, end_time=RUN_AT + timedelta(hours=1)),),
        )
        .success
    )


def _corrupt_user_window_on_plan(plans: tuple[Plan, ...], plan_id: PlanID) -> None:
    for plan in plans:
        if PlanID(plan.plan_id) != plan_id:
            continue
        for group in plan.constraint_groups:
            if group.constraint_kind != ConstraintKind.USER:
                continue
            for window in group.windows:
                window.end_time = window.start_time - timedelta(hours=1)


def _generate_instances(session: Session, repetition_id: PlanID) -> None:
    assert _repetition_service(session).generate_instances(repetition_id, RUN_AT).success


def _normalize_plan_window_timezones(plans: tuple[Plan, ...]) -> tuple[Plan, ...]:
    for plan in plans:
        for group in plan.constraint_groups:
            for window in group.windows:
                if window.start_time.tzinfo is None:
                    window.start_time = window.start_time.replace(tzinfo=UTC)
                if window.end_time.tzinfo is None:
                    window.end_time = window.end_time.replace(tzinfo=UTC)
    return plans


def _load_plans_with_utc_windows(session: Session) -> tuple[Plan, ...]:
    return _normalize_plan_window_timezones(load_plan_graph(session))


def _resolve_seam(session: Session, run_at: datetime = RUN_AT) -> ResolveTasksResult:
    return _resolve_from_current_tree(run_at, plans=_load_plans_with_utc_windows(session))


def _all_tasks(result: ResolveTasksResult) -> tuple[ResolvedTask, ...]:
    return (
        *result.valid_incomplete,
        *result.valid_completed,
        *result.invalid_incomplete,
        *result.invalid_completed,
    )


def _horizon_window_count(session: Session) -> int:
    return (
        session.scalar(
            select(func.count())
            .select_from(OrmTimeWindow)
            .join(TimeConstraintGroup)
            .where(TimeConstraintGroup.constraint_kind == ConstraintKind.SYSTEM_MASTER_HORIZON)
        )
        or 0
    )


@pytest.mark.integration
def test_resolve_tasks_returns_master_and_nested_goal_tasks(service_db_session: Session) -> None:
    master_id = _bootstrap_master(service_db_session)
    goal_result = _goal_service(service_db_session).create_child(
        master_id,
        PlanKind.GOAL,
        GoalCreatePayload("nested goal"),
        is_critical=False,
    )
    assert goal_result.success and goal_result.value is not None
    task_id = _create_task(service_db_session, goal_result.value.plan_id, name="nested task")

    result = _resolve_seam(service_db_session)

    assert task_id in {task.plan_id for task in _all_tasks(result)}
    assert any(task.plan_id == task_id for task in result.valid_incomplete)


@pytest.mark.integration
def test_resolve_tasks_repetition_two_instances_critical_first_priority_paths(
    service_db_session: Session,
) -> None:
    master_id = _bootstrap_master(service_db_session)
    repetition_id = _create_repetition(service_db_session, master_id, manual_count=2)
    _generate_instances(service_db_session, repetition_id)

    with transaction(service_db_session) as txn:
        instances = list(
            txn.scalars(
                select(RepetitionInstance)
                .where(RepetitionInstance.repetition_plan_id == repetition_id)
                .order_by(RepetitionInstance.instance_index)
            ).all()
        )
        assert len(instances) == 2
        instances[0].is_critical = False
        instances[1].is_critical = True

    result = _resolve_seam(service_db_session)
    tasks = _all_tasks(result)
    assert len(tasks) == 2
    ordered = sorted(tasks, key=lambda task: task.priority_path)
    assert ordered[0].priority_path < ordered[1].priority_path


@pytest.mark.integration
def test_resolve_tasks_excludes_template_tasks_after_generation(
    service_db_session: Session,
) -> None:
    master_id = _bootstrap_master(service_db_session)
    repetition_id = _create_repetition(service_db_session, master_id, manual_count=1)
    repetition = service_db_session.get(RepetitionPlan, repetition_id)
    assert repetition is not None
    template_task_id = PlanID(repetition.template_root_id)
    _generate_instances(service_db_session, repetition_id)

    result = _resolve_seam(service_db_session)

    resolved_ids = {task.plan_id for task in _all_tasks(result)}
    assert template_task_id not in resolved_ids
    assert len(resolved_ids) == 1


@pytest.mark.integration
def test_resolve_tasks_includes_detached_clone_subtree_tasks(
    service_db_session: Session,
) -> None:
    master_id = _bootstrap_master(service_db_session)
    repetition_id = _create_repetition(service_db_session, master_id, manual_count=1)
    _generate_instances(service_db_session, repetition_id)

    instance = service_db_session.scalar(
        select(RepetitionInstance).where(RepetitionInstance.repetition_plan_id == repetition_id)
    )
    assert instance is not None
    clone_task_id = PlanID(instance.root_clone_id)

    assert (
        _task_service(service_db_session)
        .update_scheduling_fields(clone_task_id, 45, False, None)
        .success
    )
    detached = service_db_session.get(Plan, clone_task_id)
    assert detached is not None
    assert detached.clone_status == CloneStatus.DETACHED

    result = _resolve_seam(service_db_session)

    assert clone_task_id in {task.plan_id for task in _all_tasks(result)}


@pytest.mark.integration
def test_resolve_tasks_invalid_duration_lands_in_invalid_incomplete(
    service_db_session: Session,
) -> None:
    master_id = _bootstrap_master(service_db_session)
    task_id = _create_task(service_db_session, master_id)

    plans = list(_load_plans_with_utc_windows(service_db_session))
    for plan in plans:
        if plan.task_plan is not None and PlanID(plan.plan_id) == task_id:
            plan.task_plan.duration_minutes = 0

    result = _resolve_from_current_tree(RUN_AT, plans=tuple(plans))

    assert len(result.invalid_incomplete) == 1
    assert result.invalid_incomplete[0].plan_id == task_id
    assert is_invalid_incomplete_task(result.invalid_incomplete[0])
    assert any(
        error.code == MessageCode.INVALID_DURATION
        for error in result.invalid_incomplete[0].validation_errors
    )


@pytest.mark.integration
def test_resolve_tasks_populates_effective_windows_and_constraint_sources(
    service_db_session: Session,
) -> None:
    master_id = _bootstrap_master(service_db_session)
    _create_task(service_db_session, master_id)
    clock = FakeClock(RUN_AT)
    TimeConstraintService(service_db_session, clock).add_user_group(
        master_id,
        (
            TimeWindow(
                start_time=RUN_AT,
                end_time=RUN_AT + timedelta(hours=2),
            ),
        ),
    )
    _resolution_service(service_db_session).resolve_tasks(RUN_AT)

    result = _resolve_seam(service_db_session)

    task = result.valid_incomplete[0]
    assert task.effective_time_windows
    assert any(
        source.constraint_kind == ConstraintKind.SYSTEM_MASTER_HORIZON
        for source in task.constraint_sources
    )
    assert any(source.constraint_kind == ConstraintKind.USER for source in task.constraint_sources)


@pytest.mark.integration
def test_resolve_tasks_emits_precedence_for_chain_order(service_db_session: Session) -> None:
    master_id = _bootstrap_master(service_db_session)
    first_id = _create_task(service_db_session, master_id, name="first")
    second_id = _create_task(service_db_session, master_id, name="second")
    goal_service = _goal_service(service_db_session)
    assert goal_service.move_plan(second_id, 0, 0).success
    assert goal_service.move_plan(second_id, 1).success

    result = _resolve_seam(service_db_session)

    assert any(
        edge.predecessor_task_id == first_id and edge.successor_task_id == second_id
        for edge in result.precedence_constraints
    )


@pytest.mark.integration
def test_resolve_tasks_refresh_updates_horizon_and_refreshes_repetitions(
    service_db_session: Session,
) -> None:
    master_id = _bootstrap_master(service_db_session)
    repetition_id = _create_repetition(service_db_session, master_id, manual_count=1)
    _generate_instances(service_db_session, repetition_id)

    assert _horizon_window_count(service_db_session) == 0

    result = _resolution_service(service_db_session).resolve_tasks(RUN_AT)

    assert result.success and result.value is not None
    assert _horizon_window_count(service_db_session) == 1
    instances = service_db_session.scalars(
        select(RepetitionInstance).where(RepetitionInstance.repetition_plan_id == repetition_id)
    ).all()
    assert len(instances) == 1


@pytest.mark.integration
def test_resolve_tasks_run_started_at_echoes_input(service_db_session: Session) -> None:
    master_id = _bootstrap_master(service_db_session)
    _create_task(service_db_session, master_id)

    result = _resolution_service(service_db_session).resolve_tasks(RUN_AT)

    assert result.success and result.value is not None
    assert result.value.run_started_at == RUN_AT


@pytest.mark.integration
def test_resolve_tasks_warnings_empty_in_v1(service_db_session: Session) -> None:
    master_id = _bootstrap_master(service_db_session)
    _create_task(service_db_session, master_id)

    result = _resolution_service(service_db_session).resolve_tasks(RUN_AT)

    assert result.success and result.value is not None
    assert result.value.warnings == ()


@pytest.mark.integration
def test_resolve_tasks_skips_completed_predecessor_in_precedence(
    service_db_session: Session,
) -> None:
    master_id = _bootstrap_master(service_db_session)
    first_id, second_id, third_id = _three_tasks_in_master_chain(service_db_session, master_id)
    assert _task_service(service_db_session).mark_complete(second_id).success

    result = _resolve_seam(service_db_session)

    assert (
        first_id,
        third_id,
    ) in {
        (edge.predecessor_task_id, edge.successor_task_id) for edge in result.precedence_constraints
    }
    assert not any(edge.predecessor_task_id == second_id for edge in result.precedence_constraints)


@pytest.mark.integration
def test_resolve_tasks_malformed_constraint_on_clone_ancestor_invalid_incomplete(
    service_db_session: Session,
) -> None:
    master_id = _bootstrap_master(service_db_session)
    repetition_id, _, template_task_id = _create_goal_template_repetition_with_task_child(
        service_db_session, master_id
    )
    _generate_instances(service_db_session, repetition_id)

    clone_goal_id = _instance_root_clone_goal_id(service_db_session, repetition_id)
    _attach_user_group(service_db_session, clone_goal_id)

    clone_task = service_db_session.scalar(
        select(Plan).where(
            Plan.parent_id == clone_goal_id,
            Plan.cloned_from_id == template_task_id,
        )
    )
    assert clone_task is not None
    clone_task_id = PlanID(clone_task.plan_id)

    plans = _load_plans_with_utc_windows(service_db_session)
    _corrupt_user_window_on_plan(plans, clone_goal_id)
    result = _resolve_from_current_tree(RUN_AT, plans=plans)

    assert len(result.invalid_incomplete) == 1
    assert result.invalid_incomplete[0].plan_id == clone_task_id
    assert is_invalid_incomplete_task(result.invalid_incomplete[0])
    assert any(
        error.code == MessageCode.INVALID_TIME_WINDOW
        for error in result.invalid_incomplete[0].validation_errors
    )


@pytest.mark.integration
def test_resolve_tasks_intersects_repetition_horizon_and_user_windows(
    service_db_session: Session,
) -> None:
    master_id = _bootstrap_master(service_db_session)
    repetition_id = _create_repetition(
        service_db_session, master_id, manual_count=1, start_time=RUN_AT
    )
    _generate_instances(service_db_session, repetition_id)

    user_start = RUN_AT + timedelta(minutes=15)
    user_end = RUN_AT + timedelta(minutes=45)
    clock = FakeClock(RUN_AT)
    assert (
        TimeConstraintService(service_db_session, clock)
        .add_user_group(master_id, (TimeWindow(start_time=user_start, end_time=user_end),))
        .success
    )

    result = _resolution_service(service_db_session).resolve_tasks(RUN_AT)
    assert result.success and result.value is not None

    task = result.value.valid_incomplete[0]
    assert task.effective_time_windows == (TimeWindow(start_time=user_start, end_time=user_end),)
    assert {source.constraint_kind for source in task.constraint_sources} == {
        ConstraintKind.SYSTEM_MASTER_HORIZON,
        ConstraintKind.USER,
        ConstraintKind.SYSTEM_REPETITION_WINDOW,
    }


@pytest.mark.integration
def test_resolve_tasks_orders_instances_by_sort_order_within_critical_bucket(
    service_db_session: Session,
) -> None:
    master_id = _bootstrap_master(service_db_session)
    repetition_id = _create_repetition(service_db_session, master_id, manual_count=2)
    _generate_instances(service_db_session, repetition_id)

    with transaction(service_db_session) as txn:
        instances = list(
            txn.scalars(
                select(RepetitionInstance)
                .where(RepetitionInstance.repetition_plan_id == repetition_id)
                .order_by(RepetitionInstance.instance_index)
            ).all()
        )
        assert len(instances) == 2
        for instance in instances:
            instance.is_critical = True
        instances[0].sort_order = 1
        instances[1].sort_order = 0
        lower_sort_order_task_id = PlanID(instances[1].root_clone_id)
        higher_sort_order_task_id = PlanID(instances[0].root_clone_id)

    result = _resolve_seam(service_db_session)
    tasks = _all_tasks(result)
    assert len(tasks) == 2

    task_by_root = {task.plan_id: task for task in tasks}
    assert (
        task_by_root[lower_sort_order_task_id].priority_path
        < task_by_root[higher_sort_order_task_id].priority_path
    )

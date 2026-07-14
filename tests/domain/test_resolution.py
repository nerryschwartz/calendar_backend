"""Pure tests for task resolution helpers."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from calendar_backend.domain.constraints import intersect_time_windows
from calendar_backend.domain.enums import CloneStatus, ConstraintKind, PlanKind, RepeatMode
from calendar_backend.domain.errors import MessageCode, ServiceMessage
from calendar_backend.domain.ids import GoalChildChainID, PlanID
from calendar_backend.domain.resolution import (
    ResolvedTask,
    ResolveTasksResult,
    build_resolution_indexes,
    collect_precedence_constraints,
    compute_effective_constraints,
    is_invalid_incomplete_task,
    is_invalid_task,
    resolve_tasks_from_graph,
    validate_resolve_tasks_result,
)
from calendar_backend.domain.time import TimeWindow
from calendar_backend.models.chains import GoalChildChain, GoalChildChainItem
from calendar_backend.models.constraints import TimeConstraintGroup
from calendar_backend.models.constraints import TimeWindow as OrmTimeWindow
from calendar_backend.models.plans import GoalPlan, Plan, RepetitionPlan, TaskPlan
from calendar_backend.models.repetitions import RepetitionInstance

_NOW = datetime(2026, 6, 7, 12, 0, tzinfo=UTC)
_RUN_AT = datetime(2026, 6, 7, 10, 0, tzinfo=UTC)


def _utc(h: int, mi: int = 0) -> datetime:
    return datetime(2026, 6, 7, h, mi, tzinfo=UTC)


def _window(start: datetime, end: datetime) -> TimeWindow:
    return TimeWindow(start_time=start, end_time=end)


def _plan(
    plan_id: uuid.UUID,
    *,
    plan_kind: PlanKind,
    is_master: bool = False,
    parent_id: uuid.UUID | None = None,
    name: str = "plan",
    cloned_from_id: uuid.UUID | None = None,
    clone_status: CloneStatus = CloneStatus.NOT_CLONED,
) -> Plan:
    return Plan(
        plan_id=plan_id,
        plan_kind=plan_kind,
        name=name,
        parent_id=parent_id,
        is_master=is_master,
        cloned_from_id=cloned_from_id,
        clone_status=clone_status,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _attach_goal(plan: Plan) -> None:
    plan.goal_plan = GoalPlan(plan_id=plan.plan_id)


def _attach_task(
    plan: Plan,
    *,
    duration_minutes: int = 30,
    user_completed: bool = False,
) -> None:
    plan.task_plan = TaskPlan(
        plan_id=plan.plan_id,
        duration_minutes=duration_minutes,
        divisible=False,
        minimum_chunk_size_minutes=None,
        user_completed=user_completed,
        completed_at=_NOW if user_completed else None,
    )


def _attach_repetition(
    plan: Plan, template_root_id: uuid.UUID, *, generated_at: datetime
) -> RepetitionPlan:
    repetition = RepetitionPlan(
        plan_id=plan.plan_id,
        repeat_mode=RepeatMode.MANUAL_COUNT,
        start_time=_utc(10, 0),
        repeat_interval_minutes=60,
        manual_count=1,
        end_time=None,
        template_root_id=template_root_id,
        default_instance_critical=False,
        generated_at=generated_at,
    )
    repetition.instances = []
    plan.repetition_plan = repetition
    return repetition


def _horizon_group(plan_id: uuid.UUID, start: datetime, end: datetime) -> TimeConstraintGroup:
    group_id = uuid.uuid4()
    group = TimeConstraintGroup(
        time_constraint_group_id=group_id,
        plan_id=plan_id,
        constraint_kind=ConstraintKind.SYSTEM_MASTER_HORIZON,
    )
    group.windows = [
        OrmTimeWindow(
            time_window_id=uuid.uuid4(),
            group_id=group_id,
            start_time=start,
            end_time=end,
        )
    ]
    return group


def _user_group(plan_id: uuid.UUID, start: datetime, end: datetime) -> TimeConstraintGroup:
    group_id = uuid.uuid4()
    group = TimeConstraintGroup(
        time_constraint_group_id=group_id,
        plan_id=plan_id,
        constraint_kind=ConstraintKind.USER,
    )
    group.windows = [
        OrmTimeWindow(
            time_window_id=uuid.uuid4(),
            group_id=group_id,
            start_time=start,
            end_time=end,
        )
    ]
    return group


def _malformed_user_group(plan_id: uuid.UUID) -> TimeConstraintGroup:
    group_id = uuid.uuid4()
    group = TimeConstraintGroup(
        time_constraint_group_id=group_id,
        plan_id=plan_id,
        constraint_kind=ConstraintKind.USER,
    )
    group.windows = [
        OrmTimeWindow(
            time_window_id=uuid.uuid4(),
            group_id=group_id,
            start_time=_utc(14, 0),
            end_time=_utc(12, 0),
        )
    ]
    return group


def _attach_chain_item(
    goal: Plan,
    *,
    child_plan_id: uuid.UUID,
    position: int,
    chain_id: uuid.UUID | None = None,
) -> GoalChildChainID:
    resolved_chain_id = chain_id or uuid.uuid4()
    chain = GoalChildChain(
        goal_child_chain_id=resolved_chain_id,
        parent_goal_id=goal.plan_id,
        is_critical=False,
        sort_order=0,
        created_at=_NOW,
        updated_at=_NOW,
    )
    chain.items = [
        GoalChildChainItem(
            goal_child_chain_item_id=uuid.uuid4(),
            chain_id=resolved_chain_id,
            child_plan_id=child_plan_id,
            position=position,
        )
    ]
    assert goal.goal_plan is not None
    goal.goal_plan.chains = [*goal.goal_plan.chains, chain]
    return GoalChildChainID(resolved_chain_id)


def _attach_chain_items(
    goal: Plan,
    child_plan_ids: tuple[uuid.UUID, ...],
) -> GoalChildChainID:
    chain_id = uuid.uuid4()
    chain = GoalChildChain(
        goal_child_chain_id=chain_id,
        parent_goal_id=goal.plan_id,
        is_critical=False,
        sort_order=0,
        created_at=_NOW,
        updated_at=_NOW,
    )
    chain.items = [
        GoalChildChainItem(
            goal_child_chain_item_id=uuid.uuid4(),
            chain_id=chain_id,
            child_plan_id=child_plan_id,
            position=position,
        )
        for position, child_plan_id in enumerate(child_plan_ids)
    ]
    assert goal.goal_plan is not None
    goal.goal_plan.chains = [chain]
    return GoalChildChainID(chain_id)


def _resolved_task(
    plan_id: uuid.UUID,
    *,
    user_completed: bool = False,
    validation_errors: tuple[ServiceMessage, ...] = (),
) -> ResolvedTask:
    return ResolvedTask(
        plan_id=PlanID(plan_id),
        name="task",
        duration_minutes=30,
        divisible=False,
        minimum_chunk_size_minutes=None,
        user_completed=user_completed,
        completed_at=_NOW if user_completed else None,
        effective_time_windows=(),
        constraint_sources=(),
        priority_path=(0,),
        criticality_path=(),
        parent_path=(PlanID(plan_id),),
        chain_path=(),
        validation_errors=validation_errors,
    )


def _all_tasks(result: ResolveTasksResult) -> tuple[ResolvedTask, ...]:
    return (
        *result.valid_incomplete,
        *result.valid_completed,
        *result.invalid_incomplete,
        *result.invalid_completed,
    )


def _constraint_intersection_graph() -> tuple[Plan, ...]:
    master_id = uuid.uuid4()
    task_id = uuid.uuid4()

    master = _plan(master_id, plan_kind=PlanKind.GOAL, is_master=True)
    _attach_goal(master)
    master.constraint_groups = [
        _horizon_group(master_id, _utc(10, 0), _utc(12, 0)),
        _user_group(master_id, _utc(11, 0), _utc(13, 0)),
    ]

    task = _plan(task_id, plan_kind=PlanKind.TASK, parent_id=master_id)
    _attach_task(task)
    _attach_chain_item(master, child_plan_id=task_id, position=0)

    return (master, task)


def _disjoint_constraint_graph() -> tuple[Plan, ...]:
    master_id = uuid.uuid4()
    task_id = uuid.uuid4()

    master = _plan(master_id, plan_kind=PlanKind.GOAL, is_master=True)
    _attach_goal(master)
    master.constraint_groups = [
        _horizon_group(master_id, _utc(10, 0), _utc(12, 0)),
        _user_group(master_id, _utc(14, 0), _utc(16, 0)),
    ]

    task = _plan(task_id, plan_kind=PlanKind.TASK, parent_id=master_id)
    _attach_task(task)
    _attach_chain_item(master, child_plan_id=task_id, position=0)

    return (master, task)


def _malformed_ancestor_graph() -> tuple[Plan, ...]:
    master_id = uuid.uuid4()
    goal_id = uuid.uuid4()
    task_id = uuid.uuid4()

    master = _plan(master_id, plan_kind=PlanKind.GOAL, is_master=True)
    _attach_goal(master)
    master.constraint_groups = [_horizon_group(master_id, _utc(8, 0), _utc(18, 0))]

    goal = _plan(goal_id, plan_kind=PlanKind.GOAL, parent_id=master_id)
    _attach_goal(goal)
    goal.constraint_groups = [_malformed_user_group(goal_id)]
    _attach_chain_item(master, child_plan_id=goal_id, position=0)

    task = _plan(task_id, plan_kind=PlanKind.TASK, parent_id=goal_id)
    _attach_task(task)
    _attach_chain_item(goal, child_plan_id=task_id, position=0)

    return (master, goal, task)


def _precedence_chain_graph() -> tuple[Plan, ...]:
    master_id = uuid.uuid4()
    task1_id = uuid.uuid4()
    task2_id = uuid.uuid4()
    task3_id = uuid.uuid4()

    master = _plan(master_id, plan_kind=PlanKind.GOAL, is_master=True)
    _attach_goal(master)
    master.constraint_groups = [_horizon_group(master_id, _utc(8, 0), _utc(18, 0))]

    tasks: list[Plan] = []
    for index, task_id in enumerate((task1_id, task2_id, task3_id)):
        task = _plan(task_id, plan_kind=PlanKind.TASK, parent_id=master_id, name=f"task-{index}")
        _attach_task(task, user_completed=index == 1)
        tasks.append(task)

    _attach_chain_items(master, (task1_id, task2_id, task3_id))

    return (master, *tasks)


def _goal_between_tasks_graph() -> tuple[Plan, ...]:
    master_id = uuid.uuid4()
    goal_id = uuid.uuid4()
    task1_id = uuid.uuid4()
    task2_id = uuid.uuid4()
    chain_id = uuid.uuid4()

    master = _plan(master_id, plan_kind=PlanKind.GOAL, is_master=True)
    _attach_goal(master)
    master.constraint_groups = [_horizon_group(master_id, _utc(8, 0), _utc(18, 0))]

    goal = _plan(goal_id, plan_kind=PlanKind.GOAL, parent_id=master_id)
    _attach_goal(goal)

    task1 = _plan(task1_id, plan_kind=PlanKind.TASK, parent_id=master_id)
    _attach_task(task1)
    task2 = _plan(task2_id, plan_kind=PlanKind.TASK, parent_id=goal_id)
    _attach_task(task2)

    _attach_chain_item(master, child_plan_id=task1_id, position=0, chain_id=chain_id)
    _attach_chain_item(master, child_plan_id=goal_id, position=1, chain_id=chain_id)
    _attach_chain_item(goal, child_plan_id=task2_id, position=0)

    return (master, goal, task1, task2)


def _repetition_template_task_graph() -> tuple[Plan, ...]:
    master_id = uuid.uuid4()
    repetition_id = uuid.uuid4()
    template_id = uuid.uuid4()
    template_task_id = uuid.uuid4()
    clone_id = uuid.uuid4()
    clone_task_id = uuid.uuid4()

    master = _plan(master_id, plan_kind=PlanKind.GOAL, is_master=True)
    _attach_goal(master)
    master.constraint_groups = [_horizon_group(master_id, _utc(8, 0), _utc(18, 0))]

    repetition = _plan(repetition_id, plan_kind=PlanKind.REPETITION, parent_id=master_id)
    repetition_plan = _attach_repetition(repetition, template_id, generated_at=_utc(10, 0))
    _attach_chain_item(master, child_plan_id=repetition_id, position=0)

    template = _plan(
        template_id,
        plan_kind=PlanKind.GOAL,
        parent_id=repetition_id,
        clone_status=CloneStatus.TEMPLATE,
        name="template",
    )
    _attach_goal(template)
    template_task = _plan(
        template_task_id,
        plan_kind=PlanKind.TASK,
        parent_id=template_id,
        clone_status=CloneStatus.TEMPLATE,
        name="template task",
    )
    _attach_task(template_task)
    _attach_chain_item(template, child_plan_id=template_task_id, position=0)

    clone = _plan(
        clone_id,
        plan_kind=PlanKind.GOAL,
        parent_id=repetition_id,
        cloned_from_id=template_id,
        clone_status=CloneStatus.LINKED,
        name="clone",
    )
    _attach_goal(clone)
    clone.constraint_groups = [_horizon_group(clone_id, _utc(10, 0), _utc(11, 0))]

    clone_task = _plan(
        clone_task_id,
        plan_kind=PlanKind.TASK,
        parent_id=clone_id,
        cloned_from_id=template_task_id,
        clone_status=CloneStatus.LINKED,
        name="clone task",
    )
    _attach_task(clone_task)
    _attach_chain_item(clone, child_plan_id=clone_task_id, position=0)

    repetition_plan.instances = [
        RepetitionInstance(
            repetition_instance_id=uuid.uuid4(),
            repetition_plan_id=repetition_id,
            instance_index=0,
            root_clone_id=clone_id,
            instance_start_time=_utc(10, 0),
            is_critical=False,
            sort_order=0,
        )
    ]

    return (master, repetition, template, template_task, clone, clone_task)


def _repetition_two_instance_graph() -> tuple[Plan, ...]:
    master_id = uuid.uuid4()
    repetition_id = uuid.uuid4()
    template_id = uuid.uuid4()
    clone_noncritical_id = uuid.uuid4()
    clone_critical_id = uuid.uuid4()

    master = _plan(master_id, plan_kind=PlanKind.GOAL, is_master=True)
    _attach_goal(master)
    master.constraint_groups = [_horizon_group(master_id, _utc(8, 0), _utc(18, 0))]

    repetition = _plan(repetition_id, plan_kind=PlanKind.REPETITION, parent_id=master_id)
    repetition_plan = RepetitionPlan(
        plan_id=repetition_id,
        repeat_mode=RepeatMode.MANUAL_COUNT,
        start_time=_utc(10, 0),
        repeat_interval_minutes=60,
        manual_count=2,
        end_time=None,
        template_root_id=template_id,
        default_instance_critical=False,
        generated_at=_utc(10, 0),
    )
    repetition.repetition_plan = repetition_plan
    _attach_chain_item(master, child_plan_id=repetition_id, position=0)

    template = _plan(
        template_id,
        plan_kind=PlanKind.TASK,
        parent_id=repetition_id,
        clone_status=CloneStatus.TEMPLATE,
        name="template task",
    )
    _attach_task(template)

    clone_noncritical = _plan(
        clone_noncritical_id,
        plan_kind=PlanKind.TASK,
        parent_id=repetition_id,
        cloned_from_id=template_id,
        clone_status=CloneStatus.LINKED,
        name="clone-noncritical",
    )
    _attach_task(clone_noncritical)

    clone_critical = _plan(
        clone_critical_id,
        plan_kind=PlanKind.TASK,
        parent_id=repetition_id,
        cloned_from_id=template_id,
        clone_status=CloneStatus.LINKED,
        name="clone-critical",
    )
    _attach_task(clone_critical)

    repetition_plan.instances = [
        RepetitionInstance(
            repetition_instance_id=uuid.uuid4(),
            repetition_plan_id=repetition_id,
            instance_index=0,
            root_clone_id=clone_noncritical_id,
            instance_start_time=_utc(10, 0),
            is_critical=False,
            sort_order=0,
        ),
        RepetitionInstance(
            repetition_instance_id=uuid.uuid4(),
            repetition_plan_id=repetition_id,
            instance_index=1,
            root_clone_id=clone_critical_id,
            instance_start_time=_utc(11, 0),
            is_critical=True,
            sort_order=0,
        ),
    ]

    return (master, repetition, template, clone_noncritical, clone_critical)


def _repetition_same_bucket_sort_order_graph() -> tuple[Plan, ...]:
    master_id = uuid.uuid4()
    repetition_id = uuid.uuid4()
    template_id = uuid.uuid4()
    clone_high_sort_id = uuid.uuid4()
    clone_low_sort_id = uuid.uuid4()

    master = _plan(master_id, plan_kind=PlanKind.GOAL, is_master=True)
    _attach_goal(master)
    master.constraint_groups = [_horizon_group(master_id, _utc(8, 0), _utc(18, 0))]

    repetition = _plan(repetition_id, plan_kind=PlanKind.REPETITION, parent_id=master_id)
    repetition_plan = RepetitionPlan(
        plan_id=repetition_id,
        repeat_mode=RepeatMode.MANUAL_COUNT,
        start_time=_utc(10, 0),
        repeat_interval_minutes=60,
        manual_count=2,
        end_time=None,
        template_root_id=template_id,
        default_instance_critical=False,
        generated_at=_utc(10, 0),
    )
    repetition.repetition_plan = repetition_plan
    _attach_chain_item(master, child_plan_id=repetition_id, position=0)

    template = _plan(
        template_id,
        plan_kind=PlanKind.TASK,
        parent_id=repetition_id,
        clone_status=CloneStatus.TEMPLATE,
        name="template task",
    )
    _attach_task(template)

    clone_high_sort = _plan(
        clone_high_sort_id,
        plan_kind=PlanKind.TASK,
        parent_id=repetition_id,
        cloned_from_id=template_id,
        clone_status=CloneStatus.LINKED,
        name="clone-high-sort",
    )
    _attach_task(clone_high_sort)

    clone_low_sort = _plan(
        clone_low_sort_id,
        plan_kind=PlanKind.TASK,
        parent_id=repetition_id,
        cloned_from_id=template_id,
        clone_status=CloneStatus.LINKED,
        name="clone-low-sort",
    )
    _attach_task(clone_low_sort)

    repetition_plan.instances = [
        RepetitionInstance(
            repetition_instance_id=uuid.uuid4(),
            repetition_plan_id=repetition_id,
            instance_index=0,
            root_clone_id=clone_high_sort_id,
            instance_start_time=_utc(10, 0),
            is_critical=True,
            sort_order=1,
        ),
        RepetitionInstance(
            repetition_instance_id=uuid.uuid4(),
            repetition_plan_id=repetition_id,
            instance_index=1,
            root_clone_id=clone_low_sort_id,
            instance_start_time=_utc(11, 0),
            is_critical=True,
            sort_order=0,
        ),
    ]

    return (master, repetition, template, clone_high_sort, clone_low_sort)


def test_intersect_time_windows_returns_empty_when_disjoint() -> None:
    left = (_window(_utc(9, 0), _utc(10, 0)),)
    right = (_window(_utc(11, 0), _utc(12, 0)),)

    assert intersect_time_windows(left, right) == ()


def test_intersect_time_windows_returns_overlap_for_single_pair() -> None:
    left = (_window(_utc(9, 0), _utc(12, 0)),)
    right = (_window(_utc(11, 0), _utc(13, 0)),)

    assert intersect_time_windows(left, right) == (_window(_utc(11, 0), _utc(12, 0)),)


def test_intersect_time_windows_handles_multiple_intervals() -> None:
    left = (
        _window(_utc(9, 0), _utc(11, 0)),
        _window(_utc(13, 0), _utc(15, 0)),
    )
    right = (
        _window(_utc(10, 0), _utc(12, 0)),
        _window(_utc(14, 0), _utc(16, 0)),
    )

    assert intersect_time_windows(left, right) == (
        _window(_utc(10, 0), _utc(11, 0)),
        _window(_utc(14, 0), _utc(15, 0)),
    )


def test_compute_effective_constraints_intersects_master_horizon_and_user_group() -> None:
    plans = _constraint_intersection_graph()
    indexes = build_resolution_indexes(plans)
    task = _all_tasks(resolve_tasks_from_graph(_RUN_AT, plans))[0]

    effective, sources = compute_effective_constraints(task.parent_path, indexes)

    assert effective == (_window(_utc(11, 0), _utc(12, 0)),)
    assert len(sources) == 2
    assert {source.constraint_kind for source in sources} == {
        ConstraintKind.SYSTEM_MASTER_HORIZON,
        ConstraintKind.USER,
    }


def test_compute_effective_constraints_empty_when_groups_do_not_overlap() -> None:
    plans = _disjoint_constraint_graph()
    indexes = build_resolution_indexes(plans)
    task = _all_tasks(resolve_tasks_from_graph(_RUN_AT, plans))[0]

    effective, _sources = compute_effective_constraints(task.parent_path, indexes)

    assert effective == ()


def test_malformed_ancestor_window_marks_descendant_invalid_incomplete() -> None:
    result = resolve_tasks_from_graph(_RUN_AT, _malformed_ancestor_graph())
    task = result.invalid_incomplete[0]

    assert is_invalid_incomplete_task(task)
    assert any(error.code == MessageCode.INVALID_TIME_WINDOW for error in task.validation_errors)


def test_constraint_sources_lists_malformed_group_without_intersection() -> None:
    plans = _malformed_ancestor_graph()
    indexes = build_resolution_indexes(plans)
    task = _all_tasks(resolve_tasks_from_graph(_RUN_AT, plans))[0]

    effective, sources = compute_effective_constraints(task.parent_path, indexes)

    assert effective == (_window(_utc(8, 0), _utc(18, 0)),)
    assert any(source.constraint_kind == ConstraintKind.USER for source in sources)


def test_valid_task_may_have_empty_effective_time_windows() -> None:
    result = resolve_tasks_from_graph(_RUN_AT, _disjoint_constraint_graph())

    assert len(result.valid_incomplete) == 1
    assert result.valid_incomplete[0].effective_time_windows == ()


def test_is_invalid_incomplete_task_requires_errors_and_incomplete() -> None:
    invalid_error = ServiceMessage(code=MessageCode.INVALID_DURATION, message="bad", details={})

    assert is_invalid_incomplete_task(
        _resolved_task(uuid.uuid4(), validation_errors=(invalid_error,))
    )
    assert not is_invalid_incomplete_task(
        _resolved_task(uuid.uuid4(), user_completed=True, validation_errors=(invalid_error,))
    )
    assert not is_invalid_incomplete_task(_resolved_task(uuid.uuid4()))


def test_is_invalid_task_true_when_validation_errors_present() -> None:
    error = ServiceMessage(code=MessageCode.INVALID_DURATION, message="bad", details={})
    assert is_invalid_task(_resolved_task(uuid.uuid4(), validation_errors=(error,)))


def test_validate_resolve_tasks_result_rejects_duplicate_plan_id_across_buckets() -> None:
    plan_id = uuid.uuid4()
    task = _resolved_task(plan_id)
    result = ResolveTasksResult(
        run_started_at=_RUN_AT,
        valid_incomplete=(task, task),
        valid_completed=(),
        invalid_incomplete=(),
        invalid_completed=(),
        precedence_constraints=(),
        warnings=(),
    )

    with pytest.raises(ValueError, match="appears in multiple resolution buckets"):
        validate_resolve_tasks_result(result)


def test_validate_resolve_tasks_result_rejects_mismatched_bucket_membership() -> None:
    task = _resolved_task(uuid.uuid4())
    result = ResolveTasksResult(
        run_started_at=_RUN_AT,
        valid_incomplete=(),
        valid_completed=(),
        invalid_incomplete=(task,),
        invalid_completed=(),
        precedence_constraints=(),
        warnings=(),
    )

    with pytest.raises(ValueError, match="invalid_incomplete"):
        validate_resolve_tasks_result(result)


def test_collect_precedence_constraints_links_incomplete_chain_predecessor() -> None:
    plans = _precedence_chain_graph()
    tasks = _all_tasks(resolve_tasks_from_graph(_RUN_AT, plans))
    indexes = build_resolution_indexes(plans)
    task_ids = {task.plan_id for task in tasks}

    edges = collect_precedence_constraints(tuple(tasks), plans, indexes)

    assert any(
        edge.predecessor_task_id in task_ids
        and edge.successor_task_id in task_ids
        and edge.reason == "goal_child_chain_order"
        for edge in edges
    )


def test_collect_precedence_constraints_skips_completed_predecessor() -> None:
    plans = _precedence_chain_graph()
    tasks = _all_tasks(resolve_tasks_from_graph(_RUN_AT, plans))
    indexes = build_resolution_indexes(plans)
    task_by_name = {task.name: task.plan_id for task in tasks}

    edges = collect_precedence_constraints(tuple(tasks), plans, indexes)

    assert (
        PlanID(task_by_name["task-0"]),
        PlanID(task_by_name["task-2"]),
    ) in {(edge.predecessor_task_id, edge.successor_task_id) for edge in edges}


def test_collect_precedence_constraints_ignores_non_task_chain_items() -> None:
    plans = _goal_between_tasks_graph()
    tasks = _all_tasks(resolve_tasks_from_graph(_RUN_AT, plans))
    indexes = build_resolution_indexes(plans)

    edges = collect_precedence_constraints(tuple(tasks), plans, indexes)

    assert edges == ()


def test_resolve_tasks_from_graph_excludes_template_subtree_tasks() -> None:
    plans = _repetition_template_task_graph()
    template_task_id = next(plan.plan_id for plan in plans if plan.name == "template task")
    result = resolve_tasks_from_graph(_RUN_AT, plans)
    resolved_ids = {task.plan_id for task in _all_tasks(result)}

    assert PlanID(template_task_id) not in resolved_ids
    assert any(task.name == "clone task" for task in _all_tasks(result))


def test_resolve_tasks_from_graph_orders_repetition_instances_critical_first() -> None:
    result = resolve_tasks_from_graph(_RUN_AT, _repetition_two_instance_graph())
    tasks = _all_tasks(result)
    by_name = {task.name: task for task in tasks}

    assert by_name["clone-critical"].priority_path < by_name["clone-noncritical"].priority_path


def test_resolve_tasks_from_graph_orders_instances_by_sort_order_within_bucket() -> None:
    result = resolve_tasks_from_graph(_RUN_AT, _repetition_same_bucket_sort_order_graph())
    tasks = _all_tasks(result)
    by_name = {task.name: task for task in tasks}

    assert by_name["clone-low-sort"].priority_path < by_name["clone-high-sort"].priority_path

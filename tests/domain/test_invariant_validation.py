"""Pure tests for validate_master_tree_graph (no database)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from calendar_backend.domain.enums import CloneStatus, ConstraintKind, PlanKind, RepeatMode
from calendar_backend.domain.errors import MessageCode
from calendar_backend.domain.invariant_validation import validate_master_tree_graph
from calendar_backend.models.chains import GoalChildChain, GoalChildChainItem
from calendar_backend.models.constraints import TimeConstraintGroup, TimeWindow
from calendar_backend.models.plans import GoalPlan, Plan, RepetitionPlan, TaskPlan
from calendar_backend.models.repetitions import RepetitionInstance

_NOW = datetime(2026, 6, 7, 12, 0, tzinfo=UTC)


def _utc(h: int, mi: int = 0) -> datetime:
    return datetime(2026, 6, 7, h, mi, tzinfo=UTC)


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


def _attach_task(plan: Plan) -> None:
    plan.task_plan = TaskPlan(
        plan_id=plan.plan_id,
        duration_minutes=30,
        divisible=False,
        minimum_chunk_size_minutes=None,
        user_completed=False,
        completed_at=None,
    )


def _attach_repetition(plan: Plan, template_root_id: uuid.UUID) -> RepetitionPlan:
    repetition = RepetitionPlan(
        plan_id=plan.plan_id,
        repeat_mode=RepeatMode.MANUAL_COUNT,
        start_time=_NOW,
        repeat_interval_minutes=60,
        manual_count=1,
        end_time=None,
        template_root_id=template_root_id,
        default_instance_critical=False,
        generated_at=None,
    )
    repetition.instances = []
    plan.repetition_plan = repetition
    return repetition


def _repetition_instance(
    *,
    repetition_plan_id: uuid.UUID,
    root_clone_id: uuid.UUID,
    instance_index: int,
    sort_order: int,
    is_critical: bool = False,
) -> RepetitionInstance:
    return RepetitionInstance(
        repetition_instance_id=uuid.uuid4(),
        repetition_plan_id=repetition_plan_id,
        instance_index=instance_index,
        root_clone_id=root_clone_id,
        instance_start_time=_utc(10, 0),
        is_critical=is_critical,
        sort_order=sort_order,
    )


def _attach_chain_item(
    goal: Plan,
    *,
    child_plan_id: uuid.UUID,
    is_critical: bool = False,
    sort_order: int = 0,
    position: int = 0,
) -> None:
    chain_id = uuid.uuid4()
    chain = GoalChildChain(
        goal_child_chain_id=chain_id,
        parent_goal_id=goal.plan_id,
        is_critical=is_critical,
        sort_order=sort_order,
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
    ]
    assert goal.goal_plan is not None
    goal.goal_plan.chains = [*goal.goal_plan.chains, chain]


def _valid_repetition_create_graph() -> tuple[Plan, ...]:
    master_id = uuid.uuid4()
    goal_id = uuid.uuid4()
    template_id = uuid.uuid4()
    repetition_id = uuid.uuid4()

    master = _plan(master_id, plan_kind=PlanKind.GOAL, is_master=True)
    _attach_goal(master)
    master.constraint_groups = [_horizon_group(master_id)]

    goal = _plan(goal_id, plan_kind=PlanKind.GOAL, parent_id=master_id)
    _attach_goal(goal)
    _attach_chain_item(master, child_plan_id=goal_id)

    template = _plan(
        template_id,
        plan_kind=PlanKind.GOAL,
        parent_id=repetition_id,
        clone_status=CloneStatus.TEMPLATE,
        name="template",
    )
    _attach_goal(template)

    repetition = _plan(repetition_id, plan_kind=PlanKind.REPETITION, parent_id=goal_id)
    _attach_repetition(repetition, template_id)

    _attach_chain_item(goal, child_plan_id=repetition_id)

    return (master, goal, template, repetition)


def _valid_repetition_graph() -> tuple[Plan, ...]:
    master_id = uuid.uuid4()
    goal_id = uuid.uuid4()
    template_id = uuid.uuid4()
    repetition_id = uuid.uuid4()
    clone_id = uuid.uuid4()

    master = _plan(master_id, plan_kind=PlanKind.GOAL, is_master=True)
    _attach_goal(master)
    master.constraint_groups = [_horizon_group(master_id)]

    goal = _plan(goal_id, plan_kind=PlanKind.GOAL, parent_id=master_id)
    _attach_goal(goal)
    _attach_chain_item(master, child_plan_id=goal_id)

    repetition = _plan(repetition_id, plan_kind=PlanKind.REPETITION, parent_id=goal_id)
    repetition_plan = _attach_repetition(repetition, template_id)
    repetition_plan.generated_at = _utc(10, 0)
    _attach_chain_item(goal, child_plan_id=repetition_id)

    template = _plan(
        template_id,
        plan_kind=PlanKind.GOAL,
        parent_id=repetition_id,
        clone_status=CloneStatus.TEMPLATE,
        name="template",
    )
    _attach_goal(template)

    clone = _plan(
        clone_id,
        plan_kind=PlanKind.GOAL,
        parent_id=repetition_id,
        cloned_from_id=template_id,
        clone_status=CloneStatus.LINKED,
        name="clone",
    )
    _attach_goal(clone)
    clone.constraint_groups = [_repetition_window_group(clone_id, _utc(10, 0), _utc(11, 0))]

    repetition_plan.instances = [
        _repetition_instance(
            repetition_plan_id=repetition_id,
            root_clone_id=clone_id,
            instance_index=0,
            sort_order=0,
        )
    ]

    return (master, goal, template, repetition, clone)


def _horizon_group(plan_id: uuid.UUID) -> TimeConstraintGroup:
    group_id = uuid.uuid4()
    window_id = uuid.uuid4()
    group = TimeConstraintGroup(
        time_constraint_group_id=group_id,
        plan_id=plan_id,
        constraint_kind=ConstraintKind.SYSTEM_MASTER_HORIZON,
    )
    group.windows = [
        TimeWindow(
            time_window_id=window_id,
            group_id=group_id,
            start_time=_utc(10, 0),
            end_time=_utc(12, 0),
        )
    ]
    return group


def _repetition_window_group(
    plan_id: uuid.UUID,
    start_time: datetime,
    end_time: datetime,
) -> TimeConstraintGroup:
    group_id = uuid.uuid4()
    window_id = uuid.uuid4()
    group = TimeConstraintGroup(
        time_constraint_group_id=group_id,
        plan_id=plan_id,
        constraint_kind=ConstraintKind.SYSTEM_REPETITION_WINDOW,
    )
    group.windows = [
        TimeWindow(
            time_window_id=window_id,
            group_id=group_id,
            start_time=start_time,
            end_time=end_time,
        )
    ]
    return group


def _valid_master_graph() -> tuple[Plan, ...]:
    master_id = uuid.uuid4()
    master = _plan(master_id, plan_kind=PlanKind.GOAL, is_master=True)
    _attach_goal(master)
    master.constraint_groups = [_horizon_group(master_id)]
    return (master,)


def test_validate_master_tree_graph_accepts_minimal_valid_tree() -> None:
    assert validate_master_tree_graph(_valid_master_graph()) == ()


def test_validate_master_tree_graph_reports_missing_master() -> None:
    orphan_id = uuid.uuid4()
    orphan = _plan(orphan_id, plan_kind=PlanKind.TASK, parent_id=uuid.uuid4())
    _attach_task(orphan)

    violations = validate_master_tree_graph((orphan,))

    assert len(violations) == 1
    assert violations[0].code == MessageCode.INVALID_MASTER_PLAN


def test_validate_master_tree_graph_reports_master_with_parent() -> None:
    master_id = uuid.uuid4()
    parent_id = uuid.uuid4()
    master = _plan(master_id, plan_kind=PlanKind.GOAL, is_master=True, parent_id=parent_id)
    _attach_goal(master)
    master.constraint_groups = [_horizon_group(master_id)]
    parent = _plan(parent_id, plan_kind=PlanKind.GOAL, parent_id=None)
    _attach_goal(parent)

    violations = validate_master_tree_graph((parent, master))

    assert any(v.code == MessageCode.INVALID_MASTER_PLAN for v in violations)


def test_validate_master_tree_graph_reports_orphan_plan() -> None:
    master_id = uuid.uuid4()
    orphan_id = uuid.uuid4()
    master = _plan(master_id, plan_kind=PlanKind.GOAL, is_master=True)
    _attach_goal(master)
    master.constraint_groups = [_horizon_group(master_id)]
    orphan = _plan(
        orphan_id,
        plan_kind=PlanKind.TASK,
        parent_id=uuid.uuid4(),
    )
    _attach_task(orphan)

    violations = validate_master_tree_graph((master, orphan))

    assert any(v.code == MessageCode.ORPHAN_PLAN for v in violations)


def test_validate_master_tree_graph_reports_subtype_missing_detail() -> None:
    master_id = uuid.uuid4()
    child_id = uuid.uuid4()
    master = _plan(master_id, plan_kind=PlanKind.GOAL, is_master=True)
    _attach_goal(master)
    master.constraint_groups = [_horizon_group(master_id)]
    child = _plan(child_id, plan_kind=PlanKind.TASK, parent_id=master_id)

    violations = validate_master_tree_graph((master, child))

    assert any(v.code == MessageCode.PLAN_SUBTYPE_MISMATCH for v in violations)


def test_validate_master_tree_graph_reports_subtype_conflicting_detail() -> None:
    master_id = uuid.uuid4()
    child_id = uuid.uuid4()
    master = _plan(master_id, plan_kind=PlanKind.GOAL, is_master=True)
    _attach_goal(master)
    master.constraint_groups = [_horizon_group(master_id)]
    child = _plan(child_id, plan_kind=PlanKind.TASK, parent_id=master_id)
    _attach_task(child)
    child.goal_plan = GoalPlan(plan_id=child_id)

    violations = validate_master_tree_graph((master, child))

    assert any(v.code == MessageCode.PLAN_SUBTYPE_MISMATCH for v in violations)


def test_validate_master_tree_graph_reports_missing_master_horizon() -> None:
    master_id = uuid.uuid4()
    master = _plan(master_id, plan_kind=PlanKind.GOAL, is_master=True)
    _attach_goal(master)

    violations = validate_master_tree_graph((master,))

    assert any(
        v.code == MessageCode.CONSTRAINT_INVARIANT_VIOLATION
        and "SYSTEM_MASTER_HORIZON" in v.message
        for v in violations
    )


def test_validate_master_tree_graph_reports_empty_user_group() -> None:
    master_id = uuid.uuid4()
    master = _plan(master_id, plan_kind=PlanKind.GOAL, is_master=True)
    _attach_goal(master)
    horizon = _horizon_group(master_id)
    empty_user = TimeConstraintGroup(
        time_constraint_group_id=uuid.uuid4(),
        plan_id=master_id,
        constraint_kind=ConstraintKind.USER,
    )
    empty_user.windows = []
    master.constraint_groups = [horizon, empty_user]

    violations = validate_master_tree_graph((master,))

    assert any(
        v.code == MessageCode.CONSTRAINT_INVARIANT_VIOLATION
        and "USER constraint group" in v.message
        for v in violations
    )


def test_validate_master_tree_graph_reports_unmerged_user_windows() -> None:
    master_id = uuid.uuid4()
    master = _plan(master_id, plan_kind=PlanKind.GOAL, is_master=True)
    _attach_goal(master)
    horizon = _horizon_group(master_id)
    group_id = uuid.uuid4()
    user_group = TimeConstraintGroup(
        time_constraint_group_id=group_id,
        plan_id=master_id,
        constraint_kind=ConstraintKind.USER,
    )
    user_group.windows = [
        TimeWindow(
            time_window_id=uuid.uuid4(),
            group_id=group_id,
            start_time=_utc(9, 0),
            end_time=_utc(12, 0),
        ),
        TimeWindow(
            time_window_id=uuid.uuid4(),
            group_id=group_id,
            start_time=_utc(12, 0),
            end_time=_utc(15, 0),
        ),
    ]
    master.constraint_groups = [horizon, user_group]

    violations = validate_master_tree_graph((master,))

    assert any(
        v.code == MessageCode.CONSTRAINT_INVARIANT_VIOLATION
        and "merged and non-overlapping" in v.message
        for v in violations
    )


def test_validate_master_tree_graph_reports_chain_child_wrong_parent() -> None:
    master_id = uuid.uuid4()
    goal_id = uuid.uuid4()
    child_id = uuid.uuid4()
    master = _plan(master_id, plan_kind=PlanKind.GOAL, is_master=True)
    _attach_goal(master)
    master.constraint_groups = [_horizon_group(master_id)]
    goal = _plan(goal_id, plan_kind=PlanKind.GOAL, parent_id=master_id)
    _attach_goal(goal)
    child = _plan(child_id, plan_kind=PlanKind.TASK, parent_id=master_id)
    _attach_task(child)

    chain_id = uuid.uuid4()
    chain = GoalChildChain(
        goal_child_chain_id=chain_id,
        parent_goal_id=goal_id,
        is_critical=False,
        sort_order=0,
        created_at=_NOW,
        updated_at=_NOW,
    )
    chain.items = [
        GoalChildChainItem(
            goal_child_chain_item_id=uuid.uuid4(),
            chain_id=chain_id,
            child_plan_id=child_id,
            position=0,
        )
    ]
    assert goal.goal_plan is not None
    goal.goal_plan.chains = [chain]

    violations = validate_master_tree_graph((master, goal, child))

    assert any(
        v.code == MessageCode.CHAIN_INVARIANT_VIOLATION
        and "direct child of the parent goal" in v.message
        for v in violations
    )


def test_validate_master_tree_graph_reports_non_dense_chain_position() -> None:
    master_id = uuid.uuid4()
    goal_id = uuid.uuid4()
    child_a_id = uuid.uuid4()
    child_b_id = uuid.uuid4()
    master = _plan(master_id, plan_kind=PlanKind.GOAL, is_master=True)
    _attach_goal(master)
    master.constraint_groups = [_horizon_group(master_id)]
    goal = _plan(goal_id, plan_kind=PlanKind.GOAL, parent_id=master_id)
    _attach_goal(goal)
    child_a = _plan(child_a_id, plan_kind=PlanKind.TASK, parent_id=goal_id)
    _attach_task(child_a)
    child_b = _plan(child_b_id, plan_kind=PlanKind.TASK, parent_id=goal_id)
    _attach_task(child_b)

    chain_id = uuid.uuid4()
    chain = GoalChildChain(
        goal_child_chain_id=chain_id,
        parent_goal_id=goal_id,
        is_critical=False,
        sort_order=0,
        created_at=_NOW,
        updated_at=_NOW,
    )
    chain.items = [
        GoalChildChainItem(
            goal_child_chain_item_id=uuid.uuid4(),
            chain_id=chain_id,
            child_plan_id=child_a_id,
            position=0,
        ),
        GoalChildChainItem(
            goal_child_chain_item_id=uuid.uuid4(),
            chain_id=chain_id,
            child_plan_id=child_b_id,
            position=2,
        ),
    ]
    assert goal.goal_plan is not None
    goal.goal_plan.chains = [chain]

    violations = validate_master_tree_graph((master, goal, child_a, child_b))

    assert any(
        v.code == MessageCode.CHAIN_INVARIANT_VIOLATION and "positions must be dense" in v.message
        for v in violations
    )


def test_validate_master_tree_graph_accepts_valid_repetition_create_shape() -> None:
    assert validate_master_tree_graph(_valid_repetition_create_graph()) == ()


def test_validate_master_tree_graph_accepts_post_generation_repetition_with_instances() -> None:
    assert validate_master_tree_graph(_valid_repetition_graph()) == ()


def test_validate_master_tree_graph_reports_master_critical_chain() -> None:
    master_id = uuid.uuid4()
    child_id = uuid.uuid4()
    master = _plan(master_id, plan_kind=PlanKind.GOAL, is_master=True)
    _attach_goal(master)
    master.constraint_groups = [_horizon_group(master_id)]
    child = _plan(child_id, plan_kind=PlanKind.TASK, parent_id=master_id)
    _attach_task(child)
    _attach_chain_item(master, child_plan_id=child_id, is_critical=True)

    violations = validate_master_tree_graph((master, child))

    assert any(
        v.code == MessageCode.CHAIN_INVARIANT_VIOLATION
        and "Master goal child chains must be non-critical" in v.message
        for v in violations
    )


def test_validate_master_tree_graph_reports_goal_child_missing_chain_item() -> None:
    master_id = uuid.uuid4()
    goal_id = uuid.uuid4()
    child_id = uuid.uuid4()
    master = _plan(master_id, plan_kind=PlanKind.GOAL, is_master=True)
    _attach_goal(master)
    master.constraint_groups = [_horizon_group(master_id)]
    goal = _plan(goal_id, plan_kind=PlanKind.GOAL, parent_id=master_id)
    _attach_goal(goal)
    child = _plan(child_id, plan_kind=PlanKind.TASK, parent_id=goal_id)
    _attach_task(child)

    violations = validate_master_tree_graph((master, goal, child))

    assert any(
        v.code == MessageCode.CHAIN_INVARIANT_VIOLATION
        and "exactly one goal child chain item" in v.message
        for v in violations
    )


def test_validate_master_tree_graph_reports_goal_child_with_two_chain_items() -> None:
    master_id = uuid.uuid4()
    goal_id = uuid.uuid4()
    child_id = uuid.uuid4()
    master = _plan(master_id, plan_kind=PlanKind.GOAL, is_master=True)
    _attach_goal(master)
    master.constraint_groups = [_horizon_group(master_id)]
    goal = _plan(goal_id, plan_kind=PlanKind.GOAL, parent_id=master_id)
    _attach_goal(goal)
    _attach_chain_item(master, child_plan_id=goal_id)
    child = _plan(child_id, plan_kind=PlanKind.TASK, parent_id=goal_id)
    _attach_task(child)
    _attach_chain_item(goal, child_plan_id=child_id)
    _attach_chain_item(goal, child_plan_id=child_id)

    violations = validate_master_tree_graph((master, goal, child))

    assert any(
        v.code == MessageCode.CHAIN_INVARIANT_VIOLATION
        and "exactly one goal child chain item" in v.message
        and v.details.get("chain_item_count") == "2"
        for v in violations
    )


def test_validate_master_tree_graph_reports_repetition_template_wrong_parent() -> None:
    master, goal, template, repetition = _valid_repetition_create_graph()
    template.parent_id = goal.plan_id

    violations = validate_master_tree_graph((master, goal, template, repetition))

    assert any(
        v.code == MessageCode.CHAIN_INVARIANT_VIOLATION
        and "direct child of the repetition plan" in v.message
        for v in violations
    )


def test_validate_master_tree_graph_reports_repetition_template_in_chain() -> None:
    master, goal, template, repetition = _valid_repetition_create_graph()
    _attach_chain_item(goal, child_plan_id=template.plan_id)

    violations = validate_master_tree_graph((master, goal, template, repetition))

    assert any(
        v.code == MessageCode.CHAIN_INVARIANT_VIOLATION
        and "template root must not appear in a goal child chain" in v.message
        for v in violations
    )


def test_validate_master_tree_graph_reports_repetition_non_minute_aligned_start() -> None:
    master, goal, template, repetition = _valid_repetition_create_graph()
    assert repetition.repetition_plan is not None
    repetition.repetition_plan.start_time = _utc(10, 30).replace(second=15)

    violations = validate_master_tree_graph((master, goal, template, repetition))

    assert any(
        v.code == MessageCode.INVALID_REPETITION_SETTINGS and "start_time" in v.message
        for v in violations
    )


def test_validate_master_tree_graph_reports_repetition_non_minute_aligned_end_time() -> None:
    master, goal, template, repetition = _valid_repetition_create_graph()
    assert repetition.repetition_plan is not None
    repetition.repetition_plan.end_time = _utc(11, 0).replace(second=15)

    violations = validate_master_tree_graph((master, goal, template, repetition))

    assert any(
        v.code == MessageCode.INVALID_REPETITION_SETTINGS and "end_time" in v.message
        for v in violations
    )


def test_validate_master_tree_graph_reports_duplicate_root_clone_id() -> None:
    graph = list(_valid_repetition_graph())
    _master, _goal, _template, repetition, clone = graph
    assert repetition.repetition_plan is not None
    repetition.repetition_plan.instances.append(
        _repetition_instance(
            repetition_plan_id=repetition.plan_id,
            root_clone_id=clone.plan_id,
            instance_index=1,
            sort_order=0,
        )
    )

    violations = validate_master_tree_graph(tuple(graph))

    assert any(
        v.code == MessageCode.CHAIN_INVARIANT_VIOLATION
        and "root_clone_id appears in more than one repetition instance" in v.message
        for v in violations
    )


def test_validate_master_tree_graph_reports_repetition_clone_wrong_parent() -> None:
    graph = list(_valid_repetition_graph())
    master, _goal, _template, _repetition, clone = graph
    clone.parent_id = master.plan_id

    violations = validate_master_tree_graph(tuple(graph))

    assert any(
        v.code == MessageCode.CHAIN_INVARIANT_VIOLATION
        and "Repetition root clone must be child of repetition plan" in v.message
        for v in violations
    )


def test_validate_master_tree_graph_reports_repetition_clone_wrong_lineage() -> None:
    graph = list(_valid_repetition_graph())
    master, _goal, _template, _repetition, clone = graph
    clone.cloned_from_id = master.plan_id

    violations = validate_master_tree_graph(tuple(graph))

    assert any(
        v.code == MessageCode.CHAIN_INVARIANT_VIOLATION
        and "must clone from template root" in v.message
        for v in violations
    )


def test_validate_master_tree_graph_reports_non_dense_instance_index() -> None:
    graph = list(_valid_repetition_graph())
    _master, _goal, _template, repetition, _clone = graph
    assert repetition.repetition_plan is not None
    repetition.repetition_plan.instances[0].instance_index = 1

    violations = validate_master_tree_graph(tuple(graph))

    assert any(
        v.code == MessageCode.CHAIN_INVARIANT_VIOLATION
        and "instance_index values must be dense" in v.message
        for v in violations
    )


def test_validate_master_tree_graph_reports_non_dense_repetition_sort_order() -> None:
    graph = list(_valid_repetition_graph())
    _master, _goal, _template, repetition, _clone = graph
    assert repetition.repetition_plan is not None
    repetition.repetition_plan.instances[0].sort_order = 1

    violations = validate_master_tree_graph(tuple(graph))

    assert any(
        v.code == MessageCode.CHAIN_INVARIANT_VIOLATION and "sort_order must be dense" in v.message
        for v in violations
    )


def test_validate_master_tree_graph_reports_missing_repetition_instance_window() -> None:
    graph = list(_valid_repetition_graph())
    _master, _goal, _template, _repetition, clone = graph
    clone.constraint_groups = []

    violations = validate_master_tree_graph(tuple(graph))

    assert any(
        v.code == MessageCode.CONSTRAINT_INVARIANT_VIOLATION
        and "exactly one SYSTEM_REPETITION_WINDOW group" in v.message
        for v in violations
    )


def test_validate_master_tree_graph_reports_repetition_window_on_template_root() -> None:
    graph = list(_valid_repetition_graph())
    _master, _goal, template, _repetition, _clone = graph
    template.constraint_groups = [
        _repetition_window_group(template.plan_id, _utc(10, 0), _utc(11, 0))
    ]

    violations = validate_master_tree_graph(tuple(graph))

    assert any(
        v.code == MessageCode.CONSTRAINT_INVARIANT_VIOLATION
        and "only allowed on generated repetition instance roots" in v.message
        for v in violations
    )


def test_validate_master_tree_graph_reports_completed_task_missing_completed_at() -> None:
    master_id = uuid.uuid4()
    task_id = uuid.uuid4()
    master = _plan(master_id, plan_kind=PlanKind.GOAL, is_master=True)
    _attach_goal(master)
    master.constraint_groups = [_horizon_group(master_id)]
    task = _plan(task_id, plan_kind=PlanKind.TASK, parent_id=master_id)
    _attach_task(task)
    assert task.task_plan is not None
    task.task_plan.user_completed = True

    violations = validate_master_tree_graph((master, task))

    assert any(
        v.code == MessageCode.TASK_COMPLETION_INVARIANT_VIOLATION and "completed_at" in v.message
        for v in violations
    )


def test_validate_master_tree_graph_reports_incomplete_task_with_completed_at() -> None:
    master_id = uuid.uuid4()
    task_id = uuid.uuid4()
    master = _plan(master_id, plan_kind=PlanKind.GOAL, is_master=True)
    _attach_goal(master)
    master.constraint_groups = [_horizon_group(master_id)]
    task = _plan(task_id, plan_kind=PlanKind.TASK, parent_id=master_id)
    _attach_task(task)
    assert task.task_plan is not None
    task.task_plan.completed_at = _NOW

    violations = validate_master_tree_graph((master, task))

    assert any(
        v.code == MessageCode.TASK_COMPLETION_INVARIANT_VIOLATION
        and "must not have completed_at" in v.message
        for v in violations
    )

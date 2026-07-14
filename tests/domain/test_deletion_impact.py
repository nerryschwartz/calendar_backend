"""Pure tests for compute_deletion_impact (no database)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from calendar_backend.domain.deletion import (
    DeletionOperation,
    build_deletion_preview,
    compute_deletion_impact,
)
from calendar_backend.domain.enums import CalendarEntryType, CloneStatus, PlanKind, RepeatMode
from calendar_backend.domain.ids import CalendarEntryID, PlanID
from calendar_backend.models.calendar import CalendarEntry
from calendar_backend.models.chains import GoalChildChain, GoalChildChainItem
from calendar_backend.models.plans import GoalPlan, Plan, RepetitionPlan, TaskPlan

_NOW = datetime(2026, 6, 7, 12, 0, tzinfo=UTC)


def _plan(
    plan_id: uuid.UUID,
    *,
    plan_kind: PlanKind,
    parent_id: uuid.UUID | None = None,
    name: str = "plan",
    is_master: bool = False,
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


def _attach_goal_with_chain(
    goal: Plan,
    *,
    chain_id: uuid.UUID,
    is_critical: bool,
    sort_order: int,
    members: tuple[tuple[uuid.UUID, int], ...],
) -> None:
    goal.goal_plan = GoalPlan(plan_id=goal.plan_id)
    goal_plan = goal.goal_plan
    assert goal_plan is not None
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
            child_plan_id=child_id,
            position=position,
        )
        for child_id, position in members
    ]
    goal_plan.chains = [chain]


def _attach_task(plan: Plan) -> None:
    plan.task_plan = TaskPlan(
        plan_id=plan.plan_id,
        duration_minutes=30,
        divisible=False,
        minimum_chunk_size_minutes=None,
        user_completed=False,
        completed_at=None,
    )


def _calendar_entry(entry_id: uuid.UUID, source_plan_id: uuid.UUID) -> CalendarEntry:
    end = _NOW.replace(hour=13)
    return CalendarEntry(
        calendar_entry_id=entry_id,
        entry_type=CalendarEntryType.TASK,
        start_time=_NOW,
        end_time=end,
        source_plan_id=source_plan_id,
        source_free_time_activity_id=None,
        calendar_run_id=None,
        display_label="block",
        created_at=_NOW,
        updated_at=_NOW,
    )


def test_compute_deletion_impact_leaf_only() -> None:
    leaf_id = uuid.uuid4()
    leaf = _plan(leaf_id, plan_kind=PlanKind.TASK)
    _attach_task(leaf)

    preview = compute_deletion_impact(PlanID(leaf_id), (leaf,), ())

    assert preview.affected_plan_ids == (PlanID(leaf_id),)
    assert preview.affected_calendar_entry_ids == ()


def test_compute_deletion_impact_includes_descendants() -> None:
    parent_id = uuid.uuid4()
    child_id = uuid.uuid4()
    parent = _plan(parent_id, plan_kind=PlanKind.GOAL, name="parent")
    parent.goal_plan = GoalPlan(plan_id=parent_id)
    child = _plan(child_id, plan_kind=PlanKind.TASK, parent_id=parent_id, name="child")
    _attach_task(child)

    preview = compute_deletion_impact(PlanID(parent_id), (parent, child), ())

    assert set(preview.affected_plan_ids) == {PlanID(parent_id), PlanID(child_id)}


def test_compute_deletion_impact_expands_chain_members() -> None:
    goal_id = uuid.uuid4()
    task_a_id = uuid.uuid4()
    task_b_id = uuid.uuid4()
    chain_id = uuid.uuid4()
    goal = _plan(goal_id, plan_kind=PlanKind.GOAL, name="goal")
    task_a = _plan(task_a_id, plan_kind=PlanKind.TASK, parent_id=goal_id, name="a")
    task_b = _plan(task_b_id, plan_kind=PlanKind.TASK, parent_id=goal_id, name="b")
    _attach_task(task_a)
    _attach_task(task_b)
    _attach_goal_with_chain(
        goal,
        chain_id=chain_id,
        is_critical=False,
        sort_order=0,
        members=((task_a_id, 0), (task_b_id, 1)),
    )

    preview = compute_deletion_impact(PlanID(task_a_id), (goal, task_a, task_b), ())

    assert set(preview.affected_plan_ids) == {
        PlanID(task_a_id),
        PlanID(task_b_id),
    }


def test_compute_deletion_impact_includes_critical_chain_parent() -> None:
    goal_id = uuid.uuid4()
    task_a_id = uuid.uuid4()
    task_b_id = uuid.uuid4()
    chain_id = uuid.uuid4()
    goal = _plan(goal_id, plan_kind=PlanKind.GOAL, name="goal")
    task_a = _plan(task_a_id, plan_kind=PlanKind.TASK, parent_id=goal_id, name="a")
    task_b = _plan(task_b_id, plan_kind=PlanKind.TASK, parent_id=goal_id, name="b")
    _attach_task(task_a)
    _attach_task(task_b)
    _attach_goal_with_chain(
        goal,
        chain_id=chain_id,
        is_critical=True,
        sort_order=0,
        members=((task_a_id, 0), (task_b_id, 1)),
    )

    preview = compute_deletion_impact(PlanID(task_a_id), (goal, task_a, task_b), ())

    assert set(preview.affected_plan_ids) == {
        PlanID(goal_id),
        PlanID(task_a_id),
        PlanID(task_b_id),
    }


def test_compute_deletion_impact_collects_calendar_entries() -> None:
    task_id = uuid.uuid4()
    entry_id = uuid.uuid4()
    task = _plan(task_id, plan_kind=PlanKind.TASK)
    _attach_task(task)
    entry = _calendar_entry(entry_id, task_id)

    preview = compute_deletion_impact(
        PlanID(task_id),
        (task,),
        (entry,),
    )

    assert preview.affected_calendar_entry_ids == (CalendarEntryID(entry_id),)


def test_compute_deletion_impact_returns_sorted_ids() -> None:
    low_id = uuid.UUID("00000000-0000-4000-8000-000000000001")
    high_id = uuid.UUID("00000000-0000-4000-8000-000000000002")
    low = _plan(low_id, plan_kind=PlanKind.TASK)
    high = _plan(high_id, plan_kind=PlanKind.TASK, parent_id=low_id)
    _attach_task(low)
    _attach_task(high)

    preview = compute_deletion_impact(PlanID(low_id), (low, high), ())

    assert preview.affected_plan_ids == (PlanID(low_id), PlanID(high_id))


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
        generated_at=_NOW,
    )
    plan.repetition_plan = repetition
    return repetition


def _goal_template_repetition_graph() -> tuple[Plan, ...]:
    master_id = uuid.uuid4()
    repetition_id = uuid.uuid4()
    template_id = uuid.uuid4()
    template_task_id = uuid.uuid4()
    clone_id = uuid.uuid4()
    clone_task_id = uuid.uuid4()

    master = _plan(master_id, plan_kind=PlanKind.GOAL, name="master", is_master=True)
    master.goal_plan = GoalPlan(plan_id=master_id)

    repetition = _plan(
        repetition_id,
        plan_kind=PlanKind.REPETITION,
        parent_id=master_id,
        name="repetition",
    )
    _attach_repetition(repetition, template_id)

    template = _plan(
        template_id,
        plan_kind=PlanKind.GOAL,
        parent_id=repetition_id,
        clone_status=CloneStatus.TEMPLATE,
        name="template",
    )
    template.goal_plan = GoalPlan(plan_id=template_id)
    template_task = _plan(
        template_task_id,
        plan_kind=PlanKind.TASK,
        parent_id=template_id,
        clone_status=CloneStatus.TEMPLATE,
        name="template task",
    )
    _attach_task(template_task)

    clone = _plan(
        clone_id,
        plan_kind=PlanKind.GOAL,
        parent_id=repetition_id,
        cloned_from_id=template_id,
        clone_status=CloneStatus.LINKED,
        name="clone",
    )
    clone.goal_plan = GoalPlan(plan_id=clone_id)
    clone_task = _plan(
        clone_task_id,
        plan_kind=PlanKind.TASK,
        parent_id=clone_id,
        cloned_from_id=template_task_id,
        clone_status=CloneStatus.LINKED,
        name="clone task",
    )
    _attach_task(clone_task)

    return (master, repetition, template, template_task, clone, clone_task)


def _task_template_repetition_graph() -> tuple[Plan, ...]:
    master_id = uuid.uuid4()
    repetition_id = uuid.uuid4()
    template_id = uuid.uuid4()
    clone_id = uuid.uuid4()

    master = _plan(master_id, plan_kind=PlanKind.GOAL, name="master", is_master=True)
    master.goal_plan = GoalPlan(plan_id=master_id)

    repetition = _plan(
        repetition_id,
        plan_kind=PlanKind.REPETITION,
        parent_id=master_id,
        name="repetition",
    )
    _attach_repetition(repetition, template_id)

    template = _plan(
        template_id,
        plan_kind=PlanKind.TASK,
        parent_id=repetition_id,
        clone_status=CloneStatus.TEMPLATE,
        name="template task",
    )
    _attach_task(template)

    clone = _plan(
        clone_id,
        plan_kind=PlanKind.TASK,
        parent_id=repetition_id,
        cloned_from_id=template_id,
        clone_status=CloneStatus.LINKED,
        name="clone task",
    )
    _attach_task(clone)

    return (master, repetition, template, clone)


def _two_instance_one_detached_graph() -> tuple[Plan, ...]:
    master_id = uuid.uuid4()
    repetition_id = uuid.uuid4()
    template_goal_id = uuid.uuid4()
    template_task_id = uuid.uuid4()
    clone_goal_0_id = uuid.uuid4()
    detached_task_id = uuid.uuid4()
    clone_goal_1_id = uuid.uuid4()
    linked_task_id = uuid.uuid4()

    master = _plan(master_id, plan_kind=PlanKind.GOAL, name="master", is_master=True)
    master.goal_plan = GoalPlan(plan_id=master_id)

    repetition = _plan(
        repetition_id,
        plan_kind=PlanKind.REPETITION,
        parent_id=master_id,
        name="repetition",
    )
    _attach_repetition(repetition, template_goal_id)

    template_goal = _plan(
        template_goal_id,
        plan_kind=PlanKind.GOAL,
        parent_id=repetition_id,
        clone_status=CloneStatus.TEMPLATE,
        name="template goal",
    )
    template_goal.goal_plan = GoalPlan(plan_id=template_goal_id)
    template_task = _plan(
        template_task_id,
        plan_kind=PlanKind.TASK,
        parent_id=template_goal_id,
        clone_status=CloneStatus.TEMPLATE,
        name="template task",
    )
    _attach_task(template_task)

    clone_goal_0 = _plan(
        clone_goal_0_id,
        plan_kind=PlanKind.GOAL,
        parent_id=repetition_id,
        cloned_from_id=template_goal_id,
        clone_status=CloneStatus.LINKED,
        name="clone goal 0",
    )
    clone_goal_0.goal_plan = GoalPlan(plan_id=clone_goal_0_id)
    detached_task = _plan(
        detached_task_id,
        plan_kind=PlanKind.TASK,
        parent_id=clone_goal_0_id,
        cloned_from_id=template_task_id,
        clone_status=CloneStatus.DETACHED,
        name="detached task",
    )
    _attach_task(detached_task)

    clone_goal_1 = _plan(
        clone_goal_1_id,
        plan_kind=PlanKind.GOAL,
        parent_id=repetition_id,
        cloned_from_id=template_goal_id,
        clone_status=CloneStatus.LINKED,
        name="clone goal 1",
    )
    clone_goal_1.goal_plan = GoalPlan(plan_id=clone_goal_1_id)
    linked_task = _plan(
        linked_task_id,
        plan_kind=PlanKind.TASK,
        parent_id=clone_goal_1_id,
        cloned_from_id=template_task_id,
        clone_status=CloneStatus.LINKED,
        name="linked task",
    )
    _attach_task(linked_task)

    return (
        master,
        repetition,
        template_goal,
        template_task,
        clone_goal_0,
        detached_task,
        clone_goal_1,
        linked_task,
    )


def test_compute_deletion_impact_detached_clone_stays_local() -> None:
    plans = _two_instance_one_detached_graph()
    detached_task_id = next(plan.plan_id for plan in plans if plan.name == "detached task")
    linked_task_id = next(plan.plan_id for plan in plans if plan.name == "linked task")
    repetition_id = next(plan.plan_id for plan in plans if plan.name == "repetition")
    template_task_id = next(plan.plan_id for plan in plans if plan.name == "template task")

    preview = compute_deletion_impact(PlanID(detached_task_id), plans, ())

    assert set(preview.affected_plan_ids) == {PlanID(detached_task_id)}
    assert PlanID(repetition_id) not in preview.affected_plan_ids
    assert PlanID(template_task_id) not in preview.affected_plan_ids
    assert PlanID(linked_task_id) not in preview.affected_plan_ids

    linked_preview = compute_deletion_impact(PlanID(linked_task_id), plans, ())
    assert PlanID(detached_task_id) not in linked_preview.affected_plan_ids


def test_compute_deletion_impact_template_root_includes_repetition_shell_and_clones() -> None:
    plans = _goal_template_repetition_graph()
    template_id = next(plan.plan_id for plan in plans if plan.name == "template")

    preview = compute_deletion_impact(PlanID(template_id), plans, ())

    affected = set(preview.affected_plan_ids)
    assert PlanID(next(p.plan_id for p in plans if p.name == "repetition")) in affected
    assert PlanID(next(p.plan_id for p in plans if p.name == "clone")) in affected
    assert PlanID(next(p.plan_id for p in plans if p.name == "clone task")) in affected


def test_compute_deletion_impact_task_template_root_includes_repetition_shell() -> None:
    plans = _task_template_repetition_graph()
    template_id = next(plan.plan_id for plan in plans if plan.name == "template task")

    preview = compute_deletion_impact(PlanID(template_id), plans, ())

    affected = set(preview.affected_plan_ids)
    assert PlanID(next(p.plan_id for p in plans if p.name == "repetition")) in affected
    assert PlanID(next(p.plan_id for p in plans if p.name == "clone task")) in affected


def test_compute_deletion_impact_critical_chain_indirectly_reaches_template_subtree() -> None:
    master_id = uuid.uuid4()
    goal_id = uuid.uuid4()
    repetition_id = uuid.uuid4()
    template_id = uuid.uuid4()
    task_a_id = uuid.uuid4()
    task_b_id = uuid.uuid4()
    clone_id = uuid.uuid4()
    chain_id = uuid.uuid4()

    master = _plan(master_id, plan_kind=PlanKind.GOAL, name="master", is_master=True)
    goal = _plan(goal_id, plan_kind=PlanKind.GOAL, parent_id=master_id, name="goal")
    task_a = _plan(task_a_id, plan_kind=PlanKind.TASK, parent_id=goal_id, name="a")
    task_b = _plan(task_b_id, plan_kind=PlanKind.TASK, parent_id=goal_id, name="b")
    _attach_task(task_a)
    _attach_task(task_b)
    _attach_goal_with_chain(
        goal,
        chain_id=chain_id,
        is_critical=True,
        sort_order=0,
        members=((task_a_id, 0), (task_b_id, 1)),
    )

    repetition = _plan(
        repetition_id,
        plan_kind=PlanKind.REPETITION,
        parent_id=goal_id,
        name="repetition",
    )
    _attach_repetition(repetition, template_id)
    template = _plan(
        template_id,
        plan_kind=PlanKind.TASK,
        parent_id=repetition_id,
        clone_status=CloneStatus.TEMPLATE,
        name="template task",
    )
    _attach_task(template)
    clone = _plan(
        clone_id,
        plan_kind=PlanKind.TASK,
        parent_id=repetition_id,
        cloned_from_id=template_id,
        clone_status=CloneStatus.LINKED,
        name="clone task",
    )
    _attach_task(clone)

    plans = (master, goal, task_a, task_b, repetition, template, clone)
    preview = compute_deletion_impact(PlanID(task_a_id), plans, ())

    affected = set(preview.affected_plan_ids)
    assert PlanID(goal_id) in affected
    assert PlanID(repetition_id) in affected
    assert PlanID(template_id) in affected
    assert PlanID(clone_id) in affected


def test_build_deletion_preview_populates_depth_counts_and_task_ids() -> None:
    master_id = uuid.uuid4()
    goal_id = uuid.uuid4()
    task_id = uuid.uuid4()

    master = _plan(master_id, plan_kind=PlanKind.GOAL, name="master", is_master=True)
    master.goal_plan = GoalPlan(plan_id=master_id)
    goal = _plan(goal_id, plan_kind=PlanKind.GOAL, parent_id=master_id, name="goal")
    goal.goal_plan = GoalPlan(plan_id=goal_id)
    task = _plan(task_id, plan_kind=PlanKind.TASK, parent_id=goal_id, name="task")
    _attach_task(task)

    preview = build_deletion_preview(
        DeletionOperation(root_plan_id=PlanID(task_id)),
        (master, goal, task),
        (),
    )

    assert preview.affected_task_ids == (PlanID(task_id),)
    assert preview.affected_depth_counts_from_master == (0, 0, 1)
    assert preview.legal_operation == DeletionOperation(root_plan_id=PlanID(task_id))

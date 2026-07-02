"""Pure tests for compute_deletion_impact (no database)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from calendar_backend.domain.deletion import compute_deletion_impact
from calendar_backend.domain.enums import CalendarEntryType, CloneStatus, PlanKind
from calendar_backend.domain.ids import CalendarEntryID, PlanID
from calendar_backend.models.calendar import CalendarEntry
from calendar_backend.models.chains import GoalChildChain, GoalChildChainItem
from calendar_backend.models.plans import GoalPlan, Plan, TaskPlan

_NOW = datetime(2026, 6, 7, 12, 0, tzinfo=UTC)


def _plan(
    plan_id: uuid.UUID,
    *,
    plan_kind: PlanKind,
    parent_id: uuid.UUID | None = None,
    name: str = "plan",
) -> Plan:
    return Plan(
        plan_id=plan_id,
        plan_kind=plan_kind,
        name=name,
        parent_id=parent_id,
        is_master=False,
        cloned_from_id=None,
        clone_status=CloneStatus.NOT_CLONED,
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

"""Pure tests for plan prerequisite validation helpers."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from calendar_backend.domain.enums import CloneStatus, PlanKind
from calendar_backend.domain.ids import PlanID
from calendar_backend.domain.prerequisites import (
    is_plan_subtree_complete,
    leaf_task_ids_in_subtree,
    transitive_plan_prerequisite_pairs,
    would_create_prerequisite_cycle,
)
from calendar_backend.models.blocks import BlockPlan
from calendar_backend.models.plans import GoalPlan, Plan, TaskPlan

_NOW = datetime(2026, 6, 7, 12, 0, tzinfo=UTC)


def _plan_id(label: str) -> PlanID:
    return PlanID(uuid.uuid5(uuid.NAMESPACE_DNS, label))


def _task_plan(plan_id: PlanID, *, user_completed: bool = False) -> Plan:
    plan = Plan(
        plan_id=plan_id,
        plan_kind=PlanKind.TASK,
        name=str(plan_id),
        parent_id=None,
        is_master=False,
        cloned_from_id=None,
        clone_status=CloneStatus.NOT_CLONED,
        created_at=_NOW,
        updated_at=_NOW,
    )
    plan.task_plan = TaskPlan(
        plan_id=plan_id,
        duration_minutes=30,
        divisible=False,
        minimum_chunk_size_minutes=None,
        user_completed=user_completed,
        completed_at=_NOW if user_completed else None,
    )
    return plan


def _block_plan(plan_id: PlanID, *, user_completed: bool = False) -> Plan:
    plan = Plan(
        plan_id=plan_id,
        plan_kind=PlanKind.BLOCK,
        name=str(plan_id),
        parent_id=None,
        is_master=False,
        cloned_from_id=None,
        clone_status=CloneStatus.NOT_CLONED,
        created_at=_NOW,
        updated_at=_NOW,
    )
    plan.block_plan = BlockPlan(
        plan_id=plan_id,
        duration_minutes=30,
        divisible=False,
        minimum_chunk_size_minutes=None,
        user_completed=user_completed,
        completed_at=_NOW if user_completed else None,
        block_family="focus",
        immediate_prerequisite_plan_id=None,
    )
    return plan


def _goal_plan(plan_id: PlanID, *, children: tuple[Plan, ...] = ()) -> Plan:
    plan = Plan(
        plan_id=plan_id,
        plan_kind=PlanKind.GOAL,
        name=str(plan_id),
        parent_id=None,
        is_master=False,
        cloned_from_id=None,
        clone_status=CloneStatus.NOT_CLONED,
        created_at=_NOW,
        updated_at=_NOW,
    )
    plan.goal_plan = GoalPlan(plan_id=plan_id)
    for index, child in enumerate(children):
        child.parent_id = plan_id
        child.goal_is_critical = False
        child.goal_sort_order = index
    return plan


def test_would_create_prerequisite_cycle_detects_self_edge() -> None:
    plan_id = _plan_id("self")
    assert (
        would_create_prerequisite_cycle(
            (),
            dependent_id=plan_id,
            prerequisite_id=plan_id,
        )
        is True
    )


def test_would_create_prerequisite_cycle_detects_three_cycle() -> None:
    plan_a = _plan_id("a")
    plan_b = _plan_id("b")
    plan_c = _plan_id("c")
    existing = (
        (plan_a, plan_b),
        (plan_b, plan_c),
    )
    assert (
        would_create_prerequisite_cycle(
            existing,
            dependent_id=plan_c,
            prerequisite_id=plan_a,
        )
        is True
    )


def test_would_create_prerequisite_cycle_allows_dag_extension() -> None:
    plan_a = _plan_id("a")
    plan_b = _plan_id("b")
    plan_c = _plan_id("c")
    existing = ((plan_a, plan_b),)
    assert (
        would_create_prerequisite_cycle(
            existing,
            dependent_id=plan_c,
            prerequisite_id=plan_a,
        )
        is False
    )


def test_is_plan_subtree_complete_true_when_all_task_leaves_completed() -> None:
    task_a = _task_plan(_plan_id("task-a"), user_completed=True)
    task_b = _task_plan(_plan_id("task-b"), user_completed=True)
    goal = _goal_plan(_plan_id("goal"), children=(task_a, task_b))
    plans_by_id = {plan.plan_id: plan for plan in (goal, task_a, task_b)}

    assert (
        is_plan_subtree_complete(
            _plan_id("goal"),
            plans_by_id=plans_by_id,
            template_subtree_ids=frozenset(),
        )
        is True
    )


def test_is_plan_subtree_complete_false_when_any_task_leaf_incomplete() -> None:
    task_a = _task_plan(_plan_id("task-a"), user_completed=True)
    task_b = _task_plan(_plan_id("task-b"), user_completed=False)
    goal = _goal_plan(_plan_id("goal"), children=(task_a, task_b))
    plans_by_id = {plan.plan_id: plan for plan in (goal, task_a, task_b)}

    assert (
        is_plan_subtree_complete(
            _plan_id("goal"),
            plans_by_id=plans_by_id,
            template_subtree_ids=frozenset(),
        )
        is False
    )


def test_leaf_task_ids_in_subtree_collects_nested_goal_tasks() -> None:
    nested_task = _task_plan(_plan_id("nested-task"))
    nested_goal = _goal_plan(_plan_id("nested-goal"), children=(nested_task,))
    root_task = _task_plan(_plan_id("root-task"))
    root = _goal_plan(_plan_id("root"), children=(root_task, nested_goal))
    plans_by_id = {plan.plan_id: plan for plan in (root, root_task, nested_goal, nested_task)}

    leaves = leaf_task_ids_in_subtree(
        _plan_id("root"),
        plans_by_id=plans_by_id,
        template_subtree_ids=frozenset(),
    )

    assert leaves == {_plan_id("root-task"), _plan_id("nested-task")}


def test_is_plan_subtree_complete_false_when_block_leaf_incomplete() -> None:
    task = _task_plan(_plan_id("task"), user_completed=True)
    block = _block_plan(_plan_id("block"), user_completed=False)
    goal = _goal_plan(_plan_id("goal"), children=(task, block))
    plans_by_id = {plan.plan_id: plan for plan in (goal, task, block)}

    assert (
        is_plan_subtree_complete(
            _plan_id("goal"),
            plans_by_id=plans_by_id,
            template_subtree_ids=frozenset(),
        )
        is False
    )


def test_leaf_task_ids_in_subtree_includes_block_leaves() -> None:
    block = _block_plan(_plan_id("block"))
    goal = _goal_plan(_plan_id("goal"), children=(block,))
    plans_by_id = {plan.plan_id: plan for plan in (goal, block)}

    leaves = leaf_task_ids_in_subtree(
        _plan_id("goal"),
        plans_by_id=plans_by_id,
        template_subtree_ids=frozenset(),
    )

    assert leaves == {_plan_id("block")}


def test_transitive_plan_prerequisite_pairs_includes_indirect_dependencies() -> None:
    plan_a = _plan_id("a")
    plan_b = _plan_id("b")
    plan_c = _plan_id("c")
    direct = ((plan_b, plan_a), (plan_c, plan_b))

    assert transitive_plan_prerequisite_pairs(direct) == (
        (plan_b, plan_a),
        (plan_c, plan_b),
        (plan_c, plan_a),
    )

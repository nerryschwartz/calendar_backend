"""Pure tests for plan traversal ordering helpers."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import calendar_backend.models.constraints  # noqa: F401  # pyright: ignore[reportUnusedImport]
from calendar_backend.domain.enums import CloneStatus, PlanKind
from calendar_backend.domain.plan_traversal import (
    ordered_chains,
    ordered_goal_children,
    sorted_chain_items,
)
from calendar_backend.models.chains import GoalChildChain, GoalChildChainItem
from calendar_backend.models.plans import GoalPlan, Plan

_NOW = datetime(2026, 6, 7, 12, 0, tzinfo=UTC)


def _plan(
    plan_id: uuid.UUID,
    *,
    parent_id: uuid.UUID | None = None,
    goal_is_critical: bool | None = None,
    goal_sort_order: int | None = None,
) -> Plan:
    return Plan(
        plan_id=plan_id,
        plan_kind=PlanKind.TASK,
        name="child",
        parent_id=parent_id,
        is_master=False,
        cloned_from_id=None,
        clone_status=CloneStatus.NOT_CLONED,
        goal_is_critical=goal_is_critical,
        goal_sort_order=goal_sort_order,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _chain_walk_child_ids(goal_plan: GoalPlan) -> tuple[uuid.UUID, ...]:
    child_ids: list[uuid.UUID] = []
    for chain in ordered_chains(goal_plan):
        for item in sorted_chain_items(chain):
            child_ids.append(item.child_plan_id)
    return tuple(child_ids)


def test_ordered_goal_children_critical_before_non_critical() -> None:
    master_id = uuid.uuid4()
    critical_id = uuid.uuid4()
    non_critical_id = uuid.uuid4()
    master = Plan(
        plan_id=master_id,
        plan_kind=PlanKind.GOAL,
        name="master",
        parent_id=None,
        is_master=True,
        cloned_from_id=None,
        clone_status=CloneStatus.NOT_CLONED,
        goal_is_critical=None,
        goal_sort_order=None,
        created_at=_NOW,
        updated_at=_NOW,
    )
    master.goal_plan = GoalPlan(plan_id=master_id)
    critical = _plan(critical_id, parent_id=master_id, goal_is_critical=True, goal_sort_order=0)
    non_critical = _plan(
        non_critical_id,
        parent_id=master_id,
        goal_is_critical=False,
        goal_sort_order=0,
    )
    master.children = [non_critical, critical]

    ordered = ordered_goal_children(master)

    assert [plan.plan_id for plan in ordered] == [critical_id, non_critical_id]


def test_ordered_goal_children_dense_sort_within_bucket() -> None:
    master_id = uuid.uuid4()
    first_id = uuid.uuid4()
    second_id = uuid.uuid4()
    master = Plan(
        plan_id=master_id,
        plan_kind=PlanKind.GOAL,
        name="master",
        parent_id=None,
        is_master=True,
        cloned_from_id=None,
        clone_status=CloneStatus.NOT_CLONED,
        goal_is_critical=None,
        goal_sort_order=None,
        created_at=_NOW,
        updated_at=_NOW,
    )
    master.goal_plan = GoalPlan(plan_id=master_id)
    second = _plan(second_id, parent_id=master_id, goal_is_critical=False, goal_sort_order=1)
    first = _plan(first_id, parent_id=master_id, goal_is_critical=False, goal_sort_order=0)
    master.children = [second, first]

    ordered = ordered_goal_children(master)

    assert [plan.plan_id for plan in ordered] == [first_id, second_id]


def test_ordered_goal_children_skips_children_without_ordering_fields() -> None:
    master_id = uuid.uuid4()
    ordered_id = uuid.uuid4()
    template_id = uuid.uuid4()
    master = Plan(
        plan_id=master_id,
        plan_kind=PlanKind.GOAL,
        name="master",
        parent_id=None,
        is_master=True,
        cloned_from_id=None,
        clone_status=CloneStatus.NOT_CLONED,
        goal_is_critical=None,
        goal_sort_order=None,
        created_at=_NOW,
        updated_at=_NOW,
    )
    master.goal_plan = GoalPlan(plan_id=master_id)
    ordered_child = _plan(
        ordered_id,
        parent_id=master_id,
        goal_is_critical=False,
        goal_sort_order=0,
    )
    template_root = _plan(template_id, parent_id=master_id)
    master.children = [template_root, ordered_child]

    assert ordered_goal_children(master) == (ordered_child,)


def test_ordered_goal_children_matches_chain_walk_when_flat_fields_present() -> None:
    master_id = uuid.uuid4()
    child_a_id = uuid.uuid4()
    child_b_id = uuid.uuid4()
    child_c_id = uuid.uuid4()
    chain_a_id = uuid.uuid4()
    chain_b_id = uuid.uuid4()
    now = _NOW

    child_a = _plan(child_a_id, parent_id=master_id, goal_is_critical=True, goal_sort_order=0)
    child_b = _plan(child_b_id, parent_id=master_id, goal_is_critical=True, goal_sort_order=1)
    child_c = _plan(child_c_id, parent_id=master_id, goal_is_critical=False, goal_sort_order=0)

    master = Plan(
        plan_id=master_id,
        plan_kind=PlanKind.GOAL,
        name="master",
        parent_id=None,
        is_master=True,
        cloned_from_id=None,
        clone_status=CloneStatus.NOT_CLONED,
        goal_is_critical=None,
        goal_sort_order=None,
        created_at=now,
        updated_at=now,
    )
    goal_plan = GoalPlan(plan_id=master_id)
    master.goal_plan = goal_plan
    master.children = [child_c, child_b, child_a]

    chain_a = GoalChildChain(
        goal_child_chain_id=chain_a_id,
        parent_goal_id=master_id,
        is_critical=True,
        sort_order=0,
        created_at=now,
        updated_at=now,
        items=[
            GoalChildChainItem(
                goal_child_chain_item_id=uuid.uuid4(),
                chain_id=chain_a_id,
                child_plan_id=child_a_id,
                position=0,
            ),
            GoalChildChainItem(
                goal_child_chain_item_id=uuid.uuid4(),
                chain_id=chain_a_id,
                child_plan_id=child_b_id,
                position=1,
            ),
        ],
    )
    chain_b = GoalChildChain(
        goal_child_chain_id=chain_b_id,
        parent_goal_id=master_id,
        is_critical=False,
        sort_order=0,
        created_at=now,
        updated_at=now,
        items=[
            GoalChildChainItem(
                goal_child_chain_item_id=uuid.uuid4(),
                chain_id=chain_b_id,
                child_plan_id=child_c_id,
                position=0,
            ),
        ],
    )
    goal_plan.chains = [chain_b, chain_a]
    for item in chain_a.items:
        item.child_plan = child_a if item.child_plan_id == child_a_id else child_b
    chain_b.items[0].child_plan = child_c

    chain_order = _chain_walk_child_ids(goal_plan)
    flat_order = tuple(plan.plan_id for plan in ordered_goal_children(master))

    assert flat_order == chain_order

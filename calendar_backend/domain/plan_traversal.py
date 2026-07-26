"""Shared plan-tree traversal ordering helpers."""

from __future__ import annotations

import uuid
from collections import deque

from calendar_backend.models.plans import Plan, RepetitionPlan
from calendar_backend.models.repetitions import RepetitionInstance


def collect_descendant_ids(
    root_id: uuid.UUID,
    children_by_parent: dict[uuid.UUID, list[uuid.UUID]],
    *,
    include_root: bool,
) -> set[uuid.UUID]:
    collected: set[uuid.UUID] = set()
    queue: deque[uuid.UUID] = deque([root_id])
    while queue:
        plan_id = queue.popleft()
        if plan_id in collected:
            continue
        collected.add(plan_id)
        queue.extend(children_by_parent.get(plan_id, ()))
    if not include_root:
        collected.discard(root_id)
    return collected


def _goal_child_sort_key(plan: Plan) -> tuple[bool, int, str]:
    assert plan.goal_is_critical is not None
    assert plan.goal_sort_order is not None
    return (not plan.goal_is_critical, plan.goal_sort_order, str(plan.plan_id))


def direct_goal_children(parent: Plan) -> tuple[Plan, ...]:
    return tuple(
        child
        for child in parent.children
        if child.goal_is_critical is not None and child.goal_sort_order is not None
    )


def ordered_goal_children(
    parent: Plan,
    *,
    children: tuple[Plan, ...] | None = None,
) -> tuple[Plan, ...]:
    candidates = direct_goal_children(parent) if children is None else children
    ordered = [
        child
        for child in candidates
        if child.goal_is_critical is not None and child.goal_sort_order is not None
    ]
    return tuple(sorted(ordered, key=_goal_child_sort_key))


def ordered_repetition_instances(
    repetition_plan: RepetitionPlan,
) -> tuple[RepetitionInstance, ...]:
    return tuple(
        sorted(
            repetition_plan.instances,
            key=lambda instance: (
                not instance.is_critical,
                instance.sort_order,
                str(instance.repetition_instance_id),
            ),
        )
    )

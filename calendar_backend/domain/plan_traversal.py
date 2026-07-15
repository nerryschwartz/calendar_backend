"""Shared plan-tree traversal ordering helpers."""

from __future__ import annotations

import uuid
from collections import deque

from calendar_backend.models.chains import GoalChildChain, GoalChildChainItem
from calendar_backend.models.plans import GoalPlan, RepetitionPlan
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


def ordered_chains(goal_plan: GoalPlan) -> tuple[GoalChildChain, ...]:
    return tuple(
        sorted(
            goal_plan.chains,
            key=lambda chain: (
                not chain.is_critical,
                chain.sort_order,
                str(chain.goal_child_chain_id),
            ),
        )
    )


def sorted_chain_items(chain: GoalChildChain) -> tuple[GoalChildChainItem, ...]:
    return tuple(
        sorted(
            chain.items,
            key=lambda item: (item.position, str(item.goal_child_chain_item_id)),
        )
    )


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

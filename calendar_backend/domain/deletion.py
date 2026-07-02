"""Session-free deletion impact computation over loaded plan graphs."""

from __future__ import annotations

import uuid
from collections import defaultdict

from calendar_backend.domain.dtos import PlanDeletionPreviewDTO
from calendar_backend.domain.ids import CalendarEntryID, PlanID
from calendar_backend.models.calendar import CalendarEntry
from calendar_backend.models.plans import Plan


def compute_deletion_impact(
    root_plan_id: PlanID,
    plans: tuple[Plan, ...],
    calendar_entries: tuple[CalendarEntry, ...],
) -> PlanDeletionPreviewDTO:
    children_by_parent, chain_members_by_child, critical_chains = _deletion_indexes(plans)

    affected: set[uuid.UUID] = {root_plan_id}
    changed = True
    while changed:
        changed = False
        if _expand_chain_members(affected, chain_members_by_child):
            changed = True
        if _expand_descendants(affected, children_by_parent):
            changed = True
        if _expand_critical_chain_parents(affected, critical_chains):
            changed = True

    affected_plan_ids = tuple(sorted(PlanID(plan_id) for plan_id in affected))
    affected_calendar_entry_ids = tuple(
        sorted(
            CalendarEntryID(entry.calendar_entry_id)
            for entry in calendar_entries
            if entry.source_plan_id in affected
        )
    )
    return PlanDeletionPreviewDTO(
        root_plan_id=root_plan_id,
        affected_plan_ids=affected_plan_ids,
        affected_calendar_entry_ids=affected_calendar_entry_ids,
    )


def _deletion_indexes(
    plans: tuple[Plan, ...],
) -> tuple[
    dict[uuid.UUID, list[uuid.UUID]],
    dict[uuid.UUID, frozenset[uuid.UUID]],
    list[tuple[frozenset[uuid.UUID], uuid.UUID]],
]:
    children_by_parent: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    for plan in plans:
        if plan.parent_id is not None:
            children_by_parent[plan.parent_id].append(plan.plan_id)

    chain_members_by_child: dict[uuid.UUID, frozenset[uuid.UUID]] = {}
    critical_chains: list[tuple[frozenset[uuid.UUID], uuid.UUID]] = []
    for plan in plans:
        if plan.goal_plan is None:
            continue
        for chain in plan.goal_plan.chains:
            members = frozenset(item.child_plan_id for item in chain.items)
            for child_plan_id in members:
                chain_members_by_child[child_plan_id] = members
            if chain.is_critical:
                critical_chains.append((members, chain.parent_goal_id))

    return children_by_parent, chain_members_by_child, critical_chains


def _expand_chain_members(
    affected: set[uuid.UUID],
    chain_members_by_child: dict[uuid.UUID, frozenset[uuid.UUID]],
) -> bool:
    changed = False
    for plan_id in list(affected):
        members = chain_members_by_child.get(plan_id)
        if members is None or members.issubset(affected):
            continue
        affected |= members
        changed = True
    return changed


def _expand_descendants(
    affected: set[uuid.UUID],
    children_by_parent: dict[uuid.UUID, list[uuid.UUID]],
) -> bool:
    changed = False
    stack = list(affected)
    while stack:
        parent_id = stack.pop()
        for child_id in children_by_parent.get(parent_id, []):
            if child_id not in affected:
                affected.add(child_id)
                changed = True
                stack.append(child_id)
    return changed


def _expand_critical_chain_parents(
    affected: set[uuid.UUID],
    critical_chains: list[tuple[frozenset[uuid.UUID], uuid.UUID]],
) -> bool:
    changed = False
    for members, parent_goal_id in critical_chains:
        if members.issubset(affected) and parent_goal_id not in affected:
            affected.add(parent_goal_id)
            changed = True
    return changed

"""Session-free deletion impact computation over loaded plan graphs."""

from __future__ import annotations

import uuid
from collections import defaultdict, deque
from dataclasses import dataclass

from calendar_backend.domain.dtos import PlanDeletionPreviewDTO
from calendar_backend.domain.enums import PlanKind
from calendar_backend.domain.ids import CalendarEntryID, PlanID
from calendar_backend.models.calendar import CalendarEntry
from calendar_backend.models.plans import Plan

# TODO(Prompt 14 / ConflictAnalysisService): extend AssignmentConflict with
# unschedulable_task_ids, blocking_constraint_ids, and other analyzed conflict metadata.


@dataclass(frozen=True)
class DeletionOperation:
    root_plan_id: PlanID


@dataclass(frozen=True)
class DeletionPreview:
    root_plan_id: PlanID
    legal_operation: DeletionOperation
    affected_plan_ids: tuple[PlanID, ...]
    affected_task_ids: tuple[PlanID, ...]
    affected_calendar_entry_ids: tuple[CalendarEntryID, ...]
    affected_depth_counts_from_master: tuple[int, ...]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class AssignmentConflict:
    conflicting_plan_ids: tuple[PlanID, ...]
    affected_priority_by_plan_id: tuple[tuple[PlanID, int], ...] = ()


@dataclass(frozen=True)
class DeletionCandidate:
    legal_operation: DeletionOperation
    deletion_preview: DeletionPreview
    ranking_keys: tuple[int, ...]
    explanation: str


def compute_deletion_impact(
    root_plan_id: PlanID,
    plans: tuple[Plan, ...],
    calendar_entries: tuple[CalendarEntry, ...],
) -> PlanDeletionPreviewDTO:
    (
        children_by_parent,
        chain_members_by_child,
        critical_chains,
        template_root_to_repetition,
    ) = _deletion_indexes(plans)

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
        if _expand_repetition_shells(affected, template_root_to_repetition):
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


def affected_task_ids_from_plans(
    plans: tuple[Plan, ...],
    affected_plan_ids: tuple[PlanID, ...],
) -> tuple[PlanID, ...]:
    plans_by_id = {plan.plan_id: plan for plan in plans}
    return tuple(
        sorted(
            plan_id
            for plan_id in affected_plan_ids
            if (plan := plans_by_id.get(plan_id)) is not None and plan.plan_kind == PlanKind.TASK
        )
    )


def compute_affected_depth_counts_from_master(
    plans: tuple[Plan, ...],
    affected_plan_ids: tuple[PlanID, ...],
    master_plan_id: PlanID,
) -> tuple[int, ...]:
    children_by_parent: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    for plan in plans:
        if plan.parent_id is not None:
            children_by_parent[plan.parent_id].append(plan.plan_id)

    depth_by_id: dict[uuid.UUID, int] = {master_plan_id: 0}
    queue: deque[uuid.UUID] = deque([master_plan_id])
    while queue:
        parent_id = queue.popleft()
        parent_depth = depth_by_id[parent_id]
        for child_id in children_by_parent.get(parent_id, []):
            if child_id not in depth_by_id:
                depth_by_id[child_id] = parent_depth + 1
                queue.append(child_id)

    counts_by_depth: dict[int, int] = defaultdict(int)
    for plan_id in affected_plan_ids:
        depth = depth_by_id.get(plan_id)
        if depth is not None:
            counts_by_depth[depth] += 1

    if not counts_by_depth:
        return ()

    max_depth = max(counts_by_depth)
    return tuple(counts_by_depth[depth] for depth in range(max_depth + 1))


def build_deletion_preview(
    operation: DeletionOperation,
    plans: tuple[Plan, ...],
    calendar_entries: tuple[CalendarEntry, ...],
) -> DeletionPreview:
    masters = [plan for plan in plans if plan.is_master]
    if len(masters) != 1:
        raise ValueError("deletion preview requires exactly one master plan in loaded graph")
    master_plan_id = PlanID(masters[0].plan_id)

    core = compute_deletion_impact(operation.root_plan_id, plans, calendar_entries)
    return DeletionPreview(
        root_plan_id=core.root_plan_id,
        legal_operation=operation,
        affected_plan_ids=core.affected_plan_ids,
        affected_task_ids=affected_task_ids_from_plans(plans, core.affected_plan_ids),
        affected_calendar_entry_ids=core.affected_calendar_entry_ids,
        affected_depth_counts_from_master=compute_affected_depth_counts_from_master(
            plans,
            core.affected_plan_ids,
            master_plan_id,
        ),
        warnings=core.warnings,
    )


def plan_deletion_preview_dto_from_deletion_preview(
    preview: DeletionPreview,
) -> PlanDeletionPreviewDTO:
    return PlanDeletionPreviewDTO(
        root_plan_id=preview.root_plan_id,
        affected_plan_ids=preview.affected_plan_ids,
        affected_calendar_entry_ids=preview.affected_calendar_entry_ids,
        warnings=preview.warnings,
    )


def _deletion_indexes(
    plans: tuple[Plan, ...],
) -> tuple[
    dict[uuid.UUID, list[uuid.UUID]],
    dict[uuid.UUID, frozenset[uuid.UUID]],
    list[tuple[frozenset[uuid.UUID], uuid.UUID]],
    dict[uuid.UUID, uuid.UUID],
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

    template_root_to_repetition: dict[uuid.UUID, uuid.UUID] = {}
    for plan in plans:
        repetition_plan = plan.repetition_plan
        if repetition_plan is not None:
            template_root_to_repetition[repetition_plan.template_root_id] = plan.plan_id

    return (
        children_by_parent,
        chain_members_by_child,
        critical_chains,
        template_root_to_repetition,
    )


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


def _expand_repetition_shells(
    affected: set[uuid.UUID],
    template_root_to_repetition: dict[uuid.UUID, uuid.UUID],
) -> bool:
    changed = False
    for plan_id in list(affected):
        repetition_plan_id = template_root_to_repetition.get(plan_id)
        if repetition_plan_id is None or repetition_plan_id in affected:
            continue
        affected.add(repetition_plan_id)
        changed = True
    return changed

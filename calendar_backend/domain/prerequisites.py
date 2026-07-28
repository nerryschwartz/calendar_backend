"""Pure plan-prerequisite validation helpers."""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Iterable

from calendar_backend.domain.errors import MessageCode, ServiceMessage
from calendar_backend.domain.ids import PlanID
from calendar_backend.domain.template_trace import compute_template_trace, traces_match
from calendar_backend.models.plans import Plan

PlanPrerequisiteEdge = tuple[PlanID, PlanID]


def plan_prerequisite_edges_from_plans(plans: Iterable[Plan]) -> tuple[PlanPrerequisiteEdge, ...]:
    edges: list[PlanPrerequisiteEdge] = []
    for plan in plans:
        for row in plan.prerequisite_edges:
            edges.append((PlanID(row.plan_id), PlanID(row.prerequisite_plan_id)))
    return tuple(sorted(edges, key=lambda edge: (str(edge[0]), str(edge[1]))))


def validate_plan_prerequisite_link(
    *,
    dependent_id: PlanID,
    prerequisite_id: PlanID,
    existing_edges: tuple[PlanPrerequisiteEdge, ...],
    plans_by_id: dict[uuid.UUID, Plan],
) -> ServiceMessage | None:
    if dependent_id == prerequisite_id:
        return ServiceMessage(
            code=MessageCode.PLAN_PREREQUISITE_SELF_EDGE,
            message="Plan cannot be a prerequisite of itself",
            details={
                "plan_id": str(dependent_id),
                "prerequisite_plan_id": str(prerequisite_id),
            },
        )

    if (dependent_id, prerequisite_id) in existing_edges:
        return ServiceMessage(
            code=MessageCode.DUPLICATE_PLAN_PREREQUISITE,
            message="Plan prerequisite edge already exists",
            details={
                "plan_id": str(dependent_id),
                "prerequisite_plan_id": str(prerequisite_id),
            },
        )

    if plans_by_id.get(dependent_id) is None or plans_by_id.get(prerequisite_id) is None:
        missing_id = dependent_id if plans_by_id.get(dependent_id) is None else prerequisite_id
        return ServiceMessage(
            code=MessageCode.PLAN_NOT_FOUND,
            message="Plan not found",
            details={"plan_id": str(missing_id)},
        )

    dependent_trace = compute_template_trace(dependent_id, plans_by_id)
    prerequisite_trace = compute_template_trace(prerequisite_id, plans_by_id)
    if not traces_match(dependent_trace, prerequisite_trace):
        return ServiceMessage(
            code=MessageCode.PLAN_PREREQUISITE_TRACE_MISMATCH,
            message="Plan prerequisite requires matching template traces",
            details={
                "plan_id": str(dependent_id),
                "prerequisite_plan_id": str(prerequisite_id),
            },
        )

    if would_create_prerequisite_cycle(
        existing_edges,
        dependent_id=dependent_id,
        prerequisite_id=prerequisite_id,
    ):
        return ServiceMessage(
            code=MessageCode.PLAN_PREREQUISITE_CYCLE,
            message="Plan prerequisite would create a cycle",
            details={
                "plan_id": str(dependent_id),
                "prerequisite_plan_id": str(prerequisite_id),
            },
        )

    return None


def would_create_prerequisite_cycle(
    existing_edges: tuple[PlanPrerequisiteEdge, ...],
    *,
    dependent_id: PlanID,
    prerequisite_id: PlanID,
) -> bool:
    if dependent_id == prerequisite_id:
        return True

    depends_on: dict[PlanID, set[PlanID]] = defaultdict(set)
    for dependent, prerequisite in existing_edges:
        depends_on[dependent].add(prerequisite)
    depends_on[dependent_id].add(prerequisite_id)

    visiting: set[PlanID] = set()
    visited: set[PlanID] = set()

    def dfs(node: PlanID) -> bool:
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        for prerequisite in depends_on.get(node, ()):
            if dfs(prerequisite):
                return True
        visiting.remove(node)
        visited.add(node)
        return False

    return dfs(dependent_id)

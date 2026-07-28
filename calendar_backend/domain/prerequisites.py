"""Pure plan-prerequisite validation helpers."""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Iterable

from sqlalchemy import inspect as sa_inspect
from sqlalchemy.exc import NoInspectionAvailable

from calendar_backend.domain.enums import PlanKind
from calendar_backend.domain.errors import MessageCode, ServiceMessage
from calendar_backend.domain.ids import PlanID
from calendar_backend.domain.plan_traversal import (
    ordered_goal_children,
    ordered_repetition_instances,
)
from calendar_backend.domain.template_trace import compute_template_trace, traces_match
from calendar_backend.models.plans import Plan
from calendar_backend.models.prerequisites import PlanPrerequisite

PrecedenceEdgeTriple = tuple[PlanID, PlanID, str]

PlanPrerequisiteEdge = tuple[PlanID, PlanID]


def _iter_plan_prerequisite_rows(plan: Plan) -> Iterable[PlanPrerequisite]:
    try:
        insp = sa_inspect(plan)
    except NoInspectionAvailable:
        return getattr(plan, "prerequisite_edges", ()) or ()
    if insp.session is not None and "prerequisite_edges" in insp.unloaded:
        return ()
    return plan.prerequisite_edges


def plan_prerequisite_edges_from_plans(plans: Iterable[Plan]) -> tuple[PlanPrerequisiteEdge, ...]:
    edges: list[PlanPrerequisiteEdge] = []
    for plan in plans:
        for row in _iter_plan_prerequisite_rows(plan):
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


def validate_immediate_prerequisite_link(
    *,
    task_id: PlanID,
    predecessor_id: PlanID,
    plans_by_id: dict[uuid.UUID, Plan],
) -> ServiceMessage | None:
    if task_id == predecessor_id:
        return ServiceMessage(
            code=MessageCode.IMMEDIATE_PREREQUISITE_SELF_EDGE,
            message="Task cannot be an immediate prerequisite of itself",
            details={
                "plan_id": str(task_id),
                "predecessor_plan_id": str(predecessor_id),
            },
        )

    task_plan_row = plans_by_id.get(task_id)
    predecessor_plan = plans_by_id.get(predecessor_id)
    if task_plan_row is None or predecessor_plan is None:
        missing_id = task_id if task_plan_row is None else predecessor_id
        return ServiceMessage(
            code=MessageCode.PLAN_NOT_FOUND,
            message="Plan not found",
            details={"plan_id": str(missing_id)},
        )

    if task_plan_row.plan_kind != PlanKind.TASK or predecessor_plan.plan_kind != PlanKind.TASK:
        return ServiceMessage(
            code=MessageCode.IMMEDIATE_PREREQUISITE_NOT_TASK,
            message="Immediate prerequisite predecessor and successor must be task plans",
            details={
                "plan_id": str(task_id),
                "predecessor_plan_id": str(predecessor_id),
            },
        )

    task_trace = compute_template_trace(task_id, plans_by_id)
    predecessor_trace = compute_template_trace(predecessor_id, plans_by_id)
    if not traces_match(task_trace, predecessor_trace):
        return ServiceMessage(
            code=MessageCode.IMMEDIATE_PREREQUISITE_TRACE_MISMATCH,
            message="Immediate prerequisite requires matching template traces",
            details={
                "plan_id": str(task_id),
                "predecessor_plan_id": str(predecessor_id),
            },
        )

    return None


def _goal_children(
    parent_id: uuid.UUID,
    plans_by_id: dict[uuid.UUID, Plan],
) -> tuple[Plan, ...]:
    parent = plans_by_id.get(parent_id)
    if parent is None:
        return ()
    children = tuple(child for child in plans_by_id.values() if child.parent_id == parent_id)
    if parent.goal_plan is None:
        return children
    return ordered_goal_children(parent, children=children)


def leaf_task_ids_in_subtree(
    root_id: PlanID,
    *,
    plans_by_id: dict[uuid.UUID, Plan],
    template_subtree_ids: frozenset[uuid.UUID],
) -> frozenset[PlanID]:
    collected: set[PlanID] = set()

    def walk(plan: Plan) -> None:
        plan_id = PlanID(plan.plan_id)
        if plan_id in template_subtree_ids:
            return

        if plan.plan_kind == PlanKind.TASK:
            if plan.task_plan is not None:
                collected.add(plan_id)
            return

        if plan.plan_kind == PlanKind.GOAL:
            for child in _goal_children(plan.plan_id, plans_by_id):
                walk(child)
            return

        if plan.plan_kind == PlanKind.REPETITION:
            repetition_plan = plan.repetition_plan
            if repetition_plan is None or repetition_plan.generated_at is None:
                return
            for instance in ordered_repetition_instances(repetition_plan):
                root_clone = plans_by_id.get(instance.root_clone_id)
                if root_clone is not None:
                    walk(root_clone)

    root = plans_by_id.get(root_id)
    if root is not None:
        walk(root)
    return frozenset(collected)


def is_plan_subtree_complete(
    root_id: PlanID,
    *,
    plans_by_id: dict[uuid.UUID, Plan],
    template_subtree_ids: frozenset[uuid.UUID],
) -> bool:
    leaf_ids = leaf_task_ids_in_subtree(
        root_id,
        plans_by_id=plans_by_id,
        template_subtree_ids=template_subtree_ids,
    )
    if not leaf_ids:
        return True
    for leaf_id in leaf_ids:
        plan = plans_by_id.get(leaf_id)
        if plan is None or plan.task_plan is None or not plan.task_plan.user_completed:
            return False
    return True


def transitive_plan_prerequisite_pairs(
    edges: tuple[PlanPrerequisiteEdge, ...],
) -> tuple[PlanPrerequisiteEdge, ...]:
    depends_on: dict[PlanID, set[PlanID]] = defaultdict(set)
    dependents: set[PlanID] = set()
    for dependent, prerequisite in edges:
        depends_on[dependent].add(prerequisite)
        dependents.add(dependent)

    closure: set[PlanPrerequisiteEdge] = set()
    for dependent in dependents:
        queue: list[PlanID] = list(depends_on.get(dependent, ()))
        seen: set[PlanID] = set()
        while queue:
            prerequisite = queue.pop()
            if prerequisite in seen:
                continue
            seen.add(prerequisite)
            closure.add((dependent, prerequisite))
            queue.extend(depends_on.get(prerequisite, ()))

    return tuple(sorted(closure, key=lambda edge: (str(edge[0]), str(edge[1]))))


def expand_plan_prerequisite_precedence(
    *,
    plans: tuple[Plan, ...],
    plans_by_id: dict[uuid.UUID, Plan],
    template_subtree_ids: frozenset[uuid.UUID],
    incomplete_task_ids: frozenset[PlanID],
    completed_task_ids: frozenset[PlanID],
) -> tuple[PrecedenceEdgeTriple, ...]:
    transitive_pairs = transitive_plan_prerequisite_pairs(plan_prerequisite_edges_from_plans(plans))
    edges: list[PrecedenceEdgeTriple] = []

    for dependent_id, prerequisite_id in transitive_pairs:
        if is_plan_subtree_complete(
            prerequisite_id,
            plans_by_id=plans_by_id,
            template_subtree_ids=template_subtree_ids,
        ):
            continue

        prerequisite_leaves = leaf_task_ids_in_subtree(
            prerequisite_id,
            plans_by_id=plans_by_id,
            template_subtree_ids=template_subtree_ids,
        )
        dependent_leaves = leaf_task_ids_in_subtree(
            dependent_id,
            plans_by_id=plans_by_id,
            template_subtree_ids=template_subtree_ids,
        )

        for predecessor_task_id in sorted(prerequisite_leaves, key=str):
            if predecessor_task_id in completed_task_ids:
                continue
            if predecessor_task_id not in incomplete_task_ids:
                continue
            for successor_task_id in sorted(dependent_leaves, key=str):
                if successor_task_id not in incomplete_task_ids:
                    continue
                if predecessor_task_id == successor_task_id:
                    continue
                edges.append(
                    (
                        predecessor_task_id,
                        successor_task_id,
                        "plan_prerequisite",
                    )
                )

    return tuple(edges)


def expand_immediate_precedence(
    *,
    plans: tuple[Plan, ...],
    incomplete_task_ids: frozenset[PlanID],
    completed_task_ids: frozenset[PlanID],
) -> tuple[PrecedenceEdgeTriple, ...]:
    edges: list[PrecedenceEdgeTriple] = []
    for plan in plans:
        task_plan = plan.task_plan
        if task_plan is None or task_plan.immediate_prerequisite_plan_id is None:
            continue

        successor_task_id = PlanID(plan.plan_id)
        predecessor_task_id = PlanID(task_plan.immediate_prerequisite_plan_id)
        if successor_task_id not in incomplete_task_ids:
            continue
        if predecessor_task_id in completed_task_ids:
            continue
        if predecessor_task_id not in incomplete_task_ids:
            continue

        edges.append(
            (
                predecessor_task_id,
                successor_task_id,
                "immediate_prerequisite",
            )
        )

    return tuple(edges)


def persisted_prerequisite_graph_has_cycle(
    edges: tuple[PlanPrerequisiteEdge, ...],
) -> bool:
    depends_on: dict[PlanID, set[PlanID]] = defaultdict(set)
    nodes: set[PlanID] = set()
    for dependent, prerequisite in edges:
        depends_on[dependent].add(prerequisite)
        nodes.add(dependent)
        nodes.add(prerequisite)

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

    return any(dfs(node) for node in nodes)

"""Precedence-connected assignment decomposition and CP-SAT model-size guards.

Variable-count estimate (conservative over-approximation for slice 2):
- ``horizon_minutes``: minute span from ``run_started_at`` through the latest
  ``end_time`` among task windows, occupied intervals, and stability hints.
- Per task ``max_segments``: ``1`` when indivisible; otherwise
  ``max(1, (duration_minutes + minimum_chunk - 1) // minimum_chunk)`` when
  ``minimum_chunk > 0``, else ``1``.
- Per task: ``3 * max_segments`` (presence + start + duration variables).
- Per precedence edge: ``2`` coupling variables.
- Fixed ``MODEL_OVERHEAD`` of ``10``.

Total estimate =
  ``MODEL_OVERHEAD + horizon_minutes + sum(3 * max_segments) + 2 * edge_count``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from calendar_backend.domain.ids import PlanID
from calendar_backend.domain.time import TimeWindow
from calendar_backend.scheduling.input import (
    AssignmentInput,
    OccupiedInterval,
    PrecedenceEdge,
    SchedulableTask,
    SolverLimits,
)
from calendar_backend.scheduling.types import TaskAssignment

MODEL_OVERHEAD = 10
VARS_PER_SEGMENT = 3
VARS_PER_PRECEDENCE_EDGE = 2


@dataclass(frozen=True)
class AssignmentComponent:
    run_started_at: datetime
    tasks: tuple[SchedulableTask, ...]
    precedence_edges: tuple[PrecedenceEdge, ...]
    occupied_intervals: tuple[OccupiedInterval, ...]
    previous_placements_by_task_id: tuple[tuple[PlanID, tuple[TimeWindow, ...]], ...]
    solver_limits: SolverLimits | None = None


def decompose_assignment_input(
    assignment_input: AssignmentInput,
) -> tuple[AssignmentComponent, ...]:
    """Split input into precedence-connected components in deterministic order."""
    if not assignment_input.tasks:
        return ()

    tasks_by_plan_id = {task.plan_id: task for task in assignment_input.tasks}
    task_plan_ids = set(tasks_by_plan_id)

    filtered_edges = tuple(
        edge
        for edge in assignment_input.precedence_edges
        if edge.predecessor_plan_id in task_plan_ids and edge.successor_plan_id in task_plan_ids
    )

    component_plan_ids = _connected_components(task_plan_ids, filtered_edges)
    ordered_component_plan_ids = sorted(
        component_plan_ids,
        key=lambda plan_ids: min(str(plan_id) for plan_id in plan_ids),
    )

    return tuple(
        _component_from_plan_ids(
            assignment_input,
            plan_ids=plan_ids,
            filtered_edges=filtered_edges,
            occupied_intervals=assignment_input.occupied_intervals,
        )
        for plan_ids in ordered_component_plan_ids
    )


def iter_component_sub_inputs(
    assignment_input: AssignmentInput,
    *,
    prior_solved_assignments: tuple[TaskAssignment, ...] = (),
) -> tuple[AssignmentComponent, ...]:
    """Return components with occupied intervals accumulated from earlier components.

    ``prior_solved_assignments`` should contain placements from components already
    solved in PDF section 9.3 loop order. Each output component's
    ``occupied_intervals`` equals global occupied plus segment intervals from
    assignments whose tasks belong to strictly earlier components.
    """
    base_components = decompose_assignment_input(assignment_input)
    if not base_components:
        return ()

    plan_id_to_component_index = {
        task.plan_id: index
        for index, component in enumerate(base_components)
        for task in component.tasks
    }

    result: list[AssignmentComponent] = []
    for index, component in enumerate(base_components):
        accumulated_occupied = list(assignment_input.occupied_intervals)
        for assignment in prior_solved_assignments:
            assignment_component_index = plan_id_to_component_index.get(assignment.plan_id)
            if assignment_component_index is None or assignment_component_index >= index:
                continue
            accumulated_occupied.extend(
                _occupied_from_segments(assignment.segments, source_plan_id=assignment.plan_id)
            )
        result.append(
            AssignmentComponent(
                run_started_at=component.run_started_at,
                tasks=component.tasks,
                precedence_edges=component.precedence_edges,
                occupied_intervals=tuple(accumulated_occupied),
                previous_placements_by_task_id=component.previous_placements_by_task_id,
                solver_limits=component.solver_limits,
            )
        )

    return tuple(result)


def estimate_model_variable_count(component: AssignmentComponent) -> int:
    horizon_minutes = _component_horizon_minutes(component)
    segment_vars = sum(_max_segments_for_task(task) * VARS_PER_SEGMENT for task in component.tasks)
    edge_vars = len(component.precedence_edges) * VARS_PER_PRECEDENCE_EDGE
    return MODEL_OVERHEAD + horizon_minutes + segment_vars + edge_vars


def model_size_guard_exceeded(
    component: AssignmentComponent,
    limits: SolverLimits | None,
) -> bool:
    if limits is None:
        return False
    return estimate_model_variable_count(component) > limits.model_size_limit


def _connected_components(
    task_plan_ids: set[PlanID],
    edges: tuple[PrecedenceEdge, ...],
) -> tuple[frozenset[PlanID], ...]:
    parent = {plan_id: plan_id for plan_id in task_plan_ids}

    def find(plan_id: PlanID) -> PlanID:
        root = plan_id
        while parent[root] != root:
            root = parent[root]
        while parent[plan_id] != plan_id:
            next_plan_id = parent[plan_id]
            parent[plan_id] = root
            plan_id = next_plan_id
        return root

    def union(left: PlanID, right: PlanID) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for edge in edges:
        union(edge.predecessor_plan_id, edge.successor_plan_id)

    components_by_root: dict[PlanID, set[PlanID]] = {}
    for plan_id in task_plan_ids:
        root = find(plan_id)
        components_by_root.setdefault(root, set()).add(plan_id)

    return tuple(frozenset(plan_ids) for plan_ids in components_by_root.values())


def _component_from_plan_ids(
    assignment_input: AssignmentInput,
    *,
    plan_ids: frozenset[PlanID],
    filtered_edges: tuple[PrecedenceEdge, ...],
    occupied_intervals: tuple[OccupiedInterval, ...],
) -> AssignmentComponent:
    tasks = tuple(
        sorted(
            (task for task in assignment_input.tasks if task.plan_id in plan_ids),
            key=lambda task: str(task.plan_id),
        )
    )
    precedence_edges = tuple(
        edge
        for edge in filtered_edges
        if edge.predecessor_plan_id in plan_ids and edge.successor_plan_id in plan_ids
    )
    previous_placements = tuple(
        (plan_id, segments)
        for plan_id, segments in assignment_input.previous_placements_by_task_id
        if plan_id in plan_ids
    )
    return AssignmentComponent(
        run_started_at=assignment_input.run_started_at,
        tasks=tasks,
        precedence_edges=precedence_edges,
        occupied_intervals=occupied_intervals,
        previous_placements_by_task_id=previous_placements,
        solver_limits=assignment_input.solver_limits,
    )


def _max_segments_for_task(task: SchedulableTask) -> int:
    if not task.divisible:
        return 1

    minimum_chunk = task.minimum_chunk_size_minutes
    if minimum_chunk is None or minimum_chunk <= 0:
        return 1

    return max(1, (task.duration_minutes + minimum_chunk - 1) // minimum_chunk)


def _component_horizon_minutes(component: AssignmentComponent) -> int:
    latest_end = component.run_started_at
    for task in component.tasks:
        for window in task.effective_time_windows:
            latest_end = max(latest_end, window.end_time)
    for occupied in component.occupied_intervals:
        latest_end = max(latest_end, occupied.end_time)
    for _, segments in component.previous_placements_by_task_id:
        for segment in segments:
            latest_end = max(latest_end, segment.end_time)

    delta = latest_end - component.run_started_at
    return max(0, int(delta.total_seconds() // 60))


def _occupied_from_segments(
    segments: tuple[TimeWindow, ...],
    *,
    source_plan_id: PlanID,
) -> list[OccupiedInterval]:
    return [
        OccupiedInterval(
            start_time=segment.start_time,
            end_time=segment.end_time,
            source_plan_id=source_plan_id,
        )
        for segment in segments
    ]

"""Precedence/resource-connected components and structural CP-SAT size guards."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from calendar_backend.domain.ids import PlanID
from calendar_backend.domain.task_families import (
    DownstreamTaskFeasibilitySummary,
    demand_windows_for_block_family,
)
from calendar_backend.domain.time import TimeWindow
from calendar_backend.scheduling.input import (
    AssignmentInput,
    OccupiedInterval,
    PrecedenceEdge,
    SchedulableTask,
    SolverLimits,
)
from calendar_backend.scheduling.types import TaskAssignment


@dataclass(frozen=True)
class AssignmentComponent:
    run_started_at: datetime
    tasks: tuple[SchedulableTask, ...]
    precedence_edges: tuple[PrecedenceEdge, ...]
    occupied_intervals: tuple[OccupiedInterval, ...]
    previous_placements_by_task_id: tuple[tuple[PlanID, tuple[TimeWindow, ...]], ...]
    solver_limits: SolverLimits | None = None
    downstream_task_feasibility_summaries: tuple[DownstreamTaskFeasibilitySummary, ...] = ()
    deadline: float | None = None


def decompose_assignment_input(
    assignment_input: AssignmentInput,
) -> tuple[AssignmentComponent, ...]:
    """Couple tasks connected by precedence or competition for any effective window."""
    if not assignment_input.tasks:
        return ()

    tasks_by_plan_id = {task.plan_id: task for task in assignment_input.tasks}
    task_plan_ids = set(tasks_by_plan_id)

    filtered_edges = tuple(
        edge
        for edge in assignment_input.precedence_edges
        if edge.predecessor_plan_id in task_plan_ids and edge.successor_plan_id in task_plan_ids
    )

    component_plan_ids = _connected_components(tasks_by_plan_id, filtered_edges)
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
                downstream_task_feasibility_summaries=component.downstream_task_feasibility_summaries,
            )
        )

    return tuple(result)


def assignment_input_from_component(component: AssignmentComponent) -> AssignmentInput:
    """Build a solver sub-input from a precedence-connected component."""
    return AssignmentInput(
        run_started_at=component.run_started_at,
        tasks=component.tasks,
        precedence_edges=component.precedence_edges,
        occupied_intervals=component.occupied_intervals,
        previous_placements_by_task_id=component.previous_placements_by_task_id,
        solver_limits=component.solver_limits,
        downstream_task_feasibility_summaries=component.downstream_task_feasibility_summaries,
    )


def estimate_model_variable_count(component: AssignmentComponent) -> int:
    """Bound variables through all lex objectives; time-domain width adds no variables.

    Per segment: four scheduling vars, one per window, ordering, priority,
    gap/span/start objectives. Edges add segment selectors and two extrema;
    stability hints add match/change and six movement vars per matched slot.
    """
    segments = {task.plan_id: _max_segments_for_task(task) for task in component.tasks}
    estimate = 16 + 3 * len(component.occupied_intervals)
    for task in component.tasks:
        count = segments[task.plan_id]
        estimate += count * (12 + len(task.effective_time_windows)) + 3
        if task.block_family is not None:
            estimate += count * len(
                demand_windows_for_block_family(
                    component.downstream_task_feasibility_summaries, task.block_family
                )
            )
    estimate += sum(
        segments[edge.predecessor_plan_id] + segments[edge.successor_plan_id] + 2
        for edge in component.precedence_edges
    )
    for plan_id, hints in component.previous_placements_by_task_id:
        if plan_id in segments:
            estimate += 5 + 6 * min(len(hints), segments[plan_id])
    return estimate


def model_size_guard_exceeded(
    component: AssignmentComponent,
    limits: SolverLimits | None,
) -> bool:
    if limits is None:
        return False
    return estimate_model_variable_count(component) > limits.model_size_limit


def _connected_components(
    tasks: dict[PlanID, SchedulableTask],
    edges: tuple[PrecedenceEdge, ...],
) -> tuple[frozenset[PlanID], ...]:
    parent = {plan_id: plan_id for plan_id in tasks}

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

    # The furthest-reaching active interval connects each overlap island without
    # materializing its quadratic set of pairwise resource-conflict edges.
    intervals = sorted(
        (window.start_time, window.end_time, task.plan_id)
        for task in tasks.values()
        for window in task.effective_time_windows
    )
    furthest_end = None
    representative = None
    for start, end, plan_id in intervals:
        if furthest_end is not None and start < furthest_end:
            assert representative is not None
            union(plan_id, representative)
        else:
            furthest_end, representative = end, plan_id
        if end > furthest_end:
            furthest_end, representative = end, plan_id

    components_by_root: dict[PlanID, set[PlanID]] = {}
    for plan_id in tasks:
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
        downstream_task_feasibility_summaries=assignment_input.downstream_task_feasibility_summaries,
    )


def _max_segments_for_task(task: SchedulableTask) -> int:
    if not task.divisible:
        return 1

    minimum_chunk = task.minimum_chunk_size_minutes
    if minimum_chunk is None or minimum_chunk <= 0:
        return 1

    return max(1, (task.duration_minutes + minimum_chunk - 1) // minimum_chunk)


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

"""Session-free assignment solver input DTOs and resolution-shaped mappers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from calendar_backend.domain.ids import PlanID
from calendar_backend.domain.resolution import (
    ResolvedPrecedenceConstraint,
    ResolvedTask,
    ResolveTasksResult,
)
from calendar_backend.domain.time import TimeWindow, is_minute_aligned

# TODO(Prompt 17 / heuristic stability): use previous_placements_by_task_id
# for soft placement preference.


@dataclass(frozen=True)
class OccupiedInterval:
    start_time: datetime
    end_time: datetime
    source_plan_id: PlanID | None = None


@dataclass(frozen=True)
class SchedulableTask:
    plan_id: PlanID
    duration_minutes: int
    divisible: bool
    minimum_chunk_size_minutes: int | None
    effective_time_windows: tuple[TimeWindow, ...]
    priority_path: tuple[int, ...]


@dataclass(frozen=True)
class PrecedenceEdge:
    predecessor_plan_id: PlanID
    successor_plan_id: PlanID


@dataclass(frozen=True)
class AssignmentInput:
    run_started_at: datetime
    tasks: tuple[SchedulableTask, ...]
    precedence_edges: tuple[PrecedenceEdge, ...]
    occupied_intervals: tuple[OccupiedInterval, ...]
    previous_placements_by_task_id: tuple[tuple[PlanID, tuple[TimeWindow, ...]], ...] = ()


def assignment_input_from_resolved(
    resolved: ResolveTasksResult,
    *,
    occupied_intervals: tuple[OccupiedInterval, ...] = (),
) -> AssignmentInput:
    """Build solver input from resolution output and caller-supplied occupied intervals."""
    assignment_input = AssignmentInput(
        run_started_at=resolved.run_started_at,
        tasks=tuple(_schedulable_task_from_resolved(task) for task in resolved.valid_incomplete),
        precedence_edges=tuple(
            _precedence_edge_from_resolved(edge) for edge in resolved.precedence_constraints
        ),
        occupied_intervals=occupied_intervals,
    )
    validate_assignment_input(assignment_input)
    return assignment_input


def validate_assignment_input(assignment_input: AssignmentInput) -> None:
    if not is_minute_aligned(assignment_input.run_started_at):
        raise ValueError("run_started_at must be minute-aligned")

    seen_plan_ids: set[PlanID] = set()
    for task in assignment_input.tasks:
        if task.duration_minutes <= 0:
            raise ValueError(f"task {task.plan_id} has non-positive duration_minutes")
        if task.plan_id in seen_plan_ids:
            raise ValueError(f"task {task.plan_id} appears more than once in tasks")
        seen_plan_ids.add(task.plan_id)


def _schedulable_task_from_resolved(task: ResolvedTask) -> SchedulableTask:
    return SchedulableTask(
        plan_id=task.plan_id,
        duration_minutes=task.duration_minutes,
        divisible=task.divisible,
        minimum_chunk_size_minutes=task.minimum_chunk_size_minutes,
        effective_time_windows=task.effective_time_windows,
        priority_path=task.priority_path,
    )


def _precedence_edge_from_resolved(edge: ResolvedPrecedenceConstraint) -> PrecedenceEdge:
    return PrecedenceEdge(
        predecessor_plan_id=edge.predecessor_task_id,
        successor_plan_id=edge.successor_task_id,
    )

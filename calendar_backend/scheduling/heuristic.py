"""Deterministic greedy earliest-feasible task assignment heuristic."""

from __future__ import annotations

from datetime import datetime, timedelta

from calendar_backend.domain.errors import MessageCode, ServiceMessage
from calendar_backend.domain.time import TimeWindow
from calendar_backend.scheduling.feasibility import (
    segment_within_windows,
    segments_non_overlapping,
    validate_full_assignment,
    validate_task_assignment,
)
from calendar_backend.scheduling.input import AssignmentInput, PrecedenceEdge, SchedulableTask
from calendar_backend.scheduling.types import (
    AssignmentSolverResult,
    TaskAssignment,
    feasible_result,
    infeasible_result,
)


class HeuristicAssignmentSolver:
    """Greedy earliest-feasible assignment by priority_path."""

    def solve(self, assignment_input: AssignmentInput) -> AssignmentSolverResult:
        # previous_placements_by_task_id is ignored by the heuristic in V1; exact solver
        # consumes stability hints via lex objectives.
        if not assignment_input.tasks:
            return feasible_result(())

        ordered_tasks = sorted(
            assignment_input.tasks,
            key=lambda task: (task.priority_path, str(task.plan_id)),
        )
        occupied = _initial_occupied(assignment_input)
        assignments: list[TaskAssignment] = []

        for task in ordered_tasks:
            if not task.effective_time_windows:
                return infeasible_result(
                    ServiceMessage(
                        code=MessageCode.NO_VALID_WINDOW_FOR_TASK,
                        message="Task has no effective time windows",
                        details={"plan_id": str(task.plan_id)},
                    )
                )

            earliest_start, latest_end = _precedence_bounds(
                task,
                tuple(assignments),
                assignment_input.precedence_edges,
            )
            segments = _find_earliest_placement(
                task,
                occupied=occupied,
                other_assignments=tuple(assignments),
                earliest_start=earliest_start,
                latest_end=latest_end,
            )
            if segments is None:
                return infeasible_result(_failure_for_no_placement(task, occupied))

            failure = validate_task_assignment(
                task,
                segments,
                occupied=occupied,
                other_assignments=tuple(assignments),
            )
            if failure is not None:
                return infeasible_result(failure)

            assignments.append(TaskAssignment(plan_id=task.plan_id, segments=segments))
            occupied = (*occupied, *segments)

        assignment_tuple = tuple(assignments)
        validation_failure = validate_full_assignment(assignment_input, assignment_tuple)
        if __debug__:
            assert validation_failure is None
        if validation_failure is not None:
            return infeasible_result(validation_failure)

        return feasible_result(assignment_tuple)


def _initial_occupied(assignment_input: AssignmentInput) -> tuple[TimeWindow, ...]:
    return tuple(
        TimeWindow(start_time=interval.start_time, end_time=interval.end_time)
        for interval in assignment_input.occupied_intervals
    )


def _precedence_bounds(
    task: SchedulableTask,
    assignments: tuple[TaskAssignment, ...],
    edges: tuple[PrecedenceEdge, ...],
) -> tuple[datetime | None, datetime | None]:
    segments_by_plan_id = {assignment.plan_id: assignment.segments for assignment in assignments}
    earliest_start: datetime | None = None
    latest_end: datetime | None = None

    for edge in edges:
        if edge.successor_plan_id == task.plan_id:
            predecessor_segments = segments_by_plan_id.get(edge.predecessor_plan_id)
            if predecessor_segments is not None:
                predecessor_end = max(segment.end_time for segment in predecessor_segments)
                earliest_start = (
                    predecessor_end
                    if earliest_start is None
                    else max(earliest_start, predecessor_end)
                )

        if edge.predecessor_plan_id == task.plan_id:
            successor_segments = segments_by_plan_id.get(edge.successor_plan_id)
            if successor_segments is not None:
                successor_start = min(segment.start_time for segment in successor_segments)
                latest_end = (
                    successor_start if latest_end is None else min(latest_end, successor_start)
                )

    return earliest_start, latest_end


def _find_earliest_placement(
    task: SchedulableTask,
    *,
    occupied: tuple[TimeWindow, ...],
    other_assignments: tuple[TaskAssignment, ...],
    earliest_start: datetime | None,
    latest_end: datetime | None,
) -> tuple[TimeWindow, ...] | None:
    minimum_chunk = task.minimum_chunk_size_minutes
    max_segments = 1
    if task.divisible and minimum_chunk is not None and minimum_chunk > 0:
        max_segments = max(1, (task.duration_minutes + minimum_chunk - 1) // minimum_chunk)

    for segment_count in range(1, max_segments + 1):
        if not task.divisible and segment_count > 1:
            break
        placement = _earliest_placement_with_segment_count(
            task,
            segment_count=segment_count,
            occupied=occupied,
            other_assignments=other_assignments,
            earliest_start=earliest_start,
            latest_end=latest_end,
        )
        if placement is not None:
            return placement

    return None


def _earliest_placement_with_segment_count(
    task: SchedulableTask,
    *,
    segment_count: int,
    occupied: tuple[TimeWindow, ...],
    other_assignments: tuple[TaskAssignment, ...],
    earliest_start: datetime | None,
    latest_end: datetime | None,
) -> tuple[TimeWindow, ...] | None:
    if segment_count == 1:
        segment = _earliest_segment_of_duration(
            task,
            duration_minutes=task.duration_minutes,
            occupied=occupied,
            other_assignments=other_assignments,
            earliest_start=earliest_start,
            latest_end=latest_end,
        )
        return (segment,) if segment is not None else None

    minimum_chunk = task.minimum_chunk_size_minutes
    if minimum_chunk is None:
        return None

    return _search_segment_placement(
        task,
        segments_remaining=segment_count,
        duration_remaining=task.duration_minutes,
        occupied=occupied,
        other_assignments=other_assignments,
        earliest_start=earliest_start,
        latest_end=latest_end,
        segments_so_far=(),
    )


def _search_segment_placement(
    task: SchedulableTask,
    *,
    segments_remaining: int,
    duration_remaining: int,
    occupied: tuple[TimeWindow, ...],
    other_assignments: tuple[TaskAssignment, ...],
    earliest_start: datetime | None,
    latest_end: datetime | None,
    segments_so_far: tuple[TimeWindow, ...],
) -> tuple[TimeWindow, ...] | None:
    minimum_chunk = task.minimum_chunk_size_minutes
    if minimum_chunk is None:
        return None

    if segments_remaining == 1:
        segment = _earliest_segment_of_duration(
            task,
            duration_minutes=duration_remaining,
            occupied=occupied,
            other_assignments=other_assignments,
            earliest_start=earliest_start,
            latest_end=latest_end,
        )
        if segment is None:
            return None
        return (*segments_so_far, segment)

    best: tuple[TimeWindow, ...] | None = None
    max_first_duration = duration_remaining - (segments_remaining - 1) * minimum_chunk

    for segment in _iter_segments_of_duration(
        task,
        min_duration_minutes=minimum_chunk,
        max_duration_minutes=max_first_duration,
        occupied=occupied,
        other_assignments=other_assignments,
        earliest_start=earliest_start,
        latest_end=latest_end,
    ):
        candidate = _search_segment_placement(
            task,
            segments_remaining=segments_remaining - 1,
            duration_remaining=duration_remaining - _segment_duration_minutes(segment),
            occupied=(*occupied, segment),
            other_assignments=other_assignments,
            earliest_start=segment.end_time,
            latest_end=latest_end,
            segments_so_far=(*segments_so_far, segment),
        )
        if candidate is None:
            continue
        if best is None or _ends_earlier(candidate, best):
            best = candidate

    return best


def _earliest_segment_of_duration(
    task: SchedulableTask,
    *,
    duration_minutes: int,
    occupied: tuple[TimeWindow, ...],
    other_assignments: tuple[TaskAssignment, ...],
    earliest_start: datetime | None,
    latest_end: datetime | None,
) -> TimeWindow | None:
    for segment in _iter_segments_of_duration(
        task,
        min_duration_minutes=duration_minutes,
        max_duration_minutes=duration_minutes,
        occupied=occupied,
        other_assignments=other_assignments,
        earliest_start=earliest_start,
        latest_end=latest_end,
    ):
        return segment
    return None


def _iter_segments_of_duration(
    task: SchedulableTask,
    *,
    min_duration_minutes: int,
    max_duration_minutes: int,
    occupied: tuple[TimeWindow, ...],
    other_assignments: tuple[TaskAssignment, ...],
    earliest_start: datetime | None,
    latest_end: datetime | None,
):
    ordered_windows = sorted(task.effective_time_windows, key=lambda window: window.start_time)
    for window in ordered_windows:
        candidate_start = window.start_time
        if earliest_start is not None:
            candidate_start = max(candidate_start, earliest_start)

        while candidate_start < window.end_time:
            for duration_minutes in range(min_duration_minutes, max_duration_minutes + 1):
                segment_end = candidate_start + timedelta(minutes=duration_minutes)
                if segment_end > window.end_time:
                    break
                if latest_end is not None and segment_end > latest_end:
                    continue

                segment = TimeWindow(start_time=candidate_start, end_time=segment_end)
                other_segments = tuple(
                    placed_segment
                    for assignment in other_assignments
                    for placed_segment in assignment.segments
                )
                if segment_within_windows(
                    segment, task.effective_time_windows
                ) and segments_non_overlapping((segment,), occupied + other_segments):
                    yield segment

            candidate_start += timedelta(minutes=1)


def _failure_for_no_placement(
    task: SchedulableTask,
    occupied: tuple[TimeWindow, ...],
) -> ServiceMessage:
    details = {"plan_id": str(task.plan_id)}
    if _total_free_minutes(task, occupied) < task.duration_minutes:
        return ServiceMessage(
            code=MessageCode.INSUFFICIENT_TOTAL_CAPACITY,
            message="Insufficient total capacity to assign task",
            details=details,
        )
    return ServiceMessage(
        code=MessageCode.NO_VALID_WINDOW_FOR_TASK,
        message="No valid placement found for task",
        details=details,
    )


def _total_free_minutes(task: SchedulableTask, occupied: tuple[TimeWindow, ...]) -> int:
    total = 0
    for window in task.effective_time_windows:
        for gap_start, gap_end in _gaps_in_window(window, occupied):
            total += _segment_duration_minutes(TimeWindow(start_time=gap_start, end_time=gap_end))
    return total


def _gaps_in_window(
    window: TimeWindow,
    occupied: tuple[TimeWindow, ...],
) -> list[tuple[datetime, datetime]]:
    blocking = sorted(
        (
            segment
            for segment in occupied
            if segment.start_time < window.end_time and segment.end_time > window.start_time
        ),
        key=lambda segment: segment.start_time,
    )

    gaps: list[tuple[datetime, datetime]] = []
    cursor = window.start_time
    for segment in blocking:
        gap_end = min(segment.start_time, window.end_time)
        if cursor < gap_end:
            gaps.append((cursor, gap_end))
        cursor = max(cursor, segment.end_time)
        if cursor >= window.end_time:
            break

    if cursor < window.end_time:
        gaps.append((cursor, window.end_time))

    return gaps


def _segment_duration_minutes(segment: TimeWindow) -> int:
    delta = segment.end_time - segment.start_time
    return int(delta.total_seconds() // 60)


def _ends_earlier(left: tuple[TimeWindow, ...], right: tuple[TimeWindow, ...]) -> bool:
    left_end = max(segment.end_time for segment in left)
    right_end = max(segment.end_time for segment in right)
    return left_end < right_end

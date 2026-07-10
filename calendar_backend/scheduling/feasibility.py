"""Pure hard-constraint feasibility checks for assignment candidates."""

from __future__ import annotations

from calendar_backend.domain.errors import MessageCode, ServiceMessage
from calendar_backend.domain.time import TimeWindow
from calendar_backend.scheduling.input import AssignmentInput, PrecedenceEdge, SchedulableTask
from calendar_backend.scheduling.types import TaskAssignment


def segment_within_windows(segment: TimeWindow, windows: tuple[TimeWindow, ...]) -> bool:
    if not windows:
        return False
    return any(
        segment.start_time >= window.start_time and segment.end_time <= window.end_time
        for window in windows
    )


def segments_non_overlapping(
    segments: tuple[TimeWindow, ...],
    other_segments: tuple[TimeWindow, ...],
) -> bool:
    for left_index, left in enumerate(segments):
        for right in segments[left_index + 1 :]:
            if _windows_overlap(left, right):
                return False
        for right in other_segments:
            if _windows_overlap(left, right):
                return False
    return True


def segments_respect_minimum_chunk(
    task: SchedulableTask,
    segments: tuple[TimeWindow, ...],
) -> bool:
    if not task.divisible:
        return len(segments) == 1

    minimum_chunk = task.minimum_chunk_size_minutes
    if minimum_chunk is None:
        return False

    return all(_window_duration_minutes(segment) >= minimum_chunk for segment in segments)


def segments_total_duration_matches(
    task: SchedulableTask,
    segments: tuple[TimeWindow, ...],
) -> bool:
    total_minutes = sum(_window_duration_minutes(segment) for segment in segments)
    return total_minutes == task.duration_minutes


def precedence_satisfied(
    assignments: tuple[TaskAssignment, ...],
    edges: tuple[PrecedenceEdge, ...],
) -> bool:
    segments_by_plan_id = {assignment.plan_id: assignment.segments for assignment in assignments}

    for edge in edges:
        predecessor_segments = segments_by_plan_id.get(edge.predecessor_plan_id)
        successor_segments = segments_by_plan_id.get(edge.successor_plan_id)
        if predecessor_segments is None or successor_segments is None:
            continue

        latest_predecessor_end = max(segment.end_time for segment in predecessor_segments)
        earliest_successor_start = min(segment.start_time for segment in successor_segments)
        if latest_predecessor_end > earliest_successor_start:
            return False

    return True


def validate_task_assignment(
    task: SchedulableTask,
    segments: tuple[TimeWindow, ...],
    *,
    occupied: tuple[TimeWindow, ...],
    other_assignments: tuple[TaskAssignment, ...],
) -> ServiceMessage | None:
    details = {"plan_id": str(task.plan_id)}

    if not segments_respect_minimum_chunk(task, segments):
        return ServiceMessage(
            code=MessageCode.MINIMUM_CHUNK_SIZE_IMPOSSIBLE,
            message="Task segments violate minimum chunk rules",
            details=details,
        )

    if not segments_total_duration_matches(task, segments):
        return ServiceMessage(
            code=MessageCode.INSUFFICIENT_TOTAL_CAPACITY,
            message="Task segment durations do not sum to required duration",
            details=details,
        )

    if not task.effective_time_windows:
        return ServiceMessage(
            code=MessageCode.NO_VALID_WINDOW_FOR_TASK,
            message="Task has no effective time windows",
            details=details,
        )

    for segment in segments:
        if not segment_within_windows(segment, task.effective_time_windows):
            return ServiceMessage(
                code=MessageCode.NO_VALID_WINDOW_FOR_TASK,
                message="Task segment is outside effective time windows",
                details=details,
            )

    other_segments = tuple(
        segment
        for assignment in other_assignments
        if assignment.plan_id != task.plan_id
        for segment in assignment.segments
    )
    if not segments_non_overlapping(segments, occupied + other_segments):
        return ServiceMessage(
            code=MessageCode.NO_VALID_WINDOW_FOR_TASK,
            message="Task segment overlaps occupied time or another assignment",
            details=details,
        )

    return None


def validate_full_assignment(
    assignment_input: AssignmentInput,
    assignments: tuple[TaskAssignment, ...],
) -> ServiceMessage | None:
    tasks_by_plan_id = {task.plan_id: task for task in assignment_input.tasks}
    assignments_by_plan_id = {assignment.plan_id: assignment for assignment in assignments}

    if set(tasks_by_plan_id) != set(assignments_by_plan_id):
        return ServiceMessage(
            code=MessageCode.SOLVER_FAILED_TO_FIND_FEASIBLE_ASSIGNMENT,
            message="Assignment does not cover every input task exactly once",
            details={},
        )

    occupied = tuple(
        TimeWindow(start_time=interval.start_time, end_time=interval.end_time)
        for interval in assignment_input.occupied_intervals
    )

    for task in assignment_input.tasks:
        assignment = assignments_by_plan_id[task.plan_id]
        other_assignments = tuple(other for other in assignments if other.plan_id != task.plan_id)
        failure = validate_task_assignment(
            task,
            assignment.segments,
            occupied=occupied,
            other_assignments=other_assignments,
        )
        if failure is not None:
            return failure

    if not precedence_satisfied(assignments, assignment_input.precedence_edges):
        return ServiceMessage(
            code=MessageCode.PRECEDENCE_IMPOSSIBLE,
            message="Assignment violates precedence constraints",
            details={},
        )

    return None


def _windows_overlap(left: TimeWindow, right: TimeWindow) -> bool:
    return left.start_time < right.end_time and right.start_time < left.end_time


def _window_duration_minutes(window: TimeWindow) -> int:
    delta = window.end_time - window.start_time
    return int(delta.total_seconds() // 60)

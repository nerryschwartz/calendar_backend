"""Pure hard-constraint feasibility checks for assignment candidates."""

from __future__ import annotations

from datetime import datetime, timedelta

from calendar_backend.domain.errors import MessageCode, ServiceMessage
from calendar_backend.domain.ids import PlanID
from calendar_backend.domain.time import TimeWindow, gaps_in_window
from calendar_backend.scheduling.input import AssignmentInput, PrecedenceEdge, SchedulableTask
from calendar_backend.scheduling.types import TaskAssignment

_STAGED_DIAGNOSIS_ORDER: tuple[MessageCode, ...] = (
    MessageCode.NO_VALID_WINDOW_FOR_TASK,
    MessageCode.INSUFFICIENT_TOTAL_CAPACITY,
    MessageCode.PRECEDENCE_IMPOSSIBLE,
    MessageCode.MINIMUM_CHUNK_SIZE_IMPOSSIBLE,
    MessageCode.TASK_OVERLAP_REQUIRED,
)


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


def diagnose_assignment_input(assignment_input: AssignmentInput) -> tuple[ServiceMessage, ...]:
    """Return deterministic staged input failures in PDF section 9.5 order."""
    occupied = _occupied_windows(assignment_input)
    tasks_by_plan_id = {task.plan_id: task for task in assignment_input.tasks}
    failures_by_code: dict[MessageCode, list[ServiceMessage]] = {
        code: [] for code in _STAGED_DIAGNOSIS_ORDER
    }

    for task in sorted(assignment_input.tasks, key=lambda item: str(item.plan_id)):
        if not task.effective_time_windows:
            failures_by_code[MessageCode.NO_VALID_WINDOW_FOR_TASK].append(
                _task_failure(
                    task.plan_id,
                    code=MessageCode.NO_VALID_WINDOW_FOR_TASK,
                    message="Task has no effective time windows",
                )
            )
            continue

        if available_minutes_for_task(task, occupied) < task.duration_minutes:
            failures_by_code[MessageCode.INSUFFICIENT_TOTAL_CAPACITY].append(
                _task_failure(
                    task.plan_id,
                    code=MessageCode.INSUFFICIENT_TOTAL_CAPACITY,
                    message="Insufficient total capacity to assign task",
                )
            )

        chunk_failure = _minimum_chunk_failure(task)
        if chunk_failure is not None:
            failures_by_code[MessageCode.MINIMUM_CHUNK_SIZE_IMPOSSIBLE].append(chunk_failure)

    precedence_failure = _precedence_failure(assignment_input, tasks_by_plan_id, occupied)
    if precedence_failure is not None:
        failures_by_code[MessageCode.PRECEDENCE_IMPOSSIBLE].append(precedence_failure)

    ordered_failures: list[ServiceMessage] = []
    for code in _STAGED_DIAGNOSIS_ORDER:
        ordered_failures.extend(
            sorted(
                failures_by_code[code],
                key=lambda failure: str(failure.details.get("plan_id", "")),
            )
        )
    return tuple(ordered_failures)


def available_minutes_for_task(
    task: SchedulableTask,
    occupied: tuple[TimeWindow, ...],
) -> int:
    total = 0
    for window in task.effective_time_windows:
        for gap_start, gap_end in gaps_in_window(window, occupied):
            total += _window_duration_minutes(TimeWindow(start_time=gap_start, end_time=gap_end))
    return total


def _occupied_windows(assignment_input: AssignmentInput) -> tuple[TimeWindow, ...]:
    return tuple(
        TimeWindow(start_time=interval.start_time, end_time=interval.end_time)
        for interval in assignment_input.occupied_intervals
    )


def _task_failure(
    plan_id: PlanID,
    *,
    code: MessageCode,
    message: str,
) -> ServiceMessage:
    return ServiceMessage(
        code=code,
        message=message,
        details={"plan_id": str(plan_id)},
    )


def _minimum_chunk_failure(task: SchedulableTask) -> ServiceMessage | None:
    if not task.divisible:
        return None

    details = {"plan_id": str(task.plan_id)}
    minimum_chunk = task.minimum_chunk_size_minutes
    if minimum_chunk is None:
        return ServiceMessage(
            code=MessageCode.MINIMUM_CHUNK_SIZE_IMPOSSIBLE,
            message="Divisible task is missing minimum chunk size",
            details=details,
        )

    longest_window_minutes = max(
        (_window_duration_minutes(window) for window in task.effective_time_windows),
        default=0,
    )
    if longest_window_minutes < minimum_chunk:
        return ServiceMessage(
            code=MessageCode.MINIMUM_CHUNK_SIZE_IMPOSSIBLE,
            message="Task windows are shorter than minimum chunk size",
            details=details,
        )
    return None


def _precedence_failure(
    assignment_input: AssignmentInput,
    tasks_by_plan_id: dict[PlanID, SchedulableTask],
    occupied: tuple[TimeWindow, ...],
) -> ServiceMessage | None:
    for edge in sorted(
        assignment_input.precedence_edges,
        key=lambda item: (
            str(item.predecessor_plan_id),
            str(item.successor_plan_id),
        ),
    ):
        predecessor = tasks_by_plan_id.get(edge.predecessor_plan_id)
        successor = tasks_by_plan_id.get(edge.successor_plan_id)
        if predecessor is None or successor is None:
            continue
        if not _precedence_edge_schedulable(predecessor, successor, occupied):
            return ServiceMessage(
                code=MessageCode.PRECEDENCE_IMPOSSIBLE,
                message="Assignment violates precedence constraints",
                details={},
            )
    return None


def _precedence_edge_schedulable(
    predecessor: SchedulableTask,
    successor: SchedulableTask,
    occupied: tuple[TimeWindow, ...],
) -> bool:
    if not predecessor.effective_time_windows or not successor.effective_time_windows:
        return True

    earliest_successor_start = min(window.start_time for window in successor.effective_time_windows)
    return _can_task_finish_by(predecessor, earliest_successor_start, occupied)


def _can_task_finish_by(
    task: SchedulableTask,
    deadline: datetime,
    occupied: tuple[TimeWindow, ...],
) -> bool:
    duration = timedelta(minutes=task.duration_minutes)
    for window in sorted(task.effective_time_windows, key=lambda item: item.start_time):
        latest_start = deadline - duration
        if latest_start < window.start_time:
            continue
        segment = TimeWindow(start_time=latest_start, end_time=deadline)
        if segment_within_windows(
            segment, task.effective_time_windows
        ) and segments_non_overlapping(
            (segment,),
            occupied,
        ):
            return True
    return False


def _windows_overlap(left: TimeWindow, right: TimeWindow) -> bool:
    return left.start_time < right.end_time and right.start_time < left.end_time


def _window_duration_minutes(window: TimeWindow) -> int:
    delta = window.end_time - window.start_time
    return int(delta.total_seconds() // 60)

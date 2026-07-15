from __future__ import annotations

import pytest
from calendar_backend.domain.errors import MessageCode
from calendar_backend.scheduling.feasibility import (
    diagnose_assignment_input,
    precedence_satisfied,
    segment_within_windows,
    segments_non_overlapping,
    segments_respect_minimum_chunk,
    segments_total_duration_matches,
    validate_full_assignment,
    validate_task_assignment,
)
from calendar_backend.scheduling.input import (
    AssignmentInput,
    PrecedenceEdge,
    validate_assignment_input,
)
from calendar_backend.scheduling.types import TaskAssignment

from .conftest import assignment_input, plan_id, schedulable_task, utc, window


def test_segment_within_windows_true_when_fully_inside() -> None:
    effective = (window(utc(2026, 6, 7, 9, 0), utc(2026, 6, 7, 12, 0)),)
    segment = window(utc(2026, 6, 7, 10, 0), utc(2026, 6, 7, 11, 0))

    assert segment_within_windows(segment, effective) is True


def test_segment_within_windows_false_when_outside_or_empty_windows() -> None:
    effective = (window(utc(2026, 6, 7, 9, 0), utc(2026, 6, 7, 12, 0)),)
    outside = window(utc(2026, 6, 7, 8, 0), utc(2026, 6, 7, 9, 0))

    assert segment_within_windows(outside, effective) is False
    assert segment_within_windows(outside, ()) is False


def test_segments_non_overlapping_allows_touching_half_open() -> None:
    left = window(utc(2026, 6, 7, 9, 0), utc(2026, 6, 7, 10, 0))
    right = window(utc(2026, 6, 7, 10, 0), utc(2026, 6, 7, 11, 0))

    assert segments_non_overlapping((left,), (right,)) is True


def test_segments_non_overlapping_rejects_proper_overlap() -> None:
    left = window(utc(2026, 6, 7, 9, 0), utc(2026, 6, 7, 10, 30))
    right = window(utc(2026, 6, 7, 10, 0), utc(2026, 6, 7, 11, 0))

    assert segments_non_overlapping((left,), (right,)) is False


def test_segments_respect_minimum_chunk_indivisible_requires_single_segment() -> None:
    task = schedulable_task(
        duration_minutes=30,
        effective_time_windows=(window(utc(2026, 6, 7, 9, 0), utc(2026, 6, 7, 12, 0)),),
        divisible=False,
    )
    single = (window(utc(2026, 6, 7, 9, 0), utc(2026, 6, 7, 9, 30)),)
    split = (
        window(utc(2026, 6, 7, 9, 0), utc(2026, 6, 7, 9, 15)),
        window(utc(2026, 6, 7, 9, 15), utc(2026, 6, 7, 9, 30)),
    )

    assert segments_respect_minimum_chunk(task, single) is True
    assert segments_respect_minimum_chunk(task, split) is False


def test_segments_respect_minimum_chunk_divisible_enforces_min_chunk() -> None:
    task = schedulable_task(
        duration_minutes=60,
        effective_time_windows=(window(utc(2026, 6, 7, 9, 0), utc(2026, 6, 7, 12, 0)),),
        divisible=True,
        minimum_chunk_size_minutes=30,
    )
    valid = (
        window(utc(2026, 6, 7, 9, 0), utc(2026, 6, 7, 9, 30)),
        window(utc(2026, 6, 7, 9, 30), utc(2026, 6, 7, 10, 0)),
    )
    invalid = (
        window(utc(2026, 6, 7, 9, 0), utc(2026, 6, 7, 9, 15)),
        window(utc(2026, 6, 7, 9, 15), utc(2026, 6, 7, 10, 0)),
    )

    assert segments_respect_minimum_chunk(task, valid) is True
    assert segments_respect_minimum_chunk(task, invalid) is False


def test_segments_total_duration_matches_accepts_and_rejects() -> None:
    task = schedulable_task(
        duration_minutes=60,
        effective_time_windows=(window(utc(2026, 6, 7, 9, 0), utc(2026, 6, 7, 12, 0)),),
    )
    matching = (window(utc(2026, 6, 7, 9, 0), utc(2026, 6, 7, 10, 0)),)
    short = (window(utc(2026, 6, 7, 9, 0), utc(2026, 6, 7, 9, 30)),)

    assert segments_total_duration_matches(task, matching) is True
    assert segments_total_duration_matches(task, short) is False


def test_precedence_satisfied_when_predecessor_ends_before_successor_starts() -> None:
    predecessor_id = plan_id()
    successor_id = plan_id()
    assignments = (
        TaskAssignment(
            plan_id=predecessor_id,
            segments=(window(utc(2026, 6, 7, 9, 0), utc(2026, 6, 7, 9, 30)),),
        ),
        TaskAssignment(
            plan_id=successor_id,
            segments=(window(utc(2026, 6, 7, 9, 30), utc(2026, 6, 7, 10, 0)),),
        ),
    )
    edges = (PrecedenceEdge(predecessor_plan_id=predecessor_id, successor_plan_id=successor_id),)

    assert precedence_satisfied(assignments, edges) is True


def test_precedence_satisfied_false_when_order_violated() -> None:
    predecessor_id = plan_id()
    successor_id = plan_id()
    assignments = (
        TaskAssignment(
            plan_id=predecessor_id,
            segments=(window(utc(2026, 6, 7, 9, 30), utc(2026, 6, 7, 10, 0)),),
        ),
        TaskAssignment(
            plan_id=successor_id,
            segments=(window(utc(2026, 6, 7, 9, 0), utc(2026, 6, 7, 9, 30)),),
        ),
    )
    edges = (PrecedenceEdge(predecessor_plan_id=predecessor_id, successor_plan_id=successor_id),)

    assert precedence_satisfied(assignments, edges) is False


def test_precedence_satisfied_skips_edge_when_endpoint_not_in_assignments() -> None:
    present_id = plan_id()
    missing_id = plan_id()
    assignments = (
        TaskAssignment(
            plan_id=present_id,
            segments=(window(utc(2026, 6, 7, 9, 0), utc(2026, 6, 7, 9, 30)),),
        ),
    )
    edges = (PrecedenceEdge(predecessor_plan_id=present_id, successor_plan_id=missing_id),)

    assert precedence_satisfied(assignments, edges) is True


def test_validate_task_assignment_rejects_overlap_with_occupied() -> None:
    task = schedulable_task(
        duration_minutes=30,
        effective_time_windows=(window(utc(2026, 6, 7, 9, 0), utc(2026, 6, 7, 12, 0)),),
    )
    segment = window(utc(2026, 6, 7, 9, 0), utc(2026, 6, 7, 9, 30))
    occupied = (window(utc(2026, 6, 7, 9, 0), utc(2026, 6, 7, 9, 15)),)

    failure = validate_task_assignment(task, (segment,), occupied=occupied, other_assignments=())

    assert failure is not None
    assert failure.code == MessageCode.NO_VALID_WINDOW_FOR_TASK


def test_validate_task_assignment_rejects_segment_outside_windows() -> None:
    task = schedulable_task(
        duration_minutes=30,
        effective_time_windows=(window(utc(2026, 6, 7, 9, 0), utc(2026, 6, 7, 10, 0)),),
    )
    segment = window(utc(2026, 6, 7, 10, 0), utc(2026, 6, 7, 10, 30))

    failure = validate_task_assignment(task, (segment,), occupied=(), other_assignments=())

    assert failure is not None
    assert failure.code == MessageCode.NO_VALID_WINDOW_FOR_TASK


def test_validate_task_assignment_rejects_chunk_and_duration_violations() -> None:
    divisible_task = schedulable_task(
        duration_minutes=60,
        effective_time_windows=(window(utc(2026, 6, 7, 9, 0), utc(2026, 6, 7, 12, 0)),),
        divisible=True,
        minimum_chunk_size_minutes=30,
    )
    short_chunks = (
        window(utc(2026, 6, 7, 9, 0), utc(2026, 6, 7, 9, 15)),
        window(utc(2026, 6, 7, 9, 15), utc(2026, 6, 7, 10, 0)),
    )

    chunk_failure = validate_task_assignment(
        divisible_task,
        short_chunks,
        occupied=(),
        other_assignments=(),
    )
    assert chunk_failure is not None
    assert chunk_failure.code == MessageCode.MINIMUM_CHUNK_SIZE_IMPOSSIBLE

    indivisible_task = schedulable_task(
        duration_minutes=60,
        effective_time_windows=(window(utc(2026, 6, 7, 9, 0), utc(2026, 6, 7, 12, 0)),),
        divisible=False,
    )
    short_duration = (window(utc(2026, 6, 7, 9, 0), utc(2026, 6, 7, 9, 30)),)

    duration_failure = validate_task_assignment(
        indivisible_task,
        short_duration,
        occupied=(),
        other_assignments=(),
    )
    assert duration_failure is not None
    assert duration_failure.code == MessageCode.INSUFFICIENT_TOTAL_CAPACITY


def test_validate_full_assignment_requires_exact_task_coverage() -> None:
    task = schedulable_task(
        duration_minutes=30,
        effective_time_windows=(window(utc(2026, 6, 7, 9, 0), utc(2026, 6, 7, 12, 0)),),
    )
    assignment_input_value = assignment_input(tasks=(task,))
    missing = ()

    failure = validate_full_assignment(assignment_input_value, missing)

    assert failure is not None
    assert failure.code == MessageCode.SOLVER_FAILED_TO_FIND_FEASIBLE_ASSIGNMENT


def test_validate_full_assignment_rejects_precedence_violation() -> None:
    predecessor_id = plan_id()
    successor_id = plan_id()
    tasks = (
        schedulable_task(
            task_id=predecessor_id,
            duration_minutes=30,
            effective_time_windows=(window(utc(2026, 6, 7, 9, 0), utc(2026, 6, 7, 12, 0)),),
        ),
        schedulable_task(
            task_id=successor_id,
            duration_minutes=30,
            effective_time_windows=(window(utc(2026, 6, 7, 9, 0), utc(2026, 6, 7, 12, 0)),),
        ),
    )
    assignment_input_value = assignment_input(
        tasks=tasks,
        precedence_edges=(
            PrecedenceEdge(predecessor_plan_id=predecessor_id, successor_plan_id=successor_id),
        ),
    )
    assignments = (
        TaskAssignment(
            plan_id=predecessor_id,
            segments=(window(utc(2026, 6, 7, 9, 30), utc(2026, 6, 7, 10, 0)),),
        ),
        TaskAssignment(
            plan_id=successor_id,
            segments=(window(utc(2026, 6, 7, 9, 0), utc(2026, 6, 7, 9, 30)),),
        ),
    )

    failure = validate_full_assignment(assignment_input_value, assignments)

    assert failure is not None
    assert failure.code == MessageCode.PRECEDENCE_IMPOSSIBLE


def test_validate_assignment_input_rejects_duplicate_plan_id() -> None:
    shared_id = plan_id()
    duplicate_tasks = (
        schedulable_task(
            task_id=shared_id,
            duration_minutes=30,
            effective_time_windows=(window(utc(2026, 6, 7, 9, 0), utc(2026, 6, 7, 12, 0)),),
        ),
        schedulable_task(
            task_id=shared_id,
            duration_minutes=30,
            effective_time_windows=(window(utc(2026, 6, 7, 9, 0), utc(2026, 6, 7, 12, 0)),),
        ),
    )

    with pytest.raises(ValueError, match="appears more than once"):
        validate_assignment_input(assignment_input(tasks=duplicate_tasks))


def test_validate_assignment_input_rejects_non_minute_aligned_run_started_at() -> None:
    task = schedulable_task(
        duration_minutes=30,
        effective_time_windows=(window(utc(2026, 6, 7, 9, 0), utc(2026, 6, 7, 12, 0)),),
    )
    misaligned = AssignmentInput(
        run_started_at=utc(2026, 6, 7, 10, 0).replace(second=30),
        tasks=(task,),
        precedence_edges=(),
        occupied_intervals=(),
    )

    with pytest.raises(ValueError, match="minute-aligned"):
        validate_assignment_input(misaligned)


def test_diagnose_assignment_input_reports_empty_windows_before_capacity() -> None:
    empty_windows_id = plan_id()
    schedulable = schedulable_task(
        task_id=empty_windows_id,
        duration_minutes=30,
        effective_time_windows=(),
    )

    failures = diagnose_assignment_input(assignment_input(tasks=(schedulable,)))

    assert len(failures) == 1
    assert failures[0].code == MessageCode.NO_VALID_WINDOW_FOR_TASK
    assert failures[0].details["plan_id"] == str(empty_windows_id)


def test_diagnose_assignment_input_reports_insufficient_capacity() -> None:
    task_id = plan_id()
    narrow = window(utc(2026, 6, 7, 9, 0), utc(2026, 6, 7, 9, 15))
    task = schedulable_task(
        task_id=task_id,
        duration_minutes=30,
        effective_time_windows=(narrow,),
    )

    failures = diagnose_assignment_input(assignment_input(tasks=(task,)))

    assert len(failures) == 1
    assert failures[0].code == MessageCode.INSUFFICIENT_TOTAL_CAPACITY
    assert failures[0].details["plan_id"] == str(task_id)

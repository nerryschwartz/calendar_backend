from __future__ import annotations

from calendar_backend.scheduling.exact_cp_sat import (
    _solve_single_component,  # pyright: ignore[reportPrivateUsage]
)
from calendar_backend.scheduling.feasibility import validate_full_assignment
from calendar_backend.scheduling.input import PrecedenceEdge

from .conftest import (
    assignment_component,
    assignment_input,
    occupied,
    plan_id,
    schedulable_task,
    utc,
    window,
)


def test_solve_single_indivisible_task_places_within_window() -> None:
    task = schedulable_task(
        duration_minutes=60,
        effective_time_windows=(window(utc(2026, 6, 7, 9, 0), utc(2026, 6, 7, 12, 0)),),
    )
    component = assignment_component(tasks=(task,))

    assignments = _solve_single_component(component)

    assert assignments is not None
    assert len(assignments) == 1
    assert assignments[0].plan_id == task.plan_id
    assert len(assignments[0].segments) == 1
    segment = assignments[0].segments[0]
    assert segment.start_time >= utc(2026, 6, 7, 9, 0)
    assert segment.end_time <= utc(2026, 6, 7, 12, 0)
    assert (segment.end_time - segment.start_time).total_seconds() == 60 * 60
    assert (
        validate_full_assignment(
            assignment_input(tasks=(task,)),
            assignments,
        )
        is None
    )


def test_solve_single_component_avoids_overlap_with_occupied_interval() -> None:
    task = schedulable_task(
        duration_minutes=60,
        effective_time_windows=(window(utc(2026, 6, 7, 9, 0), utc(2026, 6, 7, 12, 0)),),
    )
    component = assignment_component(
        tasks=(task,),
        occupied_intervals=(occupied(utc(2026, 6, 7, 9, 0), utc(2026, 6, 7, 10, 0)),),
    )

    assignments = _solve_single_component(component)

    assert assignments is not None
    assert assignments[0].segments == (window(utc(2026, 6, 7, 10, 0), utc(2026, 6, 7, 11, 0)),)


def test_solve_single_component_respects_precedence_edge() -> None:
    predecessor_id = plan_id()
    successor_id = plan_id()
    morning = window(utc(2026, 6, 7, 9, 0), utc(2026, 6, 7, 12, 0))
    predecessor = schedulable_task(
        task_id=predecessor_id,
        duration_minutes=30,
        effective_time_windows=(morning,),
    )
    successor = schedulable_task(
        task_id=successor_id,
        duration_minutes=30,
        effective_time_windows=(morning,),
    )
    component = assignment_component(
        tasks=(predecessor, successor),
        precedence_edges=(
            PrecedenceEdge(
                predecessor_plan_id=predecessor_id,
                successor_plan_id=successor_id,
            ),
        ),
    )

    assignments = _solve_single_component(component)

    assert assignments is not None
    by_plan_id = {assignment.plan_id: assignment for assignment in assignments}
    predecessor_end = by_plan_id[predecessor_id].segments[0].end_time
    successor_start = by_plan_id[successor_id].segments[0].start_time
    assert predecessor_end <= successor_start


def test_solve_single_component_divisible_uses_two_chunks_when_needed() -> None:
    task = schedulable_task(
        duration_minutes=90,
        effective_time_windows=(
            window(utc(2026, 6, 7, 9, 0), utc(2026, 6, 7, 10, 0)),
            window(utc(2026, 6, 7, 11, 0), utc(2026, 6, 7, 12, 0)),
        ),
        divisible=True,
        minimum_chunk_size_minutes=30,
    )
    component = assignment_component(tasks=(task,))

    assignments = _solve_single_component(component)

    assert assignments is not None
    assert len(assignments[0].segments) >= 2
    total_minutes = sum(
        int((segment.end_time - segment.start_time).total_seconds() // 60)
        for segment in assignments[0].segments
    )
    assert total_minutes == 90
    assert (
        validate_full_assignment(
            assignment_input(tasks=(task,)),
            assignments,
        )
        is None
    )


def test_solve_single_component_returns_none_when_window_too_small() -> None:
    task = schedulable_task(
        duration_minutes=120,
        effective_time_windows=(window(utc(2026, 6, 7, 9, 0), utc(2026, 6, 7, 10, 0)),),
    )
    component = assignment_component(tasks=(task,))

    assignments = _solve_single_component(component)

    assert assignments is None

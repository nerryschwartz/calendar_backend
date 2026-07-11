from __future__ import annotations

from calendar_backend.scheduling.exact_cp_sat import (
    _solve_single_component,  # pyright: ignore[reportPrivateUsage]
)

from .conftest import assignment_component, plan_id, schedulable_task, utc, window


def test_stability_hint_beats_earlier_start() -> None:
    task_id = plan_id()
    task = schedulable_task(
        task_id=task_id,
        duration_minutes=60,
        effective_time_windows=(window(utc(2026, 6, 7, 9, 0), utc(2026, 6, 7, 12, 0)),),
    )
    component = assignment_component(
        tasks=(task,),
        previous_placements_by_task_id=(
            (task_id, (window(utc(2026, 6, 7, 10, 0), utc(2026, 6, 7, 11, 0)),)),
        ),
    )

    assignments = _solve_single_component(component)

    assert assignments is not None
    assert assignments[0].segments == (window(utc(2026, 6, 7, 10, 0), utc(2026, 6, 7, 11, 0)),)


def test_priority_path_orders_tasks_before_gap_consolidation() -> None:
    high_priority_id = plan_id()
    low_priority_id = plan_id()
    morning = window(utc(2026, 6, 7, 9, 0), utc(2026, 6, 7, 12, 0))
    high_priority = schedulable_task(
        task_id=high_priority_id,
        duration_minutes=30,
        effective_time_windows=(morning,),
        priority_path=(0,),
    )
    low_priority = schedulable_task(
        task_id=low_priority_id,
        duration_minutes=30,
        effective_time_windows=(morning,),
        priority_path=(1,),
    )
    component = assignment_component(tasks=(high_priority, low_priority))

    assignments = _solve_single_component(component)

    assert assignments is not None
    by_plan_id = {assignment.plan_id: assignment for assignment in assignments}
    assert by_plan_id[high_priority_id].segments[0].start_time == utc(2026, 6, 7, 9, 0)
    assert by_plan_id[low_priority_id].segments[0].start_time == utc(2026, 6, 7, 9, 30)


def test_divisible_prefers_single_segment_when_feasible() -> None:
    task = schedulable_task(
        duration_minutes=60,
        effective_time_windows=(window(utc(2026, 6, 7, 9, 0), utc(2026, 6, 7, 12, 0)),),
        divisible=True,
        minimum_chunk_size_minutes=30,
    )
    component = assignment_component(tasks=(task,))

    assignments = _solve_single_component(component)

    assert assignments is not None
    assert len(assignments[0].segments) == 1
    assert assignments[0].segments[0] == window(utc(2026, 6, 7, 9, 0), utc(2026, 6, 7, 10, 0))


def test_solve_without_stability_hints_still_returns_feasible_assignment() -> None:
    task = schedulable_task(
        duration_minutes=30,
        effective_time_windows=(window(utc(2026, 6, 7, 9, 0), utc(2026, 6, 7, 12, 0)),),
    )
    component = assignment_component(tasks=(task,))

    assignments = _solve_single_component(component)

    assert assignments is not None
    assert len(assignments) == 1
    assert assignments[0].segments[0].start_time == utc(2026, 6, 7, 9, 0)

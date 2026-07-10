from __future__ import annotations

from calendar_backend.domain.enums import SolverStatus
from calendar_backend.domain.errors import MessageCode
from calendar_backend.scheduling.feasibility import validate_full_assignment
from calendar_backend.scheduling.heuristic import HeuristicAssignmentSolver
from calendar_backend.scheduling.input import PrecedenceEdge

from .conftest import assignment_input, occupied, plan_id, schedulable_task, utc, window


def test_solve_empty_tasks_returns_feasible_with_no_assignments() -> None:
    result = HeuristicAssignmentSolver().solve(assignment_input(tasks=()))

    assert result.status == SolverStatus.FEASIBLE
    assert result.assignments == ()
    assert result.failure is None


def test_solve_single_indivisible_task_places_earliest_in_window() -> None:
    task = schedulable_task(
        duration_minutes=60,
        effective_time_windows=(window(utc(2026, 6, 7, 9, 0), utc(2026, 6, 7, 12, 0)),),
    )

    result = HeuristicAssignmentSolver().solve(assignment_input(tasks=(task,)))

    assert result.status == SolverStatus.FEASIBLE
    assert len(result.assignments) == 1
    assert result.assignments[0].segments == (
        window(utc(2026, 6, 7, 9, 0), utc(2026, 6, 7, 10, 0)),
    )


def test_solve_avoids_overlap_with_occupied_interval() -> None:
    task = schedulable_task(
        duration_minutes=60,
        effective_time_windows=(window(utc(2026, 6, 7, 9, 0), utc(2026, 6, 7, 12, 0)),),
    )
    assignment_input_value = assignment_input(
        tasks=(task,),
        occupied_intervals=(occupied(utc(2026, 6, 7, 9, 0), utc(2026, 6, 7, 10, 0)),),
    )

    result = HeuristicAssignmentSolver().solve(assignment_input_value)

    assert result.status == SolverStatus.FEASIBLE
    assert result.assignments[0].segments == (
        window(utc(2026, 6, 7, 10, 0), utc(2026, 6, 7, 11, 0)),
    )


def test_solve_assigns_multiple_tasks_without_overlap() -> None:
    morning = window(utc(2026, 6, 7, 9, 0), utc(2026, 6, 7, 12, 0))
    first = schedulable_task(
        duration_minutes=30, effective_time_windows=(morning,), priority_path=(0,)
    )
    second = schedulable_task(
        duration_minutes=30, effective_time_windows=(morning,), priority_path=(1,)
    )

    result = HeuristicAssignmentSolver().solve(assignment_input(tasks=(first, second)))

    assert result.status == SolverStatus.FEASIBLE
    assert len(result.assignments) == 2
    first_segments = result.assignments[0].segments
    second_segments = result.assignments[1].segments
    assert first_segments[0].end_time <= second_segments[0].start_time


def test_solve_orders_by_priority_path_then_plan_id() -> None:
    morning = window(utc(2026, 6, 7, 9, 0), utc(2026, 6, 7, 10, 0))
    high_priority = schedulable_task(
        duration_minutes=30,
        effective_time_windows=(morning,),
        priority_path=(0,),
    )
    low_priority = schedulable_task(
        duration_minutes=30,
        effective_time_windows=(morning,),
        priority_path=(1,),
    )

    result = HeuristicAssignmentSolver().solve(
        assignment_input(tasks=(low_priority, high_priority))
    )

    assert result.status == SolverStatus.FEASIBLE
    by_plan_id = {assignment.plan_id: assignment for assignment in result.assignments}
    assert by_plan_id[high_priority.plan_id].segments[0].start_time == utc(2026, 6, 7, 9, 0)
    assert by_plan_id[low_priority.plan_id].segments[0].start_time == utc(2026, 6, 7, 9, 30)


def test_solve_respects_precedence_edge() -> None:
    predecessor_id = plan_id()
    successor_id = plan_id()
    morning = window(utc(2026, 6, 7, 9, 0), utc(2026, 6, 7, 12, 0))
    predecessor = schedulable_task(
        task_id=predecessor_id,
        duration_minutes=30,
        effective_time_windows=(morning,),
        priority_path=(0,),
    )
    successor = schedulable_task(
        task_id=successor_id,
        duration_minutes=30,
        effective_time_windows=(morning,),
        priority_path=(1,),
    )
    assignment_input_value = assignment_input(
        tasks=(predecessor, successor),
        precedence_edges=(
            PrecedenceEdge(predecessor_plan_id=predecessor_id, successor_plan_id=successor_id),
        ),
    )

    result = HeuristicAssignmentSolver().solve(assignment_input_value)

    assert result.status == SolverStatus.FEASIBLE
    by_plan_id = {assignment.plan_id: assignment for assignment in result.assignments}
    predecessor_end = by_plan_id[predecessor_id].segments[0].end_time
    successor_start = by_plan_id[successor_id].segments[0].start_time
    assert predecessor_end <= successor_start


def test_solve_divisible_uses_multi_segment_when_needed() -> None:
    task = schedulable_task(
        duration_minutes=90,
        effective_time_windows=(
            window(utc(2026, 6, 7, 9, 0), utc(2026, 6, 7, 10, 0)),
            window(utc(2026, 6, 7, 11, 0), utc(2026, 6, 7, 12, 0)),
        ),
        divisible=True,
        minimum_chunk_size_minutes=30,
    )

    result = HeuristicAssignmentSolver().solve(assignment_input(tasks=(task,)))

    assert result.status == SolverStatus.FEASIBLE
    assert len(result.assignments[0].segments) == 2


def test_solve_divisible_prefers_contiguous_when_possible() -> None:
    task = schedulable_task(
        duration_minutes=60,
        effective_time_windows=(window(utc(2026, 6, 7, 9, 0), utc(2026, 6, 7, 12, 0)),),
        divisible=True,
        minimum_chunk_size_minutes=30,
    )

    result = HeuristicAssignmentSolver().solve(assignment_input(tasks=(task,)))

    assert result.status == SolverStatus.FEASIBLE
    assert len(result.assignments[0].segments) == 1


def test_solve_fails_when_no_effective_windows() -> None:
    task = schedulable_task(duration_minutes=30, effective_time_windows=())

    result = HeuristicAssignmentSolver().solve(assignment_input(tasks=(task,)))

    assert result.status == SolverStatus.INFEASIBLE
    assert result.failure is not None
    assert result.failure.code == MessageCode.NO_VALID_WINDOW_FOR_TASK


def test_solve_fails_insufficient_total_capacity() -> None:
    task = schedulable_task(
        duration_minutes=90,
        effective_time_windows=(window(utc(2026, 6, 7, 9, 0), utc(2026, 6, 7, 10, 30)),),
    )
    assignment_input_value = assignment_input(
        tasks=(task,),
        occupied_intervals=(occupied(utc(2026, 6, 7, 9, 0), utc(2026, 6, 7, 10, 0)),),
    )

    result = HeuristicAssignmentSolver().solve(assignment_input_value)

    assert result.status == SolverStatus.INFEASIBLE
    assert result.failure is not None
    assert result.failure.code == MessageCode.INSUFFICIENT_TOTAL_CAPACITY


def test_solve_feasible_includes_heuristic_feasible_warning() -> None:
    task = schedulable_task(
        duration_minutes=30,
        effective_time_windows=(window(utc(2026, 6, 7, 9, 0), utc(2026, 6, 7, 12, 0)),),
    )

    result = HeuristicAssignmentSolver().solve(assignment_input(tasks=(task,)))

    assert result.status == SolverStatus.FEASIBLE
    assert any(warning.code == MessageCode.HEURISTIC_FEASIBLE for warning in result.warnings)


def test_solve_infeasible_returns_empty_assignments() -> None:
    task = schedulable_task(duration_minutes=30, effective_time_windows=())

    result = HeuristicAssignmentSolver().solve(assignment_input(tasks=(task,)))

    assert result.status == SolverStatus.INFEASIBLE
    assert result.assignments == ()
    assert result.failure is not None


def test_solve_is_deterministic_for_identical_input() -> None:
    task = schedulable_task(
        duration_minutes=45,
        effective_time_windows=(window(utc(2026, 6, 7, 9, 0), utc(2026, 6, 7, 12, 0)),),
    )
    assignment_input_value = assignment_input(tasks=(task,))
    solver = HeuristicAssignmentSolver()

    first = solver.solve(assignment_input_value)
    second = solver.solve(assignment_input_value)

    assert first == second


def test_feasible_result_passes_validate_full_assignment() -> None:
    morning = window(utc(2026, 6, 7, 9, 0), utc(2026, 6, 7, 12, 0))
    first = schedulable_task(
        duration_minutes=30, effective_time_windows=(morning,), priority_path=(0,)
    )
    second = schedulable_task(
        duration_minutes=30, effective_time_windows=(morning,), priority_path=(1,)
    )
    assignment_input_value = assignment_input(tasks=(first, second))

    result = HeuristicAssignmentSolver().solve(assignment_input_value)

    assert result.status == SolverStatus.FEASIBLE
    assert validate_full_assignment(assignment_input_value, result.assignments) is None

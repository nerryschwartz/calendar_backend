from __future__ import annotations

from unittest.mock import patch

from calendar_backend.domain.enums import SolverStatus
from calendar_backend.domain.errors import MessageCode
from calendar_backend.scheduling.exact_cp_sat import (
    ExactAssignmentSolver,
    _ComponentSolveResult,  # pyright: ignore[reportPrivateUsage]
)
from calendar_backend.scheduling.input import SolverLimits
from calendar_backend.scheduling.types import (
    AssignmentSolverResult,
    TaskAssignment,
    exact_feasible_result,
    exact_optimal_result,
    is_usable_solver_result,
    weakest_solver_status,
)

from .conftest import assignment_input, plan_id, schedulable_task, utc, window


def test_is_usable_solver_result_accepts_optimal_and_feasible_without_failure() -> None:
    optimal = exact_optimal_result(())
    feasible = exact_feasible_result(())

    assert is_usable_solver_result(optimal) is True
    assert is_usable_solver_result(feasible) is True


def test_is_usable_solver_result_rejects_infeasible_and_failure() -> None:
    infeasible = AssignmentSolverResult(
        status=SolverStatus.INFEASIBLE,
        assignments=(),
        warnings=(),
        failure=None,
    )

    assert is_usable_solver_result(infeasible) is False


def test_weakest_solver_status_all_optimal_returns_optimal() -> None:
    assert weakest_solver_status(SolverStatus.OPTIMAL, SolverStatus.OPTIMAL) == SolverStatus.OPTIMAL


def test_weakest_solver_status_any_feasible_returns_feasible() -> None:
    assert (
        weakest_solver_status(SolverStatus.OPTIMAL, SolverStatus.FEASIBLE) == SolverStatus.FEASIBLE
    )


def test_exact_optimal_result_has_no_warnings() -> None:
    result = exact_optimal_result(())

    assert result.status == SolverStatus.OPTIMAL
    assert result.warnings == ()
    assert result.failure is None


def test_exact_feasible_result_includes_not_proven_warning() -> None:
    result = exact_feasible_result(())

    assert result.status == SolverStatus.FEASIBLE
    assert len(result.warnings) == 1
    assert result.warnings[0].code == MessageCode.FEASIBLE_NOT_PROVEN_OPTIMAL


def test_exact_feasible_result_includes_limit_warning_when_requested() -> None:
    result = exact_feasible_result((), limit_reached=True)

    assert [warning.code for warning in result.warnings] == [
        MessageCode.FEASIBLE_NOT_PROVEN_OPTIMAL,
        MessageCode.SOLVER_LIMIT_REACHED,
    ]


def test_solve_empty_tasks_returns_exact_optimal_without_warnings() -> None:
    result = ExactAssignmentSolver().solve(assignment_input(tasks=()))

    assert result.status == SolverStatus.OPTIMAL
    assert result.assignments == ()
    assert result.warnings == ()
    assert is_usable_solver_result(result) is True


def test_solve_returns_optimal_for_tiny_real_model() -> None:
    task = schedulable_task(
        duration_minutes=30,
        effective_time_windows=(window(utc(2026, 6, 7, 9, 0), utc(2026, 6, 7, 12, 0)),),
    )

    result = ExactAssignmentSolver().solve(assignment_input(tasks=(task,)))

    assert result.status == SolverStatus.OPTIMAL
    assert len(result.assignments) == 1
    assert result.warnings == ()
    assert is_usable_solver_result(result) is True


def test_solve_model_size_guard_returns_not_usable_without_failure() -> None:
    task = schedulable_task(
        duration_minutes=60,
        effective_time_windows=(window(utc(2026, 6, 7, 9, 0), utc(2026, 6, 7, 18, 0)),),
    )
    limits = SolverLimits(time_limit_seconds=30, model_size_limit=1)

    result = ExactAssignmentSolver().solve(
        assignment_input(tasks=(task,), solver_limits=limits),
    )

    assert result.status == SolverStatus.INFEASIBLE
    assert result.assignments == ()
    assert result.failure is None
    assert is_usable_solver_result(result) is False


def test_solve_component_failure_returns_not_usable_with_failure_message() -> None:
    task = schedulable_task(
        duration_minutes=60,
        effective_time_windows=(),
    )

    result = ExactAssignmentSolver().solve(assignment_input(tasks=(task,)))

    assert result.status == SolverStatus.INFEASIBLE
    assert result.assignments == ()
    assert result.failure is not None
    assert result.failure.code == MessageCode.SOLVER_FAILED_TO_FIND_FEASIBLE_ASSIGNMENT
    assert is_usable_solver_result(result) is False


def test_solve_aggregates_mixed_component_statuses_to_feasible() -> None:
    first_id = plan_id()
    second_id = plan_id()
    morning = window(utc(2026, 6, 7, 9, 0), utc(2026, 6, 7, 12, 0))
    first = schedulable_task(
        task_id=first_id,
        duration_minutes=30,
        effective_time_windows=(morning,),
    )
    second = schedulable_task(
        task_id=second_id,
        duration_minutes=30,
        effective_time_windows=(morning,),
    )
    optimal_assignments = (
        TaskAssignment(
            plan_id=first_id,
            segments=(window(utc(2026, 6, 7, 9, 0), utc(2026, 6, 7, 9, 30)),),
        ),
    )
    feasible_assignments = (
        TaskAssignment(
            plan_id=second_id,
            segments=(window(utc(2026, 6, 7, 9, 30), utc(2026, 6, 7, 10, 0)),),
        ),
    )

    with patch(
        "calendar_backend.scheduling.exact_cp_sat._solve_component_with_status",
        side_effect=[
            _ComponentSolveResult(optimal_assignments, SolverStatus.OPTIMAL, False),
            _ComponentSolveResult(feasible_assignments, SolverStatus.FEASIBLE, True),
        ],
    ):
        result = ExactAssignmentSolver().solve(
            assignment_input(
                tasks=(first, second),
                precedence_edges=(),
            ),
        )

    assert result.status == SolverStatus.FEASIBLE
    assert len(result.assignments) == 2
    assert MessageCode.FEASIBLE_NOT_PROVEN_OPTIMAL in {warning.code for warning in result.warnings}
    assert MessageCode.SOLVER_LIMIT_REACHED in {warning.code for warning in result.warnings}
    assert is_usable_solver_result(result) is True


def test_solve_single_component_limit_reached_yields_feasible_usable_result() -> None:
    task_id = plan_id()
    task = schedulable_task(
        task_id=task_id,
        duration_minutes=30,
        effective_time_windows=(window(utc(2026, 6, 7, 9, 0), utc(2026, 6, 7, 12, 0)),),
    )
    assignments = (
        TaskAssignment(
            plan_id=task_id,
            segments=(window(utc(2026, 6, 7, 9, 0), utc(2026, 6, 7, 9, 30)),),
        ),
    )

    with patch(
        "calendar_backend.scheduling.exact_cp_sat._solve_component_with_status",
        return_value=_ComponentSolveResult(assignments, SolverStatus.FEASIBLE, True),
    ):
        result = ExactAssignmentSolver().solve(assignment_input(tasks=(task,)))

    assert result.status == SolverStatus.FEASIBLE
    assert result.assignments == assignments
    assert MessageCode.FEASIBLE_NOT_PROVEN_OPTIMAL in {warning.code for warning in result.warnings}
    assert MessageCode.SOLVER_LIMIT_REACHED in {warning.code for warning in result.warnings}
    assert is_usable_solver_result(result) is True

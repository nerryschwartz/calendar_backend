"""Session-free assignment solver interface and result DTOs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from calendar_backend.domain.enums import SolverStatus
from calendar_backend.domain.errors import MessageCode, ServiceMessage
from calendar_backend.domain.ids import PlanID
from calendar_backend.domain.time import TimeWindow
from calendar_backend.scheduling.input import AssignmentInput


@dataclass(frozen=True)
class TaskAssignment:
    plan_id: PlanID
    segments: tuple[TimeWindow, ...]


@dataclass(frozen=True)
class AssignmentSolverResult:
    status: SolverStatus
    assignments: tuple[TaskAssignment, ...]
    warnings: tuple[ServiceMessage, ...]
    failure: ServiceMessage | None


class AssignmentSolver(Protocol):
    def solve(self, assignment_input: AssignmentInput) -> AssignmentSolverResult: ...


def feasible_result(assignments: tuple[TaskAssignment, ...]) -> AssignmentSolverResult:
    return AssignmentSolverResult(
        status=SolverStatus.FEASIBLE,
        assignments=assignments,
        warnings=(
            ServiceMessage(
                code=MessageCode.HEURISTIC_FEASIBLE,
                message="Heuristic solver produced a feasible assignment",
                details={},
            ),
        ),
        failure=None,
    )


def infeasible_result(failure: ServiceMessage) -> AssignmentSolverResult:
    return AssignmentSolverResult(
        status=SolverStatus.INFEASIBLE,
        assignments=(),
        warnings=(),
        failure=failure,
    )


def is_usable_solver_result(result: AssignmentSolverResult) -> bool:
    return result.status in (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE) and result.failure is None


def weakest_solver_status(*statuses: SolverStatus) -> SolverStatus:
    if not statuses:
        return SolverStatus.INFEASIBLE
    if any(status == SolverStatus.FEASIBLE for status in statuses):
        return SolverStatus.FEASIBLE
    if all(status == SolverStatus.OPTIMAL for status in statuses):
        return SolverStatus.OPTIMAL
    return SolverStatus.INFEASIBLE


def exact_optimal_result(
    assignments: tuple[TaskAssignment, ...],
) -> AssignmentSolverResult:
    return AssignmentSolverResult(
        status=SolverStatus.OPTIMAL,
        assignments=assignments,
        warnings=(),
        failure=None,
    )


def exact_feasible_result(
    assignments: tuple[TaskAssignment, ...],
    *,
    limit_reached: bool = False,
) -> AssignmentSolverResult:
    warnings: list[ServiceMessage] = [
        ServiceMessage(
            code=MessageCode.FEASIBLE_NOT_PROVEN_OPTIMAL,
            message="Exact solver produced a feasible assignment without optimality proof",
            details={},
        ),
    ]
    if limit_reached:
        warnings.append(
            ServiceMessage(
                code=MessageCode.SOLVER_LIMIT_REACHED,
                message="Exact solver stopped at the configured time limit",
                details={},
            ),
        )
    return AssignmentSolverResult(
        status=SolverStatus.FEASIBLE,
        assignments=assignments,
        warnings=tuple(warnings),
        failure=None,
    )

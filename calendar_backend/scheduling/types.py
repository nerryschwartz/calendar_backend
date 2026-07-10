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

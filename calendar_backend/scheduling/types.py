"""Session-free assignment solver interface and result DTOs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from calendar_backend.domain.enums import SolverStatus
from calendar_backend.domain.errors import ServiceMessage
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

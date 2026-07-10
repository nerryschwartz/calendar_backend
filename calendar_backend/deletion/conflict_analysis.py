"""Deterministic conflict analysis after assignment solver failure."""

from __future__ import annotations

from calendar_backend.domain.assignment import analyze_assignment_conflicts
from calendar_backend.domain.deletion import AssignmentConflict
from calendar_backend.domain.resolution import ResolveTasksResult
from calendar_backend.domain.results import ServiceResult, ok
from calendar_backend.scheduling.input import AssignmentInput
from calendar_backend.scheduling.types import AssignmentSolverResult


class ConflictAnalysisService:
    """Map solver failure output to analyzed assignment conflicts (session-free)."""

    def analyze(
        self,
        assignment_input: AssignmentInput,
        resolved: ResolveTasksResult,
        solver_result: AssignmentSolverResult,
    ) -> ServiceResult[tuple[AssignmentConflict, ...]]:
        return ok(
            analyze_assignment_conflicts(
                assignment_input,
                resolved,
                solver_result,
            )
        )

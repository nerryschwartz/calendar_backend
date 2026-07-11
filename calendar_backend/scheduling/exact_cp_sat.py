"""OR-Tools CP-SAT exact assignment solver.

All ortools imports for the scheduling package must live in this module only.
"""

from __future__ import annotations

from ortools.sat.python import cp_model  # noqa: F401  # pyright: ignore[reportUnusedImport]

from calendar_backend.scheduling.input import AssignmentInput
from calendar_backend.scheduling.types import AssignmentSolverResult, feasible_result


class ExactAssignmentSolver:
    """CP-SAT exact assignment solver; full implementation deferred to later slices."""

    def solve(self, assignment_input: AssignmentInput) -> AssignmentSolverResult:
        if not assignment_input.tasks:
            return feasible_result(())

        raise NotImplementedError("ExactAssignmentSolver.solve is not implemented until slice 3")

from __future__ import annotations

import subprocess
import sys

from calendar_backend.domain.enums import SolverStatus
from calendar_backend.scheduling.exact_cp_sat import ExactAssignmentSolver

from .conftest import assignment_input


def test_non_exact_scheduling_modules_do_not_import_ortools() -> None:
    script = """
import sys

import calendar_backend.scheduling.decomposition
import calendar_backend.scheduling.feasibility
import calendar_backend.scheduling.heuristic
import calendar_backend.scheduling.input
import calendar_backend.scheduling.types

assert "ortools" not in sys.modules
"""
    subprocess.run([sys.executable, "-c", script], check=True)


def test_exact_assignment_solver_is_importable() -> None:
    solver = ExactAssignmentSolver()
    assert solver is not None


def test_exact_solver_solve_empty_tasks_returns_optimal() -> None:
    result = ExactAssignmentSolver().solve(assignment_input(tasks=()))

    assert result.status == SolverStatus.OPTIMAL
    assert result.assignments == ()
    assert result.failure is None

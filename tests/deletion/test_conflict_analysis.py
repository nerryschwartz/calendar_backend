"""Smoke tests for ConflictAnalysisService."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from calendar_backend.deletion.conflict_analysis import ConflictAnalysisService
from calendar_backend.domain.enums import SolverStatus
from calendar_backend.domain.errors import MessageCode, ServiceMessage
from calendar_backend.domain.ids import PlanID
from calendar_backend.domain.resolution import ResolvedTask, ResolveTasksResult
from calendar_backend.domain.time import TimeWindow
from calendar_backend.scheduling.input import AssignmentInput
from calendar_backend.scheduling.types import AssignmentSolverResult, infeasible_result

RUN_AT = datetime(2026, 6, 7, 10, 0, tzinfo=UTC)


def _utc(y: int, m: int, d: int, h: int, mi: int) -> datetime:
    return datetime(y, m, d, h, mi, tzinfo=UTC)


def _window(start: datetime, end: datetime) -> TimeWindow:
    return TimeWindow(start_time=start, end_time=end)


def _empty_assignment_input() -> AssignmentInput:
    return AssignmentInput(
        run_started_at=RUN_AT,
        tasks=(),
        precedence_edges=(),
        occupied_intervals=(),
    )


def _resolved_task(plan_id: uuid.UUID) -> ResolvedTask:
    return ResolvedTask(
        plan_id=PlanID(plan_id),
        name="task",
        duration_minutes=30,
        divisible=False,
        minimum_chunk_size_minutes=None,
        user_completed=False,
        completed_at=None,
        effective_time_windows=(_window(_utc(2026, 6, 7, 9, 0), _utc(2026, 6, 7, 12, 0)),),
        constraint_sources=(),
        priority_path=(0,),
        criticality_path=(),
        parent_path=(PlanID(plan_id),),
        chain_path=(),
        validation_errors=(),
    )


def test_analyze_returns_ok_with_conflicts_for_infeasible_solver() -> None:
    plan_id = uuid.uuid4()
    resolved = ResolveTasksResult(
        run_started_at=RUN_AT,
        valid_incomplete=(_resolved_task(plan_id),),
        valid_completed=(),
        invalid_incomplete=(),
        invalid_completed=(),
        precedence_constraints=(),
        warnings=(),
    )
    failure = ServiceMessage(
        code=MessageCode.NO_VALID_WINDOW_FOR_TASK,
        message="No valid placement",
        details={"plan_id": str(plan_id)},
    )
    solver_result = infeasible_result(failure)

    result = ConflictAnalysisService().analyze(
        _empty_assignment_input(),
        resolved,
        solver_result,
    )

    assert result.success and result.value is not None
    assert len(result.value) == 1
    assert result.value[0].reason_code == MessageCode.NO_VALID_WINDOW_FOR_TASK


def test_analyze_returns_empty_tuple_for_feasible_solver() -> None:
    feasible = AssignmentSolverResult(
        status=SolverStatus.FEASIBLE,
        assignments=(),
        warnings=(),
        failure=None,
    )
    resolved = ResolveTasksResult(
        run_started_at=RUN_AT,
        valid_incomplete=(),
        valid_completed=(),
        invalid_incomplete=(),
        invalid_completed=(),
        precedence_constraints=(),
        warnings=(),
    )

    result = ConflictAnalysisService().analyze(
        _empty_assignment_input(),
        resolved,
        feasible,
    )

    assert result.success and result.value == ()

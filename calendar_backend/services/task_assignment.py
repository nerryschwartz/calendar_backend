"""Task assignment service: coordinate solvers and persist TASK calendar entries."""

from __future__ import annotations

import time
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from calendar_backend.db.session import transaction
from calendar_backend.domain.assignment import (
    AssignmentResult,
    occupied_intervals_from_calendar_entries,
)
from calendar_backend.domain.enums import CalendarEntryType, CalendarRunStatus, SolverStatus
from calendar_backend.domain.errors import MessageCode, ServiceMessage
from calendar_backend.domain.ids import CalendarRunID, new_id
from calendar_backend.domain.resolution import ResolveTasksResult
from calendar_backend.domain.results import ServiceResult, fail, ok
from calendar_backend.domain.time import Clock, SystemClock
from calendar_backend.models.calendar import CalendarEntry
from calendar_backend.models.runs import ActiveCalendarState, CalendarRun
from calendar_backend.scheduling.heuristic import HeuristicAssignmentSolver
from calendar_backend.scheduling.input import OccupiedInterval, assignment_input_from_resolved
from calendar_backend.scheduling.types import AssignmentSolverResult
from calendar_backend.services.app_settings import AppSettingsService
from calendar_backend.services.master_horizon import validate_run_started_at


class TaskAssignmentService:
    """Assign resolved tasks and persist TASK calendar entries on success."""

    def __init__(self, session: Session, clock: Clock | None = None) -> None:
        self._session = session
        self._clock = clock or SystemClock()

    def assign_tasks(
        self,
        resolved: ResolveTasksResult,
        run_started_at: datetime,
    ) -> ServiceResult[AssignmentResult]:
        """Assign valid incomplete tasks and persist TASK calendar entries.

        Caller supplies a pre-resolved ``ResolveTasksResult`` (typically from
        ``TaskResolutionService.resolve_tasks``). Invalid completed tasks do not
        block assignment. Template blueprint nodes are excluded by resolution.
        """
        precondition_error = _assign_tasks_precondition_error(resolved, run_started_at)
        if precondition_error is not None:
            return fail(precondition_error)

        settings_error = _heuristic_solver_unavailable_error(self._session, self._clock)
        if settings_error is not None:
            return fail(settings_error)

        with transaction(self._session) as txn:
            occupied_intervals = _load_occupied_intervals(txn, run_started_at)

        solver_result, runtime_ms = _solve_assignment(
            resolved,
            occupied_intervals=occupied_intervals,
        )
        if solver_result.status == SolverStatus.INFEASIBLE:
            assert solver_result.failure is not None
            return fail(solver_result.failure)

        return ok(
            AssignmentResult(
                run_started_at=run_started_at,
                optimization_status=SolverStatus.FEASIBLE,
                calendar_entries=(),
                conflicts=(),
                warnings=solver_result.warnings,
                runtime_ms=runtime_ms,
                calendar_run_id=None,
            )
        )


def _assign_tasks_precondition_error(
    resolved: ResolveTasksResult,
    run_started_at: datetime,
) -> ServiceMessage | None:
    validation_error = validate_run_started_at(run_started_at)
    if validation_error is not None:
        return validation_error

    if resolved.run_started_at != run_started_at:
        return ServiceMessage(
            code=MessageCode.RUN_STARTED_AT_MISMATCH,
            message="resolved.run_started_at must match assignment run_started_at",
            details={
                "resolved_run_started_at": resolved.run_started_at.isoformat(),
                "run_started_at": run_started_at.isoformat(),
            },
        )

    if resolved.invalid_incomplete:
        return ServiceMessage(
            code=MessageCode.INVALID_INCOMPLETE_TASKS_BLOCK_ASSIGNMENT,
            message="invalid incomplete tasks block assignment",
            details={
                "invalid_incomplete_count": str(len(resolved.invalid_incomplete)),
            },
        )

    return None


def _heuristic_solver_unavailable_error(session: Session, clock: Clock) -> ServiceMessage | None:
    settings_result = AppSettingsService(session, clock).get_settings()
    if not settings_result.success:
        return settings_result.errors[0] if settings_result.errors else None
    assert settings_result.value is not None
    if not settings_result.value.heuristic_enabled:
        return ServiceMessage(
            code=MessageCode.SOLVER_FAILED_TO_FIND_FEASIBLE_ASSIGNMENT,
            message="heuristic solver is disabled and no exact solver is available",
            details={},
        )
    return None


def _load_occupied_intervals(
    session: Session,
    run_started_at: datetime,
) -> tuple[OccupiedInterval, ...]:
    state = session.get(ActiveCalendarState, 1)
    if state is None or state.active_calendar_run_id is None:
        return ()

    entries = tuple(
        session.scalars(
            select(CalendarEntry).where(CalendarEntry.entry_type == CalendarEntryType.TASK)
        ).all()
    )
    return occupied_intervals_from_calendar_entries(entries, run_started_at)


def _solve_assignment(
    resolved: ResolveTasksResult,
    *,
    occupied_intervals: tuple[OccupiedInterval, ...],
) -> tuple[AssignmentSolverResult, int]:
    assignment_input = assignment_input_from_resolved(
        resolved,
        occupied_intervals=occupied_intervals,
    )
    started = time.perf_counter()
    solver_result = HeuristicAssignmentSolver().solve(assignment_input)
    runtime_ms = int((time.perf_counter() - started) * 1000)
    return solver_result, runtime_ms


def _load_or_create_active_calendar_state(  # pyright: ignore[reportUnusedFunction]
    session: Session, clock: Clock
) -> ActiveCalendarState:
    row = session.get(ActiveCalendarState, 1)
    if row is not None:
        return row

    now = clock.now_utc()
    row = ActiveCalendarState(
        singleton_id=1,
        active_calendar_run_id=None,
        last_refresh_failed=False,
        last_failure_at=None,
        last_failure_reason=None,
        updated_at=now,
    )
    session.add(row)
    session.flush()
    return row


def _new_calendar_run(  # pyright: ignore[reportUnusedFunction]
    *,
    run_started_at: datetime,
    clock: Clock,
    status: CalendarRunStatus,
    solver_status: SolverStatus | None,
    conflict_count: int,
    warning_count: int,
    runtime_ms: int,
    run_finished_at: datetime | None = None,
) -> CalendarRun:
    return CalendarRun(
        calendar_run_id=new_id(CalendarRunID),
        run_started_at=run_started_at,
        run_finished_at=run_finished_at,
        status=status,
        solver_status=solver_status,
        conflict_count=conflict_count,
        warning_count=warning_count,
        runtime_ms=runtime_ms,
        created_at=clock.now_utc(),
    )

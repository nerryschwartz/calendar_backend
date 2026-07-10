"""Task assignment service: coordinate solvers and persist TASK calendar entries."""

from __future__ import annotations

import time
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from calendar_backend.db.session import transaction
from calendar_backend.domain.assignment import (
    AssignmentResult,
    calendar_entry_dto_from_row,
    calendar_entry_insert_specs_from_assignments,
    occupied_intervals_from_calendar_entries,
)
from calendar_backend.domain.enums import CalendarEntryType, CalendarRunStatus, SolverStatus
from calendar_backend.domain.errors import MessageCode, ServiceMessage
from calendar_backend.domain.ids import CalendarEntryID, CalendarRunID, new_id
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

        with transaction(self._session) as txn:
            assignment_result = _persist_successful_assignment(
                txn,
                self._clock,
                run_started_at=run_started_at,
                resolved=resolved,
                solver_result=solver_result,
                runtime_ms=runtime_ms,
            )
        return ok(assignment_result)


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


def _persist_successful_assignment(
    session: Session,
    clock: Clock,
    *,
    run_started_at: datetime,
    resolved: ResolveTasksResult,
    solver_result: AssignmentSolverResult,
    runtime_ms: int,
) -> AssignmentResult:
    resolved_tasks_by_id = {task.plan_id: task for task in resolved.valid_incomplete}
    insert_specs = calendar_entry_insert_specs_from_assignments(
        solver_result.assignments,
        resolved_tasks_by_id,
    )
    now = clock.now_utc()

    session.execute(
        delete(CalendarEntry).where(
            CalendarEntry.entry_type == CalendarEntryType.TASK,
            CalendarEntry.start_time >= run_started_at,
        )
    )

    calendar_run = _new_calendar_run(
        run_started_at=run_started_at,
        clock=clock,
        status=CalendarRunStatus.SUCCESS,
        solver_status=SolverStatus.FEASIBLE,
        conflict_count=0,
        warning_count=len(solver_result.warnings),
        runtime_ms=runtime_ms,
        run_finished_at=now,
    )
    session.add(calendar_run)
    session.flush()

    inserted_entries: list[CalendarEntry] = []
    for spec in insert_specs:
        entry = CalendarEntry(
            calendar_entry_id=new_id(CalendarEntryID),
            entry_type=CalendarEntryType.TASK,
            start_time=spec.start_time,
            end_time=spec.end_time,
            source_plan_id=spec.source_plan_id,
            source_free_time_activity_id=None,
            calendar_run_id=calendar_run.calendar_run_id,
            display_label=spec.display_label,
            created_at=now,
            updated_at=now,
        )
        session.add(entry)
        inserted_entries.append(entry)

    active_state = _load_or_create_active_calendar_state(session, clock)
    active_state.active_calendar_run_id = calendar_run.calendar_run_id
    active_state.last_refresh_failed = False
    active_state.last_failure_at = None
    active_state.last_failure_reason = None
    active_state.updated_at = now
    session.flush()

    return AssignmentResult(
        run_started_at=run_started_at,
        optimization_status=SolverStatus.FEASIBLE,
        calendar_entries=tuple(calendar_entry_dto_from_row(entry) for entry in inserted_entries),
        conflicts=(),
        warnings=solver_result.warnings,
        runtime_ms=runtime_ms,
        calendar_run_id=CalendarRunID(calendar_run.calendar_run_id),
    )


def _load_or_create_active_calendar_state(session: Session, clock: Clock) -> ActiveCalendarState:
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


def _new_calendar_run(
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

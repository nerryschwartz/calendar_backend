"""Task assignment service: coordinate solvers and persist TASK calendar entries."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from calendar_backend.domain.assignment import AssignmentResult
from calendar_backend.domain.enums import CalendarRunStatus, SolverStatus
from calendar_backend.domain.errors import MessageCode, ServiceMessage
from calendar_backend.domain.ids import CalendarRunID, new_id
from calendar_backend.domain.resolution import ResolveTasksResult
from calendar_backend.domain.results import ServiceResult, fail
from calendar_backend.domain.time import Clock, SystemClock
from calendar_backend.models.runs import ActiveCalendarState, CalendarRun
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
        validation_error = validate_run_started_at(run_started_at)
        if validation_error is not None:
            return fail(validation_error)

        if resolved.run_started_at != run_started_at:
            return fail(
                ServiceMessage(
                    code=MessageCode.RUN_STARTED_AT_MISMATCH,
                    message="resolved.run_started_at must match assignment run_started_at",
                    details={
                        "resolved_run_started_at": resolved.run_started_at.isoformat(),
                        "run_started_at": run_started_at.isoformat(),
                    },
                )
            )

        if resolved.invalid_incomplete:
            return fail(
                ServiceMessage(
                    code=MessageCode.INVALID_INCOMPLETE_TASKS_BLOCK_ASSIGNMENT,
                    message="invalid incomplete tasks block assignment",
                    details={
                        "invalid_incomplete_count": str(len(resolved.invalid_incomplete)),
                    },
                )
            )

        raise NotImplementedError("Slice 2: heuristic solver integration")


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

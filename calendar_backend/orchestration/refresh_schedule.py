"""Orchestration service: compose resolution, task assignment, and free-time assignment."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete
from sqlalchemy.orm import Session

from calendar_backend.db.session import transaction
from calendar_backend.domain.enums import CalendarEntryType, LastFailureReason
from calendar_backend.domain.orchestration import RefreshScheduleResult
from calendar_backend.domain.results import ServiceResult, fail, ok
from calendar_backend.domain.time import Clock, SystemClock
from calendar_backend.models.calendar import CalendarEntry
from calendar_backend.models.runs import ActiveCalendarState
from calendar_backend.services.free_time_assignment import FreeTimeAssignmentService
from calendar_backend.services.task_assignment import TaskAssignmentService
from calendar_backend.services.task_resolution import TaskResolutionService


class OrchestrationService:
    """Compose refresh_schedule from task resolution, assignment, and free-time services.

    Manual invocation only in V1; other services do not call this automatically.
    ``run_started_at`` is validated at the resolution boundary (first pipeline stage).
    """

    def __init__(self, session: Session, clock: Clock | None = None) -> None:
        self._session = session
        self._clock = clock or SystemClock()

    def refresh_schedule(
        self,
        run_started_at: datetime,
    ) -> ServiceResult[RefreshScheduleResult]:
        """Run the full refresh pipeline: resolve, assign tasks, assign free time."""
        resolve_result = TaskResolutionService(self._session, self._clock).resolve_tasks(
            run_started_at
        )
        if not resolve_result.success or resolve_result.value is None:
            return fail(*resolve_result.errors)

        resolved = resolve_result.value
        assign_result = TaskAssignmentService(self._session, self._clock).assign_tasks(
            resolved,
            run_started_at,
        )
        if not assign_result.success:
            if assign_result.value is None:
                _persist_assignment_precondition_failure(self._session, self._clock)
                return fail(
                    *assign_result.errors,
                    _value=RefreshScheduleResult(
                        run_started_at=run_started_at,
                        resolved=resolved,
                        assignment=None,
                        free_time=None,
                    ),
                )
            return fail(
                *assign_result.errors,
                _value=RefreshScheduleResult(
                    run_started_at=run_started_at,
                    resolved=resolved,
                    assignment=assign_result.value,
                    free_time=None,
                ),
            )

        assignment = assign_result.value
        assert assignment is not None

        free_time_result = FreeTimeAssignmentService(self._session, self._clock).assign_free_time(
            run_started_at
        )
        if not free_time_result.success or free_time_result.value is None:
            _persist_partial_free_time_failure(
                self._session,
                self._clock,
                run_started_at=run_started_at,
            )
            return fail(
                *free_time_result.errors,
                _value=RefreshScheduleResult(
                    run_started_at=run_started_at,
                    resolved=resolved,
                    assignment=assignment,
                    free_time=None,
                ),
            )

        return ok(
            RefreshScheduleResult(
                run_started_at=run_started_at,
                resolved=resolved,
                assignment=assignment,
                free_time=free_time_result.value,
            )
        )


def _persist_partial_free_time_failure(
    session: Session,
    clock: Clock,
    *,
    run_started_at: datetime,
) -> None:
    """Clear future FREE_TIME and record partial failure after successful task assignment."""
    with transaction(session):
        session.execute(
            delete(CalendarEntry).where(
                CalendarEntry.entry_type == CalendarEntryType.FREE_TIME,
                CalendarEntry.start_time >= run_started_at,
            )
        )
        now = clock.now_utc()
        active_state = _load_or_create_active_calendar_state(session, clock)
        active_state.last_refresh_failed = True
        active_state.last_failure_at = now
        active_state.last_failure_reason = LastFailureReason.FREE_TIME_ASSIGNMENT_FAILED
        active_state.updated_at = now


def _persist_assignment_precondition_failure(session: Session, clock: Clock) -> None:
    """Record assignment precondition failure without mutating calendar rows or active run."""
    with transaction(session):
        now = clock.now_utc()
        active_state = _load_or_create_active_calendar_state(session, clock)
        active_state.last_refresh_failed = True
        active_state.last_failure_at = now
        active_state.last_failure_reason = LastFailureReason.ASSIGNMENT_PRECONDITION_FAILED
        active_state.updated_at = now


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

"""Orchestration service: compose V2 refresh_schedule pipeline."""

from __future__ import annotations

from datetime import datetime
from dataclasses import replace

from sqlalchemy import delete
from sqlalchemy.orm import Session

from calendar_backend.db.session import transaction
from calendar_backend.domain.enums import (
    CalendarEntryType,
    CalendarRunStatus,
    LastFailureReason,
    SolverStatus,
)
from calendar_backend.domain.ids import CalendarRunID
from calendar_backend.domain.block_resolution import ResolveBlocksResult
from calendar_backend.domain.errors import MessageCode, ServiceMessage
from calendar_backend.domain.orchestration import RefreshScheduleResult
from calendar_backend.domain.prerequisites import validate_prerequisite_clones_for_refresh
from calendar_backend.domain.results import ServiceResult, fail, ok
from calendar_backend.domain.time import Clock, SystemClock
from calendar_backend.models.calendar import CalendarEntry
from calendar_backend.models.runs import ActiveCalendarState
from calendar_backend.services.block_assignment import BlockAssignmentService
from calendar_backend.services.block_resolution import BlockResolutionService
from calendar_backend.services.calendar_state import load_or_create_active_calendar_state
from calendar_backend.services.free_time_assignment import FreeTimeAssignmentService
from calendar_backend.services.repetition import RepetitionService
from calendar_backend.services.task_assignment import TaskAssignmentService, _new_calendar_run
from calendar_backend.services.task_resolution import TaskResolutionService, load_plan_graph


class OrchestrationService:
    """Compose refresh_schedule: blocks → tasks → free time on one calendar run."""

    def __init__(self, session: Session, clock: Clock | None = None) -> None:
        self._session = session
        self._clock = clock or SystemClock()

    def refresh_schedule(self, run_started_at: datetime) -> ServiceResult[RefreshScheduleResult]:
        # A block-only intermediate run must never become visible if task solving
        # fails. Keep a savepoint around publication, then persist failure metadata
        # outside it so the previous usable entries and run remain unchanged.
        blockers = tuple(
            repetition
            for repetition in RepetitionService(self._session).generation_status()
            if repetition.generated_at is None
        )
        if blockers:
            return fail(
                *(
                    ServiceMessage(
                        code=MessageCode.REPETITION_NOT_GENERATED,
                        message=f'Repetition "{repetition.name}" has not generated its instances',
                        details={
                            "repetition_plan_id": str(repetition.plan_id),
                            "name": repetition.name,
                        },
                    )
                    for repetition in blockers
                )
            )
        block_resolve_result = BlockResolutionService(self._session, self._clock).resolve_blocks(
            run_started_at
        )
        if not block_resolve_result.success or block_resolve_result.value is None:
            return fail(*block_resolve_result.errors)
        resolved_blocks = block_resolve_result.value
        with transaction(self._session) as txn:
            plans = load_plan_graph(txn)
        preflight_error = validate_prerequisite_clones_for_refresh(plans)
        if preflight_error is not None:
            _persist_preflight_failure(self._session, self._clock)
            return fail(
                preflight_error,
                _value=RefreshScheduleResult(
                    run_started_at, resolved_blocks, None, None, None, None
                ),
            )
        with transaction(self._session) as txn:
            connection = txn.connection()
            if (
                connection.dialect.name == "sqlite"
                and not connection.connection.driver_connection.in_transaction
            ):
                connection.exec_driver_sql("BEGIN")
            publication = txn.begin_nested()
            result = self._refresh_pipeline(run_started_at, resolved_blocks)
            value = result.value
            if result.success or (
                value is not None
                and value.assignment is not None
                and value.assignment.optimization_status
                in (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE)
            ):
                publication.commit()
                return result
            state = txn.get(ActiveCalendarState, 1)
            reason = (
                state.last_failure_reason
                if state is not None
                else LastFailureReason.ASSIGNMENT_PRECONDITION_FAILED
            )
            publication.rollback()
            txn.expire_all()
            if value is None and state is None:
                return result
            state = load_or_create_active_calendar_state(txn, self._clock)
            state.last_refresh_failed = True
            state.last_failure_at = self._clock.now_utc()
            state.last_failure_reason = reason or LastFailureReason.ASSIGNMENT_PRECONDITION_FAILED
            state.updated_at = self._clock.now_utc()
            if value is None or (value.block_assignment is None and value.assignment is None):
                return result
            attempted = value.assignment or value.block_assignment
            assert attempted is not None
            status = attempted.optimization_status
            if status in (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE):
                status = SolverStatus.UNKNOWN
            run = _new_calendar_run(
                run_started_at=run_started_at,
                clock=self._clock,
                status=CalendarRunStatus.FAILED,
                solver_status=status,
                conflict_count=len(value.assignment.conflicts) if value.assignment else 0,
                warning_count=len(attempted.warnings),
                runtime_ms=attempted.runtime_ms,
                run_finished_at=self._clock.now_utc(),
            )
            txn.add(run)
            txn.flush()
            run_id = CalendarRunID(run.calendar_run_id)
            return replace(
                result,
                value=replace(
                    value,
                    block_assignment=replace(
                        value.block_assignment, calendar_run_id=run_id, block_calendar_entries=()
                    )
                    if value.block_assignment
                    else None,
                    assignment=replace(
                        value.assignment, calendar_run_id=run_id, calendar_entries=()
                    )
                    if value.assignment
                    else None,
                ),
            )

    def _refresh_pipeline(  # noqa: PLR0911
        self,
        run_started_at: datetime,
        resolved_blocks: ResolveBlocksResult,
    ) -> ServiceResult[RefreshScheduleResult]:
        """Publish blocks/tasks together; preserve the existing free-time partial-failure policy."""
        prior_state = self._session.get(ActiveCalendarState, 1)
        previous_task_run_id = (
            CalendarRunID(prior_state.active_calendar_run_id)
            if prior_state is not None and prior_state.active_calendar_run_id is not None
            else None
        )
        block_assign_result = BlockAssignmentService(self._session, self._clock).assign_blocks(
            resolved_blocks,
            run_started_at,
        )
        if not block_assign_result.success:
            if block_assign_result.value is None:
                _persist_assignment_precondition_failure(self._session, self._clock)
                return fail(
                    *block_assign_result.errors,
                    _value=RefreshScheduleResult(
                        run_started_at=run_started_at,
                        resolved_blocks=resolved_blocks,
                        block_assignment=None,
                        resolved=None,
                        assignment=None,
                        free_time=None,
                    ),
                )
            return fail(
                *block_assign_result.errors,
                _value=RefreshScheduleResult(
                    run_started_at=run_started_at,
                    resolved_blocks=resolved_blocks,
                    block_assignment=block_assign_result.value,
                    resolved=None,
                    assignment=None,
                    free_time=None,
                ),
            )

        block_assignment = block_assign_result.value
        assert block_assignment is not None
        shared_run_id = block_assignment.calendar_run_id

        resolve_result = TaskResolutionService(self._session, self._clock).resolve_tasks(
            run_started_at
        )
        if not resolve_result.success or resolve_result.value is None:
            return fail(
                *resolve_result.errors,
                _value=RefreshScheduleResult(
                    run_started_at=run_started_at,
                    resolved_blocks=resolved_blocks,
                    block_assignment=block_assignment,
                    resolved=None,
                    assignment=None,
                    free_time=None,
                ),
            )

        resolved = resolve_result.value
        assign_result = TaskAssignmentService(self._session, self._clock).assign_tasks(
            resolved,
            run_started_at,
            calendar_run_id=shared_run_id,
            previous_task_run_id=previous_task_run_id,
        )
        if not assign_result.success:
            if (
                assign_result.errors
                and assign_result.errors[0].code
                == MessageCode.INVALID_INCOMPLETE_TASKS_BLOCK_ASSIGNMENT
            ):
                _persist_assignment_precondition_failure(self._session, self._clock)
            if assign_result.value is None:
                _persist_assignment_precondition_failure(self._session, self._clock)
                return fail(
                    *assign_result.errors,
                    _value=RefreshScheduleResult(
                        run_started_at=run_started_at,
                        resolved_blocks=resolved_blocks,
                        block_assignment=block_assignment,
                        resolved=resolved,
                        assignment=None,
                        free_time=None,
                    ),
                )
            return fail(
                *assign_result.errors,
                _value=RefreshScheduleResult(
                    run_started_at=run_started_at,
                    resolved_blocks=resolved_blocks,
                    block_assignment=block_assignment,
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
                    resolved_blocks=resolved_blocks,
                    block_assignment=block_assignment,
                    resolved=resolved,
                    assignment=assignment,
                    free_time=None,
                ),
            )

        return ok(
            RefreshScheduleResult(
                run_started_at=run_started_at,
                resolved_blocks=resolved_blocks,
                block_assignment=block_assignment,
                resolved=resolved,
                assignment=assignment,
                free_time=free_time_result.value,
            )
        )


def _persist_preflight_failure(session: Session, clock: Clock) -> None:
    """Record prerequisite preflight failure without mutating calendar rows."""
    with transaction(session):
        now = clock.now_utc()
        active_state = load_or_create_active_calendar_state(session, clock)
        active_state.last_refresh_failed = True
        active_state.last_failure_at = now
        active_state.last_failure_reason = LastFailureReason.ASSIGNMENT_PRECONDITION_FAILED
        active_state.updated_at = now


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
        active_state = load_or_create_active_calendar_state(session, clock)
        active_state.last_refresh_failed = True
        active_state.last_failure_at = now
        active_state.last_failure_reason = LastFailureReason.FREE_TIME_ASSIGNMENT_FAILED
        active_state.updated_at = now


def _persist_assignment_precondition_failure(session: Session, clock: Clock) -> None:
    """Record assignment precondition failure without mutating calendar rows or active run."""
    with transaction(session):
        now = clock.now_utc()
        active_state = load_or_create_active_calendar_state(session, clock)
        active_state.last_refresh_failed = True
        active_state.last_failure_at = now
        active_state.last_failure_reason = LastFailureReason.ASSIGNMENT_PRECONDITION_FAILED
        active_state.updated_at = now

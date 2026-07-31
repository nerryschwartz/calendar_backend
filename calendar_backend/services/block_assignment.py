"""Block assignment service: coordinate solvers and persist block calendar entries."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from calendar_backend.db.session import transaction
from calendar_backend.domain.block_assignment import (
    BlockAssignmentResult,
    BlockCalendarEntryInsertSpec,
    block_calendar_entry_dto_from_row,
    block_calendar_entry_insert_specs_from_assignments,
    occupied_intervals_from_task_calendar_entries_for_blocks,
)
from calendar_backend.domain.block_resolution import ResolveBlocksResult
from calendar_backend.domain.enums import (
    CalendarEntryType,
    CalendarRunStatus,
    LastFailureReason,
    SolverStatus,
)
from calendar_backend.domain.errors import MessageCode, ServiceMessage
from calendar_backend.domain.ids import BlockCalendarEntryID, CalendarRunID, new_id
from calendar_backend.domain.resolution import resolve_tasks_from_graph
from calendar_backend.domain.results import ServiceResult, fail, ok
from calendar_backend.domain.task_families import DownstreamTaskFeasibilitySummary
from calendar_backend.domain.time import Clock, SystemClock
from calendar_backend.models.blocks import BlockCalendarEntry
from calendar_backend.models.calendar import CalendarEntry
from calendar_backend.models.plans import Plan
from calendar_backend.models.runs import ActiveCalendarState
from calendar_backend.scheduling.input import (
    block_assignment_input_from_resolved,
)
from calendar_backend.scheduling.types import AssignmentSolverResult
from calendar_backend.services.app_settings import AppSettingsService
from calendar_backend.services.calendar_state import load_or_create_active_calendar_state
from calendar_backend.services.master_horizon import validate_run_started_at
from calendar_backend.services.task_assignment import (
    _exact_solver_unavailable_error,  # pyright: ignore[reportPrivateUsage]
    _new_calendar_run,  # pyright: ignore[reportPrivateUsage]
    _normalize_infeasible_solver_result,  # pyright: ignore[reportPrivateUsage]
    _solve_assignment,  # pyright: ignore[reportPrivateUsage]
    _solver_limits_from_settings,  # pyright: ignore[reportPrivateUsage]
)
from calendar_backend.services.task_resolution import load_plan_graph


class BlockAssignmentService:
    """Assign resolved blocks and persist block calendar entries on success."""

    def __init__(self, session: Session, clock: Clock | None = None) -> None:
        self._session = session
        self._clock = clock or SystemClock()

    def assign_blocks(
        self,
        resolved: ResolveBlocksResult,
        run_started_at: datetime,
        *,
        calendar_run_id: CalendarRunID | None = None,
    ) -> ServiceResult[BlockAssignmentResult]:
        """Assign valid incomplete blocks and persist block calendar entries."""
        precondition_error = _assign_blocks_precondition_error(resolved, run_started_at)
        if precondition_error is not None:
            return fail(precondition_error)

        with transaction(self._session) as txn:
            settings_result = AppSettingsService(txn, self._clock).get_settings()
            if not settings_result.success or settings_result.value is None:
                if settings_result.errors:
                    return fail(settings_result.errors[0])
                return fail(
                    ServiceMessage(
                        code=MessageCode.ACTIVE_CALENDAR_RUN_NOT_SET,
                        message="App settings could not be loaded",
                        details={},
                    )
                )
            settings = settings_result.value

            exact_unavailable_error = _exact_solver_unavailable_error(settings)
            if exact_unavailable_error is not None:
                return fail(exact_unavailable_error)

            task_entries = _load_task_calendar_entries(txn, run_started_at=run_started_at)
            occupied_intervals = occupied_intervals_from_task_calendar_entries_for_blocks(
                task_entries,
                run_started_at,
            )
            plans = load_plan_graph(txn)
            downstream_summaries = _downstream_task_feasibility_summaries(
                run_started_at,
                plans=plans,
            )

        assignment_input = block_assignment_input_from_resolved(
            resolved,
            occupied_intervals=occupied_intervals,
            solver_limits=_solver_limits_from_settings(settings),
            downstream_task_feasibility_summaries=downstream_summaries,
        )
        solver_result, runtime_ms = _solve_assignment(
            assignment_input,
            heuristic_enabled=settings.heuristic_enabled,
        )
        solver_result = _normalize_infeasible_solver_result(solver_result)
        if solver_result.status == SolverStatus.INFEASIBLE:
            assert solver_result.failure is not None
            with transaction(self._session) as txn:
                assignment_result = _persist_failed_block_assignment(
                    txn,
                    self._clock,
                    run_started_at=run_started_at,
                    solver_result=solver_result,
                    runtime_ms=runtime_ms,
                    calendar_run_id=calendar_run_id,
                )
            return fail(solver_result.failure, _value=assignment_result)

        with transaction(self._session) as txn:
            assignment_result = _persist_successful_block_assignment(
                txn,
                self._clock,
                run_started_at=run_started_at,
                resolved=resolved,
                solver_result=solver_result,
                runtime_ms=runtime_ms,
                calendar_run_id=calendar_run_id,
            )
        return ok(assignment_result)


def _assign_blocks_precondition_error(
    resolved: ResolveBlocksResult,
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
            code=MessageCode.INVALID_INCOMPLETE_BLOCKS_BLOCK_ASSIGNMENT,
            message="invalid incomplete blocks block assignment",
            details={
                "invalid_incomplete_count": str(len(resolved.invalid_incomplete)),
            },
        )

    return None


def _downstream_task_feasibility_summaries(
    run_started_at: datetime,
    *,
    plans: tuple[Plan, ...],
) -> tuple[DownstreamTaskFeasibilitySummary, ...]:
    baseline = resolve_tasks_from_graph(run_started_at, plans, block_placements=())
    return tuple(
        DownstreamTaskFeasibilitySummary(
            plan_id=task.plan_id,
            allowed_block_families=task.allowed_block_families,
            base_effective_windows=task.effective_time_windows,
        )
        for task in baseline.valid_incomplete
    )


def _load_task_calendar_entries(
    session: Session,
    *,
    run_started_at: datetime,
) -> tuple[CalendarEntry, ...]:
    state = session.get(ActiveCalendarState, 1)
    if state is None or state.active_calendar_run_id is None:
        return ()

    active_calendar_run_id = state.active_calendar_run_id
    return tuple(
        session.scalars(
            select(CalendarEntry).where(
                CalendarEntry.entry_type == CalendarEntryType.TASK,
                or_(
                    CalendarEntry.start_time < run_started_at,
                    CalendarEntry.calendar_run_id == active_calendar_run_id,
                ),
            )
        ).all()
    )


def _persist_failed_block_assignment(
    session: Session,
    clock: Clock,
    *,
    run_started_at: datetime,
    solver_result: AssignmentSolverResult,
    runtime_ms: int,
    calendar_run_id: CalendarRunID | None = None,
) -> BlockAssignmentResult:
    now = clock.now_utc()

    if calendar_run_id is None:
        calendar_run = _new_calendar_run(
            run_started_at=run_started_at,
            clock=clock,
            status=CalendarRunStatus.FAILED,
            solver_status=SolverStatus.INFEASIBLE,
            conflict_count=0,
            warning_count=len(solver_result.warnings),
            runtime_ms=runtime_ms,
            run_finished_at=now,
        )
        session.add(calendar_run)
        session.flush()
        result_run_id = CalendarRunID(calendar_run.calendar_run_id)
    else:
        result_run_id = calendar_run_id

    active_state = load_or_create_active_calendar_state(session, clock)
    active_state.last_refresh_failed = True
    active_state.last_failure_at = now
    active_state.last_failure_reason = LastFailureReason.ASSIGNMENT_FAILED
    active_state.updated_at = now
    session.flush()

    return BlockAssignmentResult(
        run_started_at=run_started_at,
        optimization_status=SolverStatus.INFEASIBLE,
        block_calendar_entries=(),
        warnings=solver_result.warnings,
        runtime_ms=runtime_ms,
        calendar_run_id=result_run_id,
    )


def _persist_successful_block_assignment(
    session: Session,
    clock: Clock,
    *,
    run_started_at: datetime,
    resolved: ResolveBlocksResult,
    solver_result: AssignmentSolverResult,
    runtime_ms: int,
    calendar_run_id: CalendarRunID | None = None,
) -> BlockAssignmentResult:
    resolved_blocks_by_id = {block.plan_id: block for block in resolved.valid_incomplete}
    insert_specs = block_calendar_entry_insert_specs_from_assignments(
        solver_result.assignments,
        resolved_blocks_by_id,
    )
    now = clock.now_utc()

    session.execute(
        delete(BlockCalendarEntry).where(BlockCalendarEntry.start_time >= run_started_at),
        execution_options={"synchronize_session": False},
    )

    if calendar_run_id is None:
        calendar_run = _new_calendar_run(
            run_started_at=run_started_at,
            clock=clock,
            status=CalendarRunStatus.SUCCESS,
            solver_status=solver_result.status,
            conflict_count=0,
            warning_count=len(solver_result.warnings),
            runtime_ms=runtime_ms,
            run_finished_at=now,
        )
        session.add(calendar_run)
        session.flush()
        result_run_id = calendar_run.calendar_run_id
    else:
        result_run_id = calendar_run_id

    inserted_entries = _insert_block_calendar_entries(
        session,
        insert_specs=insert_specs,
        calendar_run_id=result_run_id,
        now=now,
    )

    active_state = load_or_create_active_calendar_state(session, clock)
    active_state.active_calendar_run_id = result_run_id
    active_state.last_refresh_failed = False
    active_state.last_failure_at = None
    active_state.last_failure_reason = None
    active_state.updated_at = now
    session.flush()

    return BlockAssignmentResult(
        run_started_at=run_started_at,
        optimization_status=solver_result.status,
        block_calendar_entries=tuple(
            block_calendar_entry_dto_from_row(entry) for entry in inserted_entries
        ),
        warnings=solver_result.warnings,
        runtime_ms=runtime_ms,
        calendar_run_id=CalendarRunID(result_run_id),
    )


def _insert_block_calendar_entries(
    session: Session,
    *,
    insert_specs: tuple[BlockCalendarEntryInsertSpec, ...],
    calendar_run_id: uuid.UUID,
    now: datetime,
) -> list[BlockCalendarEntry]:
    inserted: list[BlockCalendarEntry] = []
    for spec in insert_specs:
        entry = BlockCalendarEntry(
            block_calendar_entry_id=new_id(BlockCalendarEntryID),
            start_time=spec.start_time,
            end_time=spec.end_time,
            source_plan_id=spec.source_plan_id,
            calendar_run_id=calendar_run_id,
            display_label=spec.display_label,
            created_at=now,
            updated_at=now,
        )
        session.add(entry)
        inserted.append(entry)
    return inserted

"""Task assignment service: coordinate solvers and persist TASK calendar entries."""

from __future__ import annotations

import importlib.util
import time
from datetime import datetime

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from calendar_backend.db.session import transaction
from calendar_backend.deletion.conflict_analysis import ConflictAnalysisService
from calendar_backend.domain.assignment import (
    AssignmentResult,
    calendar_entry_dto_from_row,
    calendar_entry_insert_specs_from_assignments,
    occupied_intervals_from_calendar_entries,
    previous_placements_from_future_task_entries,
)
from calendar_backend.domain.deletion import AssignmentConflict
from calendar_backend.domain.dtos import AppSettingsDTO
from calendar_backend.domain.enums import (
    CalendarEntryType,
    CalendarRunStatus,
    LastFailureReason,
    SolverStatus,
)
from calendar_backend.domain.errors import MessageCode, ServiceMessage
from calendar_backend.domain.ids import CalendarEntryID, CalendarRunID, new_id
from calendar_backend.domain.resolution import ResolveTasksResult
from calendar_backend.domain.results import ServiceResult, fail, ok
from calendar_backend.domain.time import Clock, SystemClock
from calendar_backend.models.calendar import CalendarEntry
from calendar_backend.models.runs import ActiveCalendarState, CalendarRun
from calendar_backend.scheduling import decomposition
from calendar_backend.scheduling.exact_cp_sat import solve_exact_component
from calendar_backend.scheduling.feasibility import validate_full_assignment
from calendar_backend.scheduling.heuristic import HeuristicAssignmentSolver
from calendar_backend.scheduling.input import (
    AssignmentInput,
    SolverLimits,
    assignment_input_from_resolved,
)
from calendar_backend.scheduling.types import (
    AssignmentSolverResult,
    TaskAssignment,
    exact_feasible_result,
    exact_optimal_result,
    infeasible_result,
    is_usable_solver_result,
    weakest_solver_status,
)
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
            occupied_intervals = occupied_intervals_from_calendar_entries(
                task_entries,
                run_started_at,
            )
            schedulable_plan_ids = frozenset(task.plan_id for task in resolved.valid_incomplete)
            previous_placements = previous_placements_from_future_task_entries(
                task_entries,
                run_started_at,
                schedulable_plan_ids,
            )

        assignment_input = assignment_input_from_resolved(
            resolved,
            occupied_intervals=occupied_intervals,
            previous_placements_by_task_id=previous_placements,
            solver_limits=_solver_limits_from_settings(settings),
        )
        solver_result, runtime_ms = _solve_assignment(
            assignment_input,
            heuristic_enabled=settings.heuristic_enabled,
        )
        solver_result = _normalize_infeasible_solver_result(solver_result)
        if solver_result.status == SolverStatus.INFEASIBLE:
            assert solver_result.failure is not None
            analysis_result = ConflictAnalysisService().analyze(
                assignment_input,
                resolved,
                solver_result,
            )
            assert analysis_result.success and analysis_result.value is not None
            with transaction(self._session) as txn:
                assignment_result = _persist_failed_assignment(
                    txn,
                    self._clock,
                    run_started_at=run_started_at,
                    solver_result=solver_result,
                    conflicts=analysis_result.value,
                    runtime_ms=runtime_ms,
                )
            return fail(solver_result.failure, _value=assignment_result)

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


def _exact_solver_unavailable_error(settings: AppSettingsDTO) -> ServiceMessage | None:
    if settings.heuristic_enabled:
        return None
    if _exact_solver_available():
        return None
    return ServiceMessage(
        code=MessageCode.SOLVER_FAILED_TO_FIND_FEASIBLE_ASSIGNMENT,
        message="exact assignment solver is unavailable and heuristic fallback is disabled",
        details={},
    )


def _exact_solver_available() -> bool:
    return importlib.util.find_spec("ortools.sat.python.cp_model") is not None


def _solver_limits_from_settings(settings: AppSettingsDTO) -> SolverLimits:
    return SolverLimits(
        time_limit_seconds=settings.exact_solver_time_limit_seconds,
        model_size_limit=settings.exact_solver_model_size_limit,
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


def _solve_assignment(
    assignment_input: AssignmentInput,
    *,
    heuristic_enabled: bool,
) -> tuple[AssignmentSolverResult, int]:
    started = time.perf_counter()

    if not assignment_input.tasks:
        runtime_ms = int((time.perf_counter() - started) * 1000)
        return exact_optimal_result(()), runtime_ms

    base_components = decomposition.decompose_assignment_input(assignment_input)
    prior_solved_assignments: tuple[TaskAssignment, ...] = ()
    component_statuses: list[SolverStatus] = []
    all_warnings: list[ServiceMessage] = []
    used_heuristic = False

    for component_index in range(len(base_components)):
        component = decomposition.iter_component_sub_inputs(
            assignment_input,
            prior_solved_assignments=prior_solved_assignments,
        )[component_index]
        exact_result = solve_exact_component(component)
        if is_usable_solver_result(exact_result):
            prior_solved_assignments = (
                *prior_solved_assignments,
                *exact_result.assignments,
            )
            component_statuses.append(exact_result.status)
            all_warnings.extend(exact_result.warnings)
            continue

        if not heuristic_enabled:
            runtime_ms = int((time.perf_counter() - started) * 1000)
            return exact_result, runtime_ms

        component_input = decomposition.assignment_input_from_component(component)
        heuristic_result = HeuristicAssignmentSolver().solve(component_input)
        if heuristic_result.status == SolverStatus.INFEASIBLE:
            runtime_ms = int((time.perf_counter() - started) * 1000)
            return heuristic_result, runtime_ms

        used_heuristic = True
        prior_solved_assignments = (
            *prior_solved_assignments,
            *heuristic_result.assignments,
        )
        component_statuses.append(SolverStatus.FEASIBLE)
        all_warnings.extend(heuristic_result.warnings)

    validation_failure = validate_full_assignment(assignment_input, prior_solved_assignments)
    if validation_failure is not None:
        runtime_ms = int((time.perf_counter() - started) * 1000)
        return infeasible_result(validation_failure), runtime_ms

    runtime_ms = int((time.perf_counter() - started) * 1000)
    return (
        _aggregate_mixed_solver_result(
            prior_solved_assignments,
            component_statuses,
            tuple(all_warnings),
            used_heuristic=used_heuristic,
        ),
        runtime_ms,
    )


def _aggregate_mixed_solver_result(
    assignments: tuple[TaskAssignment, ...],
    component_statuses: list[SolverStatus],
    warnings: tuple[ServiceMessage, ...],
    *,
    used_heuristic: bool,
) -> AssignmentSolverResult:
    if used_heuristic:
        merged_warnings = list(warnings)
        if not any(warning.code == MessageCode.HEURISTIC_FEASIBLE for warning in merged_warnings):
            merged_warnings.append(
                ServiceMessage(
                    code=MessageCode.HEURISTIC_FEASIBLE,
                    message="Heuristic solver produced a feasible assignment",
                    details={},
                )
            )
        aggregate_status = weakest_solver_status(*component_statuses)
        if aggregate_status == SolverStatus.OPTIMAL:
            aggregate_status = SolverStatus.FEASIBLE
        return AssignmentSolverResult(
            status=aggregate_status,
            assignments=assignments,
            warnings=tuple(merged_warnings),
            failure=None,
        )

    aggregate_status = weakest_solver_status(*component_statuses)
    if aggregate_status == SolverStatus.OPTIMAL:
        return exact_optimal_result(assignments)
    limit_reached = any(warning.code == MessageCode.SOLVER_LIMIT_REACHED for warning in warnings)
    return exact_feasible_result(assignments, limit_reached=limit_reached)


def _normalize_infeasible_solver_result(
    solver_result: AssignmentSolverResult,
) -> AssignmentSolverResult:
    if solver_result.status != SolverStatus.INFEASIBLE or solver_result.failure is not None:
        return solver_result

    return AssignmentSolverResult(
        status=SolverStatus.INFEASIBLE,
        assignments=(),
        warnings=(),
        failure=ServiceMessage(
            code=MessageCode.SOLVER_FAILED_TO_FIND_FEASIBLE_ASSIGNMENT,
            message="Exact solver could not produce a usable assignment",
            details={},
        ),
    )


def _persist_failed_assignment(
    session: Session,
    clock: Clock,
    *,
    run_started_at: datetime,
    solver_result: AssignmentSolverResult,
    conflicts: tuple[AssignmentConflict, ...],
    runtime_ms: int,
) -> AssignmentResult:
    now = clock.now_utc()

    calendar_run = _new_calendar_run(
        run_started_at=run_started_at,
        clock=clock,
        status=CalendarRunStatus.FAILED,
        solver_status=SolverStatus.INFEASIBLE,
        conflict_count=len(conflicts),
        warning_count=len(solver_result.warnings),
        runtime_ms=runtime_ms,
        run_finished_at=now,
    )
    session.add(calendar_run)
    session.flush()

    active_state = _load_or_create_active_calendar_state(session, clock)
    active_state.last_refresh_failed = True
    active_state.last_failure_at = now
    active_state.last_failure_reason = LastFailureReason.ASSIGNMENT_FAILED
    active_state.updated_at = now
    session.flush()

    return AssignmentResult(
        run_started_at=run_started_at,
        optimization_status=SolverStatus.INFEASIBLE,
        calendar_entries=(),
        conflicts=conflicts,
        warnings=solver_result.warnings,
        runtime_ms=runtime_ms,
        calendar_run_id=CalendarRunID(calendar_run.calendar_run_id),
    )


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
        ),
        execution_options={"synchronize_session": False},
    )

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
        optimization_status=solver_result.status,
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

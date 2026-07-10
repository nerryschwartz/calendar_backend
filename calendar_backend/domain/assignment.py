"""Frozen DTOs for task assignment service results."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from calendar_backend.domain.deletion import AssignmentConflict, build_assignment_conflict
from calendar_backend.domain.enums import CalendarEntryType, SolverStatus
from calendar_backend.domain.errors import MessageCode, ServiceMessage
from calendar_backend.domain.free_time import FreeTimeCalendarEntryInsertSpec
from calendar_backend.domain.ids import (
    CalendarEntryID,
    CalendarRunID,
    FreeTimeActivityID,
    PlanID,
)
from calendar_backend.domain.resolution import ResolvedTask, ResolveTasksResult
from calendar_backend.domain.time import TimeWindow
from calendar_backend.models.calendar import CalendarEntry
from calendar_backend.scheduling.input import AssignmentInput, OccupiedInterval
from calendar_backend.scheduling.types import AssignmentSolverResult, TaskAssignment

_GLOBAL_ASSIGNMENT_FAILURE_CODES: frozenset[MessageCode] = frozenset(
    {
        MessageCode.INSUFFICIENT_TOTAL_CAPACITY,
        MessageCode.PRECEDENCE_IMPOSSIBLE,
        MessageCode.SOLVER_FAILED_TO_FIND_FEASIBLE_ASSIGNMENT,
        MessageCode.TASK_OVERLAP_REQUIRED,
    }
)


@dataclass(frozen=True)
class CalendarEntryInsertSpec:
    source_plan_id: PlanID
    start_time: datetime
    end_time: datetime
    display_label: str


@dataclass(frozen=True)
class CalendarEntryDTO:
    calendar_entry_id: CalendarEntryID
    entry_type: CalendarEntryType
    start_time: datetime
    end_time: datetime
    source_plan_id: PlanID | None
    source_free_time_activity_id: FreeTimeActivityID | None
    display_label: str
    calendar_run_id: CalendarRunID | None


@dataclass(frozen=True)
class AssignmentResult:
    run_started_at: datetime
    optimization_status: SolverStatus
    calendar_entries: tuple[CalendarEntryDTO, ...]
    conflicts: tuple[AssignmentConflict, ...]
    warnings: tuple[ServiceMessage, ...]
    runtime_ms: int
    calendar_run_id: CalendarRunID | None


def calendar_entry_dto_from_row(entry: CalendarEntry) -> CalendarEntryDTO:
    return CalendarEntryDTO(
        calendar_entry_id=CalendarEntryID(entry.calendar_entry_id),
        entry_type=entry.entry_type,
        start_time=entry.start_time,
        end_time=entry.end_time,
        source_plan_id=PlanID(entry.source_plan_id) if entry.source_plan_id is not None else None,
        source_free_time_activity_id=(
            FreeTimeActivityID(entry.source_free_time_activity_id)
            if entry.source_free_time_activity_id is not None
            else None
        ),
        display_label=entry.display_label,
        calendar_run_id=(
            CalendarRunID(entry.calendar_run_id) if entry.calendar_run_id is not None else None
        ),
    )


def calendar_entry_insert_specs_from_assignments(
    assignments: tuple[TaskAssignment, ...],
    resolved_tasks_by_id: dict[PlanID, ResolvedTask],
) -> tuple[CalendarEntryInsertSpec, ...]:
    specs: list[CalendarEntryInsertSpec] = []
    for assignment in assignments:
        task = resolved_tasks_by_id[assignment.plan_id]
        for segment in assignment.segments:
            specs.append(
                CalendarEntryInsertSpec(
                    source_plan_id=assignment.plan_id,
                    start_time=segment.start_time,
                    end_time=segment.end_time,
                    display_label=task.name,
                )
            )
    return tuple(
        sorted(
            specs,
            key=lambda spec: (spec.start_time, spec.end_time, str(spec.source_plan_id)),
        )
    )


def sorted_free_time_calendar_insert_specs(
    specs: tuple[FreeTimeCalendarEntryInsertSpec, ...],
) -> tuple[FreeTimeCalendarEntryInsertSpec, ...]:
    """Deterministic ordering for FREE_TIME persistence: start, end, activity_id."""
    return tuple(
        sorted(
            specs,
            key=lambda spec: (
                spec.start_time,
                spec.end_time,
                str(spec.source_free_time_activity_id),
            ),
        )
    )


def analyze_assignment_conflicts(
    assignment_input: AssignmentInput,
    resolved: ResolveTasksResult,
    solver_result: AssignmentSolverResult,
) -> tuple[AssignmentConflict, ...]:
    """Derive one diagnostic AssignmentConflict from a failed solver result."""
    del assignment_input  # reserved for staged analysis extensions; v1 uses solver failure only
    if solver_result.status != SolverStatus.INFEASIBLE or solver_result.failure is None:
        return ()

    failure = solver_result.failure
    resolved_tasks_by_id = {task.plan_id: task for task in resolved.valid_incomplete}
    conflicting_plan_ids = _conflicting_plan_ids_from_failure(failure, resolved)
    task_ids = conflicting_plan_ids
    return (
        build_assignment_conflict(
            reason_code=failure.code,
            conflicting_plan_ids=conflicting_plan_ids,
            task_ids=task_ids,
            explanation=_conflict_explanation_from_failure(failure),
            affected_priority_by_plan_id=_affected_priority_by_plan_id(
                conflicting_plan_ids,
                resolved_tasks_by_id,
            ),
            is_global=failure.code in _GLOBAL_ASSIGNMENT_FAILURE_CODES,
        ),
    )


def _conflicting_plan_ids_from_failure(
    failure: ServiceMessage,
    resolved: ResolveTasksResult,
) -> tuple[PlanID, ...]:
    plan_id_value = failure.details.get("plan_id")
    if plan_id_value is not None:
        return (PlanID(UUID(plan_id_value)),)
    return tuple(sorted((task.plan_id for task in resolved.valid_incomplete), key=str))


def _affected_priority_by_plan_id(
    plan_ids: tuple[PlanID, ...],
    resolved_tasks_by_id: dict[PlanID, ResolvedTask],
) -> tuple[tuple[PlanID, int], ...]:
    priorities: list[tuple[PlanID, int]] = []
    for plan_id in plan_ids:
        task = resolved_tasks_by_id.get(plan_id)
        if task is None:
            continue
        priority = task.priority_path[-1] if task.priority_path else 0
        priorities.append((plan_id, priority))
    return tuple(sorted(priorities, key=lambda item: str(item[0])))


def _conflict_explanation_from_failure(failure: ServiceMessage) -> str:
    plan_id_value = failure.details.get("plan_id")
    if plan_id_value is None:
        return failure.message
    return f"{failure.message} (plan_id={plan_id_value})"


def occupied_intervals_from_calendar_entries(
    entries: tuple[CalendarEntry, ...],
    run_started_at: datetime,
) -> tuple[OccupiedInterval, ...]:
    """Map persisted TASK calendar rows to hard occupied intervals for the solver."""
    intervals: list[OccupiedInterval] = []
    for entry in entries:
        if entry.entry_type != CalendarEntryType.TASK:
            continue
        start_time = sqlite_utc(entry.start_time)
        if start_time >= run_started_at:
            continue
        intervals.append(
            OccupiedInterval(
                start_time=start_time,
                end_time=sqlite_utc(entry.end_time),
                source_plan_id=(
                    PlanID(entry.source_plan_id) if entry.source_plan_id is not None else None
                ),
            )
        )
    return tuple(
        sorted(
            intervals,
            key=lambda interval: (
                interval.start_time,
                interval.end_time,
                str(interval.source_plan_id) if interval.source_plan_id is not None else "",
            ),
        )
    )


def future_task_blocker_intervals_from_calendar_entries(
    entries: tuple[CalendarEntry, ...],
    run_started_at: datetime,
) -> tuple[TimeWindow, ...]:
    """Map persisted TASK calendar rows to future hard blockers for free-time gap discovery."""
    intervals: list[TimeWindow] = []
    for entry in entries:
        if entry.entry_type != CalendarEntryType.TASK:
            continue
        start_time = sqlite_utc(entry.start_time)
        if start_time < run_started_at:
            continue
        intervals.append(
            TimeWindow(
                start_time=start_time,
                end_time=sqlite_utc(entry.end_time),
            )
        )
    return tuple(sorted(intervals, key=lambda window: (window.start_time, window.end_time)))


def sqlite_utc(dt: datetime) -> datetime:
    """Normalize SQLite-read naive datetimes to UTC for comparisons."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)

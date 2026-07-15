"""Free-time assignment service: fill gaps and persist FREE_TIME calendar entries."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from calendar_backend.db.session import transaction
from calendar_backend.domain.assignment import (
    calendar_entry_dto_from_row,
    future_task_blocker_intervals_from_calendar_entries,
    sorted_free_time_calendar_insert_specs,
    sqlite_utc,
)
from calendar_backend.domain.dtos import AppSettingsDTO
from calendar_backend.domain.enums import CalendarEntryType
from calendar_backend.domain.errors import MessageCode, ServiceMessage, ServiceTransactionAborted
from calendar_backend.domain.free_time import (
    FreeTimeActivityDTO,
    FreeTimeAssignmentResult,
    FreeTimeCalendarEntryInsertSpec,
    FreeTimeGap,
    assign_free_time_to_gaps,
    blocked_activity_ids,
    compute_effective_fractions,
    discover_free_time_gaps,
    free_time_activity_dto_from_row,
    free_time_plan_graph_from_plans,
)
from calendar_backend.domain.ids import CalendarEntryID, CalendarRunID, FreeTimeActivityID, new_id
from calendar_backend.domain.results import ServiceResult, fail, ok
from calendar_backend.domain.time import Clock, SystemClock
from calendar_backend.models.calendar import CalendarEntry
from calendar_backend.models.free_time import FreeTimeActivity
from calendar_backend.models.runs import ActiveCalendarState
from calendar_backend.services.app_settings import AppSettingsService
from calendar_backend.services.master_horizon import get_master_horizon_end, validate_run_started_at
from calendar_backend.services.task_resolution import load_plan_graph


class FreeTimeAssignmentService:
    """Assign free time into gaps and persist FREE_TIME calendar entries on success.

    Caller must run after task assignment so ``ActiveCalendarState.active_calendar_run_id``
    is set. Task calendar rows are preserved when this service returns ``fail()``.
    """

    def __init__(self, session: Session, clock: Clock | None = None) -> None:
        self._session = session
        self._clock = clock or SystemClock()

    def assign_free_time(self, run_started_at: datetime) -> ServiceResult[FreeTimeAssignmentResult]:
        """Standalone callers get zero mutations on failure.

        OrchestrationService handles partial failure after successful task assignment.
        """
        validation_error = validate_run_started_at(run_started_at)
        if validation_error is not None:
            return fail(validation_error)

        started = time.perf_counter()
        try:
            with transaction(self._session) as txn:
                settings_result = AppSettingsService(txn, self._clock).get_settings()
                if not settings_result.success or settings_result.value is None:
                    raise ServiceTransactionAborted(settings_result.errors)
                loaded = _load_assignment_inputs(
                    txn,
                    run_started_at,
                    settings=settings_result.value,
                )
        except ServiceTransactionAborted as exc:
            return fail(*exc.errors)

        insert_specs = assign_free_time_to_gaps(
            gaps=loaded.gaps,
            effective_fractions=loaded.effective_fractions,
            activities_by_id=loaded.activities_by_id,
        )
        runtime_ms = int((time.perf_counter() - started) * 1000)

        with transaction(self._session) as txn:
            result = _persist_successful_free_time_assignment(
                txn,
                self._clock,
                run_started_at=run_started_at,
                active_calendar_run_id=loaded.active_calendar_run_id,
                insert_specs=insert_specs,
                runtime_ms=runtime_ms,
            )
        return ok(result)


@dataclass(frozen=True)
class _AssignmentInputs:
    active_calendar_run_id: CalendarRunID
    effective_fractions: tuple[tuple[FreeTimeActivityID, Decimal], ...]
    gaps: tuple[FreeTimeGap, ...]
    activities_by_id: dict[FreeTimeActivityID, FreeTimeActivityDTO]


def _load_assignment_inputs(
    session: Session,
    run_started_at: datetime,
    *,
    settings: AppSettingsDTO,
) -> _AssignmentInputs:
    state = session.get(ActiveCalendarState, 1)
    if state is None or state.active_calendar_run_id is None:
        raise ServiceTransactionAborted(
            (
                ServiceMessage(
                    code=MessageCode.ACTIVE_CALENDAR_RUN_NOT_SET,
                    message="active_calendar_run_id must be set before free-time assignment",
                    details={},
                ),
            )
        )

    horizon_end_raw = get_master_horizon_end(session)
    if horizon_end_raw is None:
        raise ServiceTransactionAborted(
            (
                ServiceMessage(
                    code=MessageCode.MASTER_HORIZON_NOT_FOUND,
                    message="Master horizon end not found",
                    details={},
                ),
            )
        )
    master_horizon_end = sqlite_utc(horizon_end_raw)

    activities = _load_all_activities(session)
    activity_dtos = tuple(free_time_activity_dto_from_row(activity) for activity in activities)
    activities_by_id = {dto.free_time_activity_id: dto for dto in activity_dtos}

    plans = load_plan_graph(session)
    graph = free_time_plan_graph_from_plans(plans)
    blocked = blocked_activity_ids(activity_dtos, graph)
    effective_fractions = compute_effective_fractions(activity_dtos, blocked)

    calendar_entries = tuple(
        session.scalars(
            select(CalendarEntry).where(CalendarEntry.entry_type == CalendarEntryType.TASK)
        ).all()
    )
    task_blockers = future_task_blocker_intervals_from_calendar_entries(
        calendar_entries,
        run_started_at,
    )
    gaps = discover_free_time_gaps(
        run_started_at=run_started_at,
        master_horizon_end=master_horizon_end,
        task_blockers=task_blockers,
        week_start_day=settings.free_time_week_start_day,
        local_timezone=settings.local_timezone,
    )

    return _AssignmentInputs(
        active_calendar_run_id=CalendarRunID(state.active_calendar_run_id),
        effective_fractions=effective_fractions,
        gaps=gaps,
        activities_by_id=activities_by_id,
    )


def _persist_successful_free_time_assignment(
    session: Session,
    clock: Clock,
    *,
    run_started_at: datetime,
    active_calendar_run_id: CalendarRunID,
    insert_specs: tuple[FreeTimeCalendarEntryInsertSpec, ...],
    runtime_ms: int,
) -> FreeTimeAssignmentResult:
    session.execute(
        delete(CalendarEntry).where(
            CalendarEntry.entry_type == CalendarEntryType.FREE_TIME,
            CalendarEntry.start_time >= run_started_at,
        )
    )

    now = clock.now_utc()
    ordered_specs = sorted_free_time_calendar_insert_specs(insert_specs)
    inserted_entries: list[CalendarEntry] = []
    for spec in ordered_specs:
        entry = CalendarEntry(
            calendar_entry_id=new_id(CalendarEntryID),
            entry_type=CalendarEntryType.FREE_TIME,
            start_time=spec.start_time,
            end_time=spec.end_time,
            source_plan_id=None,
            source_free_time_activity_id=spec.source_free_time_activity_id,
            calendar_run_id=active_calendar_run_id,
            display_label=spec.display_label,
            created_at=now,
            updated_at=now,
        )
        session.add(entry)
        inserted_entries.append(entry)

    session.flush()

    return FreeTimeAssignmentResult(
        run_started_at=run_started_at,
        calendar_entries=tuple(calendar_entry_dto_from_row(entry) for entry in inserted_entries),
        warnings=(),
        runtime_ms=runtime_ms,
        calendar_run_id=active_calendar_run_id,
    )


def _load_all_activities(session: Session) -> tuple[FreeTimeActivity, ...]:
    return tuple(
        session.scalars(
            select(FreeTimeActivity)
            .options(selectinload(FreeTimeActivity.prerequisites))
            .order_by(FreeTimeActivity.name, FreeTimeActivity.free_time_activity_id)
        ).all()
    )

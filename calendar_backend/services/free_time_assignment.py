"""Free-time assignment service: fill gaps and persist FREE_TIME calendar entries."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from calendar_backend.db.session import transaction
from calendar_backend.domain.assignment import (
    calendar_entry_dto_from_row,
    future_task_blocker_intervals_from_calendar_entries,
    sorted_free_time_calendar_insert_specs,
    sqlite_utc,
)
from calendar_backend.domain.block_assignment import (
    block_placements_from_block_calendar_entries,
    occupied_intervals_from_block_calendar_entries,
)
from calendar_backend.domain.dtos import AppSettingsDTO
from calendar_backend.domain.enums import CalendarEntryType, FreeTimeWeekStartDay
from calendar_backend.domain.errors import MessageCode, ServiceMessage, ServiceTransactionAborted
from calendar_backend.domain.free_time import (
    FreeTimeActivityDTO,
    FreeTimeAssignmentResult,
    FreeTimeCalendarEntryInsertSpec,
    assign_free_time_to_gaps,
    blocked_activity_ids,
    combined_gap_blocker_windows,
    compute_effective_fractions,
    eligible_free_time_gaps_for_activity,
    free_time_activity_dto_from_row,
    free_time_plan_graph_from_plans,
)
from calendar_backend.domain.ids import (
    CalendarEntryID,
    CalendarRunID,
    FreeTimeActivityID,
    PlanID,
    new_id,
)
from calendar_backend.domain.results import ServiceResult, fail, ok
from calendar_backend.domain.task_families import BlockPlacementSnapshot
from calendar_backend.domain.time import Clock, SystemClock, TimeWindow
from calendar_backend.models.blocks import BlockCalendarEntry
from calendar_backend.models.calendar import CalendarEntry
from calendar_backend.models.runs import ActiveCalendarState
from calendar_backend.services.app_settings import AppSettingsService
from calendar_backend.services.free_time_activity import load_all_activities
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

        insert_specs: list[FreeTimeCalendarEntryInsertSpec] = []
        for activity_id, fraction in loaded.effective_fractions:
            activity = loaded.activities_by_id[activity_id]
            activity_gaps = eligible_free_time_gaps_for_activity(
                run_started_at=run_started_at,
                master_horizon_end=loaded.master_horizon_end,
                week_start_day=loaded.week_start_day,
                local_timezone=loaded.local_timezone,
                combined_blockers=loaded.combined_blockers,
                task_blockers=loaded.task_blockers,
                allowed_families=activity.allowed_block_families,
                placements=loaded.placements,
            )
            insert_specs.extend(
                assign_free_time_to_gaps(
                    gaps=activity_gaps,
                    effective_fractions=((activity_id, fraction),),
                    activities_by_id={activity_id: activity},
                )
            )
        runtime_ms = int((time.perf_counter() - started) * 1000)

        with transaction(self._session) as txn:
            result = _persist_successful_free_time_assignment(
                txn,
                self._clock,
                run_started_at=run_started_at,
                active_calendar_run_id=loaded.active_calendar_run_id,
                insert_specs=tuple(insert_specs),
                runtime_ms=runtime_ms,
            )
        return ok(result)


@dataclass(frozen=True)
class _AssignmentInputs:
    active_calendar_run_id: CalendarRunID
    effective_fractions: tuple[tuple[FreeTimeActivityID, Decimal], ...]
    activities_by_id: dict[FreeTimeActivityID, FreeTimeActivityDTO]
    combined_blockers: tuple[TimeWindow, ...]
    task_blockers: tuple[TimeWindow, ...]
    placements: tuple[BlockPlacementSnapshot, ...]
    master_horizon_end: datetime
    week_start_day: FreeTimeWeekStartDay
    local_timezone: str


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
    active_calendar_run_id = CalendarRunID(state.active_calendar_run_id)

    activities = load_all_activities(session)
    activity_dtos = tuple(free_time_activity_dto_from_row(activity) for activity in activities)
    activities_by_id = {dto.free_time_activity_id: dto for dto in activity_dtos}

    plans = load_plan_graph(session)
    graph = free_time_plan_graph_from_plans(plans)
    blocked = blocked_activity_ids(activity_dtos, graph)
    effective_fractions = compute_effective_fractions(activity_dtos, blocked)

    task_entries = _load_task_calendar_entries(
        session,
        run_started_at=run_started_at,
        active_calendar_run_id=active_calendar_run_id,
    )
    block_entries = _load_block_calendar_entries(
        session,
        run_started_at=run_started_at,
        active_calendar_run_id=active_calendar_run_id,
    )
    task_blockers = future_task_blocker_intervals_from_calendar_entries(
        task_entries,
        run_started_at,
    )
    block_occupied = occupied_intervals_from_block_calendar_entries(
        block_entries,
        run_started_at,
        active_calendar_run_id=active_calendar_run_id,
    )
    block_blockers = tuple(
        TimeWindow(start_time=interval.start_time, end_time=interval.end_time)
        for interval in block_occupied
    )
    combined_blockers = combined_gap_blocker_windows(task_blockers, block_blockers)
    block_family_by_plan_id = {
        PlanID(plan.plan_id): plan.block_plan.block_family
        for plan in plans
        if plan.block_plan is not None
    }
    placements = block_placements_from_block_calendar_entries(
        block_entries,
        block_family_by_plan_id=block_family_by_plan_id,
    )

    return _AssignmentInputs(
        active_calendar_run_id=active_calendar_run_id,
        effective_fractions=effective_fractions,
        activities_by_id=activities_by_id,
        combined_blockers=combined_blockers,
        task_blockers=task_blockers,
        placements=placements,
        master_horizon_end=master_horizon_end,
        week_start_day=settings.free_time_week_start_day,
        local_timezone=settings.local_timezone,
    )


def _load_task_calendar_entries(
    session: Session,
    *,
    run_started_at: datetime,
    active_calendar_run_id: CalendarRunID,
) -> tuple[CalendarEntry, ...]:
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


def _load_block_calendar_entries(
    session: Session,
    *,
    run_started_at: datetime,
    active_calendar_run_id: CalendarRunID,
) -> tuple[BlockCalendarEntry, ...]:
    return tuple(
        session.scalars(
            select(BlockCalendarEntry).where(
                or_(
                    BlockCalendarEntry.start_time < run_started_at,
                    BlockCalendarEntry.calendar_run_id == active_calendar_run_id,
                ),
            )
        ).all()
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

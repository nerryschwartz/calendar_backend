"""Frozen DTOs for task assignment service results."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from calendar_backend.domain.deletion import AssignmentConflict
from calendar_backend.domain.enums import CalendarEntryType, SolverStatus
from calendar_backend.domain.errors import ServiceMessage
from calendar_backend.domain.ids import (
    CalendarEntryID,
    CalendarRunID,
    FreeTimeActivityID,
    PlanID,
)
from calendar_backend.models.calendar import CalendarEntry


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

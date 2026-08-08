"""Frozen DTOs for calendar read operations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from calendar_backend.domain.assignment import CalendarEntryDTO
from calendar_backend.domain.block_assignment import BlockCalendarEntryDTO
from calendar_backend.domain.enums import LastFailureReason
from calendar_backend.domain.ids import CalendarRunID


@dataclass(frozen=True)
class ScheduleStateDTO:
    active_calendar_run_id: CalendarRunID | None
    last_refresh_failed: bool
    last_failure_at: datetime | None
    last_failure_reason: LastFailureReason | None
    updated_at: datetime


@dataclass(frozen=True)
class TaskCalendarDTO:
    entries: tuple[CalendarEntryDTO, ...]
    calendar_run_id: CalendarRunID | None


@dataclass(frozen=True)
class BlockCalendarDTO:
    entries: tuple[BlockCalendarEntryDTO, ...]
    calendar_run_id: CalendarRunID | None

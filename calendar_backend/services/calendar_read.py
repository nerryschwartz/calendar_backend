"""Read-only calendar and schedule state queries."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from calendar_backend.domain.assignment import calendar_entry_dto_from_row
from calendar_backend.domain.block_assignment import block_calendar_entry_dto_from_row
from calendar_backend.domain.calendar_read import (
    BlockCalendarDTO,
    ScheduleStateDTO,
    TaskCalendarDTO,
)
from calendar_backend.domain.enums import CalendarEntryType
from calendar_backend.domain.ids import BlockCalendarEntryID, CalendarEntryID, CalendarRunID
from calendar_backend.domain.results import ServiceResult, ok
from calendar_backend.domain.time import Clock, SystemClock, sqlite_utc
from calendar_backend.models.blocks import BlockCalendarEntry
from calendar_backend.models.calendar import CalendarEntry
from calendar_backend.services.calendar_state import load_or_create_active_calendar_state


class CalendarReadService:
    def __init__(self, session: Session, clock: Clock | None = None) -> None:
        self._session = session
        self._clock = clock or SystemClock()

    def get_schedule_state(self) -> ServiceResult[ScheduleStateDTO]:
        state = load_or_create_active_calendar_state(self._session, self._clock)
        return ok(
            ScheduleStateDTO(
                active_calendar_run_id=(
                    CalendarRunID(state.active_calendar_run_id)
                    if state.active_calendar_run_id is not None
                    else None
                ),
                last_refresh_failed=state.last_refresh_failed,
                last_failure_at=sqlite_utc(state.last_failure_at)
                if state.last_failure_at is not None
                else None,
                last_failure_reason=state.last_failure_reason,
                updated_at=sqlite_utc(state.updated_at),
            )
        )

    def get_task_calendar(
        self,
        *,
        active_at: datetime | None = None,
        ending_after: datetime | None = None,
        ending_by: datetime | None = None,
        entry_id: CalendarEntryID | None = None,
        limit: int | None = None,
    ) -> ServiceResult[TaskCalendarDTO]:
        state = load_or_create_active_calendar_state(self._session, self._clock)
        run_id = state.active_calendar_run_id
        if run_id is None:
            return ok(TaskCalendarDTO(entries=(), calendar_run_id=None))

        query = (
            select(CalendarEntry)
            .where(CalendarEntry.calendar_run_id == run_id)
            .where(
                CalendarEntry.entry_type.in_((CalendarEntryType.TASK, CalendarEntryType.FREE_TIME))
            )
            .order_by(CalendarEntry.start_time, CalendarEntry.calendar_entry_id)
        )
        if active_at is not None:
            query = query.where(
                CalendarEntry.start_time <= active_at, CalendarEntry.end_time > active_at
            )
        if ending_after is not None:
            query = query.where(CalendarEntry.end_time > ending_after)
        if ending_by is not None:
            query = query.where(CalendarEntry.end_time <= ending_by)
        if entry_id is not None:
            query = query.where(CalendarEntry.calendar_entry_id == entry_id)
        if limit is not None:
            query = query.limit(limit)
        rows = self._session.scalars(query).all()
        return ok(
            TaskCalendarDTO(
                entries=tuple(calendar_entry_dto_from_row(row) for row in rows),
                calendar_run_id=CalendarRunID(run_id),
            )
        )

    def get_block_calendar(
        self,
        *,
        active_at: datetime | None = None,
        ending_after: datetime | None = None,
        ending_by: datetime | None = None,
        entry_id: BlockCalendarEntryID | None = None,
        limit: int | None = None,
    ) -> ServiceResult[BlockCalendarDTO]:
        state = load_or_create_active_calendar_state(self._session, self._clock)
        run_id = state.active_calendar_run_id
        if run_id is None:
            return ok(BlockCalendarDTO(entries=(), calendar_run_id=None))

        query = (
            select(BlockCalendarEntry)
            .where(BlockCalendarEntry.calendar_run_id == run_id)
            .order_by(BlockCalendarEntry.start_time, BlockCalendarEntry.block_calendar_entry_id)
        )
        if active_at is not None:
            query = query.where(
                BlockCalendarEntry.start_time <= active_at, BlockCalendarEntry.end_time > active_at
            )
        if ending_after is not None:
            query = query.where(BlockCalendarEntry.end_time > ending_after)
        if ending_by is not None:
            query = query.where(BlockCalendarEntry.end_time <= ending_by)
        if entry_id is not None:
            query = query.where(BlockCalendarEntry.block_calendar_entry_id == entry_id)
        if limit is not None:
            query = query.limit(limit)
        rows = self._session.scalars(query).all()
        return ok(
            BlockCalendarDTO(
                entries=tuple(block_calendar_entry_dto_from_row(row) for row in rows),
                calendar_run_id=CalendarRunID(run_id),
            )
        )

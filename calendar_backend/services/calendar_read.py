"""Read-only calendar and schedule state queries."""

from __future__ import annotations

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
from calendar_backend.domain.ids import CalendarRunID
from calendar_backend.domain.results import ServiceResult, ok
from calendar_backend.domain.time import Clock, SystemClock
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
                last_failure_at=state.last_failure_at,
                last_failure_reason=state.last_failure_reason,
                updated_at=state.updated_at,
            )
        )

    def get_task_calendar(self) -> ServiceResult[TaskCalendarDTO]:
        state = load_or_create_active_calendar_state(self._session, self._clock)
        run_id = state.active_calendar_run_id
        if run_id is None:
            return ok(TaskCalendarDTO(entries=(), calendar_run_id=None))

        rows = self._session.scalars(
            select(CalendarEntry)
            .where(CalendarEntry.calendar_run_id == run_id)
            .where(
                CalendarEntry.entry_type.in_((CalendarEntryType.TASK, CalendarEntryType.FREE_TIME))
            )
            .order_by(CalendarEntry.start_time)
        ).all()
        return ok(
            TaskCalendarDTO(
                entries=tuple(calendar_entry_dto_from_row(row) for row in rows),
                calendar_run_id=CalendarRunID(run_id),
            )
        )

    def get_block_calendar(self) -> ServiceResult[BlockCalendarDTO]:
        state = load_or_create_active_calendar_state(self._session, self._clock)
        run_id = state.active_calendar_run_id
        if run_id is None:
            return ok(BlockCalendarDTO(entries=(), calendar_run_id=None))

        rows = self._session.scalars(
            select(BlockCalendarEntry)
            .where(BlockCalendarEntry.calendar_run_id == run_id)
            .order_by(BlockCalendarEntry.start_time)
        ).all()
        return ok(
            BlockCalendarDTO(
                entries=tuple(block_calendar_entry_dto_from_row(row) for row in rows),
                calendar_run_id=CalendarRunID(run_id),
            )
        )

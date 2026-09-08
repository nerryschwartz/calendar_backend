"""Active timer computation and completion handling."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Session

from calendar_backend.domain.assignment import CalendarEntryDTO
from calendar_backend.domain.block_assignment import BlockCalendarEntryDTO
from calendar_backend.domain.enums import CalendarEntryType, NotificationSourceKind, TimerSourceKind
from calendar_backend.domain.errors import MessageCode, ServiceMessage
from calendar_backend.domain.ids import BlockCalendarEntryID, CalendarEntryID
from calendar_backend.domain.notifications import (
    ActiveTimerDTO,
    ActiveTimersDTO,
    NotificationQueueItemDTO,
    TimerDiagnosticsDTO,
)
from calendar_backend.domain.results import ServiceResult, fail, ok
from calendar_backend.domain.time import Clock, SystemClock
from calendar_backend.services.calendar_read import CalendarReadService
from calendar_backend.services.notification_queue import NotificationQueueService

NEARBY_ENTRY_LIMIT = 10


def timer_key_for_task(calendar_entry_id: CalendarEntryID) -> str:
    return f"task:{calendar_entry_id}"


def timer_key_for_block(block_calendar_entry_id: BlockCalendarEntryID) -> str:
    return f"block:{block_calendar_entry_id}"


def timer_key_for_free_time(calendar_entry_id: CalendarEntryID) -> str:
    return f"free_time:{calendar_entry_id}"


class TimerService:
    def __init__(self, session: Session, clock: Clock | None = None) -> None:
        self._session = session
        self._clock = clock or SystemClock()
        self._calendar_read = CalendarReadService(session, self._clock)
        self._notification_queue = NotificationQueueService(session, self._clock)

    def get_active_timers(self) -> ServiceResult[tuple[ActiveTimerDTO, ...]]:
        return _active_timers_from_read(self._calendar_read, self._clock.now_utc())

    def get_active_timer_snapshot(self) -> ServiceResult[ActiveTimersDTO]:
        now = self._clock.now_utc()
        state = self._calendar_read.get_schedule_state()
        if not state.success or state.value is None:
            return fail(*state.errors)
        active = _active_timers_from_read(self._calendar_read, now)
        if not active.success or active.value is None:
            return fail(*active.errors)
        tasks = self._calendar_read.get_task_calendar(ending_after=now, limit=NEARBY_ENTRY_LIMIT)
        if not tasks.success or tasks.value is None:
            return fail(*tasks.errors)
        blocks = self._calendar_read.get_block_calendar(ending_after=now, limit=NEARBY_ENTRY_LIMIT)
        if not blocks.success or blocks.value is None:
            return fail(*blocks.errors)
        nearby = tuple(
            sorted(
                (
                    *(_task_timer(entry) for entry in tasks.value.entries),
                    *(_block_timer(entry) for entry in blocks.value.entries),
                ),
                key=lambda entry: (entry.window_start_at, entry.timer_key),
            )[:NEARBY_ENTRY_LIMIT]
        )
        return ok(
            ActiveTimersDTO(
                active.value,
                TimerDiagnosticsDTO(
                    backend_now=now,
                    active_calendar_run_id=state.value.active_calendar_run_id,
                    last_refresh_failed=state.value.last_refresh_failed,
                    last_failure_at=state.value.last_failure_at,
                    last_failure_reason=state.value.last_failure_reason,
                    nearby_entries=nearby,
                ),
            )
        )

    def complete_timer(self, timer_key: str) -> ServiceResult[NotificationQueueItemDTO | None]:
        active = self.get_active_timers()
        if not active.success or active.value is None:
            return fail(*active.errors)
        match = next((timer for timer in active.value if timer.timer_key == timer_key), None)
        if match is None:
            past = _find_past_timer(self._calendar_read, self._clock, timer_key)
            if not past.success:
                return fail(*past.errors)
            match = past.value
        if match is None:
            return fail(
                ServiceMessage(
                    code=MessageCode.PLAN_NOT_FOUND,
                    message="Timer not found",
                    details={"timer_key": timer_key},
                )
            )
        if match.source_kind == TimerSourceKind.FREE_TIME:
            return ok(None)
        if match.plan_id is None:
            return fail(
                ServiceMessage(
                    code=MessageCode.PLAN_NOT_FOUND,
                    message="Timer has no associated plan",
                    details={"timer_key": timer_key},
                )
            )
        return self._notification_queue.enqueue_timer_completion(
            source_kind=(
                NotificationSourceKind.TASK
                if match.source_kind == TimerSourceKind.TASK
                else NotificationSourceKind.BLOCK
            ),
            plan_id=match.plan_id,
            timer_key=match.timer_key,
            window_end_at=match.window_end_at,
            display_label=match.display_label,
            calendar_entry_id=match.calendar_entry_id,
            block_calendar_entry_id=match.block_calendar_entry_id,
        )


def _active_timers_from_read(
    calendar_read: CalendarReadService, now: datetime
) -> ServiceResult[tuple[ActiveTimerDTO, ...]]:
    tasks = calendar_read.get_task_calendar(active_at=now)
    if not tasks.success or tasks.value is None:
        return fail(*tasks.errors)
    blocks = calendar_read.get_block_calendar(active_at=now)
    if not blocks.success or blocks.value is None:
        return fail(*blocks.errors)
    return ok(
        tuple(
            sorted(
                (
                    *(_task_timer(entry) for entry in tasks.value.entries),
                    *(_block_timer(entry) for entry in blocks.value.entries),
                ),
                key=lambda item: (item.window_end_at, item.timer_key),
            )
        )
    )


def _task_timer(entry: CalendarEntryDTO) -> ActiveTimerDTO:
    free_time = entry.entry_type == CalendarEntryType.FREE_TIME
    return ActiveTimerDTO(
        timer_key=timer_key_for_free_time(entry.calendar_entry_id)
        if free_time
        else timer_key_for_task(entry.calendar_entry_id),
        source_kind=TimerSourceKind.FREE_TIME if free_time else TimerSourceKind.TASK,
        plan_id=entry.source_plan_id,
        display_label=entry.display_label,
        window_start_at=entry.start_time,
        window_end_at=entry.end_time,
        calendar_entry_id=entry.calendar_entry_id,
        block_calendar_entry_id=None,
    )


def _block_timer(entry: BlockCalendarEntryDTO) -> ActiveTimerDTO:
    return ActiveTimerDTO(
        timer_key=timer_key_for_block(entry.block_calendar_entry_id),
        source_kind=TimerSourceKind.BLOCK,
        plan_id=entry.source_plan_id,
        display_label=entry.display_label,
        window_start_at=entry.start_time,
        window_end_at=entry.end_time,
        calendar_entry_id=None,
        block_calendar_entry_id=entry.block_calendar_entry_id,
    )


def _find_past_timer(
    calendar_read: CalendarReadService, clock: Clock, timer_key: str
) -> ServiceResult[ActiveTimerDTO | None]:
    kind, _, entry_id_text = timer_key.partition(":")
    try:
        entry_id = UUID(entry_id_text)
    except ValueError:
        return ok(None)
    now = clock.now_utc()
    if kind == "block":
        blocks = calendar_read.get_block_calendar(
            ending_by=now, entry_id=BlockCalendarEntryID(entry_id), limit=1
        )
        if not blocks.success or blocks.value is None:
            return fail(*blocks.errors)
        return ok(_block_timer(blocks.value.entries[0]) if blocks.value.entries else None)
    if kind not in ("task", "free_time"):
        return ok(None)
    tasks = calendar_read.get_task_calendar(
        ending_by=now, entry_id=CalendarEntryID(entry_id), limit=1
    )
    if not tasks.success or tasks.value is None:
        return fail(*tasks.errors)
    timer = _task_timer(tasks.value.entries[0]) if tasks.value.entries else None
    return ok(timer if timer is not None and timer.timer_key == timer_key else None)

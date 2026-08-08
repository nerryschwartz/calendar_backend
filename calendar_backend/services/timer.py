"""Active timer computation and completion handling."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from calendar_backend.domain.enums import NotificationSourceKind, TimerSourceKind
from calendar_backend.domain.errors import MessageCode, ServiceMessage
from calendar_backend.domain.ids import BlockCalendarEntryID, CalendarEntryID
from calendar_backend.domain.notifications import ActiveTimerDTO, NotificationQueueItemDTO
from calendar_backend.domain.results import ServiceResult, fail, ok
from calendar_backend.domain.time import Clock, SystemClock
from calendar_backend.services.calendar_read import CalendarReadService
from calendar_backend.services.notification_queue import NotificationQueueService


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
        return ok(_active_timers_from_read(self._calendar_read, self._clock.now_utc()))

    def complete_timer(
        self,
        timer_key: str,
    ) -> ServiceResult[NotificationQueueItemDTO | None]:
        active = self.get_active_timers()
        if not active.success or active.value is None:
            return fail(*active.errors)

        match = next((timer for timer in active.value if timer.timer_key == timer_key), None)
        if match is None:
            match = _find_past_timer(self._calendar_read, self._clock, timer_key)
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

        source_kind = (
            NotificationSourceKind.TASK
            if match.source_kind == TimerSourceKind.TASK
            else NotificationSourceKind.BLOCK
        )
        if match.plan_id is None:
            return fail(
                ServiceMessage(
                    code=MessageCode.PLAN_NOT_FOUND,
                    message="Timer has no associated plan",
                    details={"timer_key": timer_key},
                )
            )

        return self._notification_queue.enqueue_timer_completion(
            source_kind=source_kind,
            plan_id=match.plan_id,
            timer_key=match.timer_key,
            window_end_at=match.window_end_at,
            display_label=match.display_label,
            calendar_entry_id=match.calendar_entry_id,
            block_calendar_entry_id=match.block_calendar_entry_id,
        )


def _active_timers_from_read(
    calendar_read: CalendarReadService,
    now: datetime,
) -> tuple[ActiveTimerDTO, ...]:
    timers: list[ActiveTimerDTO] = []

    task_calendar = calendar_read.get_task_calendar()
    if task_calendar.success and task_calendar.value is not None:
        for entry in task_calendar.value.entries:
            if not _window_active(entry.start_time, entry.end_time, now):
                continue
            if entry.entry_type.value == "FREE_TIME":
                timers.append(
                    ActiveTimerDTO(
                        timer_key=timer_key_for_free_time(entry.calendar_entry_id),
                        source_kind=TimerSourceKind.FREE_TIME,
                        plan_id=entry.source_plan_id,
                        display_label=entry.display_label,
                        window_start_at=entry.start_time,
                        window_end_at=entry.end_time,
                        calendar_entry_id=entry.calendar_entry_id,
                        block_calendar_entry_id=None,
                    )
                )
            else:
                timers.append(
                    ActiveTimerDTO(
                        timer_key=timer_key_for_task(entry.calendar_entry_id),
                        source_kind=TimerSourceKind.TASK,
                        plan_id=entry.source_plan_id,
                        display_label=entry.display_label,
                        window_start_at=entry.start_time,
                        window_end_at=entry.end_time,
                        calendar_entry_id=entry.calendar_entry_id,
                        block_calendar_entry_id=None,
                    )
                )

    block_calendar = calendar_read.get_block_calendar()
    if block_calendar.success and block_calendar.value is not None:
        for entry in block_calendar.value.entries:
            if not _window_active(entry.start_time, entry.end_time, now):
                continue
            timers.append(
                ActiveTimerDTO(
                    timer_key=timer_key_for_block(entry.block_calendar_entry_id),
                    source_kind=TimerSourceKind.BLOCK,
                    plan_id=entry.source_plan_id,
                    display_label=entry.display_label,
                    window_start_at=entry.start_time,
                    window_end_at=entry.end_time,
                    calendar_entry_id=None,
                    block_calendar_entry_id=entry.block_calendar_entry_id,
                )
            )

    timers.sort(key=lambda item: item.window_end_at)
    return tuple(timers)


def _window_active(start: datetime, end: datetime, now: datetime) -> bool:
    return start <= now < end


def _find_past_timer(
    calendar_read: CalendarReadService,
    clock: Clock,
    timer_key: str,
) -> ActiveTimerDTO | None:
    now = clock.now_utc()
    task_calendar = calendar_read.get_task_calendar()
    if task_calendar.success and task_calendar.value is not None:
        for entry in task_calendar.value.entries:
            key = (
                timer_key_for_free_time(entry.calendar_entry_id)
                if entry.entry_type.value == "FREE_TIME"
                else timer_key_for_task(entry.calendar_entry_id)
            )
            if key != timer_key or entry.end_time > now:
                continue
            kind = (
                TimerSourceKind.FREE_TIME
                if entry.entry_type.value == "FREE_TIME"
                else TimerSourceKind.TASK
            )
            return ActiveTimerDTO(
                timer_key=key,
                source_kind=kind,
                plan_id=entry.source_plan_id,
                display_label=entry.display_label,
                window_start_at=entry.start_time,
                window_end_at=entry.end_time,
                calendar_entry_id=entry.calendar_entry_id,
                block_calendar_entry_id=None,
            )

    block_calendar = calendar_read.get_block_calendar()
    if block_calendar.success and block_calendar.value is not None:
        for entry in block_calendar.value.entries:
            key = timer_key_for_block(entry.block_calendar_entry_id)
            if key != timer_key or entry.end_time > now:
                continue
            return ActiveTimerDTO(
                timer_key=key,
                source_kind=TimerSourceKind.BLOCK,
                plan_id=entry.source_plan_id,
                display_label=entry.display_label,
                window_start_at=entry.start_time,
                window_end_at=entry.end_time,
                calendar_entry_id=None,
                block_calendar_entry_id=entry.block_calendar_entry_id,
            )
    return None

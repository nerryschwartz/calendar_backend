"""Frozen DTOs for timer and notification services."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from calendar_backend.domain.enums import NotificationSourceKind, TimerSourceKind
from calendar_backend.domain.ids import (
    BlockCalendarEntryID,
    CalendarEntryID,
    NotificationQueueItemID,
    PlanID,
)
from calendar_backend.models.notifications import NotificationQueueItem


@dataclass(frozen=True)
class ActiveTimerDTO:
    timer_key: str
    source_kind: TimerSourceKind
    plan_id: PlanID | None
    display_label: str
    window_start_at: datetime
    window_end_at: datetime
    calendar_entry_id: CalendarEntryID | None
    block_calendar_entry_id: BlockCalendarEntryID | None


@dataclass(frozen=True)
class NotificationQueueItemDTO:
    notification_id: NotificationQueueItemID
    source_kind: NotificationSourceKind
    plan_id: PlanID
    timer_key: str
    window_end_at: datetime
    calendar_entry_id: CalendarEntryID | None
    block_calendar_entry_id: BlockCalendarEntryID | None
    display_label: str
    created_at: datetime


def notification_queue_item_dto_from_row(row: NotificationQueueItem) -> NotificationQueueItemDTO:
    return NotificationQueueItemDTO(
        notification_id=NotificationQueueItemID(row.notification_id),
        source_kind=row.source_kind,
        plan_id=PlanID(row.plan_id),
        timer_key=row.timer_key,
        window_end_at=row.window_end_at,
        calendar_entry_id=(
            CalendarEntryID(row.calendar_entry_id) if row.calendar_entry_id is not None else None
        ),
        block_calendar_entry_id=(
            BlockCalendarEntryID(row.block_calendar_entry_id)
            if row.block_calendar_entry_id is not None
            else None
        ),
        display_label=row.display_label,
        created_at=row.created_at,
    )

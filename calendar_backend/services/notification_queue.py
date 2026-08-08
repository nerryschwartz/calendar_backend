"""Notification queue persistence service."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from calendar_backend.db.session import transaction
from calendar_backend.domain.enums import NotificationSourceKind
from calendar_backend.domain.errors import MessageCode, ServiceMessage
from calendar_backend.domain.ids import (
    BlockCalendarEntryID,
    CalendarEntryID,
    NotificationQueueItemID,
    PlanID,
    new_id,
)
from calendar_backend.domain.notifications import (
    NotificationQueueItemDTO,
    notification_queue_item_dto_from_row,
)
from calendar_backend.domain.results import ServiceResult, fail, ok
from calendar_backend.domain.time import Clock, SystemClock
from calendar_backend.models.notifications import NotificationQueueItem


class NotificationQueueService:
    def __init__(self, session: Session, clock: Clock | None = None) -> None:
        self._session = session
        self._clock = clock or SystemClock()

    def list_pending(self) -> ServiceResult[tuple[NotificationQueueItemDTO, ...]]:
        rows = self._session.scalars(
            select(NotificationQueueItem)
            .where(NotificationQueueItem.dismissed_at.is_(None))
            .order_by(NotificationQueueItem.created_at)
        ).all()
        return ok(tuple(notification_queue_item_dto_from_row(row) for row in rows))

    def dismiss(self, notification_id: NotificationQueueItemID) -> ServiceResult[None]:
        with transaction(self._session) as txn:
            row = txn.get(NotificationQueueItem, notification_id)
            if row is None:
                return fail(
                    ServiceMessage(
                        code=MessageCode.PLAN_NOT_FOUND,
                        message="Notification not found",
                        details={"notification_id": str(notification_id)},
                    )
                )
            if row.dismissed_at is not None:
                return ok(None)
            row.dismissed_at = self._clock.now_utc()
            txn.flush()
            return ok(None)

    def enqueue_timer_completion(
        self,
        *,
        source_kind: NotificationSourceKind,
        plan_id: PlanID,
        timer_key: str,
        window_end_at: datetime,
        display_label: str,
        calendar_entry_id: CalendarEntryID | None = None,
        block_calendar_entry_id: BlockCalendarEntryID | None = None,
    ) -> ServiceResult[NotificationQueueItemDTO | None]:
        existing = self._session.scalar(
            select(NotificationQueueItem).where(
                NotificationQueueItem.timer_key == timer_key,
                NotificationQueueItem.window_end_at == window_end_at,
            )
        )
        if existing is not None:
            if existing.dismissed_at is None:
                return ok(notification_queue_item_dto_from_row(existing))
            return ok(None)

        with transaction(self._session) as txn:
            now = self._clock.now_utc()
            row = NotificationQueueItem(
                notification_id=new_id(NotificationQueueItemID),
                source_kind=source_kind,
                plan_id=plan_id,
                timer_key=timer_key,
                window_end_at=window_end_at,
                calendar_entry_id=calendar_entry_id,
                block_calendar_entry_id=block_calendar_entry_id,
                display_label=display_label,
                created_at=now,
                dismissed_at=None,
            )
            txn.add(row)
            txn.flush()
            return ok(notification_queue_item_dto_from_row(row))

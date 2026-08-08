"""Notification queue routes."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from calendar_backend.api.deps import get_clock, get_db_session
from calendar_backend.api.errors import unwrap_result
from calendar_backend.api.serialize import dto_to_json
from calendar_backend.domain.ids import NotificationQueueItemID
from calendar_backend.domain.time import Clock
from calendar_backend.services.notification_queue import NotificationQueueService
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


@router.get("")
def list_notifications(
    session: Annotated[Session, Depends(get_db_session)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> dict[str, Any]:
    items = unwrap_result(NotificationQueueService(session, clock).list_pending())
    return {"notifications": dto_to_json(items)}


@router.post("/{notification_id}/dismiss")
def dismiss_notification(
    notification_id: UUID,
    session: Annotated[Session, Depends(get_db_session)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> dict[str, str]:
    unwrap_result(
        NotificationQueueService(session, clock).dismiss(NotificationQueueItemID(notification_id))
    )
    return {"status": "ok"}

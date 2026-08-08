"""Timer routes."""

from __future__ import annotations

from typing import Annotated, Any

from calendar_backend.api.deps import get_clock, get_db_session
from calendar_backend.api.errors import unwrap_result
from calendar_backend.api.serialize import dto_to_json
from calendar_backend.domain.time import Clock
from calendar_backend.services.timer import TimerService
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api/timers", tags=["timers"])


@router.get("/active")
def active_timers(
    session: Annotated[Session, Depends(get_db_session)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> dict[str, Any]:
    timers = unwrap_result(TimerService(session, clock).get_active_timers())
    return {"timers": dto_to_json(timers)}


@router.post("/{timer_key}/complete")
def complete_timer(
    timer_key: str,
    session: Annotated[Session, Depends(get_db_session)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> dict[str, Any]:
    notification = unwrap_result(TimerService(session, clock).complete_timer(timer_key))
    return {"notification": dto_to_json(notification)}

"""Shared persistence helpers for active calendar state."""

from __future__ import annotations

from sqlalchemy.orm import Session

from calendar_backend.domain.time import Clock
from calendar_backend.models.runs import ActiveCalendarState


def load_or_create_active_calendar_state(session: Session, clock: Clock) -> ActiveCalendarState:
    row = session.get(ActiveCalendarState, 1)
    if row is not None:
        return row

    now = clock.now_utc()
    row = ActiveCalendarState(
        singleton_id=1,
        active_calendar_run_id=None,
        last_refresh_failed=False,
        last_failure_at=None,
        last_failure_reason=None,
        updated_at=now,
    )
    session.add(row)
    session.flush()
    return row

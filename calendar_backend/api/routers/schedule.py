"""Schedule refresh and calendar read routes."""

from __future__ import annotations

from typing import Annotated, Any

from calendar_backend.api.deps import get_clock, get_db_session
from calendar_backend.api.errors import service_result_http_error, unwrap_result
from calendar_backend.api.serialize import dto_to_json
from calendar_backend.domain.time import Clock, truncate_to_minute
from calendar_backend.orchestration.refresh_schedule import OrchestrationService
from calendar_backend.services.calendar_read import CalendarReadService
from calendar_backend.services.master_horizon import MasterHorizonService
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api", tags=["schedule"])


@router.get("/schedule/state")
def schedule_state(
    session: Annotated[Session, Depends(get_db_session)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> dict[str, Any]:
    return dto_to_json(unwrap_result(CalendarReadService(session, clock).get_schedule_state()))


@router.get("/calendar/tasks")
def task_calendar(
    session: Annotated[Session, Depends(get_db_session)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> dict[str, Any]:
    return dto_to_json(unwrap_result(CalendarReadService(session, clock).get_task_calendar()))


@router.get("/calendar/blocks")
def block_calendar(
    session: Annotated[Session, Depends(get_db_session)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> dict[str, Any]:
    return dto_to_json(unwrap_result(CalendarReadService(session, clock).get_block_calendar()))


@router.post("/schedule/refresh")
def refresh_schedule(
    session: Annotated[Session, Depends(get_db_session)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> dict[str, Any]:
    run_started_at = truncate_to_minute(clock.now_utc())
    unwrap_result(MasterHorizonService(session, clock).refresh_master_horizon(run_started_at))
    result = OrchestrationService(session, clock).refresh_schedule(run_started_at)
    if not result.success:
        raise service_result_http_error(result)
    return dto_to_json(result.value)

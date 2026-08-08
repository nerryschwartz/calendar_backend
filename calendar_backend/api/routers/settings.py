"""App settings routes."""

from __future__ import annotations

from typing import Annotated, Any

from calendar_backend.api.deps import get_clock, get_db_session
from calendar_backend.api.errors import unwrap_result
from calendar_backend.api.serialize import dto_to_json
from calendar_backend.domain.enums import FreeTimeWeekStartDay
from calendar_backend.domain.time import Clock
from calendar_backend.services.app_settings import AppSettingsService
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api/settings", tags=["settings"])


class UpdateSettingsBody(BaseModel):
    local_timezone: str | None = None
    master_horizon_duration_minutes: int | None = None
    exact_solver_time_limit_seconds: int | None = None
    exact_solver_model_size_limit: int | None = None
    heuristic_enabled: bool | None = None
    free_time_week_start_day: FreeTimeWeekStartDay | None = None


@router.get("")
def get_settings(
    session: Annotated[Session, Depends(get_db_session)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> dict[str, Any]:
    return dto_to_json(unwrap_result(AppSettingsService(session, clock).get_settings()))


@router.patch("")
def update_settings(
    body: UpdateSettingsBody,
    session: Annotated[Session, Depends(get_db_session)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> dict[str, Any]:
    return dto_to_json(
        unwrap_result(
            AppSettingsService(session, clock).update_settings(
                local_timezone=body.local_timezone,
                master_horizon_duration_minutes=body.master_horizon_duration_minutes,
                exact_solver_time_limit_seconds=body.exact_solver_time_limit_seconds,
                exact_solver_model_size_limit=body.exact_solver_model_size_limit,
                heuristic_enabled=body.heuristic_enabled,
                free_time_week_start_day=body.free_time_week_start_day,
            )
        )
    )

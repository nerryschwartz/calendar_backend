"""Repetition management routes."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from calendar_backend.api.deps import get_clock, get_db_session
from calendar_backend.api.errors import unwrap_result
from calendar_backend.api.serialize import dto_to_json
from calendar_backend.domain.enums import RepeatMode
from calendar_backend.domain.ids import PlanID
from calendar_backend.domain.time import Clock
from calendar_backend.services.repetition import RepetitionService
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api/repetitions", tags=["repetitions"])


class UpdateRepetitionSettingsBody(BaseModel):
    repeat_mode: RepeatMode | None = None
    start_time: datetime | None = None
    repeat_interval_minutes: int | None = None
    manual_count: int | None = None
    end_time: datetime | None = None
    default_instance_critical: bool | None = None


@router.patch("/{repetition_id}/settings")
def update_settings(
    repetition_id: UUID,
    body: UpdateRepetitionSettingsBody,
    session: Annotated[Session, Depends(get_db_session)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> dict[str, Any]:
    return dto_to_json(
        unwrap_result(
            RepetitionService(session, clock).update_settings(
                PlanID(repetition_id),
                repeat_mode=body.repeat_mode,
                start_time=body.start_time,
                repeat_interval_minutes=body.repeat_interval_minutes,
                manual_count=body.manual_count,
                end_time=body.end_time,
                default_instance_critical=body.default_instance_critical,
            )
        )
    )


@router.post("/{repetition_id}/generate-instances")
def generate_instances(
    repetition_id: UUID,
    session: Annotated[Session, Depends(get_db_session)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> dict[str, Any]:
    return dto_to_json(
        unwrap_result(
            RepetitionService(session, clock).generate_instances(
                PlanID(repetition_id), clock.now_utc()
            )
        )
    )


@router.post("/{repetition_id}/refresh")
def refresh_repetition(
    repetition_id: UUID,
    session: Annotated[Session, Depends(get_db_session)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> dict[str, str]:
    unwrap_result(
        RepetitionService(session, clock).refresh_repetition(PlanID(repetition_id), clock.now_utc())
    )
    return {"status": "ok"}


@router.post("/refresh-all")
def refresh_all_repetitions(
    session: Annotated[Session, Depends(get_db_session)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> dict[str, str]:
    unwrap_result(RepetitionService(session, clock).refresh_all_repetitions(clock.now_utc()))
    return {"status": "ok"}

"""Free-time activity routes."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

from calendar_backend.api.deps import get_clock, get_db_session
from calendar_backend.api.errors import unwrap_result
from calendar_backend.api.serialize import dto_to_json
from calendar_backend.domain.ids import FreeTimeActivityID, FreeTimeActivityPrerequisiteID, PlanID
from calendar_backend.domain.time import Clock
from calendar_backend.services.free_time_activity import FreeTimeActivityService
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api/free-time", tags=["free-time"])


class CreateActivityBody(BaseModel):
    name: str
    real_fraction: Decimal
    minimum_block_size_minutes: int
    enabled: bool = True


class UpdateActivityBody(BaseModel):
    name: str | None = None
    real_fraction: Decimal | None = None
    minimum_block_size_minutes: int | None = None


class SetEnabledBody(BaseModel):
    enabled: bool


class PrerequisiteBody(BaseModel):
    prerequisite_plan_id: UUID


class BlockFamiliesBody(BaseModel):
    families: list[str] = Field(default_factory=list)


@router.get("/activities")
def list_activities(
    session: Annotated[Session, Depends(get_db_session)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> dict[str, Any]:
    activities = unwrap_result(FreeTimeActivityService(session, clock).list_activities())
    return {"activities": dto_to_json(activities)}


@router.post("/activities")
def create_activity(
    body: CreateActivityBody,
    session: Annotated[Session, Depends(get_db_session)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> dict[str, Any]:
    return dto_to_json(
        unwrap_result(
            FreeTimeActivityService(session, clock).create_activity(
                body.name,
                body.real_fraction,
                body.minimum_block_size_minutes,
                enabled=body.enabled,
            )
        )
    )


@router.get("/activities/{activity_id}")
def get_activity(
    activity_id: UUID,
    session: Annotated[Session, Depends(get_db_session)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> dict[str, Any]:
    return dto_to_json(
        unwrap_result(
            FreeTimeActivityService(session, clock).get_activity(FreeTimeActivityID(activity_id))
        )
    )


@router.patch("/activities/{activity_id}")
def update_activity(
    activity_id: UUID,
    body: UpdateActivityBody,
    session: Annotated[Session, Depends(get_db_session)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> dict[str, Any]:
    return dto_to_json(
        unwrap_result(
            FreeTimeActivityService(session, clock).update_activity(
                FreeTimeActivityID(activity_id),
                name=body.name,
                real_fraction=body.real_fraction,
                minimum_block_size_minutes=body.minimum_block_size_minutes,
            )
        )
    )


@router.post("/activities/{activity_id}/enabled")
def set_enabled(
    activity_id: UUID,
    body: SetEnabledBody,
    session: Annotated[Session, Depends(get_db_session)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> dict[str, Any]:
    return dto_to_json(
        unwrap_result(
            FreeTimeActivityService(session, clock).set_enabled(
                FreeTimeActivityID(activity_id), body.enabled
            )
        )
    )


@router.post("/activities/{activity_id}/prerequisites")
def add_prerequisite(
    activity_id: UUID,
    body: PrerequisiteBody,
    session: Annotated[Session, Depends(get_db_session)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> dict[str, Any]:
    return dto_to_json(
        unwrap_result(
            FreeTimeActivityService(session, clock).add_prerequisite(
                FreeTimeActivityID(activity_id), PlanID(body.prerequisite_plan_id)
            )
        )
    )


@router.delete("/activities/{activity_id}/prerequisites/{prerequisite_id}")
def remove_prerequisite(
    activity_id: UUID,
    prerequisite_id: UUID,
    session: Annotated[Session, Depends(get_db_session)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> dict[str, str]:
    unwrap_result(
        FreeTimeActivityService(session, clock).remove_prerequisite(
            FreeTimeActivityID(activity_id),
            FreeTimeActivityPrerequisiteID(prerequisite_id),
        )
    )
    return {"status": "ok"}


@router.put("/activities/{activity_id}/block-families")
def set_block_families(
    activity_id: UUID,
    body: BlockFamiliesBody,
    session: Annotated[Session, Depends(get_db_session)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> dict[str, Any]:
    return dto_to_json(
        unwrap_result(
            FreeTimeActivityService(session, clock).set_allowed_block_families(
                FreeTimeActivityID(activity_id), tuple(body.families)
            )
        )
    )


@router.delete("/activities/{activity_id}/block-families")
def clear_block_families(
    activity_id: UUID,
    session: Annotated[Session, Depends(get_db_session)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> dict[str, Any]:
    return dto_to_json(
        unwrap_result(
            FreeTimeActivityService(session, clock).clear_allowed_block_families(
                FreeTimeActivityID(activity_id)
            )
        )
    )

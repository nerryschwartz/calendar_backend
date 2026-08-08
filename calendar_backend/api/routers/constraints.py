"""Time constraint routes."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from calendar_backend.api.deps import get_clock, get_db_session
from calendar_backend.api.errors import unwrap_result
from calendar_backend.api.serialize import dto_to_json
from calendar_backend.domain.ids import PlanID, TimeConstraintGroupID, TimeWindowID
from calendar_backend.domain.time import Clock, TimeWindow
from calendar_backend.services.time_constraint import TimeConstraintService
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api", tags=["constraints"])


class UserWindowBody(BaseModel):
    start_time: datetime
    end_time: datetime


class UserGroupBody(BaseModel):
    windows: list[UserWindowBody]


@router.post("/plans/{plan_id}/constraints/groups")
def add_user_group(
    plan_id: UUID,
    body: UserGroupBody,
    session: Annotated[Session, Depends(get_db_session)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> dict[str, Any]:
    windows = tuple(
        TimeWindow(start_time=window.start_time, end_time=window.end_time)
        for window in body.windows
    )
    return dto_to_json(
        unwrap_result(
            TimeConstraintService(session, clock).add_user_group(PlanID(plan_id), windows)
        )
    )


@router.put("/constraints/groups/{group_id}/windows")
def update_user_group(
    group_id: UUID,
    body: UserGroupBody,
    session: Annotated[Session, Depends(get_db_session)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> dict[str, Any]:
    windows = tuple(
        TimeWindow(start_time=window.start_time, end_time=window.end_time)
        for window in body.windows
    )
    return dto_to_json(
        unwrap_result(
            TimeConstraintService(session, clock).update_user_group(
                TimeConstraintGroupID(group_id), windows
            )
        )
    )


@router.delete("/constraints/groups/{group_id}")
def remove_user_group(
    group_id: UUID,
    session: Annotated[Session, Depends(get_db_session)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> dict[str, str]:
    unwrap_result(
        TimeConstraintService(session, clock).remove_user_group(TimeConstraintGroupID(group_id))
    )
    return {"status": "ok"}


@router.post("/constraints/groups/{group_id}/windows")
def add_user_window(
    group_id: UUID,
    body: UserWindowBody,
    session: Annotated[Session, Depends(get_db_session)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> dict[str, Any]:
    window = TimeWindow(start_time=body.start_time, end_time=body.end_time)
    return dto_to_json(
        unwrap_result(
            TimeConstraintService(session, clock).add_user_window(
                TimeConstraintGroupID(group_id), window
            )
        )
    )


@router.delete("/constraints/groups/{group_id}/windows/{window_id}")
def remove_user_window(
    group_id: UUID,
    window_id: UUID,
    session: Annotated[Session, Depends(get_db_session)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> dict[str, Any]:
    result = TimeConstraintService(session, clock).remove_user_window(
        TimeConstraintGroupID(group_id), TimeWindowID(window_id)
    )
    value = unwrap_result(result)
    return {"group": dto_to_json(value)}

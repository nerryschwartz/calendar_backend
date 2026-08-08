"""Deletion preview and conflict suggestion routes."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from calendar_backend.api.deps import get_clock, get_db_session
from calendar_backend.api.errors import unwrap_result
from calendar_backend.api.serialize import dto_to_json
from calendar_backend.deletion.conflict_suggestions import ConflictDeletionSuggestionService
from calendar_backend.deletion.preview_service import DeletionPreviewService
from calendar_backend.domain.deletion import AssignmentConflict
from calendar_backend.domain.errors import MessageCode
from calendar_backend.domain.ids import PlanID
from calendar_backend.domain.time import Clock
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api/deletion", tags=["deletion"])


class ConflictSuggestionBody(BaseModel):
    conflict: dict[str, Any]


@router.get("/plans/{plan_id}/preview")
def preview_delete_plan(
    plan_id: UUID,
    session: Annotated[Session, Depends(get_db_session)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> dict[str, Any]:
    preview = unwrap_result(
        DeletionPreviewService(session, clock).preview_delete_plan(PlanID(plan_id))
    )
    return dto_to_json(preview)


@router.post("/conflict-suggestions")
def conflict_suggestions(
    body: ConflictSuggestionBody,
    session: Annotated[Session, Depends(get_db_session)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> dict[str, Any]:
    conflict = _conflict_from_payload(body.conflict)
    suggestions = ConflictDeletionSuggestionService(session, clock).suggest_for_conflict(conflict)
    return {"suggestions": dto_to_json(unwrap_result(suggestions))}


def _conflict_from_payload(payload: dict[str, Any]) -> AssignmentConflict:
    reason_code = payload.get("reason_code")
    return AssignmentConflict(
        conflicting_plan_ids=tuple(
            PlanID(UUID(plan_id)) for plan_id in payload.get("conflicting_plan_ids", [])
        ),
        affected_priority_by_plan_id=tuple(
            (PlanID(UUID(plan_id)), priority)
            for plan_id, priority in payload.get("affected_priority_by_plan_id", [])
        ),
        reason_code=MessageCode(reason_code) if reason_code is not None else None,
        task_ids=tuple(PlanID(UUID(plan_id)) for plan_id in payload.get("task_ids", [])),
        explanation=payload.get("explanation", ""),
        is_global=bool(payload.get("is_global", False)),
        is_approximate=bool(payload.get("is_approximate", True)),
    )

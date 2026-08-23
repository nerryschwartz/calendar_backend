"""Plan tree read and mutation routes."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from calendar_backend.api.deps import get_clock, get_db_session
from calendar_backend.api.errors import service_result_http_error, unwrap_result
from calendar_backend.api.serialize import dto_to_json
from calendar_backend.domain.enums import PlanKind, RepeatMode
from calendar_backend.domain.errors import MessageCode, ServiceMessage
from calendar_backend.domain.ids import PlanID
from calendar_backend.domain.plan_create import (
    BlockCreatePayload,
    GoalCreatePayload,
    RepetitionCreatePayload,
    TaskCreatePayload,
)
from calendar_backend.domain.results import fail
from calendar_backend.domain.time import Clock
from calendar_backend.services.block import BlockService
from calendar_backend.services.goal import GoalService
from calendar_backend.services.plan_tree import PlanTreeService
from calendar_backend.services.plan_tree_invariant import PlanTreeInvariantService
from calendar_backend.services.plan_tree_read import PlanTreeReadService
from calendar_backend.services.task import TaskService
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api/plans", tags=["plans"])


class RenameBody(BaseModel):
    name: str


class CreateChildBody(BaseModel):
    kind: PlanKind
    is_critical: bool
    name: str
    duration_minutes: int | None = None
    divisible: bool | None = None
    minimum_chunk_size_minutes: int | None = None
    block_family: str | None = None
    repeat_mode: RepeatMode | None = None
    start_time: datetime | None = None
    repeat_interval_minutes: int | None = None
    manual_count: int | None = None
    end_time: datetime | None = None
    default_instance_critical: bool | None = None
    template_type: PlanKind | None = None
    template_name: str | None = None
    template_duration_minutes: int | None = None
    template_divisible: bool | None = None
    template_minimum_chunk_size_minutes: int | None = None
    template_block_family: str | None = None


class MovePlanBody(BaseModel):
    position: int
    is_critical: bool | None = None


class PrerequisiteBody(BaseModel):
    prerequisite_plan_id: UUID


class TaskSchedulingBody(BaseModel):
    duration_minutes: int | None = None
    divisible: bool | None = None
    minimum_chunk_size_minutes: int | None = None


class BlockSchedulingBody(BaseModel):
    duration_minutes: int | None = None
    divisible: bool | None = None
    minimum_chunk_size_minutes: int | None = None
    block_family: str | None = None


class BlockFamiliesBody(BaseModel):
    families: list[str] = Field(default_factory=list)


@router.get("/master")
def get_master(
    session: Annotated[Session, Depends(get_db_session)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> dict[str, Any]:
    read = PlanTreeReadService(session, clock)
    master_id = unwrap_result(read.ensure_master_and_get_id())
    detail = unwrap_result(read.get_plan_detail(master_id))
    return {"master_plan_id": str(master_id), "plan": dto_to_json(detail)}


@router.get("/search")
def search_plans(
    q: str,
    session: Annotated[Session, Depends(get_db_session)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> dict[str, Any]:
    results = unwrap_result(PlanTreeReadService(session, clock).search_plans(q))
    return {"results": dto_to_json(results)}


@router.get("/{plan_id}")
def get_plan(
    plan_id: UUID,
    session: Annotated[Session, Depends(get_db_session)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> dict[str, Any]:
    detail = unwrap_result(PlanTreeReadService(session, clock).get_plan_detail(PlanID(plan_id)))
    return dto_to_json(detail)


@router.post("/validate")
def validate_tree(
    session: Annotated[Session, Depends(get_db_session)],
) -> dict[str, str]:
    result = PlanTreeInvariantService(session).validate_master_tree()
    if not result.success:
        raise service_result_http_error(result)
    return {"status": "ok"}


@router.patch("/{plan_id}/rename")
def rename_plan(
    plan_id: UUID,
    body: RenameBody,
    session: Annotated[Session, Depends(get_db_session)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> dict[str, str]:
    unwrap_result(PlanTreeService(session, clock).rename_plan(PlanID(plan_id), body.name))
    return {"status": "ok"}


@router.post("/{parent_id}/children")
def create_child(
    parent_id: UUID,
    body: CreateChildBody,
    session: Annotated[Session, Depends(get_db_session)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> dict[str, Any]:
    payload = _create_payload_from_body(body)
    result = GoalService(session, clock).create_child(  # pyright: ignore[reportCallIssue]
        PlanID(parent_id),
        body.kind,  # pyright: ignore[reportArgumentType]
        payload,  # pyright: ignore[reportArgumentType]
        body.is_critical,
    )
    return dto_to_json(unwrap_result(result))


@router.post("/{plan_id}/move")
def move_plan(
    plan_id: UUID,
    body: MovePlanBody,
    session: Annotated[Session, Depends(get_db_session)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> dict[str, str]:
    service = GoalService(session, clock)
    if body.is_critical is None:
        unwrap_result(service.move_plan(PlanID(plan_id), body.position))
    else:
        unwrap_result(service.move_plan(PlanID(plan_id), body.is_critical, body.position))
    return {"status": "ok"}


@router.post("/{plan_id}/prerequisites")
def add_prerequisite(
    plan_id: UUID,
    body: PrerequisiteBody,
    session: Annotated[Session, Depends(get_db_session)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> dict[str, str]:
    unwrap_result(
        PlanTreeService(session, clock).add_plan_prerequisite(
            PlanID(plan_id), PlanID(body.prerequisite_plan_id)
        )
    )
    return {"status": "ok"}


@router.delete("/{plan_id}/prerequisites/{prerequisite_plan_id}")
def remove_prerequisite(
    plan_id: UUID,
    prerequisite_plan_id: UUID,
    session: Annotated[Session, Depends(get_db_session)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> dict[str, str]:
    unwrap_result(
        PlanTreeService(session, clock).remove_plan_prerequisite(
            PlanID(plan_id), PlanID(prerequisite_plan_id)
        )
    )
    return {"status": "ok"}


@router.delete("/{plan_id}")
def delete_plan(
    plan_id: UUID,
    session: Annotated[Session, Depends(get_db_session)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> dict[str, str]:
    unwrap_result(PlanTreeService(session, clock).delete_plan(PlanID(plan_id)))
    return {"status": "ok"}


@router.get("/{plan_id}/delete-preview")
def delete_preview(
    plan_id: UUID,
    session: Annotated[Session, Depends(get_db_session)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> dict[str, Any]:
    preview = unwrap_result(PlanTreeService(session, clock).preview_delete(PlanID(plan_id)))
    return dto_to_json(preview)


@router.patch("/{plan_id}/task/scheduling")
def update_task_scheduling(
    plan_id: UUID,
    body: TaskSchedulingBody,
    session: Annotated[Session, Depends(get_db_session)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> dict[str, Any]:
    if body.duration_minutes is None or body.divisible is None:
        raise service_result_http_error(
            fail(
                ServiceMessage(
                    code=MessageCode.INVALID_TASK_SCHEDULING_FIELDS,
                    message="duration_minutes and divisible are required",
                    details={},
                )
            )
        )
    result = TaskService(session, clock).update_scheduling_fields(
        PlanID(plan_id),
        duration_minutes=body.duration_minutes,
        divisible=body.divisible,
        minimum_chunk_size_minutes=body.minimum_chunk_size_minutes,
    )
    return dto_to_json(unwrap_result(result))


@router.post("/{plan_id}/task/complete")
def complete_task(
    plan_id: UUID,
    session: Annotated[Session, Depends(get_db_session)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> dict[str, Any]:
    return dto_to_json(unwrap_result(TaskService(session, clock).mark_complete(PlanID(plan_id))))


@router.post("/{plan_id}/task/reopen")
def reopen_task(
    plan_id: UUID,
    session: Annotated[Session, Depends(get_db_session)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> dict[str, Any]:
    return dto_to_json(unwrap_result(TaskService(session, clock).reopen(PlanID(plan_id))))


@router.put("/{plan_id}/task/block-families")
def set_task_block_families(
    plan_id: UUID,
    body: BlockFamiliesBody,
    session: Annotated[Session, Depends(get_db_session)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> dict[str, Any]:
    return dto_to_json(
        unwrap_result(
            TaskService(session, clock).set_allowed_block_families(
                PlanID(plan_id), tuple(body.families)
            )
        )
    )


@router.delete("/{plan_id}/task/block-families")
def clear_task_block_families(
    plan_id: UUID,
    session: Annotated[Session, Depends(get_db_session)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> dict[str, Any]:
    return dto_to_json(
        unwrap_result(TaskService(session, clock).clear_allowed_block_families(PlanID(plan_id)))
    )


@router.patch("/{plan_id}/block/scheduling")
def update_block_scheduling(
    plan_id: UUID,
    body: BlockSchedulingBody,
    session: Annotated[Session, Depends(get_db_session)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> dict[str, Any]:
    if body.duration_minutes is None or body.divisible is None or body.block_family is None:
        raise service_result_http_error(
            fail(
                ServiceMessage(
                    code=MessageCode.INVALID_TASK_SCHEDULING_FIELDS,
                    message="duration_minutes, divisible, and block_family are required",
                    details={},
                )
            )
        )
    result = BlockService(session, clock).update_scheduling_fields(
        PlanID(plan_id),
        duration_minutes=body.duration_minutes,
        divisible=body.divisible,
        minimum_chunk_size_minutes=body.minimum_chunk_size_minutes,
        block_family=body.block_family,
    )
    return dto_to_json(unwrap_result(result))


@router.post("/{plan_id}/block/complete")
def complete_block(
    plan_id: UUID,
    session: Annotated[Session, Depends(get_db_session)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> dict[str, Any]:
    return dto_to_json(unwrap_result(BlockService(session, clock).mark_complete(PlanID(plan_id))))


@router.post("/{plan_id}/block/reopen")
def reopen_block(
    plan_id: UUID,
    session: Annotated[Session, Depends(get_db_session)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> dict[str, Any]:
    return dto_to_json(unwrap_result(BlockService(session, clock).reopen(PlanID(plan_id))))


def _create_payload_from_body(body: CreateChildBody):
    if body.kind == PlanKind.GOAL:
        return GoalCreatePayload(name=body.name)
    if body.kind == PlanKind.TASK:
        return TaskCreatePayload(
            name=body.name,
            duration_minutes=body.duration_minutes or 30,
            divisible=body.divisible or False,
            minimum_chunk_size_minutes=body.minimum_chunk_size_minutes,
        )
    if body.kind == PlanKind.BLOCK:
        return BlockCreatePayload(
            name=body.name,
            duration_minutes=body.duration_minutes or 30,
            divisible=body.divisible or False,
            minimum_chunk_size_minutes=body.minimum_chunk_size_minutes,
            block_family=body.block_family or "default",
        )
    template_type = body.template_type or PlanKind.TASK
    template_payload: GoalCreatePayload | TaskCreatePayload | BlockCreatePayload
    if template_type == PlanKind.GOAL:
        template_payload = GoalCreatePayload(name=body.template_name or "template")
    elif template_type == PlanKind.BLOCK:
        template_payload = BlockCreatePayload(
            name=body.template_name or "template",
            duration_minutes=body.template_duration_minutes or 30,
            divisible=body.template_divisible or False,
            minimum_chunk_size_minutes=body.template_minimum_chunk_size_minutes,
            block_family=body.template_block_family or "default",
        )
    else:
        template_payload = TaskCreatePayload(
            name=body.template_name or "template",
            duration_minutes=body.template_duration_minutes or 30,
            divisible=body.template_divisible or False,
            minimum_chunk_size_minutes=body.template_minimum_chunk_size_minutes,
        )
    return RepetitionCreatePayload(
        name=body.name,
        repeat_mode=body.repeat_mode or RepeatMode.MANUAL_COUNT,
        start_time=body.start_time or datetime.fromisoformat("2026-01-01T09:00:00+00:00"),
        repeat_interval_minutes=body.repeat_interval_minutes or 60,
        manual_count=body.manual_count,
        end_time=body.end_time,
        default_instance_critical=body.default_instance_critical or False,
        template_type=template_type,
        template_payload=template_payload,
    )

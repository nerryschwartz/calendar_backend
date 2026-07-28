"""Pure block scheduling validation for write paths."""

from __future__ import annotations

from calendar_backend.domain.errors import MessageCode, ServiceMessage
from calendar_backend.domain.plan_create import BlockCreatePayload
from calendar_backend.domain.tasks import validate_task_scheduling_fields


def validate_block_family(block_family: str) -> ServiceMessage | None:
    if not block_family.strip():
        return ServiceMessage(
            code=MessageCode.INVALID_CREATE_PAYLOAD,
            message="block_family must be non-empty",
            details={"block_family": block_family},
        )
    return None


def validate_block_scheduling_fields(
    duration_minutes: int,
    divisible: bool,
    minimum_chunk_size_minutes: int | None,
) -> ServiceMessage | None:
    return validate_task_scheduling_fields(
        duration_minutes,
        divisible,
        minimum_chunk_size_minutes,
    )


def validate_block_create(payload: BlockCreatePayload) -> ServiceMessage | None:
    family_error = validate_block_family(payload.block_family)
    if family_error is not None:
        return family_error
    return validate_block_scheduling_fields(
        payload.duration_minutes,
        payload.divisible,
        payload.minimum_chunk_size_minutes,
    )

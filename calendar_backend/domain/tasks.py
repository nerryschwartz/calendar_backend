"""Pure task scheduling validation for write paths."""

from __future__ import annotations

from calendar_backend.domain.errors import MessageCode, ServiceMessage
from calendar_backend.domain.plan_create import TaskCreatePayload


def validate_task_scheduling_fields(
    duration_minutes: int,
    divisible: bool,
    minimum_chunk_size_minutes: int | None,
) -> ServiceMessage | None:
    if duration_minutes <= 0:
        return ServiceMessage(
            code=MessageCode.INVALID_DURATION,
            message="Task duration must be positive",
            details={"duration_minutes": str(duration_minutes)},
        )

    if not divisible:
        if minimum_chunk_size_minutes is not None:
            return ServiceMessage(
                code=MessageCode.INVALID_TASK_SCHEDULING_FIELDS,
                message="Indivisible tasks must not set minimum_chunk_size_minutes",
                details={
                    "minimum_chunk_size_minutes": str(minimum_chunk_size_minutes),
                },
            )
        return None

    if minimum_chunk_size_minutes is None:
        chunk_error: ServiceMessage | None = ServiceMessage(
            code=MessageCode.INVALID_TASK_SCHEDULING_FIELDS,
            message="Divisible tasks require minimum_chunk_size_minutes",
            details={},
        )
    elif minimum_chunk_size_minutes <= 0:
        chunk_error = ServiceMessage(
            code=MessageCode.MINIMUM_CHUNK_SIZE_IMPOSSIBLE,
            message="Minimum chunk size must be positive",
            details={"minimum_chunk_size_minutes": str(minimum_chunk_size_minutes)},
        )
    elif minimum_chunk_size_minutes > duration_minutes:
        chunk_error = ServiceMessage(
            code=MessageCode.MINIMUM_CHUNK_SIZE_IMPOSSIBLE,
            message="Minimum chunk size cannot exceed task duration",
            details={
                "duration_minutes": str(duration_minutes),
                "minimum_chunk_size_minutes": str(minimum_chunk_size_minutes),
            },
        )
    else:
        chunk_error = None
    return chunk_error


def validate_task_create(payload: TaskCreatePayload) -> ServiceMessage | None:
    return validate_task_scheduling_fields(
        payload.duration_minutes,
        payload.divisible,
        payload.minimum_chunk_size_minutes,
    )

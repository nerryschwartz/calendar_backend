"""Frozen create payloads for goal-parent plan creation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from calendar_backend.domain.enums import PlanKind, RepeatMode
from calendar_backend.domain.errors import MessageCode, ServiceMessage


@dataclass(frozen=True)
class GoalCreatePayload:
    name: str


@dataclass(frozen=True)
class TaskCreatePayload:
    name: str
    duration_minutes: int
    divisible: bool
    minimum_chunk_size_minutes: int | None


@dataclass(frozen=True)
class RepetitionCreatePayload:
    name: str
    repeat_mode: RepeatMode
    start_time: datetime
    repeat_interval_minutes: int
    manual_count: int | None
    end_time: datetime | None
    default_instance_critical: bool
    template_type: PlanKind
    template_payload: CreatePayload


CreatePayload = GoalCreatePayload | TaskCreatePayload | RepetitionCreatePayload

_EXPECTED_PAYLOAD_TYPE: dict[PlanKind, type[CreatePayload]] = {
    PlanKind.GOAL: GoalCreatePayload,
    PlanKind.TASK: TaskCreatePayload,
    PlanKind.REPETITION: RepetitionCreatePayload,
}

from calendar_backend.domain.tasks import validate_task_create  # noqa: E402


def validate_create_payload(
    kind: PlanKind,
    payload: CreatePayload,
) -> ServiceMessage | None:
    expected_type = _EXPECTED_PAYLOAD_TYPE.get(kind)
    if expected_type is None:
        return ServiceMessage(
            code=MessageCode.INVALID_CREATE_PAYLOAD,
            message="Unsupported plan kind for create_child",
            details={"plan_kind": kind.value},
        )
    if not isinstance(payload, expected_type):
        return ServiceMessage(
            code=MessageCode.INVALID_CREATE_PAYLOAD,
            message="Create payload does not match plan kind",
            details={
                "plan_kind": kind.value,
                "payload_type": type(payload).__name__,
            },
        )
    if kind == PlanKind.TASK:
        assert isinstance(payload, TaskCreatePayload)  # type checker: isinstance above
        return validate_task_create(payload)
    if kind == PlanKind.REPETITION:
        from calendar_backend.domain.repetitions import validate_repetition_create  # noqa: PLC0415

        assert isinstance(payload, RepetitionCreatePayload)  # type checker: isinstance above
        return validate_repetition_create(payload)
    return None

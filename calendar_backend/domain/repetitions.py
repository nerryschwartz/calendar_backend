"""Pure repetition create validation for write paths."""

from __future__ import annotations

from datetime import datetime

from calendar_backend.domain.enums import PlanKind, RepeatMode, RepetitionTimestampField
from calendar_backend.domain.errors import MessageCode, ServiceMessage
from calendar_backend.domain.plan_create import (
    CreatePayload,
    GoalCreatePayload,
    RepetitionCreatePayload,
)
from calendar_backend.domain.time import is_minute_aligned, require_utc


def validate_repetition_create(payload: RepetitionCreatePayload) -> ServiceMessage | None:
    template_error = _validate_repetition_template(
        payload.template_type,
        payload.template_payload,
    )
    if template_error is not None:
        return template_error
    for check in (
        _repetition_timestamp_error(
            payload.start_time,
            field_name=RepetitionTimestampField.START_TIME,
        ),
        _repetition_interval_error(payload.repeat_interval_minutes),
        _repetition_mode_fields_error(
            payload.repeat_mode,
            payload.manual_count,
            payload.end_time,
        ),
        _repetition_end_time_error(payload.start_time, payload.end_time),
    ):
        if check is not None:
            return check
    return None


# TODO(Prompt 10 / RepetitionService slice 1): Use validate_create_payload for templates.
def _validate_repetition_template(
    template_type: PlanKind,
    template_payload: CreatePayload,
) -> ServiceMessage | None:
    if template_type != PlanKind.GOAL:
        return ServiceMessage(
            code=MessageCode.INVALID_CREATE_PAYLOAD,
            message="Repetition template type is not supported yet",
            details={"template_type": template_type.value},
        )
    if not isinstance(template_payload, GoalCreatePayload):
        return ServiceMessage(
            code=MessageCode.INVALID_CREATE_PAYLOAD,
            message="Repetition template payload does not match template type",
            details={
                "template_type": template_type.value,
                "payload_type": type(template_payload).__name__,
            },
        )
    return None


def _repetition_timestamp_error(
    value: datetime,
    *,
    field_name: RepetitionTimestampField,
) -> ServiceMessage | None:
    field_label = field_name.value.lower()
    try:
        require_utc(value)
    except ValueError:
        return ServiceMessage(
            code=MessageCode.INVALID_REPETITION_SETTINGS,
            message=f"Repetition {field_label} must be timezone-aware UTC",
            details={},
        )
    if not is_minute_aligned(value):
        return ServiceMessage(
            code=MessageCode.INVALID_REPETITION_SETTINGS,
            message=f"Repetition {field_label} must be minute-aligned",
            details={},
        )
    return None


def _repetition_interval_error(repeat_interval_minutes: int) -> ServiceMessage | None:
    if repeat_interval_minutes <= 0:
        return ServiceMessage(
            code=MessageCode.INVALID_REPETITION_SETTINGS,
            message="Repetition repeat_interval_minutes must be positive",
            details={"repeat_interval_minutes": str(repeat_interval_minutes)},
        )
    return None


def _repetition_mode_fields_error(
    repeat_mode: RepeatMode,
    manual_count: int | None,
    end_time: datetime | None,
) -> ServiceMessage | None:
    if repeat_mode == RepeatMode.MANUAL_COUNT:
        if manual_count is None or manual_count <= 0:
            return ServiceMessage(
                code=MessageCode.INVALID_REPETITION_SETTINGS,
                message="manual_count is required and must be positive for MANUAL_COUNT mode",
                details={},
            )
        if end_time is not None:
            return ServiceMessage(
                code=MessageCode.INVALID_REPETITION_SETTINGS,
                message="end_time must be unset for MANUAL_COUNT mode",
                details={},
            )
        return None
    if manual_count is not None:
        return ServiceMessage(
            code=MessageCode.INVALID_REPETITION_SETTINGS,
            message="manual_count must be unset for DATE_RANGE mode",
            details={},
        )
    return None


def _repetition_end_time_error(
    start_time: datetime,
    end_time: datetime | None,
) -> ServiceMessage | None:
    if end_time is None:
        return None
    timestamp_error = _repetition_timestamp_error(
        end_time,
        field_name=RepetitionTimestampField.END_TIME,
    )
    if timestamp_error is not None:
        return timestamp_error
    if end_time <= start_time:
        return ServiceMessage(
            code=MessageCode.INVALID_REPETITION_SETTINGS,
            message="Repetition end_time must be after start_time",
            details={},
        )
    return None

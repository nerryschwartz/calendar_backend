"""Pure repetition create and settings validation for write paths."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from calendar_backend.domain.enums import RepeatMode, RepetitionTimestampField
from calendar_backend.domain.errors import MessageCode, ServiceMessage
from calendar_backend.domain.plan_create import RepetitionCreatePayload
from calendar_backend.domain.time import is_minute_aligned, require_utc


@dataclass(frozen=True)
class RepetitionSettingsState:
    repeat_mode: RepeatMode
    start_time: datetime
    repeat_interval_minutes: int
    manual_count: int | None
    end_time: datetime | None
    default_instance_critical: bool
    generated_at: datetime | None


def validate_repetition_create(payload: RepetitionCreatePayload) -> ServiceMessage | None:
    from calendar_backend.domain.plan_create import validate_create_payload  # noqa: PLC0415

    template_error = validate_create_payload(payload.template_type, payload.template_payload)
    if template_error is not None:
        return template_error
    return _validate_repetition_settings_fields(
        repeat_mode=payload.repeat_mode,
        start_time=payload.start_time,
        repeat_interval_minutes=payload.repeat_interval_minutes,
        manual_count=payload.manual_count,
        end_time=payload.end_time,
    )


def validate_repetition_settings_update(
    current: RepetitionSettingsState,
    proposed: RepetitionSettingsState,
) -> ServiceMessage | None:
    if current.generated_at is not None:
        lock_error: ServiceMessage | None = None
        if proposed.repeat_mode != current.repeat_mode:
            lock_error = ServiceMessage(
                code=MessageCode.INVALID_REPETITION_SETTINGS,
                message="repeat_mode is locked after generation",
                details={},
            )
        elif proposed.start_time != current.start_time:
            lock_error = ServiceMessage(
                code=MessageCode.INVALID_REPETITION_SETTINGS,
                message="start_time is locked after generation",
                details={},
            )
        elif proposed.repeat_interval_minutes != current.repeat_interval_minutes:
            lock_error = ServiceMessage(
                code=MessageCode.INVALID_REPETITION_SETTINGS,
                message="repeat_interval_minutes is locked after generation",
                details={},
            )
        elif (
            current.manual_count is not None
            and proposed.manual_count is not None
            and proposed.manual_count < current.manual_count
        ):
            lock_error = ServiceMessage(
                code=MessageCode.REPETITION_COUNT_DECREASE_AFTER_GENERATION,
                message="manual_count may not decrease after generation",
                details={
                    "current_manual_count": str(current.manual_count),
                    "proposed_manual_count": str(proposed.manual_count),
                },
            )
        elif current.repeat_mode == RepeatMode.DATE_RANGE:
            if current.end_time is not None and proposed.end_time is None:
                lock_error = ServiceMessage(
                    code=MessageCode.INVALID_REPETITION_SETTINGS,
                    message="end_time may not be cleared after generation",
                    details={},
                )
            elif (
                current.end_time is not None
                and proposed.end_time is not None
                and proposed.end_time < current.end_time
            ):
                lock_error = ServiceMessage(
                    code=MessageCode.INVALID_REPETITION_SETTINGS,
                    message="end_time may only be extended after generation",
                    details={},
                )
            elif current.end_time is None and proposed.end_time is not None:
                lock_error = ServiceMessage(
                    code=MessageCode.INVALID_REPETITION_SETTINGS,
                    message="end_time may not be set after generation when currently open-ended",
                    details={},
                )
        if lock_error is not None:
            return lock_error

    return _validate_repetition_settings_fields(
        repeat_mode=proposed.repeat_mode,
        start_time=proposed.start_time,
        repeat_interval_minutes=proposed.repeat_interval_minutes,
        manual_count=proposed.manual_count,
        end_time=proposed.end_time,
    )


def _validate_repetition_settings_fields(
    *,
    repeat_mode: RepeatMode,
    start_time: datetime,
    repeat_interval_minutes: int,
    manual_count: int | None,
    end_time: datetime | None,
) -> ServiceMessage | None:
    for check in (
        _repetition_timestamp_error(
            start_time,
            field_name=RepetitionTimestampField.START_TIME,
        ),
        _repetition_interval_error(repeat_interval_minutes),
        _repetition_mode_fields_error(
            repeat_mode,
            manual_count,
            end_time,
        ),
        _repetition_end_time_error(start_time, end_time),
    ):
        if check is not None:
            return check
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

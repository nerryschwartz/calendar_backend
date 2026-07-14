"""Unit tests for repetition create and settings validation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from calendar_backend.domain.enums import PlanKind, RepeatMode
from calendar_backend.domain.errors import MessageCode, ServiceMessage
from calendar_backend.domain.plan_create import (
    GoalCreatePayload,
    RepetitionCreatePayload,
    TaskCreatePayload,
)
from calendar_backend.domain.repetitions import (
    RepetitionSettingsState,
    compute_instance_indices,
    instance_start_time,
    validate_repetition_create,
    validate_repetition_settings_update,
)

_START = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
_END = _START + timedelta(hours=2)
_EXTENDED_END = _END + timedelta(hours=1)


_DEFAULT_TEMPLATE = GoalCreatePayload(name="template")


def _repetition_payload(
    *,
    template_type: PlanKind = PlanKind.GOAL,
    template_payload: GoalCreatePayload | TaskCreatePayload | RepetitionCreatePayload | None = None,
    repeat_mode: RepeatMode = RepeatMode.MANUAL_COUNT,
    start_time: datetime = _START,
    repeat_interval_minutes: int = 60,
    manual_count: int | None = 3,
    end_time: datetime | None = None,
) -> RepetitionCreatePayload:
    if template_payload is None:
        template_payload = _DEFAULT_TEMPLATE
    return RepetitionCreatePayload(
        name="weekly",
        repeat_mode=repeat_mode,
        start_time=start_time,
        repeat_interval_minutes=repeat_interval_minutes,
        manual_count=manual_count,
        end_time=end_time,
        default_instance_critical=False,
        template_type=template_type,
        template_payload=template_payload,
    )


def _settings_state(
    *,
    repeat_mode: RepeatMode = RepeatMode.MANUAL_COUNT,
    start_time: datetime = _START,
    repeat_interval_minutes: int = 60,
    manual_count: int | None = 3,
    end_time: datetime | None = None,
    default_instance_critical: bool = False,
    generated_at: datetime | None = None,
) -> RepetitionSettingsState:
    return RepetitionSettingsState(
        repeat_mode=repeat_mode,
        start_time=start_time,
        repeat_interval_minutes=repeat_interval_minutes,
        manual_count=manual_count,
        end_time=end_time,
        default_instance_critical=default_instance_critical,
        generated_at=generated_at,
    )


def test_validate_repetition_create_accepts_valid_manual_count_payload() -> None:
    assert validate_repetition_create(_repetition_payload()) is None


def test_validate_repetition_create_accepts_task_template() -> None:
    assert (
        validate_repetition_create(
            _repetition_payload(
                template_type=PlanKind.TASK,
                template_payload=TaskCreatePayload("template task", 30, False, None),
            )
        )
        is None
    )


def test_validate_repetition_create_accepts_nested_repetition_template() -> None:
    inner = _repetition_payload()
    assert (
        validate_repetition_create(
            _repetition_payload(
                template_type=PlanKind.REPETITION,
                template_payload=inner,
            )
        )
        is None
    )


def test_validate_repetition_create_rejects_template_payload_mismatch() -> None:
    error = validate_repetition_create(
        _repetition_payload(template_payload=TaskCreatePayload("t", 30, False, None))
    )
    assert error is not None
    assert error.code == MessageCode.INVALID_CREATE_PAYLOAD
    assert "does not match plan kind" in error.message


def test_validate_repetition_create_rejects_manual_count_with_end_time() -> None:
    error = validate_repetition_create(_repetition_payload(end_time=_END))
    assert error is not None
    assert error.code == MessageCode.INVALID_REPETITION_SETTINGS
    assert "end_time must be unset for MANUAL_COUNT mode" in error.message


def test_validate_repetition_create_rejects_non_minute_aligned_start_time() -> None:
    error = validate_repetition_create(_repetition_payload(start_time=_START.replace(second=15)))
    assert error is not None
    assert error.code == MessageCode.INVALID_REPETITION_SETTINGS
    assert "start_time must be minute-aligned" in error.message


def test_validate_repetition_create_rejects_naive_start_time() -> None:
    naive_start = datetime(2026, 1, 1, 10, 0)
    error = validate_repetition_create(_repetition_payload(start_time=naive_start))
    assert error is not None
    assert error.code == MessageCode.INVALID_REPETITION_SETTINGS
    assert "timezone-aware UTC" in error.message


def test_validate_repetition_create_rejects_non_positive_repeat_interval() -> None:
    error = validate_repetition_create(_repetition_payload(repeat_interval_minutes=0))
    assert error is not None
    assert error.code == MessageCode.INVALID_REPETITION_SETTINGS
    assert "repeat_interval_minutes must be positive" in error.message


def test_validate_repetition_create_rejects_non_positive_manual_count() -> None:
    error = validate_repetition_create(_repetition_payload(manual_count=0))
    assert error is not None
    assert error.code == MessageCode.INVALID_REPETITION_SETTINGS
    assert "manual_count is required and must be positive" in error.message


def test_validate_repetition_create_rejects_date_range_with_manual_count() -> None:
    error = validate_repetition_create(
        _repetition_payload(
            repeat_mode=RepeatMode.DATE_RANGE,
            manual_count=3,
            end_time=_END,
        )
    )
    assert error is not None
    assert error.code == MessageCode.INVALID_REPETITION_SETTINGS
    assert "manual_count must be unset for DATE_RANGE mode" in error.message


def test_validate_repetition_create_rejects_end_time_before_start_time() -> None:
    error = validate_repetition_create(
        _repetition_payload(
            repeat_mode=RepeatMode.DATE_RANGE,
            manual_count=None,
            end_time=_START - timedelta(minutes=30),
        )
    )
    assert error is not None
    assert error.code == MessageCode.INVALID_REPETITION_SETTINGS
    assert "end_time must be after start_time" in error.message


def test_validate_repetition_create_rejects_invalid_task_template_scheduling() -> None:
    error = validate_repetition_create(
        _repetition_payload(
            template_type=PlanKind.TASK,
            template_payload=TaskCreatePayload("bad task", 0, False, None),
        )
    )
    assert error is not None
    assert error.code == MessageCode.INVALID_DURATION


def test_validate_repetition_settings_update_accepts_pre_generation_change() -> None:
    current = _settings_state()
    proposed = _settings_state(manual_count=5)
    assert validate_repetition_settings_update(current, proposed) is None


def test_validate_repetition_settings_update_locks_repeat_mode_after_generation() -> None:
    current = _settings_state(generated_at=_START)
    proposed = _settings_state(repeat_mode=RepeatMode.DATE_RANGE, generated_at=_START)
    error = validate_repetition_settings_update(current, proposed)
    assert error is not None
    assert error.code == MessageCode.INVALID_REPETITION_SETTINGS
    assert "repeat_mode is locked" in error.message


def test_validate_repetition_settings_update_locks_start_time_after_generation() -> None:
    current = _settings_state(generated_at=_START)
    proposed = _settings_state(start_time=_START + timedelta(hours=1), generated_at=_START)
    error = validate_repetition_settings_update(current, proposed)
    assert error is not None
    assert error.code == MessageCode.INVALID_REPETITION_SETTINGS
    assert "start_time is locked" in error.message


def test_validate_repetition_settings_update_locks_repeat_interval_after_generation() -> None:
    current = _settings_state(generated_at=_START)
    proposed = _settings_state(repeat_interval_minutes=90, generated_at=_START)
    error = validate_repetition_settings_update(current, proposed)
    assert error is not None
    assert error.code == MessageCode.INVALID_REPETITION_SETTINGS
    assert "repeat_interval_minutes is locked" in error.message


def test_validate_repetition_settings_update_rejects_manual_count_decrease_after_generation() -> (
    None
):
    current = _settings_state(generated_at=_START)
    proposed = _settings_state(manual_count=2, generated_at=_START)
    error = validate_repetition_settings_update(current, proposed)
    assert error is not None
    assert error.code == MessageCode.REPETITION_COUNT_DECREASE_AFTER_GENERATION


def test_validate_repetition_settings_update_allows_manual_count_increase_after_generation() -> (
    None
):
    current = _settings_state(generated_at=_START)
    proposed = _settings_state(manual_count=5, generated_at=_START)
    assert validate_repetition_settings_update(current, proposed) is None


def test_validate_repetition_settings_update_rejects_end_time_shorten_after_generation() -> None:
    current = _settings_state(
        repeat_mode=RepeatMode.DATE_RANGE,
        manual_count=None,
        end_time=_EXTENDED_END,
        generated_at=_START,
    )
    proposed = _settings_state(
        repeat_mode=RepeatMode.DATE_RANGE,
        manual_count=None,
        end_time=_END,
        generated_at=_START,
    )
    error = validate_repetition_settings_update(current, proposed)
    assert error is not None
    assert error.code == MessageCode.INVALID_REPETITION_SETTINGS
    assert "extended" in error.message


def test_validate_repetition_settings_update_allows_end_time_extension_after_generation() -> None:
    current = _settings_state(
        repeat_mode=RepeatMode.DATE_RANGE,
        manual_count=None,
        end_time=_END,
        generated_at=_START,
    )
    proposed = _settings_state(
        repeat_mode=RepeatMode.DATE_RANGE,
        manual_count=None,
        end_time=_EXTENDED_END,
        generated_at=_START,
    )
    assert validate_repetition_settings_update(current, proposed) is None


def test_validate_repetition_settings_update_rejects_clearing_end_time_after_generation() -> None:
    current = _settings_state(
        repeat_mode=RepeatMode.DATE_RANGE,
        manual_count=None,
        end_time=_END,
        generated_at=_START,
    )
    proposed = _settings_state(
        repeat_mode=RepeatMode.DATE_RANGE,
        manual_count=None,
        end_time=None,
        generated_at=_START,
    )
    error = validate_repetition_settings_update(current, proposed)
    assert error is not None
    assert error.code == MessageCode.INVALID_REPETITION_SETTINGS
    assert "end_time may not be cleared" in error.message


def test_validate_repetition_settings_update_rejects_end_time_on_open_ended_after_generation() -> (
    None
):
    current = _settings_state(
        repeat_mode=RepeatMode.DATE_RANGE,
        manual_count=None,
        end_time=None,
        generated_at=_START,
    )
    proposed = _settings_state(
        repeat_mode=RepeatMode.DATE_RANGE,
        manual_count=None,
        end_time=_END,
        generated_at=_START,
    )
    error = validate_repetition_settings_update(current, proposed)
    assert error is not None
    assert error.code == MessageCode.INVALID_REPETITION_SETTINGS
    assert "may not be set after generation when currently open-ended" in error.message


def test_compute_instance_indices_manual_count() -> None:
    result = compute_instance_indices(
        repeat_mode=RepeatMode.MANUAL_COUNT,
        start_time=_START,
        repeat_interval_minutes=60,
        manual_count=3,
        end_time=None,
        master_horizon_end=None,
    )
    assert result == (0, 1, 2)


def test_compute_instance_indices_date_range_explicit_end() -> None:
    result = compute_instance_indices(
        repeat_mode=RepeatMode.DATE_RANGE,
        start_time=_START,
        repeat_interval_minutes=60,
        manual_count=None,
        end_time=_END,
        master_horizon_end=None,
    )
    assert result == (0, 1)


def test_compute_instance_indices_date_range_open_end_requires_horizon() -> None:
    result = compute_instance_indices(
        repeat_mode=RepeatMode.DATE_RANGE,
        start_time=_START,
        repeat_interval_minutes=60,
        manual_count=None,
        end_time=None,
        master_horizon_end=None,
    )
    assert isinstance(result, ServiceMessage)
    assert result.code == MessageCode.MASTER_HORIZON_NOT_FOUND


def test_instance_start_time_offsets_by_index() -> None:
    assert instance_start_time(
        _START, repeat_interval_minutes=60, instance_index=2
    ) == _START + timedelta(hours=2)

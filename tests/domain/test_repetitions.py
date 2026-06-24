"""Unit tests for repetition create validation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from calendar_backend.domain.enums import PlanKind, RepeatMode
from calendar_backend.domain.errors import MessageCode
from calendar_backend.domain.plan_create import (
    GoalCreatePayload,
    RepetitionCreatePayload,
    TaskCreatePayload,
)
from calendar_backend.domain.repetitions import validate_repetition_create

_START = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
_END = _START + timedelta(hours=2)


_DEFAULT_TEMPLATE = GoalCreatePayload(name="template")


def _repetition_payload(
    *,
    template_type: PlanKind = PlanKind.GOAL,
    template_payload: GoalCreatePayload | TaskCreatePayload | RepetitionCreatePayload | None = None,
    end_time: datetime | None = None,
) -> RepetitionCreatePayload:
    if template_payload is None:
        template_payload = _DEFAULT_TEMPLATE
    return RepetitionCreatePayload(
        name="weekly",
        repeat_mode=RepeatMode.MANUAL_COUNT,
        start_time=_START,
        repeat_interval_minutes=60,
        manual_count=3,
        end_time=end_time,
        default_instance_critical=False,
        template_type=template_type,
        template_payload=template_payload,
    )


def test_validate_repetition_create_accepts_valid_manual_count_payload() -> None:
    assert validate_repetition_create(_repetition_payload()) is None


def test_validate_repetition_create_rejects_non_goal_template_type() -> None:
    error = validate_repetition_create(
        _repetition_payload(
            template_type=PlanKind.TASK,
            template_payload=GoalCreatePayload(name="template"),
        )
    )
    assert error is not None
    assert error.code == MessageCode.INVALID_CREATE_PAYLOAD
    assert "not supported yet" in error.message


def test_validate_repetition_create_rejects_template_payload_mismatch() -> None:
    error = validate_repetition_create(
        _repetition_payload(template_payload=TaskCreatePayload("t", 30, False, None))
    )
    assert error is not None
    assert error.code == MessageCode.INVALID_CREATE_PAYLOAD
    assert "does not match template type" in error.message


def test_validate_repetition_create_rejects_nested_repetition_template_payload() -> None:
    nested = _repetition_payload()
    error = validate_repetition_create(
        _repetition_payload(template_payload=nested),
    )
    assert error is not None
    assert error.code == MessageCode.INVALID_CREATE_PAYLOAD


def test_validate_repetition_create_rejects_manual_count_with_end_time() -> None:
    error = validate_repetition_create(_repetition_payload(end_time=_END))
    assert error is not None
    assert error.code == MessageCode.INVALID_REPETITION_SETTINGS
    assert "end_time must be unset for MANUAL_COUNT mode" in error.message

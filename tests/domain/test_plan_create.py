"""Unit tests for plan create payload validation."""

from __future__ import annotations

from datetime import UTC, datetime

from calendar_backend.domain.enums import PlanKind, RepeatMode
from calendar_backend.domain.errors import MessageCode
from calendar_backend.domain.plan_create import (
    GoalCreatePayload,
    RepetitionCreatePayload,
    TaskCreatePayload,
    validate_create_payload,
)


def test_validate_create_payload_accepts_matching_goal_payload() -> None:
    assert validate_create_payload(PlanKind.GOAL, GoalCreatePayload(name="g")) is None


def test_validate_create_payload_rejects_kind_payload_mismatch() -> None:
    error = validate_create_payload(PlanKind.GOAL, TaskCreatePayload("t", 30, False, None))
    assert error is not None
    assert error.code == MessageCode.INVALID_CREATE_PAYLOAD


def test_validate_create_payload_accepts_valid_task_payload() -> None:
    payload = TaskCreatePayload(
        name="task", duration_minutes=30, divisible=False, minimum_chunk_size_minutes=None
    )
    assert validate_create_payload(PlanKind.TASK, payload) is None


def test_validate_create_payload_rejects_invalid_task_scheduling_fields() -> None:
    payload = TaskCreatePayload(
        name="task", duration_minutes=0, divisible=False, minimum_chunk_size_minutes=None
    )
    error = validate_create_payload(PlanKind.TASK, payload)
    assert error is not None
    assert error.code == MessageCode.INVALID_DURATION


def test_validate_create_payload_accepts_valid_repetition_payload() -> None:
    payload = RepetitionCreatePayload(
        name="weekly",
        repeat_mode=RepeatMode.MANUAL_COUNT,
        start_time=datetime(2026, 1, 1, 10, 0, tzinfo=UTC),
        repeat_interval_minutes=60,
        manual_count=1,
        end_time=None,
        default_instance_critical=False,
        template_type=PlanKind.GOAL,
        template_payload=GoalCreatePayload(name="template"),
    )
    assert validate_create_payload(PlanKind.REPETITION, payload) is None


def test_validate_create_payload_rejects_invalid_repetition_settings() -> None:
    payload = RepetitionCreatePayload(
        name="weekly",
        repeat_mode=RepeatMode.MANUAL_COUNT,
        start_time=datetime(2026, 1, 1, 10, 0, tzinfo=UTC),
        repeat_interval_minutes=60,
        manual_count=1,
        end_time=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
        default_instance_critical=False,
        template_type=PlanKind.GOAL,
        template_payload=GoalCreatePayload(name="template"),
    )
    error = validate_create_payload(PlanKind.REPETITION, payload)
    assert error is not None
    assert error.code == MessageCode.INVALID_REPETITION_SETTINGS

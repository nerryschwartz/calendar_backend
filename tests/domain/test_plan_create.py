"""Unit tests for plan create payload validation."""

from __future__ import annotations

from calendar_backend.domain.enums import PlanKind
from calendar_backend.domain.errors import MessageCode
from calendar_backend.domain.plan_create import (
    GoalCreatePayload,
    TaskCreatePayload,
    validate_create_payload,
)


def test_validate_create_payload_accepts_matching_goal_payload() -> None:
    assert validate_create_payload(PlanKind.GOAL, GoalCreatePayload(name="g")) is None


def test_validate_create_payload_rejects_kind_payload_mismatch() -> None:
    error = validate_create_payload(PlanKind.GOAL, TaskCreatePayload("t", 30, False, None))
    assert error is not None
    assert error.code == MessageCode.INVALID_CREATE_PAYLOAD

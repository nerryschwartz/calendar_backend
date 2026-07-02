"""Unit tests for task scheduling validation."""

from __future__ import annotations

from calendar_backend.domain.errors import MessageCode
from calendar_backend.domain.plan_create import TaskCreatePayload
from calendar_backend.domain.tasks import validate_task_create, validate_task_scheduling_fields


def test_validate_task_scheduling_fields_accepts_indivisible_without_chunk() -> None:
    assert validate_task_scheduling_fields(30, False, None) is None


def test_validate_task_scheduling_fields_rejects_indivisible_with_chunk() -> None:
    error = validate_task_scheduling_fields(30, False, 15)
    assert error is not None
    assert error.code == MessageCode.INVALID_TASK_SCHEDULING_FIELDS


def test_validate_task_scheduling_fields_accepts_divisible_with_valid_chunk() -> None:
    assert validate_task_scheduling_fields(30, True, 15) is None


def test_validate_task_scheduling_fields_rejects_divisible_without_chunk() -> None:
    error = validate_task_scheduling_fields(30, True, None)
    assert error is not None
    assert error.code == MessageCode.INVALID_TASK_SCHEDULING_FIELDS


def test_validate_task_scheduling_fields_rejects_non_positive_duration() -> None:
    error = validate_task_scheduling_fields(0, True, 15)
    assert error is not None
    assert error.code == MessageCode.INVALID_DURATION


def test_validate_task_scheduling_fields_rejects_chunk_exceeding_duration() -> None:
    error = validate_task_scheduling_fields(30, True, 31)
    assert error is not None
    assert error.code == MessageCode.MINIMUM_CHUNK_SIZE_IMPOSSIBLE


def test_validate_task_scheduling_fields_rejects_non_positive_chunk() -> None:
    error = validate_task_scheduling_fields(30, True, 0)
    assert error is not None
    assert error.code == MessageCode.MINIMUM_CHUNK_SIZE_IMPOSSIBLE


def test_validate_task_create_delegates_to_scheduling_fields() -> None:
    assert validate_task_create(TaskCreatePayload("t", 30, False, None)) is None
    error = validate_task_create(TaskCreatePayload("t", 30, True, None))
    assert error is not None
    assert error.code == MessageCode.INVALID_TASK_SCHEDULING_FIELDS

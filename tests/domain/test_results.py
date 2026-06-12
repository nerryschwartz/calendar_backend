from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest
from calendar_backend.domain.errors import MessageCode, ServiceMessage
from calendar_backend.domain.results import ServiceResult, fail, ok


def _message(code: MessageCode, text: str) -> ServiceMessage:
    return ServiceMessage(code=code, message=text)


def test_ok_success_result() -> None:
    result = ok("payload")
    assert result.success is True
    assert result.value == "payload"
    assert result.errors == ()
    assert result.warnings == ()
    assert result.metadata == {}


def test_ok_with_warnings_and_metadata() -> None:
    warning = _message(MessageCode.HEURISTIC_FEASIBLE, "heuristic used")
    metadata = {"runtime_ms": "12"}
    result = ok("payload", warnings=(warning,), metadata=metadata)
    assert result.success is True
    assert result.warnings == (warning,)
    assert result.metadata == metadata


def test_fail_failure_result() -> None:
    error = _message(MessageCode.INVALID_DURATION, "bad duration")
    result: ServiceResult[str] = fail(error)
    assert result.success is False
    assert result.value is None
    assert result.errors == (error,)
    assert result.metadata == {}


def test_fail_with_metadata() -> None:
    error = _message(MessageCode.INVALID_TIME_WINDOW, "bad window")
    result: ServiceResult[None] = fail(error, metadata={"field": "start_time"})
    assert result.success is False
    assert result.metadata == {"field": "start_time"}


def test_service_result_is_frozen() -> None:
    result = ok("payload")
    with pytest.raises(FrozenInstanceError):
        result.success = False  # type: ignore[misc]


def test_ok_metadata_is_not_aliased_to_caller_dict() -> None:
    metadata = {"key": "original"}
    result = ok("payload", metadata=metadata)
    metadata["key"] = "mutated"
    assert result.metadata == {"key": "original"}

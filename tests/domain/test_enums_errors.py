from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest
from calendar_backend.domain.enums import PlanKind, SolverStatus
from calendar_backend.domain.errors import MessageCode, ServiceMessage, WrongPlanTypeError


def test_plan_kind_string_values_are_stable() -> None:
    assert PlanKind.GOAL == "GOAL"
    assert PlanKind.TASK.value == "TASK"


@pytest.mark.parametrize(
    "code",
    [
        MessageCode.INVALID_DURATION,
        MessageCode.NON_MINUTE_ALIGNED_WINDOW,
    ],
)
def test_message_code_validation_examples_exist(code: MessageCode) -> None:
    assert code.value == code.name


def test_message_code_precondition_example_exists() -> None:
    assert MessageCode.INVALID_INCOMPLETE_TASKS_BLOCK_ASSIGNMENT.value == (
        "INVALID_INCOMPLETE_TASKS_BLOCK_ASSIGNMENT"
    )


def test_message_code_conflict_example_exists() -> None:
    assert MessageCode.NO_VALID_WINDOW_FOR_TASK.value == "NO_VALID_WINDOW_FOR_TASK"


def test_message_code_solver_warning_example_exists() -> None:
    assert MessageCode.SOLVER_LIMIT_REACHED.value == "SOLVER_LIMIT_REACHED"
    assert MessageCode.FEASIBLE_NOT_PROVEN_OPTIMAL.value == "FEASIBLE_NOT_PROVEN_OPTIMAL"
    assert MessageCode.HEURISTIC_FEASIBLE.value == "HEURISTIC_FEASIBLE"


def test_solver_status_is_mutually_exclusive_outcome() -> None:
    assert set(SolverStatus) == {
        SolverStatus.OPTIMAL,
        SolverStatus.FEASIBLE,
        SolverStatus.INFEASIBLE,
    }


def test_service_message_is_frozen() -> None:
    message = ServiceMessage(code=MessageCode.INVALID_DURATION, message="bad duration")
    with pytest.raises(FrozenInstanceError):
        message.message = "changed"  # type: ignore[misc]


def test_wrong_plan_type_error_is_type_error() -> None:
    assert issubclass(WrongPlanTypeError, TypeError)

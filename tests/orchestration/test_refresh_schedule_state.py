"""Integration tests for OrchestrationService refresh_schedule persistence effects."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from calendar_backend.domain.enums import CalendarEntryType, LastFailureReason
from calendar_backend.domain.errors import MessageCode, ServiceMessage
from calendar_backend.domain.resolution import ResolveTasksResult
from calendar_backend.domain.results import fail, ok
from calendar_backend.models.calendar import CalendarEntry
from calendar_backend.services.free_time_assignment import FreeTimeAssignmentService
from calendar_backend.services.repetition import RepetitionService
from calendar_backend.services.task_resolution import TaskResolutionService
from calendar_backend.services.time_constraint import TimeConstraintService
from sqlalchemy import select
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parent))
import orch_helpers as oh


@pytest.mark.integration
def test_refresh_schedule_success_clears_last_refresh_failed(
    service_db_session: Session,
) -> None:
    oh.bootstrap_assignable_task(service_db_session)
    result = oh.orchestration_service(service_db_session).refresh_schedule(oh.RUN_AT)
    assert result.success

    state = oh.active_state(service_db_session)
    assert state is not None
    assert state.last_refresh_failed is False
    assert state.last_failure_at is None
    assert state.last_failure_reason is None
    assert state.active_calendar_run_id is not None


@pytest.mark.integration
def test_refresh_schedule_solver_failure_preserves_active_calendar_run_id(
    service_db_session: Session,
) -> None:
    master_id = oh.bootstrap_master_with_horizon(service_db_session)
    oh.create_task(service_db_session, master_id, name="solo")
    oh.create_enabled_activity(service_db_session)
    success = oh.orchestration_service(service_db_session).refresh_schedule(oh.RUN_AT)
    assert success.success
    state = oh.active_state(service_db_session)
    assert state is not None
    prior_active_run_id = state.active_calendar_run_id

    TimeConstraintService(service_db_session, oh.clock()).add_user_group(
        master_id,
        (oh.window(oh.RUN_AT, oh.RUN_AT + timedelta(minutes=30)),),
    )
    oh.create_task(service_db_session, master_id, name="extra")
    failure = oh.orchestration_service(service_db_session).refresh_schedule(oh.RUN_AT)

    assert not failure.success
    state = oh.active_state(service_db_session)
    assert state is not None
    assert state.active_calendar_run_id == prior_active_run_id
    assert state.last_refresh_failed is True
    assert state.last_failure_reason == LastFailureReason.ASSIGNMENT_FAILED


@pytest.mark.integration
def test_refresh_schedule_precondition_failure_sets_reason_without_calendar_mutation(
    service_db_session: Session,
) -> None:
    master_id = oh.bootstrap_master_with_horizon(service_db_session)
    oh.create_task(service_db_session, master_id)
    entries_before = oh.calendar_entry_count(service_db_session)
    runs_before = oh.calendar_run_count(service_db_session)

    resolve_result = ok(
        ResolveTasksResult(
            run_started_at=oh.RUN_AT,
            valid_incomplete=(),
            valid_completed=(),
            invalid_incomplete=oh.invalid_incomplete_task(),
            invalid_completed=(),
            precedence_constraints=(),
            warnings=(),
        )
    )
    with patch.object(TaskResolutionService, "resolve_tasks", return_value=resolve_result):
        result = oh.orchestration_service(service_db_session).refresh_schedule(oh.RUN_AT)

    assert not result.success
    assert result.errors[0].code == MessageCode.INVALID_INCOMPLETE_TASKS_BLOCK_ASSIGNMENT
    assert result.value is not None
    assert result.value.resolved is not None
    assert len(result.value.resolved.invalid_incomplete) == 1
    assert oh.calendar_entry_count(service_db_session) == entries_before
    assert oh.calendar_run_count(service_db_session) == runs_before
    state = oh.active_state(service_db_session)
    assert state is not None
    assert state.last_refresh_failed is True
    assert state.last_failure_reason == LastFailureReason.ASSIGNMENT_PRECONDITION_FAILED
    assert state.active_calendar_run_id is None


@pytest.mark.integration
def test_refresh_schedule_partial_free_time_failure_clears_future_free_time(
    service_db_session: Session,
) -> None:
    _, task_id = oh.bootstrap_assignable_task(service_db_session)
    success = oh.orchestration_service(service_db_session).refresh_schedule(oh.RUN_AT)
    assert success.success and success.value is not None
    state = oh.active_state(service_db_session)
    assert state is not None
    prior_active_run_id = state.active_calendar_run_id
    assert oh.future_task_entry_count(service_db_session, task_id) == 1
    activity_id = service_db_session.scalar(
        select(CalendarEntry.source_free_time_activity_id).where(
            CalendarEntry.entry_type == CalendarEntryType.FREE_TIME,
            CalendarEntry.start_time >= oh.RUN_AT,
        )
    )
    assert activity_id is not None
    stale_future_id = oh.add_calendar_entry(
        service_db_session,
        entry_type=CalendarEntryType.FREE_TIME,
        start_time=oh.utc(2026, 6, 7, 11, 0),
        end_time=oh.utc(2026, 6, 7, 12, 0),
        source_free_time_activity_id=activity_id,
        calendar_run_id=prior_active_run_id,
    )
    past_free_time_id = oh.add_calendar_entry(
        service_db_session,
        entry_type=CalendarEntryType.FREE_TIME,
        start_time=oh.utc(2026, 6, 7, 9, 0),
        end_time=oh.utc(2026, 6, 7, 9, 30),
        calendar_run_id=prior_active_run_id,
    )

    with patch.object(
        FreeTimeAssignmentService,
        "assign_free_time",
        return_value=fail(
            ServiceMessage(
                code=MessageCode.SOLVER_FAILED_TO_FIND_FEASIBLE_ASSIGNMENT,
                message="forced free-time failure",
            )
        ),
    ):
        result = oh.orchestration_service(service_db_session).refresh_schedule(oh.RUN_AT)

    assert not result.success
    assert result.value is not None
    assert result.value.assignment is not None
    state = oh.active_state(service_db_session)
    assert state is not None
    assert state.active_calendar_run_id == result.value.assignment.calendar_run_id
    assert state.last_refresh_failed is True
    assert state.last_failure_reason == LastFailureReason.FREE_TIME_ASSIGNMENT_FAILED
    assert service_db_session.get(CalendarEntry, stale_future_id) is None
    assert service_db_session.get(CalendarEntry, past_free_time_id) is not None
    assert oh.future_task_entry_count(service_db_session, task_id) == 1


@pytest.mark.integration
def test_refresh_schedule_resolution_failure_writes_no_active_state(
    service_db_session: Session,
) -> None:
    entries_before = oh.calendar_entry_count(service_db_session)
    naive_run_at = datetime(2026, 6, 7, 10, 0)

    result = oh.orchestration_service(service_db_session).refresh_schedule(naive_run_at)

    assert not result.success
    assert result.errors[0].code == MessageCode.INVALID_TIME_WINDOW
    assert oh.active_state(service_db_session) is None
    assert oh.calendar_entry_count(service_db_session) == entries_before


@pytest.mark.integration
def test_refresh_schedule_repetition_refresh_failure_writes_no_active_state(
    service_db_session: Session,
) -> None:
    oh.bootstrap_assignable_task(service_db_session)
    entries_before, runs_before = oh.entries_and_runs_before(service_db_session)

    with patch.object(
        RepetitionService,
        "refresh_all_repetitions",
        return_value=fail(
            ServiceMessage(
                code=MessageCode.REPETITION_NOT_GENERATED,
                message="forced repetition refresh failure",
            )
        ),
    ):
        result = oh.orchestration_service(service_db_session).refresh_schedule(oh.RUN_AT)

    assert not result.success
    assert result.value is None
    assert oh.active_state(service_db_session) is None
    assert oh.calendar_entry_count(service_db_session) == entries_before
    assert oh.calendar_run_count(service_db_session) == runs_before

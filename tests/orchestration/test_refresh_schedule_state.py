"""Integration tests for OrchestrationService refresh_schedule persistence effects."""

from __future__ import annotations

import uuid
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from calendar_backend.db.session import transaction
from calendar_backend.domain.enums import (
    CalendarEntryType,
    LastFailureReason,
    PlanKind,
)
from calendar_backend.domain.errors import MessageCode, ServiceMessage
from calendar_backend.domain.ids import PlanID
from calendar_backend.domain.plan_create import TaskCreatePayload
from calendar_backend.domain.resolution import ResolvedTask, ResolveTasksResult
from calendar_backend.domain.results import fail, ok
from calendar_backend.domain.time import TimeWindow
from calendar_backend.models.calendar import CalendarEntry
from calendar_backend.models.plans import Plan
from calendar_backend.models.runs import ActiveCalendarState, CalendarRun
from calendar_backend.orchestration.refresh_schedule import OrchestrationService
from calendar_backend.services import task_resolution as task_resolution_module
from calendar_backend.services.app_settings import AppSettingsService
from calendar_backend.services.free_time_activity import FreeTimeActivityService
from calendar_backend.services.free_time_assignment import FreeTimeAssignmentService
from calendar_backend.services.goal import GoalService
from calendar_backend.services.master_horizon import MasterHorizonService
from calendar_backend.services.master_plan import MasterPlanService
from calendar_backend.services.task_resolution import (
    TaskResolutionService,
    _load_plan_graph,  # pyright: ignore[reportPrivateUsage]
)
from calendar_backend.services.time_constraint import TimeConstraintService
from sqlalchemy import func, select
from sqlalchemy.orm import Session

RUN_AT = datetime(2026, 6, 7, 10, 0, tzinfo=UTC)


@dataclass(frozen=True)
class FakeClock:
    fixed: datetime

    def now_utc(self) -> datetime:
        return self.fixed


def _utc(y: int, m: int, d: int, h: int, mi: int = 0) -> datetime:
    return datetime(y, m, d, h, mi, tzinfo=UTC)


def _window(start: datetime, end: datetime) -> TimeWindow:
    return TimeWindow(start_time=start, end_time=end)


def _clock() -> FakeClock:
    return FakeClock(RUN_AT)


def _orchestration_service(session: Session) -> OrchestrationService:
    return OrchestrationService(session, _clock())


def _goal_service(session: Session) -> GoalService:
    return GoalService(session, _clock())


def _bootstrap_master_with_horizon(session: Session) -> PlanID:
    clock = _clock()
    master = MasterPlanService(session, clock).ensure_master_exists()
    assert master.success and master.value is not None
    AppSettingsService(session, clock).get_settings()
    assert MasterHorizonService(session, clock).refresh_master_horizon(RUN_AT).success
    return master.value.plan_id


def _create_task(session: Session, parent_id: PlanID, *, name: str = "task") -> PlanID:
    result = _goal_service(session).create_child(
        parent_id,
        PlanKind.TASK,
        TaskCreatePayload(name, 30, False, None),
        is_critical=False,
    )
    assert result.success and result.value is not None
    return result.value.plan_id


def _create_enabled_activity(session: Session) -> uuid.UUID:
    result = FreeTimeActivityService(session, _clock()).create_activity(
        "reading",
        Decimal("1"),
        minimum_block_size_minutes=0,
    )
    assert result.success and result.value is not None
    return result.value.free_time_activity_id


def _active_state(session: Session) -> ActiveCalendarState | None:
    return session.get(ActiveCalendarState, 1)


def _calendar_entry_count(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(CalendarEntry)) or 0


def _calendar_run_count(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(CalendarRun)) or 0


def _add_calendar_entry(
    session: Session,
    *,
    entry_type: CalendarEntryType,
    start_time: datetime,
    end_time: datetime,
    source_plan_id: PlanID | None = None,
    source_free_time_activity_id: uuid.UUID | None = None,
    calendar_run_id: uuid.UUID | None = None,
) -> uuid.UUID:
    entry_id = uuid.uuid4()
    with transaction(session) as txn:
        txn.add(
            CalendarEntry(
                calendar_entry_id=entry_id,
                entry_type=entry_type,
                start_time=start_time,
                end_time=end_time,
                source_plan_id=source_plan_id,
                source_free_time_activity_id=source_free_time_activity_id,
                calendar_run_id=calendar_run_id,
                display_label="seed",
                created_at=RUN_AT,
                updated_at=RUN_AT,
            )
        )
        txn.flush()
    return entry_id


def _normalize_plan_window_timezones(plans: tuple[Plan, ...]) -> tuple[Plan, ...]:
    for plan in plans:
        for group in plan.constraint_groups:
            for window_row in group.windows:
                if window_row.start_time.tzinfo is None:
                    window_row.start_time = window_row.start_time.replace(tzinfo=UTC)
                if window_row.end_time.tzinfo is None:
                    window_row.end_time = window_row.end_time.replace(tzinfo=UTC)
    return plans


def _normalized_load_plan_graph(session: Session) -> tuple[Plan, ...]:
    return _normalize_plan_window_timezones(_load_plan_graph(session))


@contextmanager
def _normalized_resolution_graph() -> Generator[None]:
    """SQLite stores naive UTC; normalize loaded windows for full-pipeline integration."""
    with patch.object(
        task_resolution_module,
        "_load_plan_graph",
        side_effect=_normalized_load_plan_graph,
    ):
        yield


def _invalid_incomplete_task() -> tuple[ResolvedTask, ...]:
    plan_id = uuid.uuid4()
    return (
        ResolvedTask(
            plan_id=PlanID(plan_id),
            name="bad",
            duration_minutes=0,
            divisible=False,
            minimum_chunk_size_minutes=None,
            user_completed=False,
            completed_at=None,
            effective_time_windows=(),
            constraint_sources=(),
            priority_path=(0,),
            criticality_path=(),
            parent_path=(PlanID(plan_id),),
            chain_path=(),
            validation_errors=(
                ServiceMessage(
                    code=MessageCode.INVALID_DURATION,
                    message="invalid duration",
                    details={},
                ),
            ),
        ),
    )


def _future_task_entry_count(session: Session, task_id: PlanID) -> int:
    return (
        session.scalar(
            select(func.count())
            .select_from(CalendarEntry)
            .where(
                CalendarEntry.entry_type == CalendarEntryType.TASK,
                CalendarEntry.source_plan_id == task_id,
                CalendarEntry.start_time >= RUN_AT,
            )
        )
        or 0
    )


def _bootstrap_refresh_success(session: Session) -> PlanID:
    master_id = _bootstrap_master_with_horizon(session)
    task_id = _create_task(session, master_id)
    TimeConstraintService(session, _clock()).add_user_group(
        master_id,
        (_window(RUN_AT, RUN_AT + timedelta(hours=2)),),
    )
    _create_enabled_activity(session)
    with _normalized_resolution_graph():
        result = _orchestration_service(session).refresh_schedule(RUN_AT)
    assert result.success and result.value is not None
    return task_id


@pytest.mark.integration
def test_refresh_schedule_success_clears_last_refresh_failed(
    service_db_session: Session,
) -> None:
    _bootstrap_refresh_success(service_db_session)

    state = _active_state(service_db_session)
    assert state is not None
    assert state.last_refresh_failed is False
    assert state.last_failure_at is None
    assert state.last_failure_reason is None
    assert state.active_calendar_run_id is not None


@pytest.mark.integration
def test_refresh_schedule_solver_failure_preserves_active_calendar_run_id(
    service_db_session: Session,
) -> None:
    master_id = _bootstrap_master_with_horizon(service_db_session)
    _create_task(service_db_session, master_id, name="solo")
    _create_enabled_activity(service_db_session)
    with _normalized_resolution_graph():
        success = _orchestration_service(service_db_session).refresh_schedule(RUN_AT)
    assert success.success
    state = _active_state(service_db_session)
    assert state is not None
    prior_active_run_id = state.active_calendar_run_id

    clock = _clock()
    TimeConstraintService(service_db_session, clock).add_user_group(
        master_id,
        (_window(RUN_AT, RUN_AT + timedelta(minutes=30)),),
    )
    _create_task(service_db_session, master_id, name="extra")
    with _normalized_resolution_graph():
        failure = _orchestration_service(service_db_session).refresh_schedule(RUN_AT)

    assert not failure.success
    state = _active_state(service_db_session)
    assert state is not None
    assert state.active_calendar_run_id == prior_active_run_id
    assert state.last_refresh_failed is True
    assert state.last_failure_reason == LastFailureReason.ASSIGNMENT_FAILED


@pytest.mark.integration
def test_refresh_schedule_precondition_failure_sets_reason_without_calendar_mutation(
    service_db_session: Session,
) -> None:
    master_id = _bootstrap_master_with_horizon(service_db_session)
    _create_task(service_db_session, master_id)
    entries_before = _calendar_entry_count(service_db_session)
    runs_before = _calendar_run_count(service_db_session)

    resolve_result = ok(
        ResolveTasksResult(
            run_started_at=RUN_AT,
            valid_incomplete=(),
            valid_completed=(),
            invalid_incomplete=_invalid_incomplete_task(),
            invalid_completed=(),
            precedence_constraints=(),
            warnings=(),
        )
    )
    with patch.object(TaskResolutionService, "resolve_tasks", return_value=resolve_result):
        result = _orchestration_service(service_db_session).refresh_schedule(RUN_AT)

    assert not result.success
    assert result.errors[0].code == MessageCode.INVALID_INCOMPLETE_TASKS_BLOCK_ASSIGNMENT
    assert _calendar_entry_count(service_db_session) == entries_before
    assert _calendar_run_count(service_db_session) == runs_before
    state = _active_state(service_db_session)
    assert state is not None
    assert state.last_refresh_failed is True
    assert state.last_failure_reason == LastFailureReason.ASSIGNMENT_PRECONDITION_FAILED
    assert state.active_calendar_run_id is None


@pytest.mark.integration
def test_refresh_schedule_partial_free_time_failure_clears_future_free_time(
    service_db_session: Session,
) -> None:
    task_id = _bootstrap_refresh_success(service_db_session)
    state = _active_state(service_db_session)
    assert state is not None
    prior_active_run_id = state.active_calendar_run_id
    assert _future_task_entry_count(service_db_session, task_id) >= 1
    activity_id = service_db_session.scalar(
        select(CalendarEntry.source_free_time_activity_id).where(
            CalendarEntry.entry_type == CalendarEntryType.FREE_TIME,
            CalendarEntry.start_time >= RUN_AT,
        )
    )
    assert activity_id is not None
    stale_future_id = _add_calendar_entry(
        service_db_session,
        entry_type=CalendarEntryType.FREE_TIME,
        start_time=_utc(2026, 6, 7, 11, 0),
        end_time=_utc(2026, 6, 7, 12, 0),
        source_free_time_activity_id=activity_id,
        calendar_run_id=prior_active_run_id,
    )
    past_free_time_id = _add_calendar_entry(
        service_db_session,
        entry_type=CalendarEntryType.FREE_TIME,
        start_time=_utc(2026, 6, 7, 9, 0),
        end_time=_utc(2026, 6, 7, 9, 30),
        calendar_run_id=prior_active_run_id,
    )

    with (
        _normalized_resolution_graph(),
        patch.object(
            FreeTimeAssignmentService,
            "assign_free_time",
            return_value=fail(
                ServiceMessage(
                    code=MessageCode.SOLVER_FAILED_TO_FIND_FEASIBLE_ASSIGNMENT,
                    message="forced free-time failure",
                )
            ),
        ),
    ):
        result = _orchestration_service(service_db_session).refresh_schedule(RUN_AT)

    assert not result.success
    assert result.value is not None
    assert result.value.assignment is not None
    state = _active_state(service_db_session)
    assert state is not None
    assert state.active_calendar_run_id == result.value.assignment.calendar_run_id
    assert state.last_refresh_failed is True
    assert state.last_failure_reason == LastFailureReason.FREE_TIME_ASSIGNMENT_FAILED
    assert service_db_session.get(CalendarEntry, stale_future_id) is None
    assert service_db_session.get(CalendarEntry, past_free_time_id) is not None
    assert _future_task_entry_count(service_db_session, task_id) >= 1


@pytest.mark.integration
def test_refresh_schedule_resolution_failure_writes_no_active_state(
    service_db_session: Session,
) -> None:
    entries_before = _calendar_entry_count(service_db_session)
    naive_run_at = datetime(2026, 6, 7, 10, 0)

    result = _orchestration_service(service_db_session).refresh_schedule(naive_run_at)

    assert not result.success
    assert result.errors[0].code == MessageCode.INVALID_TIME_WINDOW
    assert _active_state(service_db_session) is None
    assert _calendar_entry_count(service_db_session) == entries_before

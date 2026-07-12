"""Integration tests for TaskAssignmentService."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from calendar_backend.db.session import transaction
from calendar_backend.domain.assignment import previous_placements_from_future_task_entries
from calendar_backend.domain.enums import (
    CalendarEntryType,
    CalendarRunStatus,
    LastFailureReason,
    PlanKind,
    SolverStatus,
)
from calendar_backend.domain.errors import MessageCode, ServiceMessage
from calendar_backend.domain.ids import PlanID
from calendar_backend.domain.plan_create import TaskCreatePayload
from calendar_backend.domain.resolution import ResolvedTask, ResolveTasksResult
from calendar_backend.domain.time import TimeWindow
from calendar_backend.models.calendar import CalendarEntry
from calendar_backend.models.free_time import FreeTimeActivity
from calendar_backend.models.plans import Plan
from calendar_backend.models.runs import ActiveCalendarState, CalendarRun
from calendar_backend.scheduling.exact_cp_sat import ExactAssignmentSolver
from calendar_backend.scheduling.input import AssignmentInput
from calendar_backend.scheduling.types import AssignmentSolverResult
from calendar_backend.services.app_settings import AppSettingsService
from calendar_backend.services.goal import GoalService
from calendar_backend.services.master_horizon import MasterHorizonService
from calendar_backend.services.master_plan import MasterPlanService
from calendar_backend.services.task import TaskService
from calendar_backend.services.task_assignment import TaskAssignmentService
from calendar_backend.services.task_resolution import (
    _load_plan_graph,  # pyright: ignore[reportPrivateUsage]
    _resolve_from_current_tree,  # pyright: ignore[reportPrivateUsage]
)
from calendar_backend.services.time_constraint import TimeConstraintService
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .conftest import FakeClock

RUN_AT = datetime(2026, 6, 7, 10, 0, tzinfo=UTC)


def _utc(y: int, m: int, d: int, h: int, mi: int) -> datetime:
    return datetime(y, m, d, h, mi, tzinfo=UTC)


def _window(start: datetime, end: datetime) -> TimeWindow:
    return TimeWindow(start_time=start, end_time=end)


def _clock() -> FakeClock:
    return FakeClock(RUN_AT)


def _assignment_service(session: Session) -> TaskAssignmentService:
    return TaskAssignmentService(session, _clock())


def _goal_service(session: Session) -> GoalService:
    return GoalService(session, _clock())


def _bootstrap_master_with_horizon(session: Session) -> PlanID:
    clock = _clock()
    master = MasterPlanService(session, clock).ensure_master_exists()
    assert master.success and master.value is not None
    AppSettingsService(session, clock).get_settings()
    horizon = MasterHorizonService(session, clock).refresh_master_horizon(RUN_AT)
    assert horizon.success
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


def _normalize_plan_window_timezones(plans: tuple[Plan, ...]) -> tuple[Plan, ...]:
    for plan in plans:
        for group in plan.constraint_groups:
            for window_row in group.windows:
                if window_row.start_time.tzinfo is None:
                    window_row.start_time = window_row.start_time.replace(tzinfo=UTC)
                if window_row.end_time.tzinfo is None:
                    window_row.end_time = window_row.end_time.replace(tzinfo=UTC)
    return plans


def _resolve_seam(session: Session) -> ResolveTasksResult:
    plans = _normalize_plan_window_timezones(_load_plan_graph(session))
    return _resolve_from_current_tree(RUN_AT, plans=plans)


def _calendar_entry_count(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(CalendarEntry)) or 0


def _calendar_run_count(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(CalendarRun)) or 0


def _active_state(session: Session) -> ActiveCalendarState | None:
    return session.get(ActiveCalendarState, 1)


def _empty_resolve_result(
    *,
    run_started_at: datetime = RUN_AT,
    invalid_incomplete: tuple[ResolvedTask, ...] = (),
) -> ResolveTasksResult:
    return ResolveTasksResult(
        run_started_at=run_started_at,
        valid_incomplete=(),
        valid_completed=(),
        invalid_incomplete=invalid_incomplete,
        invalid_completed=(),
        precedence_constraints=(),
        warnings=(),
    )


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


def _add_calendar_entry(
    session: Session,
    *,
    entry_type: CalendarEntryType,
    start_time: datetime,
    end_time: datetime,
    source_plan_id: PlanID | None = None,
    source_free_time_activity_id: uuid.UUID | None = None,
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
                calendar_run_id=None,
                display_label="seed",
                created_at=RUN_AT,
                updated_at=RUN_AT,
            )
        )
        txn.flush()
    return entry_id


def _seed_active_calendar_state_with_past_task(
    session: Session,
    *,
    past_start: datetime,
    past_end: datetime,
    source_plan_id: PlanID,
) -> None:
    run_id = uuid.uuid4()
    with transaction(session) as txn:
        txn.add(
            CalendarRun(
                calendar_run_id=run_id,
                run_started_at=RUN_AT,
                run_finished_at=RUN_AT,
                status=CalendarRunStatus.SUCCESS,
                solver_status=SolverStatus.FEASIBLE,
                conflict_count=0,
                warning_count=0,
                runtime_ms=1,
                created_at=RUN_AT,
            )
        )
        txn.add(
            ActiveCalendarState(
                singleton_id=1,
                active_calendar_run_id=run_id,
                last_refresh_failed=False,
                last_failure_at=None,
                last_failure_reason=None,
                updated_at=RUN_AT,
            )
        )
        txn.add(
            CalendarEntry(
                calendar_entry_id=uuid.uuid4(),
                entry_type=CalendarEntryType.TASK,
                start_time=past_start,
                end_time=past_end,
                source_plan_id=source_plan_id,
                source_free_time_activity_id=None,
                calendar_run_id=run_id,
                display_label="past block",
                created_at=RUN_AT,
                updated_at=RUN_AT,
            )
        )
        txn.flush()


@pytest.mark.integration
def test_assign_tasks_invalid_incomplete_blocks_without_db_mutation(
    service_db_session: Session,
) -> None:
    _bootstrap_master_with_horizon(service_db_session)
    entries_before = _calendar_entry_count(service_db_session)
    runs_before = _calendar_run_count(service_db_session)

    result = _assignment_service(service_db_session).assign_tasks(
        _empty_resolve_result(invalid_incomplete=_invalid_incomplete_task()),
        RUN_AT,
    )

    assert not result.success
    assert result.errors[0].code == MessageCode.INVALID_INCOMPLETE_TASKS_BLOCK_ASSIGNMENT
    assert _calendar_entry_count(service_db_session) == entries_before
    assert _calendar_run_count(service_db_session) == runs_before
    assert _active_state(service_db_session) is None


@pytest.mark.integration
def test_assign_tasks_run_started_at_mismatch_blocks_without_db_mutation(
    service_db_session: Session,
) -> None:
    _bootstrap_master_with_horizon(service_db_session)
    entries_before = _calendar_entry_count(service_db_session)

    result = _assignment_service(service_db_session).assign_tasks(
        _empty_resolve_result(run_started_at=_utc(2026, 6, 7, 11, 0)),
        RUN_AT,
    )

    assert not result.success
    assert result.errors[0].code == MessageCode.RUN_STARTED_AT_MISMATCH
    assert _calendar_entry_count(service_db_session) == entries_before


@pytest.mark.integration
def test_assign_tasks_heuristic_disabled_uses_exact_solver_only(
    service_db_session: Session,
) -> None:
    master_id = _bootstrap_master_with_horizon(service_db_session)
    _create_task(service_db_session, master_id)
    AppSettingsService(service_db_session, _clock()).update_settings(
        heuristic_enabled=False,
        exact_solver_model_size_limit=2_000_000,
    )

    result = _assignment_service(service_db_session).assign_tasks(
        _resolve_seam(service_db_session),
        RUN_AT,
    )

    assert result.success and result.value is not None
    assert result.value.optimization_status in (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE)
    assert len(result.value.calendar_entries) == 1


@pytest.mark.integration
def test_assign_tasks_falls_back_to_heuristic_when_exact_not_usable(
    service_db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bootstrap_master_with_horizon(service_db_session)

    def exact_not_usable(
        self: ExactAssignmentSolver,
        assignment_input: AssignmentInput,
    ) -> AssignmentSolverResult:
        del self, assignment_input
        return AssignmentSolverResult(
            status=SolverStatus.INFEASIBLE,
            assignments=(),
            warnings=(),
            failure=None,
        )

    monkeypatch.setattr(ExactAssignmentSolver, "solve", exact_not_usable)

    result = _assignment_service(service_db_session).assign_tasks(
        _resolve_seam(service_db_session),
        RUN_AT,
    )

    assert result.success and result.value is not None
    assert result.value.optimization_status == SolverStatus.FEASIBLE
    assert any(warning.code == MessageCode.HEURISTIC_FEASIBLE for warning in result.value.warnings)


@pytest.mark.integration
def test_assign_tasks_loads_stability_hints_from_future_task_entries(
    service_db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    master_id = _bootstrap_master_with_horizon(service_db_session)
    task_id = _create_task(service_db_session, master_id)
    _seed_active_calendar_state_with_past_task(
        service_db_session,
        past_start=_utc(2026, 6, 7, 8, 0),
        past_end=_utc(2026, 6, 7, 8, 30),
        source_plan_id=task_id,
    )
    hint_window = _window(_utc(2026, 6, 7, 11, 0), _utc(2026, 6, 7, 11, 30))
    _add_calendar_entry(
        service_db_session,
        entry_type=CalendarEntryType.TASK,
        start_time=hint_window.start_time,
        end_time=hint_window.end_time,
        source_plan_id=task_id,
    )
    captured_inputs: list[AssignmentInput] = []
    original_solve = ExactAssignmentSolver.solve

    def capture_exact_solve(
        self: ExactAssignmentSolver,
        assignment_input: AssignmentInput,
    ) -> AssignmentSolverResult:
        captured_inputs.append(assignment_input)
        return original_solve(self, assignment_input)

    monkeypatch.setattr(ExactAssignmentSolver, "solve", capture_exact_solve)

    result = _assignment_service(service_db_session).assign_tasks(
        _resolve_seam(service_db_session),
        RUN_AT,
    )

    assert result.success
    assert len(captured_inputs) == 1
    assert captured_inputs[0].previous_placements_by_task_id == ((task_id, (hint_window,)),)


def test_previous_placements_from_future_task_entries_filters_schedulable_tasks_only() -> None:
    schedulable_id = PlanID(uuid.uuid4())
    other_id = PlanID(uuid.uuid4())
    hint_window = _window(_utc(2026, 6, 7, 11, 0), _utc(2026, 6, 7, 11, 30))
    past_window = _window(_utc(2026, 6, 7, 8, 0), _utc(2026, 6, 7, 8, 30))
    entries = (
        CalendarEntry(
            calendar_entry_id=uuid.uuid4(),
            entry_type=CalendarEntryType.TASK,
            start_time=hint_window.start_time,
            end_time=hint_window.end_time,
            source_plan_id=schedulable_id,
            source_free_time_activity_id=None,
            calendar_run_id=None,
            display_label="future",
            created_at=RUN_AT,
            updated_at=RUN_AT,
        ),
        CalendarEntry(
            calendar_entry_id=uuid.uuid4(),
            entry_type=CalendarEntryType.TASK,
            start_time=past_window.start_time,
            end_time=past_window.end_time,
            source_plan_id=schedulable_id,
            source_free_time_activity_id=None,
            calendar_run_id=None,
            display_label="past",
            created_at=RUN_AT,
            updated_at=RUN_AT,
        ),
        CalendarEntry(
            calendar_entry_id=uuid.uuid4(),
            entry_type=CalendarEntryType.TASK,
            start_time=hint_window.start_time,
            end_time=hint_window.end_time,
            source_plan_id=other_id,
            source_free_time_activity_id=None,
            calendar_run_id=None,
            display_label="other",
            created_at=RUN_AT,
            updated_at=RUN_AT,
        ),
    )

    placements = previous_placements_from_future_task_entries(
        entries,
        RUN_AT,
        frozenset({schedulable_id}),
    )

    assert placements == ((schedulable_id, (hint_window,)),)


@pytest.mark.integration
def test_assign_tasks_success_replaces_future_task_entries_only(
    service_db_session: Session,
) -> None:
    master_id = _bootstrap_master_with_horizon(service_db_session)
    task_id = _create_task(service_db_session, master_id)
    stale_future_id = _add_calendar_entry(
        service_db_session,
        entry_type=CalendarEntryType.TASK,
        start_time=_utc(2026, 6, 7, 11, 0),
        end_time=_utc(2026, 6, 7, 11, 30),
        source_plan_id=task_id,
    )

    result = _assignment_service(service_db_session).assign_tasks(
        _resolve_seam(service_db_session),
        RUN_AT,
    )

    assert result.success and result.value is not None
    assert service_db_session.get(CalendarEntry, stale_future_id) is None
    assert len(result.value.calendar_entries) == 1
    assert result.value.calendar_entries[0].start_time >= RUN_AT


@pytest.mark.integration
def test_assign_tasks_success_preserves_past_task_entries(
    service_db_session: Session,
) -> None:
    master_id = _bootstrap_master_with_horizon(service_db_session)
    task_id = _create_task(service_db_session, master_id)
    past_entry_id = _add_calendar_entry(
        service_db_session,
        entry_type=CalendarEntryType.TASK,
        start_time=_utc(2026, 6, 7, 9, 0),
        end_time=_utc(2026, 6, 7, 9, 30),
        source_plan_id=task_id,
    )

    result = _assignment_service(service_db_session).assign_tasks(
        _resolve_seam(service_db_session),
        RUN_AT,
    )

    assert result.success
    assert service_db_session.get(CalendarEntry, past_entry_id) is not None


@pytest.mark.integration
def test_assign_tasks_success_leaves_free_time_entries_untouched(
    service_db_session: Session,
) -> None:
    master_id = _bootstrap_master_with_horizon(service_db_session)
    _create_task(service_db_session, master_id)
    activity_id = uuid.uuid4()
    with transaction(service_db_session) as txn:
        txn.add(
            FreeTimeActivity(
                free_time_activity_id=activity_id,
                name="reading",
                enabled=True,
                real_fraction=Decimal("1"),
                minimum_block_size_minutes=0,
                created_at=RUN_AT,
                updated_at=RUN_AT,
            )
        )
        txn.flush()
    free_entry_id = _add_calendar_entry(
        service_db_session,
        entry_type=CalendarEntryType.FREE_TIME,
        start_time=_utc(2026, 6, 7, 11, 0),
        end_time=_utc(2026, 6, 7, 12, 0),
        source_free_time_activity_id=activity_id,
    )

    result = _assignment_service(service_db_session).assign_tasks(
        _resolve_seam(service_db_session),
        RUN_AT,
    )

    assert result.success
    assert service_db_session.get(CalendarEntry, free_entry_id) is not None


@pytest.mark.integration
def test_assign_tasks_empty_valid_incomplete_clears_future_tasks(
    service_db_session: Session,
) -> None:
    master_id = _bootstrap_master_with_horizon(service_db_session)
    stale_future_id = _add_calendar_entry(
        service_db_session,
        entry_type=CalendarEntryType.TASK,
        start_time=_utc(2026, 6, 7, 11, 0),
        end_time=_utc(2026, 6, 7, 11, 30),
        source_plan_id=master_id,
    )

    result = _assignment_service(service_db_session).assign_tasks(
        _empty_resolve_result(),
        RUN_AT,
    )

    assert result.success and result.value is not None
    assert result.value.calendar_entries == ()
    assert service_db_session.get(CalendarEntry, stale_future_id) is None
    assert result.value.calendar_run_id is not None


@pytest.mark.integration
def test_assign_tasks_divisible_task_produces_multiple_calendar_entries(
    service_db_session: Session,
) -> None:
    master_id = _bootstrap_master_with_horizon(service_db_session)
    task_id = _create_task(service_db_session, master_id, name="divisible")
    clock = _clock()
    TimeConstraintService(service_db_session, clock).add_user_group(
        master_id,
        (
            _window(_utc(2026, 6, 7, 10, 0), _utc(2026, 6, 7, 11, 0)),
            _window(_utc(2026, 6, 7, 12, 0), _utc(2026, 6, 7, 13, 0)),
        ),
    )
    assert (
        TaskService(service_db_session, clock)
        .update_scheduling_fields(task_id, 90, True, 30)
        .success
    )

    result = _assignment_service(service_db_session).assign_tasks(
        _resolve_seam(service_db_session),
        RUN_AT,
    )

    assert result.success and result.value is not None
    entries_for_task = [
        entry for entry in result.value.calendar_entries if entry.source_plan_id == task_id
    ]
    assert len(entries_for_task) >= 2


@pytest.mark.integration
def test_assign_tasks_success_sets_active_calendar_run_id(
    service_db_session: Session,
) -> None:
    master_id = _bootstrap_master_with_horizon(service_db_session)
    _create_task(service_db_session, master_id)

    result = _assignment_service(service_db_session).assign_tasks(
        _resolve_seam(service_db_session),
        RUN_AT,
    )

    assert result.success and result.value is not None
    state = _active_state(service_db_session)
    assert state is not None
    assert state.active_calendar_run_id == result.value.calendar_run_id
    assert state.last_refresh_failed is False


@pytest.mark.integration
def test_assign_tasks_failure_leaves_calendar_unchanged(
    service_db_session: Session,
) -> None:
    master_id = _bootstrap_master_with_horizon(service_db_session)
    clock = _clock()
    TimeConstraintService(service_db_session, clock).add_user_group(
        master_id,
        (_window(RUN_AT, RUN_AT + timedelta(minutes=30)),),
    )
    _create_task(service_db_session, master_id, name="first")
    _create_task(service_db_session, master_id, name="second")
    entry_id = _add_calendar_entry(
        service_db_session,
        entry_type=CalendarEntryType.TASK,
        start_time=_utc(2026, 6, 7, 9, 0),
        end_time=_utc(2026, 6, 7, 9, 30),
    )
    entries_before = _calendar_entry_count(service_db_session)

    result = _assignment_service(service_db_session).assign_tasks(
        _resolve_seam(service_db_session),
        RUN_AT,
    )

    assert not result.success
    assert _calendar_entry_count(service_db_session) == entries_before
    assert service_db_session.get(CalendarEntry, entry_id) is not None


@pytest.mark.integration
def test_assign_tasks_failure_persists_failed_run_and_last_refresh_failed(
    service_db_session: Session,
) -> None:
    master_id = _bootstrap_master_with_horizon(service_db_session)
    clock = _clock()
    TimeConstraintService(service_db_session, clock).add_user_group(
        master_id,
        (_window(RUN_AT, RUN_AT + timedelta(minutes=30)),),
    )
    _create_task(service_db_session, master_id, name="first")
    _create_task(service_db_session, master_id, name="second")

    result = _assignment_service(service_db_session).assign_tasks(
        _resolve_seam(service_db_session),
        RUN_AT,
    )

    assert not result.success and result.value is not None
    failed_run = service_db_session.get(CalendarRun, result.value.calendar_run_id)
    assert failed_run is not None
    assert failed_run.status == CalendarRunStatus.FAILED
    assert failed_run.conflict_count >= 1
    state = _active_state(service_db_session)
    assert state is not None
    assert state.last_refresh_failed is True
    assert state.last_failure_reason == LastFailureReason.ASSIGNMENT_FAILED
    assert state.last_failure_at is not None


@pytest.mark.integration
def test_assign_tasks_failure_preserves_active_calendar_run_id(
    service_db_session: Session,
) -> None:
    master_id = _bootstrap_master_with_horizon(service_db_session)
    _create_task(service_db_session, master_id, name="solo")
    success = _assignment_service(service_db_session).assign_tasks(
        _resolve_seam(service_db_session),
        RUN_AT,
    )
    assert success.success and success.value is not None
    state = _active_state(service_db_session)
    assert state is not None
    prior_active_run_id = state.active_calendar_run_id

    clock = _clock()
    TimeConstraintService(service_db_session, clock).add_user_group(
        master_id,
        (_window(RUN_AT, RUN_AT + timedelta(minutes=30)),),
    )
    second_id = _create_task(service_db_session, master_id, name="extra")
    assert second_id
    failure = _assignment_service(service_db_session).assign_tasks(
        _resolve_seam(service_db_session),
        RUN_AT,
    )

    assert not failure.success
    state = _active_state(service_db_session)
    assert state is not None
    assert state.active_calendar_run_id == prior_active_run_id


@pytest.mark.integration
def test_assign_tasks_failure_returns_conflicts_in_result_value(
    service_db_session: Session,
) -> None:
    master_id = _bootstrap_master_with_horizon(service_db_session)
    clock = _clock()
    TimeConstraintService(service_db_session, clock).add_user_group(
        master_id,
        (_window(RUN_AT, RUN_AT + timedelta(minutes=30)),),
    )
    _create_task(service_db_session, master_id, name="first")
    _create_task(service_db_session, master_id, name="second")

    result = _assignment_service(service_db_session).assign_tasks(
        _resolve_seam(service_db_session),
        RUN_AT,
    )

    assert not result.success
    assert result.value is not None
    assert len(result.value.conflicts) == 1
    assert result.value.optimization_status == SolverStatus.INFEASIBLE
    assert result.value.calendar_entries == ()


@pytest.mark.integration
def test_assign_tasks_occupied_past_task_blocks_placement(
    service_db_session: Session,
) -> None:
    master_id = _bootstrap_master_with_horizon(service_db_session)
    task_id = _create_task(service_db_session, master_id)
    clock = _clock()
    TimeConstraintService(service_db_session, clock).add_user_group(
        master_id,
        (_window(_utc(2026, 6, 7, 9, 0), _utc(2026, 6, 7, 12, 0)),),
    )
    _seed_active_calendar_state_with_past_task(
        service_db_session,
        past_start=_utc(2026, 6, 7, 9, 0),
        past_end=_utc(2026, 6, 7, 10, 30),
        source_plan_id=task_id,
    )

    result = _assignment_service(service_db_session).assign_tasks(
        _resolve_seam(service_db_session),
        RUN_AT,
    )

    assert result.success and result.value is not None
    assert len(result.value.calendar_entries) == 1
    assert result.value.calendar_entries[0].start_time == _utc(2026, 6, 7, 10, 30)

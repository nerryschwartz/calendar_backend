"""Integration tests for FreeTimeAssignmentService."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from calendar_backend.db.session import transaction
from calendar_backend.domain.assignment import CalendarEntryDTO
from calendar_backend.domain.enums import (
    CalendarEntryType,
    CalendarRunStatus,
    PlanKind,
    RepeatMode,
    SolverStatus,
)
from calendar_backend.domain.errors import MessageCode
from calendar_backend.domain.ids import FreeTimeActivityID, PlanID
from calendar_backend.domain.plan_create import (
    GoalCreatePayload,
    RepetitionCreatePayload,
    TaskCreatePayload,
)
from calendar_backend.domain.resolution import ResolveTasksResult
from calendar_backend.models.calendar import CalendarEntry
from calendar_backend.models.free_time import FreeTimeActivity
from calendar_backend.models.plans import RepetitionPlan
from calendar_backend.models.runs import ActiveCalendarState, CalendarRun
from calendar_backend.services.app_settings import AppSettingsService
from calendar_backend.services.free_time_activity import FreeTimeActivityService
from calendar_backend.services.free_time_assignment import FreeTimeAssignmentService
from calendar_backend.services.goal import GoalService
from calendar_backend.services.master_horizon import MasterHorizonService
from calendar_backend.services.master_plan import MasterPlanService
from calendar_backend.services.repetition import RepetitionService
from calendar_backend.services.task import TaskService
from calendar_backend.services.task_assignment import TaskAssignmentService
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .conftest import FakeClock

RUN_AT = datetime(2026, 6, 7, 10, 0, tzinfo=UTC)


def _utc(y: int, m: int, d: int, h: int, mi: int = 0) -> datetime:
    return datetime(y, m, d, h, mi, tzinfo=UTC)


def _clock() -> FakeClock:
    return FakeClock(RUN_AT)


def _free_time_assignment_service(session: Session) -> FreeTimeAssignmentService:
    return FreeTimeAssignmentService(session, _clock())


def _task_assignment_service(session: Session) -> TaskAssignmentService:
    return TaskAssignmentService(session, _clock())


def _bootstrap_master_with_horizon(session: Session) -> PlanID:
    clock = _clock()
    master = MasterPlanService(session, clock).ensure_master_exists()
    assert master.success and master.value is not None
    AppSettingsService(session, clock).get_settings()
    assert MasterHorizonService(session, clock).refresh_master_horizon(RUN_AT).success
    return master.value.plan_id


def _bootstrap_master_with_short_horizon(
    session: Session,
    *,
    duration_minutes: int,
) -> PlanID:
    clock = _clock()
    master = MasterPlanService(session, clock).ensure_master_exists()
    assert master.success and master.value is not None
    AppSettingsService(session, clock).get_settings()
    assert (
        AppSettingsService(session, clock)
        .update_settings(master_horizon_duration_minutes=duration_minutes)
        .success
    )
    assert MasterHorizonService(session, clock).refresh_master_horizon(RUN_AT).success
    return master.value.plan_id


def _goal_service(session: Session) -> GoalService:
    return GoalService(session, _clock())


def _repetition_service(session: Session) -> RepetitionService:
    return RepetitionService(session, _clock())


def _create_goal_template_repetition_with_task_child(
    session: Session,
    master_plan_id: PlanID,
) -> tuple[PlanID, PlanID, PlanID]:
    repetition_result = _goal_service(session).create_child(
        master_plan_id,
        PlanKind.REPETITION,
        RepetitionCreatePayload(
            name="weekly",
            repeat_mode=RepeatMode.MANUAL_COUNT,
            start_time=RUN_AT,
            repeat_interval_minutes=60,
            manual_count=1,
            end_time=None,
            default_instance_critical=False,
            template_type=PlanKind.GOAL,
            template_payload=GoalCreatePayload(name="template goal"),
        ),
        is_critical=False,
    )
    assert repetition_result.success and repetition_result.value is not None
    repetition = session.get(RepetitionPlan, repetition_result.value.plan_id)
    assert repetition is not None
    template_goal_id = PlanID(repetition.template_root_id)
    child_result = _goal_service(session).create_child(
        template_goal_id,
        PlanKind.TASK,
        TaskCreatePayload("template child", 30, False, None),
        is_critical=False,
    )
    assert child_result.success and child_result.value is not None
    return (
        repetition_result.value.plan_id,
        template_goal_id,
        child_result.value.plan_id,
    )


def _generate_instances(session: Session, repetition_id: PlanID) -> None:
    assert _repetition_service(session).generate_instances(repetition_id, RUN_AT).success


def _empty_resolve_result() -> ResolveTasksResult:
    return ResolveTasksResult(
        run_started_at=RUN_AT,
        valid_incomplete=(),
        valid_completed=(),
        invalid_incomplete=(),
        invalid_completed=(),
        precedence_constraints=(),
        warnings=(),
    )


def _seed_active_calendar_run(session: Session) -> uuid.UUID:
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
        txn.flush()
    return run_id


def _run_task_assignment_for_active_run(session: Session) -> uuid.UUID:
    _bootstrap_master_with_horizon(session)
    result = _task_assignment_service(session).assign_tasks(_empty_resolve_result(), RUN_AT)
    assert result.success and result.value is not None
    assert result.value.calendar_run_id is not None
    return result.value.calendar_run_id


def _create_enabled_activity(
    session: Session,
    *,
    name: str = "reading",
    real_fraction: Decimal = Decimal("1"),
) -> uuid.UUID:
    result = FreeTimeActivityService(session, _clock()).create_activity(
        name,
        real_fraction,
        minimum_block_size_minutes=0,
    )
    assert result.success and result.value is not None
    return result.value.free_time_activity_id


def _create_two_enabled_activities(
    session: Session,
    *,
    first_name: str = "reading",
    second_name: str = "gaming",
) -> tuple[uuid.UUID, uuid.UUID]:
    reading_id = uuid.uuid4()
    gaming_id = uuid.uuid4()
    with transaction(session) as txn:
        txn.add(
            FreeTimeActivity(
                free_time_activity_id=reading_id,
                name=first_name,
                enabled=True,
                real_fraction=Decimal("0.5"),
                minimum_block_size_minutes=0,
                created_at=RUN_AT,
                updated_at=RUN_AT,
            )
        )
        txn.add(
            FreeTimeActivity(
                free_time_activity_id=gaming_id,
                name=second_name,
                enabled=True,
                real_fraction=Decimal("0.5"),
                minimum_block_size_minutes=0,
                created_at=RUN_AT,
                updated_at=RUN_AT,
            )
        )
        txn.flush()
    return reading_id, gaming_id


def _assigned_minutes_by_activity(
    entries: tuple[CalendarEntryDTO, ...],
    activity_id: uuid.UUID,
) -> int:
    total = timedelta()
    for entry in entries:
        if entry.source_free_time_activity_id != activity_id:
            continue
        start_time = _normalize_entry_time(entry.start_time)
        end_time = _normalize_entry_time(entry.end_time)
        total += end_time - start_time
    return int(total.total_seconds() // 60)


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


def _calendar_entry_count(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(CalendarEntry)) or 0


def _free_time_entry_count(session: Session) -> int:
    return (
        session.scalar(
            select(func.count())
            .select_from(CalendarEntry)
            .where(CalendarEntry.entry_type == CalendarEntryType.FREE_TIME)
        )
        or 0
    )


def _normalize_entry_time(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


@pytest.mark.integration
def test_assign_free_time_rejects_missing_active_calendar_run(
    service_db_session: Session,
) -> None:
    _bootstrap_master_with_horizon(service_db_session)
    _create_enabled_activity(service_db_session)
    entries_before = _calendar_entry_count(service_db_session)

    result = _free_time_assignment_service(service_db_session).assign_free_time(RUN_AT)

    assert not result.success
    assert any(error.code == MessageCode.ACTIVE_CALENDAR_RUN_NOT_SET for error in result.errors)
    assert _calendar_entry_count(service_db_session) == entries_before


@pytest.mark.integration
def test_assign_free_time_rejects_invalid_run_started_at_without_mutation(
    service_db_session: Session,
) -> None:
    _run_task_assignment_for_active_run(service_db_session)
    _create_enabled_activity(service_db_session)
    entries_before = _calendar_entry_count(service_db_session)
    naive_run_at = datetime(2026, 6, 7, 10, 0)

    result = _free_time_assignment_service(service_db_session).assign_free_time(naive_run_at)

    assert not result.success
    assert any(error.code == MessageCode.INVALID_TIME_WINDOW for error in result.errors)
    assert _calendar_entry_count(service_db_session) == entries_before


@pytest.mark.integration
def test_assign_free_time_rejects_missing_master_horizon_without_mutation(
    service_db_session: Session,
) -> None:
    clock = _clock()
    MasterPlanService(service_db_session, clock).ensure_master_exists()
    AppSettingsService(service_db_session, clock).get_settings()
    _seed_active_calendar_run(service_db_session)
    _create_enabled_activity(service_db_session)
    entries_before = _calendar_entry_count(service_db_session)

    result = _free_time_assignment_service(service_db_session).assign_free_time(RUN_AT)

    assert not result.success
    assert any(error.code == MessageCode.MASTER_HORIZON_NOT_FOUND for error in result.errors)
    assert _calendar_entry_count(service_db_session) == entries_before


@pytest.mark.integration
def test_assign_free_time_success_replaces_future_free_time_only(
    service_db_session: Session,
) -> None:
    active_run_id = _run_task_assignment_for_active_run(service_db_session)
    activity_id = _create_enabled_activity(service_db_session)
    past_entry_id = _add_calendar_entry(
        service_db_session,
        entry_type=CalendarEntryType.FREE_TIME,
        start_time=_utc(2026, 6, 7, 8, 0),
        end_time=_utc(2026, 6, 7, 9, 0),
        source_free_time_activity_id=activity_id,
        calendar_run_id=active_run_id,
    )
    stale_future_id = _add_calendar_entry(
        service_db_session,
        entry_type=CalendarEntryType.FREE_TIME,
        start_time=_utc(2026, 6, 7, 11, 0),
        end_time=_utc(2026, 6, 7, 12, 0),
        source_free_time_activity_id=activity_id,
        calendar_run_id=active_run_id,
    )

    result = _free_time_assignment_service(service_db_session).assign_free_time(RUN_AT)

    assert result.success and result.value is not None
    assert service_db_session.get(CalendarEntry, past_entry_id) is not None
    assert service_db_session.get(CalendarEntry, stale_future_id) is None
    assert _free_time_entry_count(service_db_session) >= 1


@pytest.mark.integration
def test_assign_free_time_success_attaches_active_calendar_run_id(
    service_db_session: Session,
) -> None:
    active_run_id = _run_task_assignment_for_active_run(service_db_session)
    _create_enabled_activity(service_db_session)

    result = _free_time_assignment_service(service_db_session).assign_free_time(RUN_AT)

    assert result.success and result.value is not None
    assert result.value.calendar_run_id == active_run_id
    for entry in result.value.calendar_entries:
        assert entry.calendar_run_id == active_run_id


@pytest.mark.integration
def test_assign_free_time_empty_pool_clears_future_free_time_only(
    service_db_session: Session,
) -> None:
    active_run_id = _run_task_assignment_for_active_run(service_db_session)
    activity_id = _create_enabled_activity(service_db_session)
    disabled = FreeTimeActivityService(service_db_session, _clock()).set_enabled(
        FreeTimeActivityID(activity_id),
        False,
    )
    assert disabled.success
    stale_future_id = _add_calendar_entry(
        service_db_session,
        entry_type=CalendarEntryType.FREE_TIME,
        start_time=_utc(2026, 6, 7, 11, 0),
        end_time=_utc(2026, 6, 7, 12, 0),
        source_free_time_activity_id=activity_id,
        calendar_run_id=active_run_id,
    )

    result = _free_time_assignment_service(service_db_session).assign_free_time(RUN_AT)

    assert result.success and result.value is not None
    assert result.value.calendar_entries == ()
    assert service_db_session.get(CalendarEntry, stale_future_id) is None


@pytest.mark.integration
def test_assign_free_time_blocked_activity_clears_future_free_time_without_inserts(
    service_db_session: Session,
) -> None:
    master_id = _bootstrap_master_with_horizon(service_db_session)
    task_result = GoalService(service_db_session, _clock()).create_child(
        master_id,
        PlanKind.TASK,
        TaskCreatePayload("blocked-task", 30, False, None),
        is_critical=False,
    )
    assert task_result.success and task_result.value is not None
    created = FreeTimeActivityService(service_db_session, _clock()).create_activity(
        "reading",
        Decimal("1"),
        minimum_block_size_minutes=0,
    )
    assert created.success and created.value is not None
    assert (
        FreeTimeActivityService(service_db_session, _clock())
        .add_prerequisite(created.value.free_time_activity_id, task_result.value.plan_id)
        .success
    )
    active_run_id = _seed_active_calendar_run(service_db_session)
    stale_future_id = _add_calendar_entry(
        service_db_session,
        entry_type=CalendarEntryType.FREE_TIME,
        start_time=_utc(2026, 6, 7, 11, 0),
        end_time=_utc(2026, 6, 7, 12, 0),
        source_free_time_activity_id=created.value.free_time_activity_id,
        calendar_run_id=active_run_id,
    )

    result = _free_time_assignment_service(service_db_session).assign_free_time(RUN_AT)

    assert result.success and result.value is not None
    assert result.value.calendar_entries == ()
    assert service_db_session.get(CalendarEntry, stale_future_id) is None


@pytest.mark.integration
def test_assign_free_time_leaves_task_entries_untouched(service_db_session: Session) -> None:
    master_id = _bootstrap_master_with_horizon(service_db_session)
    active_run_id = _seed_active_calendar_run(service_db_session)
    task_entry_id = _add_calendar_entry(
        service_db_session,
        entry_type=CalendarEntryType.TASK,
        start_time=_utc(2026, 6, 7, 11, 0),
        end_time=_utc(2026, 6, 7, 11, 30),
        source_plan_id=master_id,
        calendar_run_id=active_run_id,
    )
    _create_enabled_activity(service_db_session)

    result = _free_time_assignment_service(service_db_session).assign_free_time(RUN_AT)

    assert result.success
    assert service_db_session.get(CalendarEntry, task_entry_id) is not None


@pytest.mark.integration
def test_assign_free_time_second_run_replaces_future_free_time_only(
    service_db_session: Session,
) -> None:
    active_run_id = _run_task_assignment_for_active_run(service_db_session)
    _create_enabled_activity(service_db_session)
    first = _free_time_assignment_service(service_db_session).assign_free_time(RUN_AT)
    assert first.success and first.value is not None
    assert first.value.calendar_entries
    first_future_id = first.value.calendar_entries[0].calendar_entry_id

    second = _free_time_assignment_service(service_db_session).assign_free_time(RUN_AT)

    assert second.success and second.value is not None
    assert service_db_session.get(CalendarEntry, first_future_id) is None
    assert _free_time_entry_count(service_db_session) >= 1
    for entry in second.value.calendar_entries:
        assert entry.calendar_run_id == active_run_id


@pytest.mark.integration
def test_assign_free_time_respects_future_task_blocker_gap(
    service_db_session: Session,
) -> None:
    master_id = _bootstrap_master_with_horizon(service_db_session)
    active_run_id = _seed_active_calendar_run(service_db_session)
    _create_enabled_activity(service_db_session)
    _add_calendar_entry(
        service_db_session,
        entry_type=CalendarEntryType.TASK,
        start_time=_utc(2026, 6, 7, 10, 0),
        end_time=_utc(2026, 6, 7, 11, 0),
        source_plan_id=master_id,
        calendar_run_id=active_run_id,
    )

    result = _free_time_assignment_service(service_db_session).assign_free_time(RUN_AT)

    assert result.success and result.value is not None
    for entry in result.value.calendar_entries:
        start_time = _normalize_entry_time(entry.start_time)
        end_time = _normalize_entry_time(entry.end_time)
        blocker_start = _utc(2026, 6, 7, 10, 0)
        blocker_end = _utc(2026, 6, 7, 11, 0)
        overlaps = start_time < blocker_end and end_time > blocker_start
        assert not overlaps


@pytest.mark.integration
def test_assign_free_time_does_not_create_new_calendar_run(
    service_db_session: Session,
) -> None:
    _run_task_assignment_for_active_run(service_db_session)
    _create_enabled_activity(service_db_session)
    runs_before = service_db_session.scalar(select(func.count()).select_from(CalendarRun)) or 0

    result = _free_time_assignment_service(service_db_session).assign_free_time(RUN_AT)

    assert result.success
    runs_after = service_db_session.scalar(select(func.count()).select_from(CalendarRun)) or 0
    assert runs_after == runs_before


@pytest.mark.integration
def test_assign_free_time_two_activities_split_proportionally_around_task_blocker(
    service_db_session: Session,
) -> None:
    master_id = _bootstrap_master_with_short_horizon(service_db_session, duration_minutes=240)
    active_run_id = _seed_active_calendar_run(service_db_session)
    reading_id, gaming_id = _create_two_enabled_activities(service_db_session)
    _add_calendar_entry(
        service_db_session,
        entry_type=CalendarEntryType.TASK,
        start_time=_utc(2026, 6, 7, 10, 0),
        end_time=_utc(2026, 6, 7, 11, 0),
        source_plan_id=master_id,
        calendar_run_id=active_run_id,
    )

    result = _free_time_assignment_service(service_db_session).assign_free_time(RUN_AT)

    assert result.success and result.value is not None
    entries = result.value.calendar_entries
    assert entries
    gap_start = _utc(2026, 6, 7, 11, 0)
    gap_end = _utc(2026, 6, 7, 14, 0)
    for entry in entries:
        start_time = _normalize_entry_time(entry.start_time)
        end_time = _normalize_entry_time(entry.end_time)
        assert start_time >= gap_start
        assert end_time <= gap_end
    assert _assigned_minutes_by_activity(entries, reading_id) == 90
    assert _assigned_minutes_by_activity(entries, gaming_id) == 90


@pytest.mark.integration
def test_assign_free_time_unblocks_after_prerequisite_mark_complete(
    service_db_session: Session,
) -> None:
    master_id = _bootstrap_master_with_horizon(service_db_session)
    task_result = GoalService(service_db_session, _clock()).create_child(
        master_id,
        PlanKind.TASK,
        TaskCreatePayload("blocked-task", 30, False, None),
        is_critical=False,
    )
    assert task_result.success and task_result.value is not None
    task_id = task_result.value.plan_id
    created = FreeTimeActivityService(service_db_session, _clock()).create_activity(
        "reading",
        Decimal("1"),
        minimum_block_size_minutes=0,
    )
    assert created.success and created.value is not None
    activity_id = created.value.free_time_activity_id
    assert (
        FreeTimeActivityService(service_db_session, _clock())
        .add_prerequisite(FreeTimeActivityID(activity_id), task_id)
        .success
    )
    active_run_id = _seed_active_calendar_run(service_db_session)
    stale_future_id = _add_calendar_entry(
        service_db_session,
        entry_type=CalendarEntryType.FREE_TIME,
        start_time=_utc(2026, 6, 7, 11, 0),
        end_time=_utc(2026, 6, 7, 12, 0),
        source_free_time_activity_id=activity_id,
        calendar_run_id=active_run_id,
    )

    blocked = _free_time_assignment_service(service_db_session).assign_free_time(RUN_AT)

    assert blocked.success and blocked.value is not None
    assert blocked.value.calendar_entries == ()
    assert service_db_session.get(CalendarEntry, stale_future_id) is None

    assert TaskService(service_db_session, _clock()).mark_complete(task_id).success

    unblocked = _free_time_assignment_service(service_db_session).assign_free_time(RUN_AT)

    assert unblocked.success and unblocked.value is not None
    assert unblocked.value.calendar_entries
    assert all(
        entry.source_free_time_activity_id == activity_id
        for entry in unblocked.value.calendar_entries
    )
    for entry in unblocked.value.calendar_entries:
        assert _normalize_entry_time(entry.start_time) >= RUN_AT


@pytest.mark.integration
def test_assign_free_time_leaves_sub_minimum_gap_unassigned(
    service_db_session: Session,
) -> None:
    master_id = _bootstrap_master_with_short_horizon(service_db_session, duration_minutes=80)
    active_run_id = _seed_active_calendar_run(service_db_session)
    created = FreeTimeActivityService(service_db_session, _clock()).create_activity(
        "reading",
        Decimal("1"),
        minimum_block_size_minutes=25,
    )
    assert created.success and created.value is not None
    _add_calendar_entry(
        service_db_session,
        entry_type=CalendarEntryType.TASK,
        start_time=_utc(2026, 6, 7, 10, 0),
        end_time=_utc(2026, 6, 7, 11, 0),
        source_plan_id=master_id,
        calendar_run_id=active_run_id,
    )

    result = _free_time_assignment_service(service_db_session).assign_free_time(RUN_AT)

    assert result.success and result.value is not None
    assert result.value.calendar_entries == ()


@pytest.mark.integration
def test_assign_free_time_blocked_by_template_subtree_prerequisite(
    service_db_session: Session,
) -> None:
    master_id = _bootstrap_master_with_horizon(service_db_session)
    repetition_id, _, template_task_id = _create_goal_template_repetition_with_task_child(
        service_db_session, master_id
    )
    _generate_instances(service_db_session, repetition_id)
    created = FreeTimeActivityService(service_db_session, _clock()).create_activity(
        "reading",
        Decimal("1"),
        minimum_block_size_minutes=0,
    )
    assert created.success and created.value is not None
    activity_id = created.value.free_time_activity_id
    assert (
        FreeTimeActivityService(service_db_session, _clock())
        .add_prerequisite(FreeTimeActivityID(activity_id), template_task_id)
        .success
    )
    active_run_id = _seed_active_calendar_run(service_db_session)
    stale_future_id = _add_calendar_entry(
        service_db_session,
        entry_type=CalendarEntryType.FREE_TIME,
        start_time=_utc(2026, 6, 7, 11, 0),
        end_time=_utc(2026, 6, 7, 12, 0),
        source_free_time_activity_id=activity_id,
        calendar_run_id=active_run_id,
    )

    result = _free_time_assignment_service(service_db_session).assign_free_time(RUN_AT)

    assert result.success and result.value is not None
    assert result.value.calendar_entries == ()
    assert service_db_session.get(CalendarEntry, stale_future_id) is None

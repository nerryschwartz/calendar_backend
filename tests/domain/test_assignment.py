"""Pure tests for task assignment domain helpers."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from calendar_backend.domain.assignment import (
    analyze_assignment_conflicts,
    calendar_entry_insert_specs_from_assignments,
    occupied_intervals_from_calendar_entries,
)
from calendar_backend.domain.enums import CalendarEntryType, SolverStatus
from calendar_backend.domain.errors import MessageCode, ServiceMessage
from calendar_backend.domain.ids import PlanID
from calendar_backend.domain.resolution import ResolvedTask, ResolveTasksResult
from calendar_backend.domain.time import TimeWindow
from calendar_backend.models.calendar import CalendarEntry
from calendar_backend.scheduling.input import AssignmentInput
from calendar_backend.scheduling.types import (
    AssignmentSolverResult,
    TaskAssignment,
    infeasible_result,
)

RUN_AT = datetime(2026, 6, 7, 10, 0, tzinfo=UTC)


def _utc(y: int, m: int, d: int, h: int, mi: int) -> datetime:
    return datetime(y, m, d, h, mi, tzinfo=UTC)


def _window(start: datetime, end: datetime) -> TimeWindow:
    return TimeWindow(start_time=start, end_time=end)


def _empty_assignment_input() -> AssignmentInput:
    return AssignmentInput(
        run_started_at=RUN_AT,
        tasks=(),
        precedence_edges=(),
        occupied_intervals=(),
    )


def _calendar_entry(
    *,
    entry_id: uuid.UUID,
    entry_type: CalendarEntryType,
    start_time: datetime,
    end_time: datetime,
    source_plan_id: uuid.UUID | None = None,
) -> CalendarEntry:
    return CalendarEntry(
        calendar_entry_id=entry_id,
        entry_type=entry_type,
        start_time=start_time,
        end_time=end_time,
        source_plan_id=source_plan_id,
        source_free_time_activity_id=None,
        calendar_run_id=None,
        display_label="label",
        created_at=start_time,
        updated_at=start_time,
    )


def _resolved_task(
    plan_id: uuid.UUID,
    *,
    priority_path: tuple[int, ...] = (0,),
    name: str = "task",
) -> ResolvedTask:
    return ResolvedTask(
        plan_id=PlanID(plan_id),
        name=name,
        duration_minutes=30,
        divisible=False,
        minimum_chunk_size_minutes=None,
        user_completed=False,
        completed_at=None,
        effective_time_windows=(_window(_utc(2026, 6, 7, 9, 0), _utc(2026, 6, 7, 12, 0)),),
        constraint_sources=(),
        priority_path=priority_path,
        criticality_path=(),
        parent_path=(PlanID(plan_id),),
        chain_path=(),
        validation_errors=(),
    )


def _resolve_result(
    *,
    valid_incomplete: tuple[ResolvedTask, ...] = (),
) -> ResolveTasksResult:
    return ResolveTasksResult(
        run_started_at=RUN_AT,
        valid_incomplete=valid_incomplete,
        valid_completed=(),
        invalid_incomplete=(),
        invalid_completed=(),
        precedence_constraints=(),
        warnings=(),
    )


def test_occupied_intervals_from_calendar_entries_includes_past_task_only() -> None:
    plan_id = uuid.uuid4()
    past = _calendar_entry(
        entry_id=uuid.uuid4(),
        entry_type=CalendarEntryType.TASK,
        start_time=_utc(2026, 6, 7, 9, 0),
        end_time=_utc(2026, 6, 7, 9, 30),
        source_plan_id=plan_id,
    )

    intervals = occupied_intervals_from_calendar_entries((past,), RUN_AT)

    assert len(intervals) == 1
    assert intervals[0].source_plan_id == PlanID(plan_id)


def test_occupied_intervals_from_calendar_entries_excludes_future_task() -> None:
    future = _calendar_entry(
        entry_id=uuid.uuid4(),
        entry_type=CalendarEntryType.TASK,
        start_time=_utc(2026, 6, 7, 10, 0),
        end_time=_utc(2026, 6, 7, 11, 0),
    )

    assert occupied_intervals_from_calendar_entries((future,), RUN_AT) == ()


def test_occupied_intervals_from_calendar_entries_excludes_free_time() -> None:
    free_time = _calendar_entry(
        entry_id=uuid.uuid4(),
        entry_type=CalendarEntryType.FREE_TIME,
        start_time=_utc(2026, 6, 7, 9, 0),
        end_time=_utc(2026, 6, 7, 9, 30),
    )

    assert occupied_intervals_from_calendar_entries((free_time,), RUN_AT) == ()


def test_occupied_intervals_from_calendar_entries_sorts_deterministically() -> None:
    first_plan = uuid.uuid4()
    second_plan = uuid.uuid4()
    later = _calendar_entry(
        entry_id=uuid.uuid4(),
        entry_type=CalendarEntryType.TASK,
        start_time=_utc(2026, 6, 7, 9, 30),
        end_time=_utc(2026, 6, 7, 10, 0),
        source_plan_id=second_plan,
    )
    earlier = _calendar_entry(
        entry_id=uuid.uuid4(),
        entry_type=CalendarEntryType.TASK,
        start_time=_utc(2026, 6, 7, 9, 0),
        end_time=_utc(2026, 6, 7, 9, 30),
        source_plan_id=first_plan,
    )

    intervals = occupied_intervals_from_calendar_entries((later, earlier), RUN_AT)

    assert [interval.source_plan_id for interval in intervals] == [
        PlanID(first_plan),
        PlanID(second_plan),
    ]


def test_analyze_assignment_conflicts_task_local_failure_uses_plan_id_from_details() -> None:
    plan_id = uuid.uuid4()
    task = _resolved_task(plan_id)
    failure = ServiceMessage(
        code=MessageCode.NO_VALID_WINDOW_FOR_TASK,
        message="No valid placement found for task",
        details={"plan_id": str(plan_id)},
    )
    solver_result = infeasible_result(failure)
    resolved = _resolve_result(valid_incomplete=(task,))

    conflicts = analyze_assignment_conflicts(
        _empty_assignment_input(),
        resolved,
        solver_result,
    )

    assert len(conflicts) == 1
    conflict = conflicts[0]
    assert conflict.reason_code == MessageCode.NO_VALID_WINDOW_FOR_TASK
    assert conflict.conflicting_plan_ids == (PlanID(plan_id),)
    assert conflict.is_global is False


def test_analyze_assignment_conflicts_global_failure_uses_all_valid_incomplete() -> None:
    first_id = uuid.uuid4()
    second_id = uuid.uuid4()
    resolved = _resolve_result(
        valid_incomplete=(
            _resolved_task(first_id, priority_path=(0,)),
            _resolved_task(second_id, priority_path=(1,)),
        )
    )
    failure = ServiceMessage(
        code=MessageCode.INSUFFICIENT_TOTAL_CAPACITY,
        message="Insufficient total capacity to assign task",
        details={},
    )
    solver_result = infeasible_result(failure)

    conflicts = analyze_assignment_conflicts(
        _empty_assignment_input(),
        resolved,
        solver_result,
    )

    assert len(conflicts) == 1
    conflict = conflicts[0]
    assert conflict.conflicting_plan_ids == tuple(
        sorted((PlanID(first_id), PlanID(second_id)), key=str)
    )
    assert conflict.is_global is True


def test_analyze_assignment_conflicts_populates_priority_metadata() -> None:
    plan_id = uuid.uuid4()
    task = _resolved_task(plan_id, priority_path=(2, 5))
    failure = ServiceMessage(
        code=MessageCode.NO_VALID_WINDOW_FOR_TASK,
        message="No valid placement",
        details={"plan_id": str(plan_id)},
    )
    resolved = _resolve_result(valid_incomplete=(task,))

    conflicts = analyze_assignment_conflicts(
        _empty_assignment_input(),
        resolved,
        infeasible_result(failure),
    )

    assert conflicts[0].affected_priority_by_plan_id == ((PlanID(plan_id), 5),)


def test_analyze_assignment_conflicts_returns_empty_for_feasible_solver() -> None:
    feasible = AssignmentSolverResult(
        status=SolverStatus.FEASIBLE,
        assignments=(),
        warnings=(),
        failure=None,
    )

    assert (
        analyze_assignment_conflicts(
            _empty_assignment_input(),
            _resolve_result(),
            feasible,
        )
        == ()
    )


def test_calendar_entry_insert_specs_from_assignments_one_spec_per_segment() -> None:
    plan_id = uuid.uuid4()
    task = _resolved_task(plan_id, name="divisible task")
    assignments = (
        TaskAssignment(
            plan_id=PlanID(plan_id),
            segments=(
                _window(_utc(2026, 6, 7, 10, 0), _utc(2026, 6, 7, 10, 30)),
                _window(_utc(2026, 6, 7, 11, 0), _utc(2026, 6, 7, 11, 30)),
            ),
        ),
    )

    specs = calendar_entry_insert_specs_from_assignments(
        assignments,
        {PlanID(plan_id): task},
    )

    assert len(specs) == 2
    assert specs[0].display_label == "divisible task"
    assert specs[0].start_time == _utc(2026, 6, 7, 10, 0)
    assert specs[1].start_time == _utc(2026, 6, 7, 11, 0)

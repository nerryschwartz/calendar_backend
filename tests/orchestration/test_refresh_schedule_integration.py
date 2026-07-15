"""End-to-end integration tests for OrchestrationService.refresh_schedule."""

from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from calendar_backend.domain.enums import (
    CalendarEntryType,
    CloneStatus,
    PlanKind,
    SolverStatus,
)
from calendar_backend.domain.errors import MessageCode, ServiceMessage
from calendar_backend.domain.ids import PlanID
from calendar_backend.domain.plan_create import TaskCreatePayload
from calendar_backend.domain.resolution import ResolveTasksResult
from calendar_backend.domain.results import fail, ok
from calendar_backend.models.calendar import CalendarEntry
from calendar_backend.models.plans import Plan, TaskPlan
from calendar_backend.services.app_settings import AppSettingsService
from calendar_backend.services.free_time_assignment import FreeTimeAssignmentService
from calendar_backend.services.repetition import RepetitionService
from calendar_backend.services.task_resolution import TaskResolutionService
from calendar_backend.services.time_constraint import TimeConstraintService
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parent))
import orch_helpers as oh


@pytest.mark.integration
def test_refresh_schedule_happy_path_produces_task_and_free_time_entries(
    service_db_session: Session,
) -> None:
    _, task_id = oh.bootstrap_assignable_task(service_db_session)

    result = oh.orchestration_service(service_db_session).refresh_schedule(oh.RUN_AT)

    assert result.success and result.value is not None
    assert result.value.resolved is not None
    assert result.value.assignment is not None
    assert result.value.free_time is not None
    assert oh.future_task_entry_count(service_db_session, task_id) == 1
    assert oh.future_free_time_entry_count(service_db_session) == 1
    state = oh.active_state(service_db_session)
    assert state is not None
    assert state.last_refresh_failed is False


@pytest.mark.integration
def test_refresh_schedule_happy_path_uses_exact_solver_not_heuristic_fallback(
    service_db_session: Session,
) -> None:
    oh.bootstrap_assignable_task(service_db_session)
    AppSettingsService(service_db_session, oh.clock()).update_settings(
        exact_solver_model_size_limit=10_000,
        heuristic_enabled=True,
    )

    result = oh.orchestration_service(service_db_session).refresh_schedule(oh.RUN_AT)

    assert result.success and result.value is not None
    assert result.value.assignment is not None
    assert result.value.assignment.optimization_status in (
        SolverStatus.OPTIMAL,
        SolverStatus.FEASIBLE,
    )
    assert not any(
        warning.code == MessageCode.HEURISTIC_FEASIBLE
        for warning in result.value.assignment.warnings
    )


@pytest.mark.integration
def test_refresh_schedule_invalid_incomplete_blocks_before_assignment(
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
    assert result.value.assignment is None
    assert result.value.free_time is None
    assert oh.calendar_entry_count(service_db_session) == entries_before
    assert oh.calendar_run_count(service_db_session) == runs_before


@pytest.mark.integration
def test_refresh_schedule_infeasible_assignment_returns_conflicts(
    service_db_session: Session,
) -> None:
    master_id = oh.bootstrap_master_with_horizon(service_db_session)
    oh.create_enabled_activity(service_db_session)
    TimeConstraintService(service_db_session, oh.clock()).add_user_group(
        master_id,
        (oh.window(oh.RUN_AT, oh.RUN_AT + timedelta(minutes=30)),),
    )
    oh.create_task(service_db_session, master_id, name="first")
    oh.create_task(service_db_session, master_id, name="second")

    result = oh.orchestration_service(service_db_session).refresh_schedule(oh.RUN_AT)

    assert not result.success
    assert result.value is not None
    assert result.value.assignment is not None
    assert len(result.value.assignment.conflicts) == 1
    assert result.value.assignment.optimization_status == SolverStatus.INFEASIBLE
    assert result.value.assignment.calendar_entries == ()
    assert result.value.free_time is None


@pytest.mark.integration
def test_refresh_schedule_partial_free_time_failure_preserves_future_tasks_only(
    service_db_session: Session,
) -> None:
    master_id = oh.bootstrap_master_with_horizon(service_db_session)
    task_id = oh.create_task(service_db_session, master_id)
    TimeConstraintService(service_db_session, oh.clock()).add_user_group(
        master_id,
        (oh.window(oh.RUN_AT, oh.RUN_AT + timedelta(hours=2)),),
    )
    oh.create_two_enabled_activities(service_db_session)
    success = oh.orchestration_service(service_db_session).refresh_schedule(oh.RUN_AT)
    assert success.success
    state = oh.active_state(service_db_session)
    assert state is not None
    stale_future_id = oh.add_calendar_entry(
        service_db_session,
        entry_type=CalendarEntryType.FREE_TIME,
        start_time=oh.utc(2026, 6, 7, 11, 0),
        end_time=oh.utc(2026, 6, 7, 12, 0),
        calendar_run_id=state.active_calendar_run_id,
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
    assert result.value.free_time is None
    assert oh.future_task_entry_count(service_db_session, task_id) == 1
    assert service_db_session.get(CalendarEntry, stale_future_id) is None


@pytest.mark.integration
def test_refresh_schedule_repetition_instances_resolve_and_assign(
    service_db_session: Session,
) -> None:
    master_id = oh.bootstrap_master_with_horizon(service_db_session)
    repetition_id, _, template_task_id = oh.setup_goal_repetition_with_task_child(
        service_db_session,
        master_id,
        manual_count=1,
    )
    oh.generate_instances(service_db_session, repetition_id)
    root_clone_id = oh.instance_root_clone_id(service_db_session, repetition_id, 0)
    task_clone_id = oh.clone_for_template(
        service_db_session,
        parent_clone_id=root_clone_id,
        template_plan_id=template_task_id,
    )
    oh.create_enabled_activity(service_db_session)

    result = oh.orchestration_service(service_db_session).refresh_schedule(oh.RUN_AT)

    assert result.success and result.value is not None
    resolved_ids = {task.plan_id for task in oh.all_resolved_tasks(result.value)}
    assert template_task_id not in resolved_ids
    assert task_clone_id in resolved_ids
    calendar_ids = oh.calendar_source_plan_ids(service_db_session)
    assert template_task_id not in calendar_ids
    assert task_clone_id in calendar_ids
    assert oh.future_free_time_entry_count(service_db_session) == 1


@pytest.mark.integration
def test_refresh_schedule_repetition_refresh_runs_before_resolve(
    service_db_session: Session,
) -> None:
    master_id = oh.bootstrap_master_with_horizon(service_db_session)
    repetition_id, template_goal_id, template_task_id = oh.setup_goal_repetition_with_task_child(
        service_db_session,
        master_id,
        manual_count=2,
    )
    oh.generate_instances(service_db_session, repetition_id)
    root_clone_0 = oh.instance_root_clone_id(service_db_session, repetition_id, 0)
    root_clone_1 = oh.instance_root_clone_id(service_db_session, repetition_id, 1)
    task_clone_0 = oh.clone_for_template(
        service_db_session,
        parent_clone_id=root_clone_0,
        template_plan_id=template_task_id,
    )
    task_clone_1 = oh.clone_for_template(
        service_db_session,
        parent_clone_id=root_clone_1,
        template_plan_id=template_task_id,
    )
    oh.detach_task_clone(service_db_session, task_clone_0, duration_minutes=25)
    assert (
        oh.task_service(service_db_session)
        .update_scheduling_fields(template_task_id, 30, False, None)
        .success
    )
    oh.create_enabled_activity(service_db_session)

    result = oh.orchestration_service(service_db_session).refresh_schedule(oh.RUN_AT)

    assert result.success
    oh.assert_clone_status(service_db_session, task_clone_0, CloneStatus.DETACHED)
    oh.assert_clone_status(service_db_session, task_clone_1, CloneStatus.LINKED)
    detached_task = service_db_session.get(TaskPlan, task_clone_0)
    linked_task = service_db_session.get(TaskPlan, task_clone_1)
    assert detached_task is not None
    assert linked_task is not None
    assert detached_task.duration_minutes == 25
    assert linked_task.duration_minutes == 30
    detached_entries = oh.calendar_entries_for_plan(service_db_session, task_clone_0)
    linked_entries = oh.calendar_entries_for_plan(service_db_session, task_clone_1)
    assert len(detached_entries) == 1
    assert len(linked_entries) == 1
    assert detached_entries[0].end_time - detached_entries[0].start_time == timedelta(minutes=25)
    assert linked_entries[0].end_time - linked_entries[0].start_time == timedelta(minutes=30)

    new_child = oh.goal_service(service_db_session).create_child(
        template_goal_id,
        PlanKind.TASK,
        TaskCreatePayload("detached guard child", 15, False, None),
        is_critical=False,
    )
    assert new_child.success and new_child.value is not None
    new_template_child_id = new_child.value.plan_id

    second_refresh = oh.orchestration_service(service_db_session).refresh_schedule(oh.RUN_AT)
    assert second_refresh.success
    oh.assert_linked_clone_child_exists(
        service_db_session,
        root_clone_id=root_clone_1,
        template_child_id=new_template_child_id,
    )
    assert oh.task_clone_duration(service_db_session, task_clone_0) == 25
    oh.assert_clone_status(service_db_session, task_clone_0, CloneStatus.DETACHED)
    second_detached_entries = oh.calendar_entries_for_plan(service_db_session, task_clone_0)
    assert len(second_detached_entries) == 1
    assert second_detached_entries[0].end_time - second_detached_entries[0].start_time == timedelta(
        minutes=25
    )


@pytest.mark.integration
def test_refresh_schedule_template_goal_child_materialized_on_instances(
    service_db_session: Session,
) -> None:
    master_id = oh.bootstrap_master_with_horizon(service_db_session)
    repetition_id, template_goal_id, _ = oh.setup_goal_repetition_with_task_child(
        service_db_session,
        master_id,
        manual_count=2,
    )
    oh.generate_instances(service_db_session, repetition_id)
    new_child = oh.goal_service(service_db_session).create_child(
        template_goal_id,
        PlanKind.TASK,
        TaskCreatePayload("new template child", 20, False, None),
        is_critical=False,
    )
    assert new_child.success and new_child.value is not None
    new_template_child_id = new_child.value.plan_id
    oh.create_enabled_activity(service_db_session)

    result = oh.orchestration_service(service_db_session).refresh_schedule(oh.RUN_AT)

    assert result.success and result.value is not None
    resolved_ids = oh.resolved_plan_ids(result.value)
    calendar_ids = oh.calendar_source_plan_ids(service_db_session)
    materialized_clone_ids: list[PlanID] = []
    for instance_index in (0, 1):
        root_clone_id = oh.instance_root_clone_id(service_db_session, repetition_id, instance_index)
        materialized_clone_ids.append(
            oh.assert_linked_clone_child_exists(
                service_db_session,
                root_clone_id=root_clone_id,
                template_child_id=new_template_child_id,
            )
        )
    assert new_template_child_id not in resolved_ids
    assert new_template_child_id not in calendar_ids
    assert set(materialized_clone_ids).issubset(resolved_ids)
    assert set(materialized_clone_ids).issubset(calendar_ids)


@pytest.mark.integration
def test_refresh_schedule_critical_instance_ordering_affects_assignment(
    service_db_session: Session,
) -> None:
    master_id = oh.bootstrap_master_with_horizon(service_db_session)
    repetition_id, _, template_task_id = oh.setup_goal_repetition_with_task_child(
        service_db_session,
        master_id,
        manual_count=2,
    )
    oh.generate_instances(service_db_session, repetition_id)
    oh.set_instance_critical_flags(
        service_db_session,
        repetition_id,
        critical_by_index={0: False, 1: True},
    )

    clone_ids: list[PlanID] = []
    for instance_index in (0, 1):
        root_clone_id = oh.instance_root_clone_id(service_db_session, repetition_id, instance_index)
        clone_ids.append(
            oh.clone_for_template(
                service_db_session,
                parent_clone_id=root_clone_id,
                template_plan_id=template_task_id,
            )
        )
    oh.create_enabled_activity(service_db_session)

    result = oh.orchestration_service(service_db_session).refresh_schedule(oh.RUN_AT)

    assert result.success and result.value is not None
    resolved = result.value.resolved
    assert resolved is not None
    tasks = [task for task in resolved.valid_incomplete if task.plan_id in clone_ids]
    assert len(tasks) == 2
    ordered = sorted(tasks, key=lambda task: task.priority_path)
    assert ordered[0].priority_path < ordered[1].priority_path
    assert ordered[0].plan_id == clone_ids[1]


@pytest.mark.integration
def test_refresh_schedule_heuristic_fallback_through_full_pipeline(
    service_db_session: Session,
) -> None:
    _, task_id = oh.bootstrap_narrow_assignable_task(service_db_session)
    oh.create_enabled_activity(service_db_session)
    oh.enable_heuristic_fallback_settings(service_db_session)

    result = oh.orchestration_service(service_db_session).refresh_schedule(oh.RUN_AT)

    assert result.success and result.value is not None
    assert result.value.assignment is not None
    assert result.value.assignment.optimization_status == SolverStatus.FEASIBLE
    assert any(
        warning.code == MessageCode.HEURISTIC_FEASIBLE
        for warning in result.value.assignment.warnings
    )
    assert oh.future_task_entry_count(service_db_session, task_id) == 1
    assert oh.future_free_time_entry_count(service_db_session) == 1


@pytest.mark.integration
def test_refresh_schedule_multi_activity_proportional_split(
    service_db_session: Session,
) -> None:
    _, task_id, reading_id, gaming_id = oh.bootstrap_multi_activity_refresh_fixture(
        service_db_session
    )

    result = oh.orchestration_service(service_db_session).refresh_schedule(oh.RUN_AT)

    assert result.success and result.value is not None
    assert result.value.free_time is not None
    assert oh.future_task_entry_count(service_db_session, task_id) == 1
    free_time_entries = result.value.free_time.calendar_entries
    activity_ids = {
        entry.source_free_time_activity_id
        for entry in free_time_entries
        if entry.source_free_time_activity_id is not None
    }
    assert reading_id in activity_ids
    assert gaming_id in activity_ids
    assert oh.assigned_minutes_from_dtos(free_time_entries, reading_id) == 90
    assert oh.assigned_minutes_from_dtos(free_time_entries, gaming_id) == 90


@pytest.mark.integration
def test_refresh_schedule_multi_repetition_template_edit_isolates_refresh(
    service_db_session: Session,
) -> None:
    master_id = oh.bootstrap_master_with_horizon(service_db_session)
    (
        repetition_a_id,
        template_task_a_id,
        repetition_b_id,
        template_task_b_id,
    ) = oh.setup_two_goal_repetitions_with_task_children(service_db_session, master_id)
    oh.generate_instances(service_db_session, repetition_a_id)
    oh.generate_instances(service_db_session, repetition_b_id)
    clone_ids_a = oh.repetition_task_clone_ids(
        service_db_session,
        repetition_a_id,
        template_task_a_id,
    )
    clone_ids_b = oh.repetition_task_clone_ids(
        service_db_session,
        repetition_b_id,
        template_task_b_id,
    )
    assert (
        oh.task_service(service_db_session)
        .update_scheduling_fields(template_task_a_id, 60, False, None)
        .success
    )
    oh.create_enabled_activity(service_db_session)

    result = oh.orchestration_service(service_db_session).refresh_schedule(oh.RUN_AT)

    assert result.value is not None
    resolved_ids = oh.resolved_plan_ids(result.value)
    assert template_task_a_id not in resolved_ids
    assert template_task_b_id not in resolved_ids
    assert set(clone_ids_a + clone_ids_b).issubset(resolved_ids)
    assert oh.task_clone_duration(service_db_session, clone_ids_a[0]) == 60
    assert oh.task_clone_duration(service_db_session, clone_ids_b[0]) == 30


@pytest.mark.integration
def test_refresh_schedule_template_root_delete_then_refresh_clean_graph(
    service_db_session: Session,
) -> None:
    master_id = oh.bootstrap_master_with_horizon(service_db_session)
    repetition_id, template_task_id = oh.setup_task_template_repetition(
        service_db_session,
        master_id,
        manual_count=2,
    )
    oh.generate_instances(service_db_session, repetition_id)
    clone_ids = oh.repetition_task_clone_ids(
        service_db_session,
        repetition_id,
        template_task_id,
    )
    oh.create_enabled_activity(service_db_session)

    first_refresh = oh.orchestration_service(service_db_session).refresh_schedule(oh.RUN_AT)
    assert first_refresh.success and first_refresh.value is not None
    assert set(clone_ids).issubset(oh.resolved_plan_ids(first_refresh.value))
    assert set(clone_ids).issubset(oh.calendar_source_plan_ids(service_db_session))

    oh.delete_plan(service_db_session, template_task_id)
    oh.assert_repetition_shell_removed(service_db_session, repetition_id)
    for clone_id in clone_ids:
        assert service_db_session.get(Plan, clone_id) is None

    second_refresh = oh.orchestration_service(service_db_session).refresh_schedule(oh.RUN_AT)
    assert second_refresh.success and second_refresh.value is not None
    resolved_ids = oh.resolved_plan_ids(second_refresh.value)
    calendar_ids = oh.calendar_source_plan_ids(service_db_session)
    assert template_task_id not in resolved_ids
    assert repetition_id not in resolved_ids
    assert not set(clone_ids) & resolved_ids
    assert not set(clone_ids) & calendar_ids
    assert template_task_id not in calendar_ids


@pytest.mark.integration
def test_refresh_schedule_post_delete_instance_clone_clears_calendar(
    service_db_session: Session,
) -> None:
    master_id = oh.bootstrap_master_with_horizon(service_db_session)
    repetition_id, _, template_task_id = oh.setup_goal_repetition_with_task_child(
        service_db_session,
        master_id,
        manual_count=2,
    )
    oh.generate_instances(service_db_session, repetition_id)
    root_clone_0 = oh.instance_root_clone_id(service_db_session, repetition_id, 0)
    root_clone_1 = oh.instance_root_clone_id(service_db_session, repetition_id, 1)
    task_clone_0 = oh.clone_for_template(
        service_db_session,
        parent_clone_id=root_clone_0,
        template_plan_id=template_task_id,
    )
    task_clone_1 = oh.clone_for_template(
        service_db_session,
        parent_clone_id=root_clone_1,
        template_plan_id=template_task_id,
    )
    oh.create_enabled_activity(service_db_session)

    first_refresh = oh.orchestration_service(service_db_session).refresh_schedule(oh.RUN_AT)
    assert first_refresh.success
    assert oh.future_task_entry_count(service_db_session, task_clone_0) == 1
    assert oh.future_task_entry_count(service_db_session, task_clone_1) == 1

    oh.delete_plan(service_db_session, task_clone_0)
    assert service_db_session.get(Plan, task_clone_0) is None

    second_refresh = oh.orchestration_service(service_db_session).refresh_schedule(oh.RUN_AT)
    assert second_refresh.success and second_refresh.value is not None
    resolved_ids = oh.resolved_plan_ids(second_refresh.value)
    calendar_ids = oh.calendar_source_plan_ids(service_db_session)
    assert task_clone_0 not in resolved_ids
    assert task_clone_0 not in calendar_ids
    assert oh.calendar_entries_for_plan(service_db_session, task_clone_0) == []
    assert task_clone_1 in resolved_ids
    assert task_clone_1 in calendar_ids


@pytest.mark.integration
def test_refresh_schedule_repetition_refresh_failure_aborts_before_assignment(
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
    assert oh.calendar_entry_count(service_db_session) == entries_before
    assert oh.calendar_run_count(service_db_session) == runs_before

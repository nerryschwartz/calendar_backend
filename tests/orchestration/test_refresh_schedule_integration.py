"""End-to-end integration tests for OrchestrationService.refresh_schedule."""

from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from calendar_backend.domain.enums import (
    CalendarEntryType,
    PlanKind,
    SolverStatus,
)
from calendar_backend.domain.errors import MessageCode, ServiceMessage
from calendar_backend.domain.ids import PlanID
from calendar_backend.domain.plan_create import TaskCreatePayload
from calendar_backend.domain.resolution import ResolveTasksResult
from calendar_backend.domain.results import fail, ok
from calendar_backend.models.calendar import CalendarEntry
from calendar_backend.models.plans import TaskPlan
from calendar_backend.services.app_settings import AppSettingsService
from calendar_backend.services.free_time_assignment import FreeTimeAssignmentService
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
    assert oh.future_task_entry_count(service_db_session, task_id) >= 1
    assert oh.future_free_time_entry_count(service_db_session) >= 1
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
    _, task_id = oh.bootstrap_assignable_task(service_db_session)
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
    assert oh.future_task_entry_count(service_db_session, task_id) >= 1
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
    assert oh.future_free_time_entry_count(service_db_session) >= 1


@pytest.mark.integration
def test_refresh_schedule_repetition_refresh_runs_before_resolve(
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
    assert (
        oh.task_service(service_db_session)
        .update_scheduling_fields(task_clone_0, 45, False, None)
        .success
    )
    assert (
        oh.task_service(service_db_session)
        .update_scheduling_fields(template_task_id, 60, False, None)
        .success
    )
    oh.create_enabled_activity(service_db_session)

    result = oh.orchestration_service(service_db_session).refresh_schedule(oh.RUN_AT)

    assert result.success
    detached_task = service_db_session.get(TaskPlan, task_clone_0)
    linked_task = service_db_session.get(TaskPlan, task_clone_1)
    assert detached_task is not None
    assert linked_task is not None
    assert detached_task.duration_minutes == 45
    assert linked_task.duration_minutes == 60


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

    assert result.success
    for instance_index in (0, 1):
        root_clone_id = oh.instance_root_clone_id(service_db_session, repetition_id, instance_index)
        oh.assert_linked_clone_child_exists(
            service_db_session,
            root_clone_id=root_clone_id,
            template_child_id=new_template_child_id,
        )


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

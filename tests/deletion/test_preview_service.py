"""Integration tests for DeletionPreviewService."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from calendar_backend.deletion.preview_service import DeletionPreviewService
from calendar_backend.domain.enums import PlanKind, RepeatMode
from calendar_backend.domain.ids import PlanID
from calendar_backend.domain.plan_create import (
    GoalCreatePayload,
    RepetitionCreatePayload,
    TaskCreatePayload,
)
from calendar_backend.models.plans import RepetitionPlan
from calendar_backend.services.app_settings import AppSettingsService
from calendar_backend.services.goal import GoalService
from calendar_backend.services.master_horizon import MasterHorizonService
from calendar_backend.services.master_plan import MasterPlanService
from calendar_backend.services.plan_tree import PlanTreeService
from calendar_backend.services.repetition import RepetitionService
from sqlalchemy.orm import Session

from .conftest import FakeClock

RUN_AT = datetime(2026, 6, 7, 10, 0, tzinfo=UTC)
_START = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)


def _bootstrap_master_with_horizon(session: Session) -> PlanID:
    clock = FakeClock(RUN_AT)
    master = MasterPlanService(session, clock).ensure_master_exists()
    assert master.success and master.value is not None
    AppSettingsService(session, clock).get_settings()
    MasterHorizonService(session, clock).refresh_master_horizon(RUN_AT)
    return master.value.plan_id


@pytest.fixture
def master_plan_id(service_db_session: Session) -> PlanID:
    return _bootstrap_master_with_horizon(service_db_session)


def _goal_service(session: Session) -> GoalService:
    return GoalService(session, FakeClock(RUN_AT))


def _preview_service(session: Session) -> DeletionPreviewService:
    return DeletionPreviewService(session, FakeClock(RUN_AT))


def _plan_tree_service(session: Session) -> PlanTreeService:
    return PlanTreeService(session, FakeClock(RUN_AT))


def _repetition_service(session: Session) -> RepetitionService:
    return RepetitionService(session, FakeClock(RUN_AT))


def _create_goal_with_task(
    session: Session,
    master_plan_id: PlanID,
) -> tuple[PlanID, PlanID]:
    goal = _goal_service(session)
    parent = goal.create_child(
        master_plan_id,
        PlanKind.GOAL,
        GoalCreatePayload(name="parent"),
        is_critical=False,
    )
    assert parent.success and parent.value is not None
    child = goal.create_child(
        parent.value.plan_id,
        PlanKind.TASK,
        TaskCreatePayload("child", 30, False, None),
        is_critical=False,
    )
    assert child.success and child.value is not None
    return parent.value.plan_id, child.value.plan_id


@pytest.mark.integration
def test_preview_delete_plan_returns_full_deletion_preview_metadata(
    service_db_session: Session,
    master_plan_id: PlanID,
) -> None:
    _parent_id, task_id = _create_goal_with_task(service_db_session, master_plan_id)

    result = _preview_service(service_db_session).preview_delete_plan(task_id)

    assert result.success and result.value is not None
    preview = result.value
    assert preview.affected_task_ids == (task_id,)
    assert preview.affected_depth_counts_from_master == (0, 0, 1)
    assert preview.legal_operation.root_plan_id == task_id


@pytest.mark.integration
def test_preview_delete_plan_delegation_matches_plan_tree_affected_ids(
    service_db_session: Session,
    master_plan_id: PlanID,
) -> None:
    _parent_id, task_id = _create_goal_with_task(service_db_session, master_plan_id)

    preview_result = _preview_service(service_db_session).preview_delete_plan(task_id)
    plan_tree_result = _plan_tree_service(service_db_session).preview_delete(task_id)

    assert preview_result.success and preview_result.value is not None
    assert plan_tree_result.success and plan_tree_result.value is not None
    assert preview_result.value.affected_plan_ids == plan_tree_result.value.affected_plan_ids
    assert (
        preview_result.value.affected_calendar_entry_ids
        == plan_tree_result.value.affected_calendar_entry_ids
    )


@pytest.mark.integration
def test_preview_delete_plan_template_root_metadata(
    service_db_session: Session,
    master_plan_id: PlanID,
) -> None:
    repetition_payload = RepetitionCreatePayload(
        name="weekly",
        repeat_mode=RepeatMode.MANUAL_COUNT,
        start_time=_START,
        repeat_interval_minutes=60,
        manual_count=2,
        end_time=None,
        default_instance_critical=False,
        template_type=PlanKind.TASK,
        template_payload=TaskCreatePayload("template task", 30, False, None),
    )
    repetition_result = _goal_service(service_db_session).create_child(
        master_plan_id,
        PlanKind.REPETITION,
        repetition_payload,
        is_critical=False,
    )
    assert repetition_result.success and repetition_result.value is not None
    repetition_id = repetition_result.value.plan_id
    repetition = service_db_session.get(RepetitionPlan, repetition_id)
    assert repetition is not None
    template_task_id = PlanID(repetition.template_root_id)

    assert _repetition_service(service_db_session).generate_instances(repetition_id, RUN_AT).success

    result = _preview_service(service_db_session).preview_delete_plan(template_task_id)

    assert result.success and result.value is not None
    preview = result.value
    assert repetition_id in preview.affected_plan_ids
    assert template_task_id in preview.affected_plan_ids
    assert template_task_id in preview.affected_task_ids
    assert preview.affected_depth_counts_from_master[0] == 0
    assert sum(preview.affected_depth_counts_from_master) == len(preview.affected_plan_ids)

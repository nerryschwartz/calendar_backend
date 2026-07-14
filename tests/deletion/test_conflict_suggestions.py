"""Integration tests for ConflictDeletionSuggestionService."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from calendar_backend.deletion.conflict_suggestions import ConflictDeletionSuggestionService
from calendar_backend.deletion.preview_service import DeletionPreviewService
from calendar_backend.domain.deletion import AssignmentConflict, DeletionOperation
from calendar_backend.domain.enums import PlanKind, RepeatMode
from calendar_backend.domain.ids import PlanID
from calendar_backend.domain.plan_create import (
    GoalCreatePayload,
    RepetitionCreatePayload,
    TaskCreatePayload,
)
from calendar_backend.models.plans import Plan, RepetitionPlan
from calendar_backend.models.repetitions import RepetitionInstance
from calendar_backend.services.app_settings import AppSettingsService
from calendar_backend.services.goal import GoalService
from calendar_backend.services.master_horizon import MasterHorizonService
from calendar_backend.services.master_plan import MasterPlanService
from calendar_backend.services.repetition import RepetitionService
from sqlalchemy import func, select
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


def _suggestion_service(session: Session) -> ConflictDeletionSuggestionService:
    return ConflictDeletionSuggestionService(session, FakeClock(RUN_AT))


def _preview_service(session: Session) -> DeletionPreviewService:
    return DeletionPreviewService(session, FakeClock(RUN_AT))


def _repetition_service(session: Session) -> RepetitionService:
    return RepetitionService(session, FakeClock(RUN_AT))


def _repetition_payload(*, manual_count: int = 2) -> RepetitionCreatePayload:
    return RepetitionCreatePayload(
        name="weekly",
        repeat_mode=RepeatMode.MANUAL_COUNT,
        start_time=_START,
        repeat_interval_minutes=60,
        manual_count=manual_count,
        end_time=None,
        default_instance_critical=False,
        template_type=PlanKind.GOAL,
        template_payload=GoalCreatePayload(name="template"),
    )


def _create_repetition(session: Session, master_plan_id: PlanID) -> PlanID:
    result = _goal_service(session).create_child(
        master_plan_id,
        PlanKind.REPETITION,
        _repetition_payload(),
        is_critical=False,
    )
    assert result.success and result.value is not None
    return result.value.plan_id


def _generate_instances(session: Session, repetition_id: PlanID) -> None:
    assert _repetition_service(session).generate_instances(repetition_id, RUN_AT).success


def _setup_goal_repetition_with_task_child(
    session: Session,
    master_plan_id: PlanID,
) -> tuple[PlanID, PlanID, PlanID]:
    repetition_id = _create_repetition(session, master_plan_id)
    repetition = session.get(RepetitionPlan, repetition_id)
    assert repetition is not None
    template_goal_id = PlanID(repetition.template_root_id)
    child_result = _goal_service(session).create_child(
        template_goal_id,
        PlanKind.TASK,
        TaskCreatePayload("template task", 30, False, None),
        is_critical=False,
    )
    assert child_result.success and child_result.value is not None
    return repetition_id, template_goal_id, child_result.value.plan_id


def _instance_root_clone_id(
    session: Session,
    repetition_id: PlanID,
    instance_index: int,
) -> PlanID:
    instance = session.scalar(
        select(RepetitionInstance)
        .where(RepetitionInstance.repetition_plan_id == repetition_id)
        .where(RepetitionInstance.instance_index == instance_index)
    )
    assert instance is not None
    return PlanID(instance.root_clone_id)


def _clone_for_template(
    session: Session,
    *,
    parent_clone_id: PlanID,
    template_plan_id: PlanID,
) -> PlanID:
    clone = session.scalar(
        select(Plan).where(
            Plan.parent_id == parent_clone_id,
            Plan.cloned_from_id == template_plan_id,
        )
    )
    assert clone is not None
    return PlanID(clone.plan_id)


def _create_shallow_and_deep_tasks(
    session: Session,
    master_plan_id: PlanID,
) -> tuple[PlanID, PlanID, PlanID]:
    goal = _goal_service(session)
    parent = goal.create_child(
        master_plan_id,
        PlanKind.GOAL,
        GoalCreatePayload(name="parent"),
        is_critical=False,
    )
    assert parent.success and parent.value is not None
    deep_task = goal.create_child(
        parent.value.plan_id,
        PlanKind.TASK,
        TaskCreatePayload("deep", 30, False, None),
        is_critical=False,
    )
    shallow_task = goal.create_child(
        master_plan_id,
        PlanKind.TASK,
        TaskCreatePayload("shallow", 30, False, None),
        is_critical=False,
    )
    assert deep_task.success and deep_task.value is not None
    assert shallow_task.success and shallow_task.value is not None
    return parent.value.plan_id, deep_task.value.plan_id, shallow_task.value.plan_id


def _create_three_leaf_tasks(
    session: Session,
    master_plan_id: PlanID,
) -> tuple[PlanID, PlanID, PlanID]:
    goal = _goal_service(session)
    ids: list[PlanID] = []
    for name in ("alpha", "beta", "gamma"):
        created = goal.create_child(
            master_plan_id,
            PlanKind.TASK,
            TaskCreatePayload(name, 30, False, None),
            is_critical=False,
        )
        assert created.success and created.value is not None
        ids.append(created.value.plan_id)
    return ids[0], ids[1], ids[2]


@pytest.mark.integration
def test_suggest_for_conflict_prefers_shallow_impact_delete(
    service_db_session: Session,
    master_plan_id: PlanID,
) -> None:
    goal_id, deep_task_id, shallow_task_id = _create_shallow_and_deep_tasks(
        service_db_session,
        master_plan_id,
    )
    conflict = AssignmentConflict(
        conflicting_plan_ids=(goal_id, deep_task_id, shallow_task_id),
    )

    result = _suggestion_service(service_db_session).suggest_for_conflict(conflict)

    assert result.success and result.value is not None
    ranked_roots = [candidate.legal_operation.root_plan_id for candidate in result.value]
    assert deep_task_id in ranked_roots
    assert goal_id in ranked_roots
    assert shallow_task_id in ranked_roots
    assert ranked_roots.index(deep_task_id) < ranked_roots.index(goal_id)


@pytest.mark.integration
def test_suggest_for_conflict_priority_tie_break(
    service_db_session: Session,
    master_plan_id: PlanID,
) -> None:
    goal = _goal_service(service_db_session)
    high_priority = goal.create_child(
        master_plan_id,
        PlanKind.TASK,
        TaskCreatePayload("high", 30, False, None),
        is_critical=False,
    )
    low_priority = goal.create_child(
        master_plan_id,
        PlanKind.TASK,
        TaskCreatePayload("low", 30, False, None),
        is_critical=False,
    )
    assert high_priority.success and high_priority.value is not None
    assert low_priority.success and low_priority.value is not None
    high_id = high_priority.value.plan_id
    low_id = low_priority.value.plan_id

    conflict = AssignmentConflict(
        conflicting_plan_ids=(high_id, low_id),
        affected_priority_by_plan_id=((high_id, 10), (low_id, 2)),
    )

    result = _suggestion_service(service_db_session).suggest_for_conflict(conflict)

    assert result.success and result.value is not None
    assert len(result.value) == 2
    assert result.value[0].legal_operation.root_plan_id == low_id


@pytest.mark.integration
def test_suggest_for_conflict_orders_multiple_candidates_deterministically(
    service_db_session: Session,
    master_plan_id: PlanID,
) -> None:
    first_id, second_id, third_id = _create_three_leaf_tasks(service_db_session, master_plan_id)
    conflict = AssignmentConflict(
        conflicting_plan_ids=(third_id, first_id, second_id),
        affected_priority_by_plan_id=((first_id, 0), (second_id, 0), (third_id, 0)),
    )

    result = _suggestion_service(service_db_session).suggest_for_conflict(conflict)

    assert result.success and result.value is not None
    ranked_roots = [candidate.legal_operation.root_plan_id for candidate in result.value]
    assert ranked_roots == sorted(ranked_roots, key=str)


@pytest.mark.integration
def test_suggest_for_conflict_skips_master_and_not_found(
    service_db_session: Session,
    master_plan_id: PlanID,
) -> None:
    created = _goal_service(service_db_session).create_child(
        master_plan_id,
        PlanKind.TASK,
        TaskCreatePayload("valid", 30, False, None),
        is_critical=False,
    )
    assert created.success and created.value is not None
    valid_id = created.value.plan_id
    missing_id = PlanID(uuid.uuid4())
    plan_count_before = service_db_session.scalar(select(func.count()).select_from(Plan))

    conflict = AssignmentConflict(
        conflicting_plan_ids=(master_plan_id, missing_id, valid_id),
    )
    result = _suggestion_service(service_db_session).suggest_for_conflict(conflict)

    assert result.success and result.value is not None
    assert len(result.value) == 1
    assert result.value[0].legal_operation.root_plan_id == valid_id
    assert service_db_session.scalar(select(func.count()).select_from(Plan)) == plan_count_before


@pytest.mark.integration
def test_suggest_for_conflict_empty_when_no_legal_candidates(
    service_db_session: Session,
    master_plan_id: PlanID,
) -> None:
    conflict = AssignmentConflict(
        conflicting_plan_ids=(master_plan_id, PlanID(uuid.uuid4())),
    )

    result = _suggestion_service(service_db_session).suggest_for_conflict(conflict)

    assert result.success
    assert result.value == ()


@pytest.mark.integration
def test_suggest_for_conflict_preview_matches_deletion_preview_service(
    service_db_session: Session,
    master_plan_id: PlanID,
) -> None:
    goal_id, deep_task_id, _shallow_task_id = _create_shallow_and_deep_tasks(
        service_db_session,
        master_plan_id,
    )
    conflict = AssignmentConflict(conflicting_plan_ids=(goal_id, deep_task_id))
    suggestion_service = _suggestion_service(service_db_session)
    preview_service = _preview_service(service_db_session)

    result = suggestion_service.suggest_for_conflict(conflict)

    assert result.success and result.value is not None
    for candidate in result.value:
        preview_result = preview_service.preview_delete(candidate.legal_operation)
        assert preview_result.success and preview_result.value is not None
        assert candidate.deletion_preview == preview_result.value
        assert candidate.deletion_preview.legal_operation == DeletionOperation(
            root_plan_id=candidate.legal_operation.root_plan_id
        )


@pytest.mark.integration
def test_suggest_for_conflict_ranks_repetition_clone_before_template_root(
    service_db_session: Session,
    master_plan_id: PlanID,
) -> None:
    repetition_id, template_goal_id, template_task_id = _setup_goal_repetition_with_task_child(
        service_db_session,
        master_plan_id,
    )
    _generate_instances(service_db_session, repetition_id)
    task_clone_0 = _clone_for_template(
        service_db_session,
        parent_clone_id=_instance_root_clone_id(service_db_session, repetition_id, 0),
        template_plan_id=template_task_id,
    )
    task_clone_1 = _clone_for_template(
        service_db_session,
        parent_clone_id=_instance_root_clone_id(service_db_session, repetition_id, 1),
        template_plan_id=template_task_id,
    )
    conflict = AssignmentConflict(
        conflicting_plan_ids=(task_clone_1, template_goal_id, task_clone_0),
    )
    suggestion_service = _suggestion_service(service_db_session)
    preview_service = _preview_service(service_db_session)

    result = suggestion_service.suggest_for_conflict(conflict)

    assert result.success and result.value is not None
    ranked_roots = [candidate.legal_operation.root_plan_id for candidate in result.value]
    assert task_clone_0 in ranked_roots
    assert task_clone_1 in ranked_roots
    assert template_goal_id in ranked_roots
    assert ranked_roots.index(task_clone_0) < ranked_roots.index(template_goal_id)
    assert ranked_roots.index(task_clone_1) < ranked_roots.index(template_goal_id)

    template_candidate = next(
        candidate
        for candidate in result.value
        if candidate.legal_operation.root_plan_id == template_goal_id
    )
    assert repetition_id in template_candidate.deletion_preview.affected_plan_ids

    for candidate in result.value:
        preview_result = preview_service.preview_delete(candidate.legal_operation)
        assert preview_result.success and preview_result.value is not None
        assert candidate.deletion_preview == preview_result.value

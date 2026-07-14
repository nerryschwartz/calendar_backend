"""Integration tests for PlanTreeService rename, preview_delete, and delete_plan."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from calendar_backend.db.session import transaction
from calendar_backend.domain.enums import CalendarEntryType, CloneStatus, PlanKind, RepeatMode
from calendar_backend.domain.errors import MessageCode
from calendar_backend.domain.ids import CalendarEntryID, PlanID
from calendar_backend.domain.plan_create import (
    GoalCreatePayload,
    RepetitionCreatePayload,
    TaskCreatePayload,
)
from calendar_backend.models.calendar import CalendarEntry
from calendar_backend.models.chains import GoalChildChainItem
from calendar_backend.models.plans import Plan, RepetitionPlan
from calendar_backend.models.repetitions import RepetitionInstance
from calendar_backend.services.app_settings import AppSettingsService
from calendar_backend.services.goal import GoalService
from calendar_backend.services.master_horizon import MasterHorizonService
from calendar_backend.services.master_plan import MasterPlanService
from calendar_backend.services.plan_tree import PlanTreeService
from calendar_backend.services.plan_tree_invariant import PlanTreeInvariantService
from calendar_backend.services.repetition import RepetitionService
from calendar_backend.services.task import TaskService
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


def _plan_tree_service(session: Session) -> PlanTreeService:
    return PlanTreeService(session, FakeClock(RUN_AT))


def _repetition_service(session: Session) -> RepetitionService:
    return RepetitionService(session, FakeClock(RUN_AT))


def _task_service(session: Session) -> TaskService:
    return TaskService(session, FakeClock(RUN_AT))


def _all_plan_ids(session: Session) -> set[PlanID]:
    return {PlanID(plan_id) for plan_id in session.scalars(select(Plan.plan_id)).all()}


def _all_calendar_entry_ids(session: Session) -> set[CalendarEntryID]:
    return {
        CalendarEntryID(entry_id)
        for entry_id in session.scalars(select(CalendarEntry.calendar_entry_id)).all()
    }


def _assert_delete_matches_preview(
    session: Session,
    *,
    root_plan_id: PlanID,
    master_plan_id: PlanID,
) -> None:
    plan_tree = _plan_tree_service(session)
    plans_before = _all_plan_ids(session)
    entries_before = _all_calendar_entry_ids(session)

    preview = plan_tree.preview_delete(root_plan_id)
    assert preview.success and preview.value is not None
    affected_plans = set(preview.value.affected_plan_ids)
    affected_entries = set(preview.value.affected_calendar_entry_ids)

    delete = plan_tree.delete_plan(root_plan_id)
    assert delete.success

    for plan_id in affected_plans:
        assert session.get(Plan, plan_id) is None
    assert _all_plan_ids(session) == plans_before - affected_plans

    for entry_id in affected_entries:
        assert session.get(CalendarEntry, entry_id) is None
    assert _all_calendar_entry_ids(session) == entries_before - affected_entries

    assert session.get(Plan, master_plan_id) is not None
    _assert_tree_invariant(session)


def _repetition_payload(
    *,
    manual_count: int = 2,
    template_type: PlanKind = PlanKind.GOAL,
    template_payload: GoalCreatePayload | TaskCreatePayload | None = None,
) -> RepetitionCreatePayload:
    if template_payload is None:
        template_payload = GoalCreatePayload(name="template")
    return RepetitionCreatePayload(
        name="weekly",
        repeat_mode=RepeatMode.MANUAL_COUNT,
        start_time=_START,
        repeat_interval_minutes=60,
        manual_count=manual_count,
        end_time=None,
        default_instance_critical=False,
        template_type=template_type,
        template_payload=template_payload,
    )


def _create_repetition(
    session: Session, master_plan_id: PlanID, payload: RepetitionCreatePayload
) -> PlanID:
    result = _goal_service(session).create_child(
        master_plan_id,
        PlanKind.REPETITION,
        payload,
        is_critical=False,
    )
    assert result.success and result.value is not None
    return result.value.plan_id


def _generate_instances(session: Session, repetition_id: PlanID) -> None:
    assert _repetition_service(session).generate_instances(repetition_id, RUN_AT).success


def _setup_goal_repetition_with_task_child(
    session: Session,
    master_plan_id: PlanID,
    *,
    manual_count: int = 2,
) -> tuple[PlanID, PlanID, PlanID]:
    repetition_id = _create_repetition(
        session, master_plan_id, _repetition_payload(manual_count=manual_count)
    )
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


def _assert_tree_invariant(session: Session) -> None:
    result = PlanTreeInvariantService(session).validate_master_tree()
    assert result.success, result.errors


def _add_calendar_entry(session: Session, *, source_plan_id: PlanID) -> uuid.UUID:
    entry_id = uuid.uuid4()
    with transaction(session) as txn:
        txn.add(
            CalendarEntry(
                calendar_entry_id=entry_id,
                entry_type=CalendarEntryType.TASK,
                start_time=RUN_AT,
                end_time=RUN_AT + timedelta(hours=1),
                source_plan_id=source_plan_id,
                source_free_time_activity_id=None,
                calendar_run_id=None,
                display_label="task block",
                created_at=RUN_AT,
                updated_at=RUN_AT,
            )
        )
        txn.flush()
    return entry_id


def _two_tasks_same_chain(session: Session, master_plan_id: PlanID) -> tuple[PlanID, PlanID]:
    goal = _goal_service(session)
    first = goal.create_child(
        master_plan_id,
        PlanKind.TASK,
        TaskCreatePayload("first", 30, False, None),
        is_critical=False,
    )
    second = goal.create_child(
        master_plan_id,
        PlanKind.TASK,
        TaskCreatePayload("second", 30, False, None),
        is_critical=False,
    )
    assert first.success and first.value is not None
    assert second.success and second.value is not None
    assert goal.move_plan(second.value.plan_id, 0, 0).success
    return first.value.plan_id, second.value.plan_id


@pytest.mark.integration
def test_rename_plan_persists_name(service_db_session: Session, master_plan_id: PlanID) -> None:
    created = _goal_service(service_db_session).create_child(
        master_plan_id,
        PlanKind.TASK,
        TaskCreatePayload("old", 30, False, None),
        is_critical=False,
    )
    assert created.success and created.value is not None

    result = _plan_tree_service(service_db_session).rename_plan(created.value.plan_id, "new name")
    assert result.success

    plan = service_db_session.get(Plan, created.value.plan_id)
    assert plan is not None
    assert plan.name == "new name"
    _assert_tree_invariant(service_db_session)


@pytest.mark.integration
def test_rename_plan_master_forbidden(service_db_session: Session, master_plan_id: PlanID) -> None:
    result = _plan_tree_service(service_db_session).rename_plan(master_plan_id, "renamed")
    assert not result.success
    assert any(error.code == MessageCode.MASTER_MUTATION_FORBIDDEN for error in result.errors)


@pytest.mark.integration
def test_preview_delete_leaf_plan(service_db_session: Session, master_plan_id: PlanID) -> None:
    created = _goal_service(service_db_session).create_child(
        master_plan_id,
        PlanKind.TASK,
        TaskCreatePayload("leaf", 30, False, None),
        is_critical=False,
    )
    assert created.success and created.value is not None
    plan_id = created.value.plan_id

    preview = _plan_tree_service(service_db_session).preview_delete(plan_id)
    assert preview.success and preview.value is not None
    assert preview.value.affected_plan_ids == (plan_id,)
    assert preview.value.affected_calendar_entry_ids == ()


@pytest.mark.integration
def test_preview_delete_descendant_cascade(
    service_db_session: Session, master_plan_id: PlanID
) -> None:
    goal = _goal_service(service_db_session)
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

    preview = _plan_tree_service(service_db_session).preview_delete(parent.value.plan_id)
    assert preview.success and preview.value is not None
    assert set(preview.value.affected_plan_ids) == {
        parent.value.plan_id,
        child.value.plan_id,
    }


@pytest.mark.integration
def test_preview_delete_expands_whole_chain(
    service_db_session: Session, master_plan_id: PlanID
) -> None:
    first_id, second_id = _two_tasks_same_chain(service_db_session, master_plan_id)

    preview = _plan_tree_service(service_db_session).preview_delete(first_id)
    assert preview.success and preview.value is not None
    assert set(preview.value.affected_plan_ids) == {first_id, second_id}


@pytest.mark.integration
def test_preview_delete_critical_chain_includes_parent_goal(
    service_db_session: Session,
    master_plan_id: PlanID,
) -> None:
    goal = _goal_service(service_db_session)
    parent = goal.create_child(
        master_plan_id,
        PlanKind.GOAL,
        GoalCreatePayload(name="parent"),
        is_critical=False,
    )
    assert parent.success and parent.value is not None
    first = goal.create_child(
        parent.value.plan_id,
        PlanKind.TASK,
        TaskCreatePayload("first", 30, False, None),
        is_critical=True,
    )
    second = goal.create_child(
        parent.value.plan_id,
        PlanKind.TASK,
        TaskCreatePayload("second", 30, False, None),
        is_critical=True,
    )
    assert first.success and first.value is not None
    assert second.success and second.value is not None
    assert goal.move_plan(second.value.plan_id, 0, 0).success

    preview = _plan_tree_service(service_db_session).preview_delete(first.value.plan_id)
    assert preview.success and preview.value is not None
    assert set(preview.value.affected_plan_ids) == {
        parent.value.plan_id,
        first.value.plan_id,
        second.value.plan_id,
    }


@pytest.mark.integration
def test_preview_delete_collects_calendar_entries(
    service_db_session: Session,
    master_plan_id: PlanID,
) -> None:
    created = _goal_service(service_db_session).create_child(
        master_plan_id,
        PlanKind.TASK,
        TaskCreatePayload("task", 30, False, None),
        is_critical=False,
    )
    assert created.success and created.value is not None
    entry_id = _add_calendar_entry(service_db_session, source_plan_id=created.value.plan_id)

    preview = _plan_tree_service(service_db_session).preview_delete(created.value.plan_id)
    assert preview.success and preview.value is not None
    assert preview.value.affected_calendar_entry_ids == (CalendarEntryID(entry_id),)


@pytest.mark.integration
def test_preview_delete_master_forbidden(
    service_db_session: Session, master_plan_id: PlanID
) -> None:
    result = _plan_tree_service(service_db_session).preview_delete(master_plan_id)
    assert not result.success
    assert any(error.code == MessageCode.MASTER_DELETE_FORBIDDEN for error in result.errors)


@pytest.mark.integration
def test_preview_delete_plan_not_found(service_db_session: Session, master_plan_id: PlanID) -> None:
    result = _plan_tree_service(service_db_session).preview_delete(PlanID(uuid.uuid4()))
    assert not result.success
    assert any(error.code == MessageCode.PLAN_NOT_FOUND for error in result.errors)


@pytest.mark.integration
def test_delete_plan_matches_preview_and_removes_rows(
    service_db_session: Session,
    master_plan_id: PlanID,
) -> None:
    first_id, _second_id = _two_tasks_same_chain(service_db_session, master_plan_id)
    _add_calendar_entry(service_db_session, source_plan_id=first_id)

    _assert_delete_matches_preview(
        service_db_session,
        root_plan_id=first_id,
        master_plan_id=master_plan_id,
    )
    assert service_db_session.scalar(select(func.count()).select_from(GoalChildChainItem)) == 0


@pytest.mark.integration
def test_delete_plan_master_forbidden(service_db_session: Session, master_plan_id: PlanID) -> None:
    result = _plan_tree_service(service_db_session).delete_plan(master_plan_id)
    assert not result.success
    assert any(error.code == MessageCode.MASTER_DELETE_FORBIDDEN for error in result.errors)


@pytest.mark.integration
def test_delete_plan_subtree_removes_descendants(
    service_db_session: Session,
    master_plan_id: PlanID,
) -> None:
    goal = _goal_service(service_db_session)
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

    result = _plan_tree_service(service_db_session).delete_plan(parent.value.plan_id)
    assert result.success
    assert service_db_session.get(Plan, parent.value.plan_id) is None
    assert service_db_session.get(Plan, child.value.plan_id) is None
    _assert_tree_invariant(service_db_session)


@pytest.mark.integration
def test_delete_plan_parity_leaf(service_db_session: Session, master_plan_id: PlanID) -> None:
    created = _goal_service(service_db_session).create_child(
        master_plan_id,
        PlanKind.TASK,
        TaskCreatePayload("leaf", 30, False, None),
        is_critical=False,
    )
    assert created.success and created.value is not None
    _assert_delete_matches_preview(
        service_db_session,
        root_plan_id=created.value.plan_id,
        master_plan_id=master_plan_id,
    )


@pytest.mark.integration
def test_delete_plan_parity_descendant_subtree(
    service_db_session: Session,
    master_plan_id: PlanID,
) -> None:
    goal = _goal_service(service_db_session)
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

    _assert_delete_matches_preview(
        service_db_session,
        root_plan_id=parent.value.plan_id,
        master_plan_id=master_plan_id,
    )


@pytest.mark.integration
def test_delete_plan_parity_whole_chain(
    service_db_session: Session,
    master_plan_id: PlanID,
) -> None:
    first_id, _second_id = _two_tasks_same_chain(service_db_session, master_plan_id)
    _assert_delete_matches_preview(
        service_db_session,
        root_plan_id=first_id,
        master_plan_id=master_plan_id,
    )


@pytest.mark.integration
def test_delete_plan_parity_critical_chain(
    service_db_session: Session,
    master_plan_id: PlanID,
) -> None:
    goal = _goal_service(service_db_session)
    parent = goal.create_child(
        master_plan_id,
        PlanKind.GOAL,
        GoalCreatePayload(name="parent"),
        is_critical=False,
    )
    assert parent.success and parent.value is not None
    first = goal.create_child(
        parent.value.plan_id,
        PlanKind.TASK,
        TaskCreatePayload("first", 30, False, None),
        is_critical=True,
    )
    second = goal.create_child(
        parent.value.plan_id,
        PlanKind.TASK,
        TaskCreatePayload("second", 30, False, None),
        is_critical=True,
    )
    assert first.success and first.value is not None
    assert second.success and second.value is not None
    assert goal.move_plan(second.value.plan_id, 0, 0).success

    _assert_delete_matches_preview(
        service_db_session,
        root_plan_id=first.value.plan_id,
        master_plan_id=master_plan_id,
    )


@pytest.mark.integration
def test_delete_plan_parity_calendar_entries(
    service_db_session: Session,
    master_plan_id: PlanID,
) -> None:
    created = _goal_service(service_db_session).create_child(
        master_plan_id,
        PlanKind.TASK,
        TaskCreatePayload("task", 30, False, None),
        is_critical=False,
    )
    assert created.success and created.value is not None
    _add_calendar_entry(service_db_session, source_plan_id=created.value.plan_id)

    _assert_delete_matches_preview(
        service_db_session,
        root_plan_id=created.value.plan_id,
        master_plan_id=master_plan_id,
    )


@pytest.mark.integration
def test_preview_delete_template_root_includes_shell_and_instances(
    service_db_session: Session,
    master_plan_id: PlanID,
) -> None:
    repetition_id, template_goal_id, template_task_id = _setup_goal_repetition_with_task_child(
        service_db_session,
        master_plan_id,
    )
    _generate_instances(service_db_session, repetition_id)

    instance_root_ids = [
        _instance_root_clone_id(service_db_session, repetition_id, index) for index in range(2)
    ]
    instance_task_ids = [
        _clone_for_template(
            service_db_session,
            parent_clone_id=root_clone_id,
            template_plan_id=template_task_id,
        )
        for root_clone_id in instance_root_ids
    ]

    preview = _plan_tree_service(service_db_session).preview_delete(template_goal_id)
    assert preview.success and preview.value is not None
    affected = set(preview.value.affected_plan_ids)
    assert repetition_id in affected
    assert template_goal_id in affected
    assert template_task_id in affected
    assert set(instance_root_ids).issubset(affected)
    assert set(instance_task_ids).issubset(affected)


@pytest.mark.integration
def test_delete_plan_parity_template_root(
    service_db_session: Session,
    master_plan_id: PlanID,
) -> None:
    repetition_id, template_goal_id, _template_task_id = _setup_goal_repetition_with_task_child(
        service_db_session,
        master_plan_id,
    )
    _generate_instances(service_db_session, repetition_id)

    _assert_delete_matches_preview(
        service_db_session,
        root_plan_id=template_goal_id,
        master_plan_id=master_plan_id,
    )


@pytest.mark.integration
def test_preview_delete_task_template_root_includes_shell(
    service_db_session: Session,
    master_plan_id: PlanID,
) -> None:
    repetition_id = _create_repetition(
        service_db_session,
        master_plan_id,
        _repetition_payload(
            manual_count=2,
            template_type=PlanKind.TASK,
            template_payload=TaskCreatePayload("template task", 30, False, None),
        ),
    )
    repetition = service_db_session.get(RepetitionPlan, repetition_id)
    assert repetition is not None
    template_task_id = PlanID(repetition.template_root_id)
    _generate_instances(service_db_session, repetition_id)

    instance_root_ids = [
        _instance_root_clone_id(service_db_session, repetition_id, index) for index in range(2)
    ]

    preview = _plan_tree_service(service_db_session).preview_delete(template_task_id)
    assert preview.success and preview.value is not None
    affected = set(preview.value.affected_plan_ids)
    assert repetition_id in affected
    assert template_task_id in affected
    assert set(instance_root_ids).issubset(affected)


@pytest.mark.integration
def test_delete_plan_parity_task_template_root(
    service_db_session: Session,
    master_plan_id: PlanID,
) -> None:
    repetition_id = _create_repetition(
        service_db_session,
        master_plan_id,
        _repetition_payload(
            manual_count=2,
            template_type=PlanKind.TASK,
            template_payload=TaskCreatePayload("template task", 30, False, None),
        ),
    )
    repetition = service_db_session.get(RepetitionPlan, repetition_id)
    assert repetition is not None
    template_task_id = PlanID(repetition.template_root_id)
    _generate_instances(service_db_session, repetition_id)

    _assert_delete_matches_preview(
        service_db_session,
        root_plan_id=template_task_id,
        master_plan_id=master_plan_id,
    )


@pytest.mark.integration
def test_delete_plan_parity_calendar_entries_on_instance_clone(
    service_db_session: Session,
    master_plan_id: PlanID,
) -> None:
    repetition_id, _, template_task_id = _setup_goal_repetition_with_task_child(
        service_db_session,
        master_plan_id,
        manual_count=2,
    )
    _generate_instances(service_db_session, repetition_id)
    root_clone_0 = _instance_root_clone_id(service_db_session, repetition_id, 0)
    root_clone_1 = _instance_root_clone_id(service_db_session, repetition_id, 1)
    task_clone_0 = _clone_for_template(
        service_db_session,
        parent_clone_id=root_clone_0,
        template_plan_id=template_task_id,
    )
    task_clone_1 = _clone_for_template(
        service_db_session,
        parent_clone_id=root_clone_1,
        template_plan_id=template_task_id,
    )
    _add_calendar_entry(service_db_session, source_plan_id=task_clone_0)
    sibling_entry_id = _add_calendar_entry(service_db_session, source_plan_id=task_clone_1)

    _assert_delete_matches_preview(
        service_db_session,
        root_plan_id=task_clone_0,
        master_plan_id=master_plan_id,
    )
    assert service_db_session.get(CalendarEntry, sibling_entry_id) is not None
    assert service_db_session.get(Plan, repetition_id) is not None


@pytest.mark.integration
def test_delete_plan_parity_detached_clone(
    service_db_session: Session,
    master_plan_id: PlanID,
) -> None:
    repetition_id, template_goal_id, template_task_id = _setup_goal_repetition_with_task_child(
        service_db_session,
        master_plan_id,
        manual_count=2,
    )
    _generate_instances(service_db_session, repetition_id)
    root_clone_0 = _instance_root_clone_id(service_db_session, repetition_id, 0)
    root_clone_1 = _instance_root_clone_id(service_db_session, repetition_id, 1)
    detached_task_id = _clone_for_template(
        service_db_session,
        parent_clone_id=root_clone_0,
        template_plan_id=template_task_id,
    )
    linked_task_id = _clone_for_template(
        service_db_session,
        parent_clone_id=root_clone_1,
        template_plan_id=template_task_id,
    )
    assert (
        _task_service(service_db_session)
        .update_scheduling_fields(detached_task_id, 45, False, None)
        .success
    )
    detached_plan = service_db_session.get(Plan, detached_task_id)
    assert detached_plan is not None
    assert detached_plan.clone_status == CloneStatus.DETACHED

    preview = _plan_tree_service(service_db_session).preview_delete(detached_task_id)
    assert preview.success and preview.value is not None
    affected = set(preview.value.affected_plan_ids)
    assert affected == {detached_task_id}
    assert linked_task_id not in affected
    assert repetition_id not in affected
    assert template_goal_id not in affected

    _assert_delete_matches_preview(
        service_db_session,
        root_plan_id=detached_task_id,
        master_plan_id=master_plan_id,
    )
    assert service_db_session.get(Plan, linked_task_id) is not None
    assert service_db_session.get(Plan, repetition_id) is not None
    assert service_db_session.get(Plan, template_goal_id) is not None

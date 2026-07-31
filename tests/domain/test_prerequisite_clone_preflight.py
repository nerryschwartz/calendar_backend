"""Pure tests for refresh preflight when prerequisite clones are missing."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from calendar_backend.domain.enums import CloneStatus, PlanKind
from calendar_backend.domain.errors import MessageCode
from calendar_backend.domain.ids import PlanID
from calendar_backend.domain.prerequisites import (
    find_missing_prerequisite_clone_targets,
    validate_prerequisite_clones_for_refresh,
)
from calendar_backend.models.plans import GoalPlan, Plan, TaskPlan
from calendar_backend.models.prerequisites import PlanPrerequisite

_NOW = datetime(2026, 6, 7, 12, 0, tzinfo=UTC)


def _plan_id(label: str) -> PlanID:
    return PlanID(uuid.uuid5(uuid.NAMESPACE_DNS, label))


def _goal(plan_id: PlanID, *, parent_id: PlanID | None = None) -> Plan:
    plan = Plan(
        plan_id=plan_id,
        plan_kind=PlanKind.GOAL,
        name=str(plan_id),
        parent_id=parent_id,
        is_master=False,
        cloned_from_id=None,
        clone_status=CloneStatus.NOT_CLONED,
        created_at=_NOW,
        updated_at=_NOW,
    )
    plan.goal_plan = GoalPlan(plan_id=plan_id)
    return plan


def _task(
    plan_id: PlanID,
    *,
    parent_id: PlanID | None = None,
    clone_status: CloneStatus = CloneStatus.NOT_CLONED,
    cloned_from_id: PlanID | None = None,
) -> Plan:
    plan = Plan(
        plan_id=plan_id,
        plan_kind=PlanKind.TASK,
        name=str(plan_id),
        parent_id=parent_id,
        is_master=False,
        cloned_from_id=cloned_from_id,
        clone_status=clone_status,
        created_at=_NOW,
        updated_at=_NOW,
    )
    plan.task_plan = TaskPlan(
        plan_id=plan_id,
        duration_minutes=30,
        divisible=False,
        minimum_chunk_size_minutes=None,
        user_completed=False,
        completed_at=None,
        immediate_prerequisite_plan_id=None,
    )
    return plan


def _edge(dependent_id: PlanID, prerequisite_id: PlanID) -> PlanPrerequisite:
    return PlanPrerequisite(
        plan_id=dependent_id,
        prerequisite_plan_id=prerequisite_id,
    )


def test_find_missing_prerequisite_clone_targets_empty_for_master_tree_edge() -> None:
    master = _goal(_plan_id("master"), parent_id=None)
    master.is_master = True
    task_a = _task(_plan_id("task-a"), parent_id=PlanID(master.plan_id))
    task_b = _task(_plan_id("task-b"), parent_id=PlanID(master.plan_id))
    task_a.prerequisite_edges = [_edge(PlanID(task_a.plan_id), PlanID(task_b.plan_id))]

    missing = find_missing_prerequisite_clone_targets((master, task_a, task_b))

    assert missing == ()


def test_find_missing_prerequisite_clone_targets_flags_template_prereq_from_clone() -> None:
    template_goal = _goal(_plan_id("template-goal"))
    template_goal.clone_status = CloneStatus.TEMPLATE
    template_prereq = _task(_plan_id("template-prereq"), parent_id=PlanID(template_goal.plan_id))
    template_prereq.clone_status = CloneStatus.TEMPLATE
    template_dependent = _task(
        _plan_id("template-dependent"),
        parent_id=PlanID(template_goal.plan_id),
    )
    template_dependent.clone_status = CloneStatus.TEMPLATE
    template_dependent.prerequisite_edges = [
        _edge(PlanID(template_dependent.plan_id), PlanID(template_prereq.plan_id))
    ]

    clone_dependent = _task(
        _plan_id("clone-dependent"),
        parent_id=_plan_id("instance-root"),
        clone_status=CloneStatus.LINKED,
        cloned_from_id=PlanID(template_dependent.plan_id),
    )
    clone_dependent.prerequisite_edges = [
        _edge(PlanID(clone_dependent.plan_id), PlanID(template_prereq.plan_id))
    ]

    missing = find_missing_prerequisite_clone_targets(
        (template_goal, template_prereq, template_dependent, clone_dependent)
    )

    assert missing == (PlanID(template_prereq.plan_id),)


def test_validate_prerequisite_clones_for_refresh_returns_service_message() -> None:
    template_task = _task(_plan_id("template-task"))
    template_task.clone_status = CloneStatus.TEMPLATE
    clone_task = _task(
        _plan_id("clone-task"),
        clone_status=CloneStatus.LINKED,
        cloned_from_id=PlanID(_plan_id("other-template")),
    )
    clone_task.prerequisite_edges = [
        _edge(PlanID(clone_task.plan_id), PlanID(template_task.plan_id))
    ]

    result = validate_prerequisite_clones_for_refresh((template_task, clone_task))

    assert result is not None
    assert result.code == MessageCode.PREREQUISITE_CLONES_NOT_GENERATED

"""Pure tests for template trace computation."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import calendar_backend.models.constraints  # pyright: ignore[reportUnusedImport]
import calendar_backend.models.prerequisites  # pyright: ignore[reportUnusedImport]
import calendar_backend.models.repetitions  # noqa: F401  # pyright: ignore[reportUnusedImport]
from calendar_backend.domain.enums import CloneStatus, PlanKind, RepeatMode
from calendar_backend.domain.ids import PlanID
from calendar_backend.domain.template_trace import (
    TemplateTraceStep,
    compute_template_trace,
    traces_match,
)
from calendar_backend.models.plans import GoalPlan, Plan, RepetitionPlan, TaskPlan

_NOW = datetime(2026, 6, 7, 12, 0, tzinfo=UTC)


def _plan(
    plan_id: uuid.UUID,
    *,
    plan_kind: PlanKind = PlanKind.TASK,
    parent_id: uuid.UUID | None = None,
    is_master: bool = False,
    cloned_from_id: uuid.UUID | None = None,
    clone_status: CloneStatus = CloneStatus.NOT_CLONED,
) -> Plan:
    return Plan(
        plan_id=plan_id,
        plan_kind=plan_kind,
        name="plan",
        parent_id=parent_id,
        is_master=is_master,
        cloned_from_id=cloned_from_id,
        clone_status=clone_status,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _attach_goal(plan: Plan) -> None:
    plan.goal_plan = GoalPlan(plan_id=plan.plan_id)


def _attach_task(plan: Plan) -> None:
    plan.task_plan = TaskPlan(
        plan_id=plan.plan_id,
        duration_minutes=30,
        divisible=False,
        minimum_chunk_size_minutes=None,
        user_completed=False,
        completed_at=None,
        immediate_prerequisite_plan_id=None,
    )


def _attach_repetition(
    plan: Plan,
    template_root_id: uuid.UUID,
    *,
    repeat_interval_minutes: int = 60,
) -> RepetitionPlan:
    repetition_plan = RepetitionPlan(
        plan_id=plan.plan_id,
        repeat_mode=RepeatMode.MANUAL_COUNT,
        start_time=_NOW,
        repeat_interval_minutes=repeat_interval_minutes,
        manual_count=1,
        end_time=None,
        template_root_id=template_root_id,
        default_instance_critical=False,
        generated_at=_NOW,
    )
    plan.repetition_plan = repetition_plan
    return repetition_plan


def _plans_by_id(*plans: Plan) -> dict[uuid.UUID, Plan]:
    return {plan.plan_id: plan for plan in plans}


def test_master_only_plan_has_empty_trace() -> None:
    master_id = uuid.uuid4()
    master = _plan(master_id, plan_kind=PlanKind.GOAL, is_master=True)
    _attach_goal(master)

    assert compute_template_trace(master_id, _plans_by_id(master)) == ()


def test_repetition_template_and_clone_share_trace() -> None:
    master_id = uuid.uuid4()
    repetition_id = uuid.uuid4()
    template_id = uuid.uuid4()
    template_task_id = uuid.uuid4()
    clone_id = uuid.uuid4()
    clone_task_id = uuid.uuid4()

    master = _plan(master_id, plan_kind=PlanKind.GOAL, is_master=True)
    _attach_goal(master)
    repetition = _plan(repetition_id, plan_kind=PlanKind.REPETITION, parent_id=master_id)
    _attach_repetition(repetition, template_id, repeat_interval_minutes=90)
    template = _plan(
        template_id,
        plan_kind=PlanKind.GOAL,
        parent_id=repetition_id,
        clone_status=CloneStatus.TEMPLATE,
    )
    _attach_goal(template)
    template_task = _plan(
        template_task_id,
        plan_kind=PlanKind.TASK,
        parent_id=template_id,
        clone_status=CloneStatus.TEMPLATE,
    )
    _attach_task(template_task)
    clone = _plan(
        clone_id,
        plan_kind=PlanKind.GOAL,
        parent_id=repetition_id,
        cloned_from_id=template_id,
        clone_status=CloneStatus.LINKED,
    )
    _attach_goal(clone)
    clone_task = _plan(
        clone_task_id,
        plan_kind=PlanKind.TASK,
        parent_id=clone_id,
        cloned_from_id=template_task_id,
        clone_status=CloneStatus.LINKED,
    )
    _attach_task(clone_task)

    plans = _plans_by_id(
        master,
        repetition,
        template,
        template_task,
        clone,
        clone_task,
    )
    expected = (
        TemplateTraceStep(repetition_plan_id=PlanID(repetition_id), repeat_interval_minutes=90),
    )

    assert compute_template_trace(template_task_id, plans) == expected
    assert compute_template_trace(clone_task_id, plans) == expected
    assert traces_match(
        compute_template_trace(template_task_id, plans),
        compute_template_trace(clone_task_id, plans),
    )


def test_nested_repetitions_accumulate_outer_then_inner_trace_order() -> None:
    master_id = uuid.uuid4()
    outer_repetition_id = uuid.uuid4()
    outer_template_id = uuid.uuid4()
    inner_repetition_id = uuid.uuid4()
    inner_template_id = uuid.uuid4()
    inner_task_id = uuid.uuid4()

    master = _plan(master_id, plan_kind=PlanKind.GOAL, is_master=True)
    _attach_goal(master)
    outer_repetition = _plan(
        outer_repetition_id,
        plan_kind=PlanKind.REPETITION,
        parent_id=master_id,
    )
    _attach_repetition(outer_repetition, outer_template_id, repeat_interval_minutes=120)
    outer_template = _plan(
        outer_template_id,
        plan_kind=PlanKind.GOAL,
        parent_id=outer_repetition_id,
        clone_status=CloneStatus.TEMPLATE,
    )
    _attach_goal(outer_template)
    inner_repetition = _plan(
        inner_repetition_id,
        plan_kind=PlanKind.REPETITION,
        parent_id=outer_template_id,
    )
    _attach_repetition(inner_repetition, inner_template_id, repeat_interval_minutes=30)
    inner_template = _plan(
        inner_template_id,
        plan_kind=PlanKind.TASK,
        parent_id=inner_repetition_id,
        clone_status=CloneStatus.TEMPLATE,
    )
    _attach_task(inner_template)
    inner_task = _plan(
        inner_task_id,
        plan_kind=PlanKind.TASK,
        parent_id=inner_template_id,
        cloned_from_id=inner_template_id,
        clone_status=CloneStatus.LINKED,
    )
    _attach_task(inner_task)

    plans = _plans_by_id(
        master,
        outer_repetition,
        outer_template,
        inner_repetition,
        inner_template,
        inner_task,
    )

    assert compute_template_trace(inner_task_id, plans) == (
        TemplateTraceStep(
            repetition_plan_id=PlanID(inner_repetition_id),
            repeat_interval_minutes=30,
        ),
        TemplateTraceStep(
            repetition_plan_id=PlanID(outer_repetition_id),
            repeat_interval_minutes=120,
        ),
    )


def test_traces_do_not_match_when_repeat_interval_differs() -> None:
    master_id = uuid.uuid4()
    repetition_a_id = uuid.uuid4()
    repetition_b_id = uuid.uuid4()
    template_a_id = uuid.uuid4()
    template_b_id = uuid.uuid4()
    task_a_id = uuid.uuid4()
    task_b_id = uuid.uuid4()

    master = _plan(master_id, plan_kind=PlanKind.GOAL, is_master=True)
    _attach_goal(master)

    repetition_a = _plan(repetition_a_id, plan_kind=PlanKind.REPETITION, parent_id=master_id)
    _attach_repetition(repetition_a, template_a_id, repeat_interval_minutes=60)
    template_a = _plan(
        template_a_id,
        plan_kind=PlanKind.TASK,
        parent_id=repetition_a_id,
        clone_status=CloneStatus.TEMPLATE,
    )
    _attach_task(template_a)
    task_a = _plan(task_a_id, plan_kind=PlanKind.TASK, parent_id=template_a_id)
    _attach_task(task_a)

    repetition_b = _plan(repetition_b_id, plan_kind=PlanKind.REPETITION, parent_id=master_id)
    _attach_repetition(repetition_b, template_b_id, repeat_interval_minutes=90)
    template_b = _plan(
        template_b_id,
        plan_kind=PlanKind.TASK,
        parent_id=repetition_b_id,
        clone_status=CloneStatus.TEMPLATE,
    )
    _attach_task(template_b)
    task_b = _plan(task_b_id, plan_kind=PlanKind.TASK, parent_id=template_b_id)
    _attach_task(task_b)

    plans = _plans_by_id(
        master,
        repetition_a,
        template_a,
        task_a,
        repetition_b,
        template_b,
        task_b,
    )

    trace_a = compute_template_trace(task_a_id, plans)
    trace_b = compute_template_trace(task_b_id, plans)

    assert trace_a != trace_b
    assert traces_match(trace_a, trace_a) is True
    assert traces_match(trace_a, trace_b) is False


def test_traces_do_not_match_when_repetition_shell_differs() -> None:
    master_id = uuid.uuid4()
    repetition_a_id = uuid.uuid4()
    repetition_b_id = uuid.uuid4()
    template_a_id = uuid.uuid4()
    template_b_id = uuid.uuid4()
    task_a_id = uuid.uuid4()
    task_b_id = uuid.uuid4()

    master = _plan(master_id, plan_kind=PlanKind.GOAL, is_master=True)
    _attach_goal(master)

    repetition_a = _plan(repetition_a_id, plan_kind=PlanKind.REPETITION, parent_id=master_id)
    _attach_repetition(repetition_a, template_a_id, repeat_interval_minutes=60)
    template_a = _plan(
        template_a_id,
        plan_kind=PlanKind.TASK,
        parent_id=repetition_a_id,
        clone_status=CloneStatus.TEMPLATE,
    )
    _attach_task(template_a)
    task_a = _plan(task_a_id, plan_kind=PlanKind.TASK, parent_id=template_a_id)
    _attach_task(task_a)

    repetition_b = _plan(repetition_b_id, plan_kind=PlanKind.REPETITION, parent_id=master_id)
    _attach_repetition(repetition_b, template_b_id, repeat_interval_minutes=60)
    template_b = _plan(
        template_b_id,
        plan_kind=PlanKind.TASK,
        parent_id=repetition_b_id,
        clone_status=CloneStatus.TEMPLATE,
    )
    _attach_task(template_b)
    task_b = _plan(task_b_id, plan_kind=PlanKind.TASK, parent_id=template_b_id)
    _attach_task(task_b)

    plans = _plans_by_id(
        master,
        repetition_a,
        template_a,
        task_a,
        repetition_b,
        template_b,
        task_b,
    )

    assert not traces_match(
        compute_template_trace(task_a_id, plans),
        compute_template_trace(task_b_id, plans),
    )

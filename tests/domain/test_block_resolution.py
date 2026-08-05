"""Pure tests for block resolution helpers."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from calendar_backend.domain.block_resolution import (
    ResolveBlocksResult,
    ResolvedBlock,
    is_invalid_block,
    is_invalid_incomplete_block,
    resolve_blocks_from_graph,
    validate_resolve_blocks_result,
)
from calendar_backend.domain.enums import CloneStatus, ConstraintKind, PlanKind
from calendar_backend.domain.errors import MessageCode, ServiceMessage
from calendar_backend.domain.ids import PlanID
from calendar_backend.domain.resolution import (
    ResolvedPrecedenceConstraint,
    build_resolution_indexes,
    collect_precedence_constraints,
    resolve_tasks_from_graph,
)
from calendar_backend.models.blocks import BlockPlan
from calendar_backend.models.constraints import TimeConstraintGroup
from calendar_backend.models.constraints import TimeWindow as OrmTimeWindow
from calendar_backend.models.plans import GoalPlan, Plan, TaskPlan
from calendar_backend.models.prerequisites import PlanPrerequisite

_NOW = datetime(2026, 6, 7, 12, 0, tzinfo=UTC)
_RUN_AT = datetime(2026, 6, 7, 10, 0, tzinfo=UTC)


def _utc(h: int, mi: int = 0) -> datetime:
    return datetime(2026, 6, 7, h, mi, tzinfo=UTC)


def _plan(
    plan_id: uuid.UUID,
    *,
    plan_kind: PlanKind,
    is_master: bool = False,
    parent_id: uuid.UUID | None = None,
    name: str = "plan",
) -> Plan:
    return Plan(
        plan_id=plan_id,
        plan_kind=plan_kind,
        name=name,
        parent_id=parent_id,
        is_master=is_master,
        cloned_from_id=None,
        clone_status=CloneStatus.NOT_CLONED,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _attach_goal(plan: Plan) -> None:
    plan.goal_plan = GoalPlan(plan_id=plan.plan_id)


def _attach_block(
    plan: Plan,
    *,
    duration_minutes: int = 30,
    user_completed: bool = False,
    block_family: str = "focus",
    immediate_prerequisite_plan_id: uuid.UUID | None = None,
) -> None:
    plan.block_plan = BlockPlan(
        plan_id=plan.plan_id,
        duration_minutes=duration_minutes,
        divisible=False,
        minimum_chunk_size_minutes=None,
        user_completed=user_completed,
        completed_at=_NOW if user_completed else None,
        block_family=block_family,
        immediate_prerequisite_plan_id=immediate_prerequisite_plan_id,
    )


def _attach_task(
    plan: Plan,
    *,
    duration_minutes: int = 30,
    user_completed: bool = False,
    immediate_prerequisite_plan_id: uuid.UUID | None = None,
) -> None:
    plan.task_plan = TaskPlan(
        plan_id=plan.plan_id,
        duration_minutes=duration_minutes,
        divisible=False,
        minimum_chunk_size_minutes=None,
        user_completed=user_completed,
        completed_at=_NOW if user_completed else None,
        immediate_prerequisite_plan_id=immediate_prerequisite_plan_id,
    )


def _horizon_group(plan_id: uuid.UUID, start: datetime, end: datetime) -> TimeConstraintGroup:
    group_id = uuid.uuid4()
    group = TimeConstraintGroup(
        time_constraint_group_id=group_id,
        plan_id=plan_id,
        constraint_kind=ConstraintKind.SYSTEM_MASTER_HORIZON,
    )
    group.windows = [
        OrmTimeWindow(
            time_window_id=uuid.uuid4(),
            group_id=group_id,
            start_time=start,
            end_time=end,
        )
    ]
    return group


def _attach_ordered_child(
    parent: Plan,
    child: Plan,
    *,
    is_critical: bool = False,
    sort_order: int = 0,
) -> None:
    child.parent_id = parent.plan_id
    child.goal_is_critical = is_critical
    child.goal_sort_order = sort_order


def _all_blocks(result: ResolveBlocksResult) -> tuple[ResolvedBlock, ...]:
    return (
        *result.valid_incomplete,
        *result.valid_completed,
        *result.invalid_incomplete,
        *result.invalid_completed,
    )


def _single_block_graph(*, duration_minutes: int = 30) -> tuple[Plan, ...]:
    master_id = uuid.uuid4()
    block_id = uuid.uuid4()
    master = _plan(master_id, plan_kind=PlanKind.GOAL, is_master=True)
    _attach_goal(master)
    master.constraint_groups = [_horizon_group(master_id, _utc(8, 0), _utc(18, 0))]
    block = _plan(block_id, plan_kind=PlanKind.BLOCK, parent_id=master_id, name="focus block")
    _attach_block(block, duration_minutes=duration_minutes)
    _attach_ordered_child(master, block, is_critical=False, sort_order=0)
    return (master, block)


def test_resolve_blocks_from_graph_emits_valid_incomplete_block() -> None:
    result = resolve_blocks_from_graph(_RUN_AT, _single_block_graph())

    assert len(result.valid_incomplete) == 1
    block = result.valid_incomplete[0]
    assert block.name == "focus block"
    assert block.block_family == "focus"
    assert block.duration_minutes == 30
    assert block.effective_time_windows


def test_resolve_blocks_from_graph_excludes_tasks() -> None:
    master_id = uuid.uuid4()
    task_id = uuid.uuid4()
    master = _plan(master_id, plan_kind=PlanKind.GOAL, is_master=True)
    _attach_goal(master)
    master.constraint_groups = [_horizon_group(master_id, _utc(8, 0), _utc(18, 0))]
    task = _plan(task_id, plan_kind=PlanKind.TASK, parent_id=master_id)
    _attach_task(task)
    _attach_ordered_child(master, task)

    result = resolve_blocks_from_graph(_RUN_AT, (master, task))

    assert _all_blocks(result) == ()


def test_resolve_blocks_from_graph_invalid_duration_goes_to_invalid_incomplete() -> None:
    result = resolve_blocks_from_graph(_RUN_AT, _single_block_graph(duration_minutes=0))

    assert result.valid_incomplete == ()
    assert len(result.invalid_incomplete) == 1
    assert is_invalid_incomplete_block(result.invalid_incomplete[0])
    assert result.invalid_incomplete[0].validation_errors[0].code == MessageCode.INVALID_DURATION


def test_validate_resolve_blocks_result_rejects_duplicate_plan_ids() -> None:
    block = _single_block_graph()[1]
    duplicate = ResolvedBlock(
        plan_id=PlanID(block.plan_id),
        name="dup",
        duration_minutes=30,
        divisible=False,
        minimum_chunk_size_minutes=None,
        block_family="focus",
        user_completed=False,
        completed_at=None,
        effective_time_windows=(),
        constraint_sources=(),
        priority_path=(0,),
        criticality_path=(),
        parent_path=(PlanID(block.plan_id),),
        validation_errors=(),
    )
    result = ResolveBlocksResult(
        run_started_at=_RUN_AT,
        valid_incomplete=(duplicate, duplicate),
        valid_completed=(),
        invalid_incomplete=(),
        invalid_completed=(),
        precedence_constraints=(),
        warnings=(),
    )

    with pytest.raises(ValueError, match="multiple resolution buckets"):
        validate_resolve_blocks_result(result)


def test_collect_precedence_constraints_emits_block_immediate_edge() -> None:
    master_id = uuid.uuid4()
    predecessor_id = uuid.uuid4()
    successor_id = uuid.uuid4()

    master = _plan(master_id, plan_kind=PlanKind.GOAL, is_master=True)
    _attach_goal(master)
    master.constraint_groups = [_horizon_group(master_id, _utc(8, 0), _utc(18, 0))]

    predecessor = _plan(predecessor_id, plan_kind=PlanKind.BLOCK, parent_id=master_id, name="a")
    _attach_block(predecessor)
    successor = _plan(successor_id, plan_kind=PlanKind.BLOCK, parent_id=master_id, name="b")
    _attach_block(successor, immediate_prerequisite_plan_id=predecessor_id)

    _attach_ordered_child(master, predecessor, sort_order=0)
    _attach_ordered_child(master, successor, sort_order=1)

    plans = (master, predecessor, successor)
    indexes = build_resolution_indexes(plans)
    edges = collect_precedence_constraints(plans, indexes, invalid_leaf_ids=frozenset())

    assert edges == (
        ResolvedPrecedenceConstraint(
            predecessor_task_id=PlanID(predecessor_id),
            successor_task_id=PlanID(successor_id),
            reason="immediate_prerequisite",
        ),
    )


def test_collect_precedence_constraints_emits_task_to_block_plan_prerequisite() -> None:
    master_id = uuid.uuid4()
    prereq_task_id = uuid.uuid4()
    dependent_block_id = uuid.uuid4()

    master = _plan(master_id, plan_kind=PlanKind.GOAL, is_master=True)
    _attach_goal(master)
    master.constraint_groups = [_horizon_group(master_id, _utc(8, 0), _utc(18, 0))]

    prereq_task = _plan(prereq_task_id, plan_kind=PlanKind.TASK, parent_id=master_id)
    _attach_task(prereq_task)
    dependent_block = _plan(
        dependent_block_id,
        plan_kind=PlanKind.BLOCK,
        parent_id=master_id,
    )
    _attach_block(dependent_block)
    dependent_block.prerequisite_edges = [
        PlanPrerequisite(plan_id=dependent_block_id, prerequisite_plan_id=prereq_task_id)
    ]

    _attach_ordered_child(master, prereq_task, sort_order=0)
    _attach_ordered_child(master, dependent_block, sort_order=1)

    plans = (master, prereq_task, dependent_block)
    indexes = build_resolution_indexes(plans)
    edges = collect_precedence_constraints(plans, indexes, invalid_leaf_ids=frozenset())

    assert edges == (
        ResolvedPrecedenceConstraint(
            predecessor_task_id=PlanID(prereq_task_id),
            successor_task_id=PlanID(dependent_block_id),
            reason="plan_prerequisite",
        ),
    )


def test_resolve_blocks_from_graph_includes_precedence_constraints() -> None:
    master_id = uuid.uuid4()
    predecessor_id = uuid.uuid4()
    successor_id = uuid.uuid4()

    master = _plan(master_id, plan_kind=PlanKind.GOAL, is_master=True)
    _attach_goal(master)
    master.constraint_groups = [_horizon_group(master_id, _utc(8, 0), _utc(18, 0))]

    predecessor = _plan(predecessor_id, plan_kind=PlanKind.BLOCK, parent_id=master_id)
    _attach_block(predecessor)
    successor = _plan(successor_id, plan_kind=PlanKind.BLOCK, parent_id=master_id)
    _attach_block(successor, immediate_prerequisite_plan_id=predecessor_id)
    _attach_ordered_child(master, predecessor, sort_order=0)
    _attach_ordered_child(master, successor, sort_order=1)

    result = resolve_blocks_from_graph(_RUN_AT, (master, predecessor, successor))

    assert len(result.precedence_constraints) == 1
    assert result.precedence_constraints[0].reason == "immediate_prerequisite"


def test_task_resolution_still_excludes_blocks_from_buckets() -> None:
    result = resolve_tasks_from_graph(_RUN_AT, _single_block_graph())

    assert result.valid_incomplete == ()


def test_is_invalid_block() -> None:
    block = ResolvedBlock(
        plan_id=PlanID(uuid.uuid4()),
        name="b",
        duration_minutes=30,
        divisible=False,
        minimum_chunk_size_minutes=None,
        block_family="focus",
        user_completed=False,
        completed_at=None,
        effective_time_windows=(),
        constraint_sources=(),
        priority_path=(0,),
        criticality_path=(),
        parent_path=(),
        validation_errors=(
            ServiceMessage(code=MessageCode.INVALID_DURATION, message="bad", details={}),
        ),
    )
    assert is_invalid_block(block)

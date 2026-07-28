"""Frozen DTOs and pure block-resolution helpers per design §8.2 / §11."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime

from calendar_backend.domain.enums import PlanKind
from calendar_backend.domain.errors import MessageCode, ServiceMessage
from calendar_backend.domain.ids import PlanID
from calendar_backend.domain.plan_traversal import (
    ordered_goal_children,
    ordered_repetition_instances,
)
from calendar_backend.domain.resolution import (
    ConstraintSource,
    ResolutionIndexes,
    ResolvedPrecedenceConstraint,
    build_resolution_indexes,
    collect_precedence_constraints,
    compute_effective_constraints,
    constraint_errors_for_plan,
)
from calendar_backend.domain.time import TimeWindow
from calendar_backend.models.plans import Plan


@dataclass(frozen=True)
class ResolvedBlock:
    plan_id: PlanID
    name: str
    duration_minutes: int
    divisible: bool
    minimum_chunk_size_minutes: int | None
    block_family: str
    user_completed: bool
    completed_at: datetime | None
    effective_time_windows: tuple[TimeWindow, ...]
    constraint_sources: tuple[ConstraintSource, ...]
    priority_path: tuple[int, ...]
    criticality_path: tuple[bool, ...]
    parent_path: tuple[PlanID, ...]
    validation_errors: tuple[ServiceMessage, ...]


@dataclass(frozen=True)
class ResolveBlocksResult:
    run_started_at: datetime
    valid_incomplete: tuple[ResolvedBlock, ...]
    valid_completed: tuple[ResolvedBlock, ...]
    invalid_incomplete: tuple[ResolvedBlock, ...]
    invalid_completed: tuple[ResolvedBlock, ...]
    precedence_constraints: tuple[ResolvedPrecedenceConstraint, ...]
    warnings: tuple[ServiceMessage, ...]


def resolve_blocks_from_graph(
    run_started_at: datetime,
    plans: tuple[Plan, ...],
) -> ResolveBlocksResult:
    indexes = build_resolution_indexes(plans)
    collector = _BlockCollector(indexes=indexes)
    collector.traverse_goal_children(
        indexes.master_plan_id,
        parent_path=(indexes.master_plan_id,),
        criticality_path=(),
        inherited_errors=(),
    )
    enriched_blocks = _apply_effective_constraints(collector.blocks, indexes)
    precedence_constraints = collect_precedence_constraints(
        plans,
        indexes,
        invalid_leaf_ids=frozenset(
            block.plan_id for block in enriched_blocks if is_invalid_block(block)
        ),
    )
    (
        valid_incomplete,
        valid_completed,
        invalid_incomplete,
        invalid_completed,
    ) = _partition_resolved_blocks(enriched_blocks)
    result = ResolveBlocksResult(
        run_started_at=run_started_at,
        valid_incomplete=valid_incomplete,
        valid_completed=valid_completed,
        invalid_incomplete=invalid_incomplete,
        invalid_completed=invalid_completed,
        precedence_constraints=precedence_constraints,
        warnings=(),
    )
    validate_resolve_blocks_result(result)
    return result


def is_invalid_block(block: ResolvedBlock) -> bool:
    return bool(block.validation_errors)


def is_invalid_incomplete_block(block: ResolvedBlock) -> bool:
    return is_invalid_block(block) and not block.user_completed


def _is_valid_incomplete_block(block: ResolvedBlock) -> bool:
    return not is_invalid_block(block) and not block.user_completed


def _is_valid_completed_block(block: ResolvedBlock) -> bool:
    return not is_invalid_block(block) and block.user_completed


def _is_invalid_completed_block(block: ResolvedBlock) -> bool:
    return is_invalid_block(block) and block.user_completed


def validate_resolve_blocks_result(result: ResolveBlocksResult) -> None:
    seen_plan_ids: set[PlanID] = set()

    def check_bucket(
        bucket_name: str,
        blocks: tuple[ResolvedBlock, ...],
        matches_bucket: Callable[[ResolvedBlock], bool],
    ) -> None:
        for block in blocks:
            if not matches_bucket(block):
                raise ValueError(
                    f"{bucket_name} contains block with mismatched validity or completion"
                )
            if block.plan_id in seen_plan_ids:
                raise ValueError(f"block {block.plan_id} appears in multiple resolution buckets")
            seen_plan_ids.add(block.plan_id)

    check_bucket("valid_incomplete", result.valid_incomplete, _is_valid_incomplete_block)
    check_bucket("valid_completed", result.valid_completed, _is_valid_completed_block)
    check_bucket("invalid_incomplete", result.invalid_incomplete, is_invalid_incomplete_block)
    check_bucket("invalid_completed", result.invalid_completed, _is_invalid_completed_block)


def _partition_resolved_blocks(
    blocks: list[ResolvedBlock],
) -> tuple[
    tuple[ResolvedBlock, ...],
    tuple[ResolvedBlock, ...],
    tuple[ResolvedBlock, ...],
    tuple[ResolvedBlock, ...],
]:
    valid_incomplete: list[ResolvedBlock] = []
    valid_completed: list[ResolvedBlock] = []
    invalid_incomplete: list[ResolvedBlock] = []
    invalid_completed: list[ResolvedBlock] = []
    for block in blocks:
        if is_invalid_block(block):
            if block.user_completed:
                invalid_completed.append(block)
            else:
                invalid_incomplete.append(block)
        elif block.user_completed:
            valid_completed.append(block)
        else:
            valid_incomplete.append(block)
    return (
        tuple(valid_incomplete),
        tuple(valid_completed),
        tuple(invalid_incomplete),
        tuple(invalid_completed),
    )


def _apply_effective_constraints(
    blocks: list[ResolvedBlock],
    indexes: ResolutionIndexes,
) -> list[ResolvedBlock]:
    enriched: list[ResolvedBlock] = []
    for block in blocks:
        effective, sources = compute_effective_constraints(block.parent_path, indexes)
        enriched.append(
            replace(
                block,
                effective_time_windows=effective,
                constraint_sources=sources,
            )
        )
    return enriched


def _ordered_children_for_goal(indexes: ResolutionIndexes, goal: Plan) -> tuple[Plan, ...]:
    children = tuple(
        child for child in indexes.plans_by_id.values() if child.parent_id == goal.plan_id
    )
    return ordered_goal_children(goal, children=children)


@dataclass(frozen=True)
class _WalkContext:
    parent_path: tuple[PlanID, ...]
    criticality_path: tuple[bool, ...]
    inherited_errors: tuple[ServiceMessage, ...]
    priority_path: tuple[int, ...]


@dataclass
class _BlockCollector:
    indexes: ResolutionIndexes
    blocks: list[ResolvedBlock] = field(default_factory=list)
    _priority_counter: int = 0

    def traverse_goal_children(
        self,
        goal_id: PlanID,
        *,
        parent_path: tuple[PlanID, ...],
        criticality_path: tuple[bool, ...],
        inherited_errors: tuple[ServiceMessage, ...],
        priority_path: tuple[int, ...] = (),
    ) -> None:
        plan = self.indexes.plans_by_id[goal_id]
        if plan.goal_plan is None:
            return

        goal_errors = constraint_errors_for_plan(plan)
        subtree_errors = inherited_errors + goal_errors

        for child in _ordered_children_for_goal(self.indexes, plan):
            child_id = PlanID(child.plan_id)
            if child_id in self.indexes.template_subtree_ids:
                continue

            assert child.goal_is_critical is not None
            step_criticality = (*criticality_path, child.goal_is_critical)
            child_parent_path = (*parent_path, child_id)
            step_priority = (*priority_path, self._priority_counter)
            self._priority_counter += 1

            child_context = _WalkContext(
                parent_path=child_parent_path,
                criticality_path=step_criticality,
                inherited_errors=subtree_errors,
                priority_path=step_priority,
            )
            self._visit_goal_child(child, child_context)

    def _visit_goal_child(self, plan: Plan, context: _WalkContext) -> None:
        if plan.plan_kind == PlanKind.GOAL:
            self.traverse_goal_children(
                PlanID(plan.plan_id),
                parent_path=context.parent_path,
                criticality_path=context.criticality_path,
                inherited_errors=context.inherited_errors,
                priority_path=context.priority_path,
            )
            return

        if plan.plan_kind == PlanKind.BLOCK:
            self._emit_block(plan, context)
            return

        if plan.plan_kind == PlanKind.REPETITION:
            self._expand_repetition(plan, context)

    def _expand_repetition(self, plan: Plan, context: _WalkContext) -> None:
        repetition_plan = plan.repetition_plan
        if repetition_plan is None or repetition_plan.generated_at is None:
            return

        for instance_index, instance in enumerate(ordered_repetition_instances(repetition_plan)):
            root_id = PlanID(instance.root_clone_id)
            if root_id in self.indexes.template_subtree_ids:
                continue
            root_plan = self.indexes.plans_by_id.get(instance.root_clone_id)
            if root_plan is None:
                continue

            instance_priority = (*context.priority_path, instance_index)
            instance_context = _WalkContext(
                parent_path=(*context.parent_path, root_id),
                criticality_path=(*context.criticality_path, instance.is_critical),
                inherited_errors=context.inherited_errors,
                priority_path=instance_priority,
            )
            self._enter_subtree_root(root_plan, instance_context)

    def _enter_subtree_root(self, plan: Plan, context: _WalkContext) -> None:
        if plan.plan_id in self.indexes.template_subtree_ids:
            return

        if plan.plan_kind == PlanKind.GOAL:
            self.traverse_goal_children(
                PlanID(plan.plan_id),
                parent_path=context.parent_path,
                criticality_path=context.criticality_path,
                inherited_errors=context.inherited_errors,
                priority_path=context.priority_path,
            )
            return

        if plan.plan_kind == PlanKind.BLOCK:
            self._emit_block(plan, context)
            return

        if plan.plan_kind == PlanKind.REPETITION:
            self._expand_repetition(plan, context)

    def _emit_block(self, plan: Plan, context: _WalkContext) -> None:
        block_plan = plan.block_plan
        if block_plan is None:
            return

        validation_errors = list(context.inherited_errors)
        validation_errors.extend(constraint_errors_for_plan(plan))
        if block_plan.duration_minutes <= 0:
            validation_errors.append(
                ServiceMessage(
                    code=MessageCode.INVALID_DURATION,
                    message="Block duration must be positive",
                    details={
                        "plan_id": str(plan.plan_id),
                        "duration_minutes": str(block_plan.duration_minutes),
                    },
                )
            )

        self.blocks.append(
            ResolvedBlock(
                plan_id=PlanID(plan.plan_id),
                name=plan.name,
                duration_minutes=block_plan.duration_minutes,
                divisible=block_plan.divisible,
                minimum_chunk_size_minutes=block_plan.minimum_chunk_size_minutes,
                block_family=block_plan.block_family,
                user_completed=block_plan.user_completed,
                completed_at=block_plan.completed_at,
                effective_time_windows=(),
                constraint_sources=(),
                priority_path=context.priority_path,
                criticality_path=context.criticality_path,
                parent_path=context.parent_path,
                validation_errors=tuple(validation_errors),
            )
        )

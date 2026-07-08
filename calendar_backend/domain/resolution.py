"""Frozen DTOs and pure task-resolution helpers per design §8.2 / §9.1."""

from __future__ import annotations

import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime

from calendar_backend.domain.constraints import intersect_time_windows, merge_or_windows
from calendar_backend.domain.enums import ConstraintKind, PlanKind
from calendar_backend.domain.errors import MessageCode, ServiceMessage
from calendar_backend.domain.ids import GoalChildChainID, PlanID, TimeConstraintGroupID
from calendar_backend.domain.time import TimeWindow, validate_time_window
from calendar_backend.models.chains import GoalChildChain, GoalChildChainItem
from calendar_backend.models.constraints import TimeConstraintGroup
from calendar_backend.models.plans import GoalPlan, Plan, RepetitionPlan
from calendar_backend.models.repetitions import RepetitionInstance

ChainPathStep = tuple[GoalChildChainID, int]


@dataclass(frozen=True)
class ConstraintSource:
    plan_id: PlanID
    constraint_kind: ConstraintKind
    constraint_group_id: TimeConstraintGroupID


@dataclass(frozen=True)
class ResolvedTask:
    plan_id: PlanID
    name: str
    duration_minutes: int
    divisible: bool
    minimum_chunk_size_minutes: int | None
    user_completed: bool
    completed_at: datetime | None
    effective_time_windows: tuple[TimeWindow, ...]
    constraint_sources: tuple[ConstraintSource, ...]
    priority_path: tuple[int, ...]
    criticality_path: tuple[bool, ...]
    parent_path: tuple[PlanID, ...]
    chain_path: tuple[ChainPathStep, ...]
    validation_errors: tuple[ServiceMessage, ...]


@dataclass(frozen=True)
class ResolvedPrecedenceConstraint:
    predecessor_task_id: PlanID
    successor_task_id: PlanID
    source_chain_id: GoalChildChainID
    reason: str


@dataclass(frozen=True)
class ResolveTasksResult:
    run_started_at: datetime
    valid_incomplete: tuple[ResolvedTask, ...]
    valid_completed: tuple[ResolvedTask, ...]
    invalid_incomplete: tuple[ResolvedTask, ...]
    invalid_completed: tuple[ResolvedTask, ...]
    precedence_constraints: tuple[ResolvedPrecedenceConstraint, ...]
    warnings: tuple[ServiceMessage, ...]


@dataclass(frozen=True)
class ResolutionIndexes:
    plans_by_id: dict[uuid.UUID, Plan]
    template_subtree_ids: frozenset[uuid.UUID]
    master_plan_id: PlanID


def build_resolution_indexes(plans: tuple[Plan, ...]) -> ResolutionIndexes:
    plans_by_id = {plan.plan_id: plan for plan in plans}
    children_by_parent: dict[uuid.UUID, list[uuid.UUID]] = {}
    for plan in plans:
        if plan.parent_id is not None:
            children_by_parent.setdefault(plan.parent_id, []).append(plan.plan_id)

    template_roots: list[uuid.UUID] = []
    for plan in plans:
        if plan.repetition_plan is not None:
            template_roots.append(plan.repetition_plan.template_root_id)

    template_subtree_ids: set[uuid.UUID] = set()
    for template_root_id in template_roots:
        template_subtree_ids.update(
            _collect_descendant_ids(template_root_id, children_by_parent, include_root=True)
        )

    masters = [plan for plan in plans if plan.is_master]
    if len(masters) != 1:
        raise ValueError("resolution requires exactly one master plan in loaded graph")
    master_plan_id = PlanID(masters[0].plan_id)

    return ResolutionIndexes(
        plans_by_id=plans_by_id,
        template_subtree_ids=frozenset(template_subtree_ids),
        master_plan_id=master_plan_id,
    )


def constraint_errors_for_plan(plan: Plan) -> tuple[ServiceMessage, ...]:
    errors: list[ServiceMessage] = []
    for group in plan.constraint_groups:
        for window_index, window in enumerate(group.windows):
            try:
                validate_time_window(
                    TimeWindow(start_time=window.start_time, end_time=window.end_time)
                )
            except ValueError as exc:
                message = str(exc)
                details = {
                    "plan_id": str(plan.plan_id),
                    "constraint_group_id": str(group.time_constraint_group_id),
                    "window_index": str(window_index),
                }
                if "minute-aligned" in message:
                    errors.append(
                        ServiceMessage(
                            code=MessageCode.NON_MINUTE_ALIGNED_WINDOW,
                            message=message,
                            details=details,
                        )
                    )
                else:
                    errors.append(
                        ServiceMessage(
                            code=MessageCode.INVALID_TIME_WINDOW,
                            message=message,
                            details=details,
                        )
                    )
    return tuple(errors)


def compute_effective_constraints(
    parent_path: tuple[PlanID, ...],
    indexes: ResolutionIndexes,
) -> tuple[tuple[TimeWindow, ...], tuple[ConstraintSource, ...]]:
    effective: tuple[TimeWindow, ...] | None = None
    sources: list[ConstraintSource] = []

    for plan_id in parent_path:
        plan = indexes.plans_by_id.get(plan_id)
        if plan is None:
            continue

        ordered_groups = sorted(
            plan.constraint_groups,
            key=lambda group: (group.constraint_kind, str(group.time_constraint_group_id)),
        )
        for group in ordered_groups:
            if not group.windows:
                continue

            sources.append(
                ConstraintSource(
                    plan_id=PlanID(plan.plan_id),
                    constraint_kind=group.constraint_kind,
                    constraint_group_id=TimeConstraintGroupID(group.time_constraint_group_id),
                )
            )

            valid_windows = _valid_windows_for_group(group)
            if not valid_windows:
                continue

            merged_group = merge_or_windows(valid_windows)
            if effective is None:
                effective = merged_group
            else:
                effective = intersect_time_windows(effective, merged_group)
                if not effective:
                    break

        if effective is not None and not effective:
            break

    return (effective or (), tuple(sources))


def _valid_windows_for_group(group: TimeConstraintGroup) -> tuple[TimeWindow, ...]:
    valid: list[TimeWindow] = []
    for window in group.windows:
        domain_window = TimeWindow(start_time=window.start_time, end_time=window.end_time)
        try:
            validate_time_window(domain_window)
        except ValueError:
            continue
        valid.append(domain_window)
    return tuple(valid)


def _apply_effective_constraints(
    tasks: list[ResolvedTask],
    indexes: ResolutionIndexes,
) -> list[ResolvedTask]:
    enriched: list[ResolvedTask] = []
    for task in tasks:
        effective, sources = compute_effective_constraints(task.parent_path, indexes)
        enriched.append(
            replace(
                task,
                effective_time_windows=effective,
                constraint_sources=sources,
            )
        )
    return enriched


def collect_precedence_constraints(
    tasks: tuple[ResolvedTask, ...],
    plans: tuple[Plan, ...],
    indexes: ResolutionIndexes,
) -> tuple[ResolvedPrecedenceConstraint, ...]:
    task_by_id = {task.plan_id: task for task in tasks}
    edges: list[ResolvedPrecedenceConstraint] = []

    for plan in plans:
        if plan.plan_id in indexes.template_subtree_ids:
            continue
        if plan.goal_plan is None:
            continue

        for chain in plan.goal_plan.chains:
            incomplete_predecessor: PlanID | None = None
            for item in _sorted_chain_items(chain):
                successor_id = PlanID(item.child_plan_id)
                successor = task_by_id.get(successor_id)
                if successor is None:
                    continue

                if incomplete_predecessor is not None:
                    edges.append(
                        ResolvedPrecedenceConstraint(
                            predecessor_task_id=incomplete_predecessor,
                            successor_task_id=successor_id,
                            source_chain_id=GoalChildChainID(chain.goal_child_chain_id),
                            reason="goal_child_chain_order",
                        )
                    )

                if not successor.user_completed:
                    incomplete_predecessor = successor_id

    edges.sort(
        key=lambda edge: (
            str(edge.source_chain_id),
            str(edge.successor_task_id),
            str(edge.predecessor_task_id),
        )
    )
    return tuple(edges)


def resolve_tasks_from_graph(
    run_started_at: datetime,
    plans: tuple[Plan, ...],
) -> ResolveTasksResult:
    indexes = build_resolution_indexes(plans)
    collector = _TaskCollector(indexes=indexes)
    collector.traverse_goal_chains(
        indexes.master_plan_id,
        parent_path=(indexes.master_plan_id,),
        criticality_path=(),
        chain_path=(),
        inherited_errors=(),
    )
    enriched_tasks = _apply_effective_constraints(collector.tasks, indexes)
    precedence_constraints = collect_precedence_constraints(
        tuple(enriched_tasks),
        plans,
        indexes,
    )
    (
        valid_incomplete,
        valid_completed,
        invalid_incomplete,
        invalid_completed,
    ) = _partition_resolved_tasks(enriched_tasks)
    result = ResolveTasksResult(
        run_started_at=run_started_at,
        valid_incomplete=valid_incomplete,
        valid_completed=valid_completed,
        invalid_incomplete=invalid_incomplete,
        invalid_completed=invalid_completed,
        precedence_constraints=precedence_constraints,
        warnings=(),
    )
    validate_resolve_tasks_result(result)
    return result


def _collect_descendant_ids(
    root_id: uuid.UUID,
    children_by_parent: dict[uuid.UUID, list[uuid.UUID]],
    *,
    include_root: bool,
) -> set[uuid.UUID]:
    collected: set[uuid.UUID] = set()
    queue: deque[uuid.UUID] = deque([root_id])
    while queue:
        plan_id = queue.popleft()
        if plan_id in collected:
            continue
        collected.add(plan_id)
        queue.extend(children_by_parent.get(plan_id, ()))
    if not include_root:
        collected.discard(root_id)
    return collected


def _ordered_chains(goal_plan: GoalPlan) -> tuple[GoalChildChain, ...]:
    return tuple(
        sorted(
            goal_plan.chains,
            key=lambda chain: (
                not chain.is_critical,
                chain.sort_order,
                str(chain.goal_child_chain_id),
            ),
        )
    )


def _sorted_chain_items(chain: GoalChildChain) -> tuple[GoalChildChainItem, ...]:
    return tuple(
        sorted(
            chain.items,
            key=lambda item: (item.position, str(item.goal_child_chain_item_id)),
        )
    )


def _ordered_repetition_instances(
    repetition_plan: RepetitionPlan,
) -> tuple[RepetitionInstance, ...]:
    return tuple(
        sorted(
            repetition_plan.instances,
            key=lambda instance: (
                not instance.is_critical,
                instance.sort_order,
                str(instance.repetition_instance_id),
            ),
        )
    )


def is_invalid_task(task: ResolvedTask) -> bool:
    return bool(task.validation_errors)


def is_invalid_incomplete_task(task: ResolvedTask) -> bool:
    return is_invalid_task(task) and not task.user_completed


def _is_valid_incomplete_task(task: ResolvedTask) -> bool:
    return not is_invalid_task(task) and not task.user_completed


def _is_valid_completed_task(task: ResolvedTask) -> bool:
    return not is_invalid_task(task) and task.user_completed


def _is_invalid_completed_task(task: ResolvedTask) -> bool:
    return is_invalid_task(task) and task.user_completed


def validate_resolve_tasks_result(result: ResolveTasksResult) -> None:
    seen_plan_ids: set[PlanID] = set()

    def check_bucket(
        bucket_name: str,
        tasks: tuple[ResolvedTask, ...],
        matches_bucket: Callable[[ResolvedTask], bool],
    ) -> None:
        for task in tasks:
            if not matches_bucket(task):
                raise ValueError(
                    f"{bucket_name} contains task with mismatched validity or completion"
                )
            if task.plan_id in seen_plan_ids:
                raise ValueError(f"task {task.plan_id} appears in multiple resolution buckets")
            seen_plan_ids.add(task.plan_id)

    check_bucket("valid_incomplete", result.valid_incomplete, _is_valid_incomplete_task)
    check_bucket("valid_completed", result.valid_completed, _is_valid_completed_task)
    check_bucket("invalid_incomplete", result.invalid_incomplete, is_invalid_incomplete_task)
    check_bucket("invalid_completed", result.invalid_completed, _is_invalid_completed_task)


def _partition_resolved_tasks(
    tasks: list[ResolvedTask],
) -> tuple[
    tuple[ResolvedTask, ...],
    tuple[ResolvedTask, ...],
    tuple[ResolvedTask, ...],
    tuple[ResolvedTask, ...],
]:
    valid_incomplete: list[ResolvedTask] = []
    valid_completed: list[ResolvedTask] = []
    invalid_incomplete: list[ResolvedTask] = []
    invalid_completed: list[ResolvedTask] = []
    for task in tasks:
        if is_invalid_task(task):
            if task.user_completed:
                invalid_completed.append(task)
            else:
                invalid_incomplete.append(task)
        elif task.user_completed:
            valid_completed.append(task)
        else:
            valid_incomplete.append(task)
    return (
        tuple(valid_incomplete),
        tuple(valid_completed),
        tuple(invalid_incomplete),
        tuple(invalid_completed),
    )


@dataclass(frozen=True)
class _WalkContext:
    parent_path: tuple[PlanID, ...]
    criticality_path: tuple[bool, ...]
    chain_path: tuple[ChainPathStep, ...]
    inherited_errors: tuple[ServiceMessage, ...]
    priority_path: tuple[int, ...]


@dataclass
class _TaskCollector:
    indexes: ResolutionIndexes
    tasks: list[ResolvedTask] = field(default_factory=lambda: [])
    _priority_counter: int = 0

    def traverse_goal_chains(
        self,
        goal_id: PlanID,
        *,
        parent_path: tuple[PlanID, ...],
        criticality_path: tuple[bool, ...],
        chain_path: tuple[ChainPathStep, ...],
        inherited_errors: tuple[ServiceMessage, ...],
        priority_path: tuple[int, ...] = (),
    ) -> None:
        plan = self.indexes.plans_by_id[goal_id]
        if plan.goal_plan is None:
            return

        goal_errors = constraint_errors_for_plan(plan)
        subtree_errors = inherited_errors + goal_errors

        for chain in _ordered_chains(plan.goal_plan):
            for item in _sorted_chain_items(chain):
                child_id = PlanID(item.child_plan_id)
                if child_id in self.indexes.template_subtree_ids:
                    continue
                child = self.indexes.plans_by_id.get(item.child_plan_id)
                if child is None:
                    continue

                step_chain_path = (
                    *chain_path,
                    (GoalChildChainID(chain.goal_child_chain_id), item.position),
                )
                step_criticality = (*criticality_path, chain.is_critical)
                child_parent_path = (*parent_path, child_id)
                step_priority = (*priority_path, self._priority_counter)
                self._priority_counter += 1

                child_context = _WalkContext(
                    parent_path=child_parent_path,
                    criticality_path=step_criticality,
                    chain_path=step_chain_path,
                    inherited_errors=subtree_errors,
                    priority_path=step_priority,
                )
                self._visit_chain_child(child, child_context)

    def _visit_chain_child(self, plan: Plan, context: _WalkContext) -> None:
        if plan.plan_kind == PlanKind.GOAL:
            self.traverse_goal_chains(
                PlanID(plan.plan_id),
                parent_path=context.parent_path,
                criticality_path=context.criticality_path,
                chain_path=context.chain_path,
                inherited_errors=context.inherited_errors,
                priority_path=context.priority_path,
            )
            return

        if plan.plan_kind == PlanKind.TASK:
            self._emit_task(plan, context)
            return

        if plan.plan_kind == PlanKind.REPETITION:
            self._expand_repetition(plan, context)

    def _expand_repetition(self, plan: Plan, context: _WalkContext) -> None:
        repetition_plan = plan.repetition_plan
        if repetition_plan is None or repetition_plan.generated_at is None:
            return

        for instance_index, instance in enumerate(_ordered_repetition_instances(repetition_plan)):
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
                chain_path=context.chain_path,
                inherited_errors=context.inherited_errors,
                priority_path=instance_priority,
            )
            self._enter_subtree_root(root_plan, instance_context)

    def _enter_subtree_root(self, plan: Plan, context: _WalkContext) -> None:
        if plan.plan_id in self.indexes.template_subtree_ids:
            return

        if plan.plan_kind == PlanKind.GOAL:
            self.traverse_goal_chains(
                PlanID(plan.plan_id),
                parent_path=context.parent_path,
                criticality_path=context.criticality_path,
                chain_path=context.chain_path,
                inherited_errors=context.inherited_errors,
                priority_path=context.priority_path,
            )
            return

        if plan.plan_kind == PlanKind.TASK:
            self._emit_task(plan, context)
            return

        if plan.plan_kind == PlanKind.REPETITION:
            self._expand_repetition(plan, context)

    def _emit_task(self, plan: Plan, context: _WalkContext) -> None:
        task_plan = plan.task_plan
        if task_plan is None:
            return

        validation_errors = list(context.inherited_errors)
        validation_errors.extend(constraint_errors_for_plan(plan))
        if task_plan.duration_minutes <= 0:
            validation_errors.append(
                ServiceMessage(
                    code=MessageCode.INVALID_DURATION,
                    message="Task duration must be positive",
                    details={
                        "plan_id": str(plan.plan_id),
                        "duration_minutes": str(task_plan.duration_minutes),
                    },
                )
            )

        self.tasks.append(
            ResolvedTask(
                plan_id=PlanID(plan.plan_id),
                name=plan.name,
                duration_minutes=task_plan.duration_minutes,
                divisible=task_plan.divisible,
                minimum_chunk_size_minutes=task_plan.minimum_chunk_size_minutes,
                user_completed=task_plan.user_completed,
                completed_at=task_plan.completed_at,
                effective_time_windows=(),
                constraint_sources=(),
                priority_path=context.priority_path,
                criticality_path=context.criticality_path,
                parent_path=context.parent_path,
                chain_path=context.chain_path,
                validation_errors=tuple(validation_errors),
            )
        )

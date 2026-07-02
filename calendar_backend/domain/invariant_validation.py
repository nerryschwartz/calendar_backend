"""Session-free ORM plan-tree invariant checks ([repo convention §9]).

Callers pass the full committed ORM graph already loaded from persistence; these
functions do not query or mutate the database. Validates **ideal post-change
persisted shape** ([repo convention §7]) — including existence rules (master,
master horizon) that may fail in transient states before bootstrap completes.

Rules already enforced by SQLite CHECK/UNIQUE on commit are not re-checked
([repo convention §8]) — for example master goal kind, unique chain
child_plan_id, window start before end.
"""

from __future__ import annotations

import uuid
from collections import deque

from calendar_backend.domain.constraints import merge_or_windows
from calendar_backend.domain.enums import CloneStatus, ConstraintKind, PlanKind
from calendar_backend.domain.errors import MessageCode, ServiceMessage
from calendar_backend.domain.time import (
    TimeWindow,
    is_minute_aligned,
)
from calendar_backend.models.constraints import TimeConstraintGroup
from calendar_backend.models.plans import Plan

_PLAN_KIND_TO_DETAIL_ATTR: dict[PlanKind, str] = {
    PlanKind.GOAL: "goal_plan",
    PlanKind.TASK: "task_plan",
    PlanKind.REPETITION: "repetition_plan",
}


def validate_master_tree_graph(plans: tuple[Plan, ...]) -> tuple[ServiceMessage, ...]:
    """Return one ServiceMessage per invariant violation (empty when clean)."""
    violations: list[ServiceMessage] = []
    master_violations, master = _check_master(plans)
    violations.extend(master_violations)
    if master is not None:
        violations.extend(_check_reachability(plans, master))
    violations.extend(_check_subtype_pairing(plans))
    violations.extend(_check_task_completion_pairing(plans))
    violations.extend(_check_master_chains_non_critical(plans))
    violations.extend(_check_goal_chain_membership(plans))
    violations.extend(_check_chains(plans))
    violations.extend(_check_repetition_plans(plans))
    violations.extend(_check_repetition_instances(plans))
    violations.extend(_check_constraints(plans))
    return tuple(violations)


def _check_master(plans: tuple[Plan, ...]) -> tuple[list[ServiceMessage], Plan | None]:
    masters = [plan for plan in plans if plan.is_master]
    violations: list[ServiceMessage] = []

    if not masters:
        violations.append(
            ServiceMessage(
                code=MessageCode.INVALID_MASTER_PLAN,
                message="Master plan is missing",
                details={"master_count": "0"},
            )
        )
        return violations, None

    master = masters[0]
    if master.parent_id is not None:
        violations.append(
            ServiceMessage(
                code=MessageCode.INVALID_MASTER_PLAN,
                message="Master plan must have no parent",
                details={
                    "plan_id": str(master.plan_id),
                    "parent_id": str(master.parent_id),
                },
            )
        )

    return violations, master


def _check_reachability(plans: tuple[Plan, ...], master: Plan) -> list[ServiceMessage]:
    reachable: set[uuid.UUID] = set()
    queue: deque[uuid.UUID] = deque([master.plan_id])

    while queue:
        plan_id = queue.popleft()
        if plan_id in reachable:
            continue
        reachable.add(plan_id)
        for plan in plans:
            if plan.parent_id == plan_id:
                queue.append(plan.plan_id)

    violations: list[ServiceMessage] = []
    for plan in plans:
        if plan.plan_id in reachable:
            continue
        details = {"plan_id": str(plan.plan_id)}
        if plan.parent_id is not None:
            details["parent_id"] = str(plan.parent_id)
        violations.append(
            ServiceMessage(
                code=MessageCode.ORPHAN_PLAN,
                message="Plan is not reachable from master",
                details=details,
            )
        )

    return violations


def _check_subtype_pairing(plans: tuple[Plan, ...]) -> list[ServiceMessage]:
    violations: list[ServiceMessage] = []

    for plan in plans:
        plan_label = f"{plan.plan_kind.value.title()} plan"

        for detail_kind, detail_attr in _PLAN_KIND_TO_DETAIL_ATTR.items():
            has_detail = getattr(plan, detail_attr) is not None
            detail_label = f"{detail_attr.removesuffix('_plan').replace('_', ' ')} plan"

            if plan.plan_kind == detail_kind:
                if not has_detail:
                    violations.append(
                        ServiceMessage(
                            code=MessageCode.PLAN_SUBTYPE_MISMATCH,
                            message=f"{plan_label} is missing {detail_label} detail row",
                            details={
                                "plan_id": str(plan.plan_id),
                                "expected_detail": detail_attr,
                            },
                        )
                    )
            elif has_detail:
                violations.append(
                    ServiceMessage(
                        code=MessageCode.PLAN_SUBTYPE_MISMATCH,
                        message=f"{plan_label} has {detail_label} detail row",
                        details={
                            "plan_id": str(plan.plan_id),
                            "conflicting_detail": detail_attr,
                        },
                    )
                )

    return violations


def _check_task_completion_pairing(plans: tuple[Plan, ...]) -> list[ServiceMessage]:
    violations: list[ServiceMessage] = []
    for plan in plans:
        if plan.task_plan is None:
            continue
        task_plan = plan.task_plan
        if task_plan.user_completed and task_plan.completed_at is None:
            violations.append(
                ServiceMessage(
                    code=MessageCode.TASK_COMPLETION_INVARIANT_VIOLATION,
                    message="Completed task must have completed_at set",
                    details={
                        "plan_id": str(plan.plan_id),
                        "user_completed": "true",
                    },
                )
            )
        elif not task_plan.user_completed and task_plan.completed_at is not None:
            violations.append(
                ServiceMessage(
                    code=MessageCode.TASK_COMPLETION_INVARIANT_VIOLATION,
                    message="Incomplete task must not have completed_at set",
                    details={
                        "plan_id": str(plan.plan_id),
                        "user_completed": "false",
                        "completed_at": str(task_plan.completed_at),
                    },
                )
            )
    return violations


def _chain_child_counts(plans: tuple[Plan, ...]) -> dict[uuid.UUID, int]:
    counts: dict[uuid.UUID, int] = {}
    for plan in plans:
        if plan.goal_plan is None:
            continue
        for chain in plan.goal_plan.chains:
            for item in chain.items:
                counts[item.child_plan_id] = counts.get(item.child_plan_id, 0) + 1
    return counts


def _check_master_chains_non_critical(plans: tuple[Plan, ...]) -> list[ServiceMessage]:
    violations: list[ServiceMessage] = []
    for plan in plans:
        if not plan.is_master or plan.goal_plan is None:
            continue
        for chain in plan.goal_plan.chains:
            if chain.is_critical:
                violations.append(
                    ServiceMessage(
                        code=MessageCode.CHAIN_INVARIANT_VIOLATION,
                        message="Master goal child chains must be non-critical",
                        details={
                            "parent_goal_id": str(plan.plan_id),
                            "goal_child_chain_id": str(chain.goal_child_chain_id),
                        },
                    )
                )
    return violations


def _check_goal_chain_membership(plans: tuple[Plan, ...]) -> list[ServiceMessage]:
    goal_ids = {plan.plan_id for plan in plans if plan.goal_plan is not None}
    chain_counts = _chain_child_counts(plans)
    violations: list[ServiceMessage] = []
    for plan in plans:
        if plan.parent_id is None or plan.parent_id not in goal_ids:
            continue
        if plan.clone_status == CloneStatus.TEMPLATE:
            continue
        count = chain_counts.get(plan.plan_id, 0)
        if count != 1:
            violations.append(
                ServiceMessage(
                    code=MessageCode.CHAIN_INVARIANT_VIOLATION,
                    message="Direct goal child must appear in exactly one goal child chain item",
                    details={
                        "child_plan_id": str(plan.plan_id),
                        "parent_goal_id": str(plan.parent_id),
                        "chain_item_count": str(count),
                    },
                )
            )
    return violations


def _check_repetition_plans(plans: tuple[Plan, ...]) -> list[ServiceMessage]:
    plan_by_id = {plan.plan_id: plan for plan in plans}
    template_root_ids: set[uuid.UUID] = set()
    violations: list[ServiceMessage] = []

    for plan in plans:
        if plan.repetition_plan is None:
            continue

        repetition_plan_id = plan.plan_id
        repetition_detail = plan.repetition_plan
        template_root_ids.add(repetition_detail.template_root_id)

        if not is_minute_aligned(repetition_detail.start_time):
            violations.append(
                ServiceMessage(
                    code=MessageCode.INVALID_REPETITION_SETTINGS,
                    message="Repetition start_time must be minute-aligned",
                    details={"repetition_plan_id": str(repetition_plan_id)},
                )
            )
        if repetition_detail.end_time is not None and not is_minute_aligned(
            repetition_detail.end_time
        ):
            violations.append(
                ServiceMessage(
                    code=MessageCode.INVALID_REPETITION_SETTINGS,
                    message="Repetition end_time must be minute-aligned",
                    details={"repetition_plan_id": str(repetition_plan_id)},
                )
            )

        # TODO(Prompt 10 / RepetitionService): relax when generation materializes instances.
        if repetition_detail.generated_at is not None or repetition_detail.instances:
            violations.append(
                ServiceMessage(
                    code=MessageCode.CHAIN_INVARIANT_VIOLATION,
                    message=(
                        "Repetition plan must be pre-generation (generated_at unset, no instances)"
                    ),
                    details={
                        "repetition_plan_id": str(repetition_plan_id),
                        "generated_at": (
                            "set" if repetition_detail.generated_at is not None else "unset"
                        ),
                        "instance_count": str(len(repetition_detail.instances)),
                    },
                )
            )

        template = plan_by_id.get(repetition_detail.template_root_id)
        if template is None:
            violations.append(
                ServiceMessage(
                    code=MessageCode.CHAIN_INVARIANT_VIOLATION,
                    message="Repetition template_root_id must reference a loaded plan",
                    details={
                        "repetition_plan_id": str(repetition_plan_id),
                        "template_root_id": str(repetition_detail.template_root_id),
                    },
                )
            )
            continue
        # TODO(Prompt 10 / RepetitionService): Relax plan_kind == GOAL for non-goal templates.
        if template.plan_kind != PlanKind.GOAL or template.clone_status != CloneStatus.TEMPLATE:
            violations.append(
                ServiceMessage(
                    code=MessageCode.CHAIN_INVARIANT_VIOLATION,
                    message="Repetition template root must be a TEMPLATE goal plan",
                    details={
                        "repetition_plan_id": str(repetition_plan_id),
                        "template_root_id": str(repetition_detail.template_root_id),
                        "plan_kind": template.plan_kind.value,
                        "clone_status": template.clone_status.value,
                    },
                )
            )
        if template.parent_id != repetition_plan_id:
            violations.append(
                ServiceMessage(
                    code=MessageCode.CHAIN_INVARIANT_VIOLATION,
                    message=(
                        "Repetition template root must be a direct child of the repetition plan"
                    ),
                    details={
                        "repetition_plan_id": str(repetition_plan_id),
                        "template_root_id": str(repetition_detail.template_root_id),
                        "actual_parent_id": str(template.parent_id),
                    },
                )
            )

    chain_counts = _chain_child_counts(plans)
    for template_root_id in template_root_ids:
        if chain_counts.get(template_root_id, 0) > 0:
            violations.append(
                ServiceMessage(
                    code=MessageCode.CHAIN_INVARIANT_VIOLATION,
                    message="Repetition template root must not appear in a goal child chain",
                    details={"template_root_id": str(template_root_id)},
                )
            )

    return violations


def _violations_for_non_dense_sequence(
    values: list[int],
    *,
    code: MessageCode,
    message: str,
    details: dict[str, str],
) -> list[ServiceMessage]:
    if not values:
        return []
    if frozenset(values) == frozenset(range(len(values))):
        return []
    return [ServiceMessage(code=code, message=message, details=details)]


def _check_chains(plans: tuple[Plan, ...]) -> list[ServiceMessage]:
    violations: list[ServiceMessage] = []
    plan_by_id = {plan.plan_id: plan for plan in plans}

    for plan in plans:
        if plan.goal_plan is None:
            continue

        chain_sort_orders_by_bucket: dict[bool, list[int]] = {}

        parent_goal_id = plan.plan_id
        for chain in plan.goal_plan.chains:
            chain_sort_orders_by_bucket.setdefault(chain.is_critical, []).append(chain.sort_order)

            positions: list[int] = []
            for item in chain.items:
                positions.append(item.position)

                child_plan = plan_by_id[item.child_plan_id]
                if child_plan.parent_id != parent_goal_id:
                    violations.append(
                        ServiceMessage(
                            code=MessageCode.CHAIN_INVARIANT_VIOLATION,
                            message="Chain item child must be a direct child of the parent goal",
                            details={
                                "child_plan_id": str(item.child_plan_id),
                                "goal_child_chain_id": str(chain.goal_child_chain_id),
                                "parent_goal_id": str(parent_goal_id),
                                "actual_parent_id": str(child_plan.parent_id),
                            },
                        )
                    )

            violations.extend(
                _violations_for_non_dense_sequence(
                    positions,
                    code=MessageCode.CHAIN_INVARIANT_VIOLATION,
                    message="Goal child chain item positions must be dense starting at 0",
                    details={
                        "goal_child_chain_id": str(chain.goal_child_chain_id),
                        "parent_goal_id": str(parent_goal_id),
                    },
                )
            )

        for is_critical, sort_orders in chain_sort_orders_by_bucket.items():
            violations.extend(
                _violations_for_non_dense_sequence(
                    sort_orders,
                    code=MessageCode.CHAIN_INVARIANT_VIOLATION,
                    message="Goal child chain sort_order must be dense from 0 per bucket",
                    details={
                        "parent_goal_id": str(parent_goal_id),
                        "is_critical": str(is_critical).lower(),
                    },
                )
            )

    return violations


def _check_repetition_instances(plans: tuple[Plan, ...]) -> list[ServiceMessage]:
    violations: list[ServiceMessage] = []
    plan_by_id = {plan.plan_id: plan for plan in plans}
    root_clone_ids_seen: dict[uuid.UUID, uuid.UUID] = {}

    for plan in plans:
        if plan.repetition_plan is None:
            continue

        repetition_plan_id = plan.plan_id
        template_root_id = plan.repetition_plan.template_root_id

        instance_indices: list[int] = []
        sort_orders_by_critical: dict[bool, list[int]] = {}

        for instance in plan.repetition_plan.instances:
            instance_indices.append(instance.instance_index)
            sort_orders_by_critical.setdefault(instance.is_critical, []).append(instance.sort_order)

            prior_instance_id = root_clone_ids_seen.get(instance.root_clone_id)
            if prior_instance_id is not None:
                violations.append(
                    ServiceMessage(
                        code=MessageCode.CHAIN_INVARIANT_VIOLATION,
                        message="root_clone_id appears in more than one repetition instance",
                        details={
                            "root_clone_id": str(instance.root_clone_id),
                            "repetition_instance_id": str(instance.repetition_instance_id),
                            "prior_repetition_instance_id": str(prior_instance_id),
                        },
                    )
                )
            else:
                root_clone_ids_seen[instance.root_clone_id] = instance.repetition_instance_id

            root_clone = plan_by_id[instance.root_clone_id]
            if root_clone.parent_id != repetition_plan_id:
                violations.append(
                    ServiceMessage(
                        code=MessageCode.CHAIN_INVARIANT_VIOLATION,
                        message="Repetition root clone must be child of repetition plan",
                        details={
                            "repetition_instance_id": str(instance.repetition_instance_id),
                            "root_clone_id": str(instance.root_clone_id),
                            "repetition_plan_id": str(repetition_plan_id),
                            "actual_parent_id": str(root_clone.parent_id),
                        },
                    )
                )
            if root_clone.cloned_from_id != template_root_id:
                violations.append(
                    ServiceMessage(
                        code=MessageCode.CHAIN_INVARIANT_VIOLATION,
                        message="Repetition instance root clone must clone from template root",
                        details={
                            "repetition_instance_id": str(instance.repetition_instance_id),
                            "root_clone_id": str(instance.root_clone_id),
                            "template_root_id": str(template_root_id),
                            "cloned_from_id": str(root_clone.cloned_from_id),
                        },
                    )
                )

        violations.extend(
            _violations_for_non_dense_sequence(
                instance_indices,
                code=MessageCode.CHAIN_INVARIANT_VIOLATION,
                message="Repetition instance_index values must be dense starting at 0",
                details={"repetition_plan_id": str(repetition_plan_id)},
            )
        )

        for is_critical, sort_orders in sort_orders_by_critical.items():
            violations.extend(
                _violations_for_non_dense_sequence(
                    sort_orders,
                    code=MessageCode.CHAIN_INVARIANT_VIOLATION,
                    message="Repetition instance sort_order must be dense from 0 per bucket",
                    details={
                        "repetition_plan_id": str(repetition_plan_id),
                        "is_critical": str(is_critical).lower(),
                    },
                )
            )

    return violations


def _violations_for_persisted_group_windows(
    windows: tuple[TimeWindow, ...],
    *,
    plan_id: uuid.UUID,
    group: TimeConstraintGroup,
) -> list[ServiceMessage]:
    violations: list[ServiceMessage] = []
    base_details = {
        "plan_id": str(plan_id),
        "constraint_group_id": str(group.time_constraint_group_id),
        "constraint_kind": group.constraint_kind.value,
    }

    for index, window in enumerate(windows):
        details = {**base_details, "window_index": str(index)}
        if not is_minute_aligned(window.start_time):
            violations.append(
                ServiceMessage(
                    code=MessageCode.NON_MINUTE_ALIGNED_WINDOW,
                    message="start_time must be minute-aligned",
                    details=details,
                )
            )
        if not is_minute_aligned(window.end_time):
            violations.append(
                ServiceMessage(
                    code=MessageCode.NON_MINUTE_ALIGNED_WINDOW,
                    message="end_time must be minute-aligned",
                    details=details,
                )
            )

    if not windows or violations:
        return violations

    merged = merge_or_windows(windows)
    if merged != windows:
        violations.append(
            ServiceMessage(
                code=MessageCode.CONSTRAINT_INVARIANT_VIOLATION,
                message="Constraint group windows must be merged and non-overlapping",
                details=base_details,
            )
        )

    return violations


def _check_constraints(plans: tuple[Plan, ...]) -> list[ServiceMessage]:
    violations: list[ServiceMessage] = []

    for plan in plans:
        has_master_horizon = False

        for group in plan.constraint_groups:
            if group.constraint_kind == ConstraintKind.SYSTEM_MASTER_HORIZON:
                if plan.is_master:
                    has_master_horizon = True
                else:
                    violations.append(
                        ServiceMessage(
                            code=MessageCode.CONSTRAINT_INVARIANT_VIOLATION,
                            message="SYSTEM_MASTER_HORIZON group must be on master plan",
                            details={
                                "plan_id": str(plan.plan_id),
                                "constraint_group_id": str(group.time_constraint_group_id),
                            },
                        )
                    )
                if len(group.windows) != 1:
                    violations.append(
                        ServiceMessage(
                            code=MessageCode.CONSTRAINT_INVARIANT_VIOLATION,
                            message="SYSTEM_MASTER_HORIZON group must have exactly one window",
                            details={
                                "plan_id": str(plan.plan_id),
                                "constraint_group_id": str(group.time_constraint_group_id),
                                "window_count": str(len(group.windows)),
                            },
                        )
                    )
            elif group.constraint_kind == ConstraintKind.USER:
                if not group.windows:
                    violations.append(
                        ServiceMessage(
                            code=MessageCode.CONSTRAINT_INVARIANT_VIOLATION,
                            message="USER constraint group must contain at least one window",
                            details={
                                "plan_id": str(plan.plan_id),
                                "constraint_group_id": str(group.time_constraint_group_id),
                            },
                        )
                    )
                    continue
            elif group.constraint_kind == ConstraintKind.SYSTEM_REPETITION_WINDOW:
                if not group.windows:
                    violations.append(
                        ServiceMessage(
                            code=MessageCode.CONSTRAINT_INVARIANT_VIOLATION,
                            message="SYSTEM_REPETITION_WINDOW group must have at least one window",
                            details={
                                "plan_id": str(plan.plan_id),
                                "constraint_group_id": str(group.time_constraint_group_id),
                            },
                        )
                    )
                    continue
            ordered = sorted(group.windows, key=lambda window: window.start_time)
            violations.extend(
                _violations_for_persisted_group_windows(
                    tuple(TimeWindow(window.start_time, window.end_time) for window in ordered),
                    plan_id=plan.plan_id,
                    group=group,
                )
            )

        if plan.is_master and not has_master_horizon:
            violations.append(
                ServiceMessage(
                    code=MessageCode.CONSTRAINT_INVARIANT_VIOLATION,
                    message="Master plan is missing SYSTEM_MASTER_HORIZON constraint group",
                    details={"plan_id": str(plan.plan_id)},
                )
            )

    return violations

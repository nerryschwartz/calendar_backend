"""Repetition plan subtype self-edit service."""

from __future__ import annotations

import uuid
from collections import deque
from datetime import datetime, timedelta
from typing import cast

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from calendar_backend.db.session import transaction
from calendar_backend.domain.dtos import RepetitionPlanDTO, repetition_plan_dto_from_rows
from calendar_backend.domain.enums import CloneStatus, ConstraintKind, PlanKind, RepeatMode
from calendar_backend.domain.errors import MessageCode, ServiceMessage
from calendar_backend.domain.ids import (
    GoalChildChainID,
    GoalChildChainItemID,
    PlanID,
    RepetitionInstanceID,
    TimeConstraintGroupID,
    TimeWindowID,
    new_id,
)
from calendar_backend.domain.repetitions import (
    RepetitionSettingsState,
    compute_instance_indices,
    instance_start_time,
    validate_repetition_settings_update,
)
from calendar_backend.domain.results import ServiceResult, fail, ok
from calendar_backend.domain.time import Clock, SystemClock
from calendar_backend.models.chains import GoalChildChain, GoalChildChainItem
from calendar_backend.models.constraints import TimeConstraintGroup, TimeWindow
from calendar_backend.models.plans import GoalPlan, Plan, RepetitionPlan, TaskPlan
from calendar_backend.models.repetitions import RepetitionInstance
from calendar_backend.services.master_horizon import get_master_horizon_end, validate_run_started_at
from calendar_backend.services.plan_tree import load_plan_with_subtype


class _UnsetType:
    __slots__ = ()


_UNSET = _UnsetType()


class RepetitionService:
    def __init__(self, session: Session, clock: Clock | None = None) -> None:
        self._session = session
        self._clock = clock or SystemClock()

    def update_settings(
        self,
        repetition_plan_id: PlanID,
        *,
        repeat_mode: RepeatMode | None = None,
        start_time: datetime | None = None,
        repeat_interval_minutes: int | None = None,
        manual_count: int | None | _UnsetType = _UNSET,
        end_time: datetime | None | _UnsetType = _UNSET,
        default_instance_critical: bool | None = None,
    ) -> ServiceResult[RepetitionPlanDTO]:
        with transaction(self._session) as txn:
            loaded = load_plan_with_subtype(
                txn, repetition_plan_id, expected_kind=PlanKind.REPETITION
            )
            if isinstance(loaded, ServiceMessage):
                return fail(loaded)
            plan, repetition_plan = loaded

            current = RepetitionSettingsState(
                repeat_mode=repetition_plan.repeat_mode,
                start_time=repetition_plan.start_time,
                repeat_interval_minutes=repetition_plan.repeat_interval_minutes,
                manual_count=repetition_plan.manual_count,
                end_time=repetition_plan.end_time,
                default_instance_critical=repetition_plan.default_instance_critical,
                generated_at=repetition_plan.generated_at,
            )
            if manual_count is _UNSET:
                merged_manual_count = current.manual_count
            else:
                merged_manual_count = cast(int | None, manual_count)
            if end_time is _UNSET:
                merged_end_time = current.end_time
            else:
                merged_end_time = cast(datetime | None, end_time)
            proposed = RepetitionSettingsState(
                repeat_mode=repeat_mode if repeat_mode is not None else current.repeat_mode,
                start_time=start_time if start_time is not None else current.start_time,
                repeat_interval_minutes=(
                    repeat_interval_minutes
                    if repeat_interval_minutes is not None
                    else current.repeat_interval_minutes
                ),
                manual_count=merged_manual_count,
                end_time=merged_end_time,
                default_instance_critical=(
                    default_instance_critical
                    if default_instance_critical is not None
                    else current.default_instance_critical
                ),
                generated_at=current.generated_at,
            )

            validation_error = validate_repetition_settings_update(current, proposed)
            if validation_error is not None:
                return fail(validation_error)

            now = self._clock.now_utc()
            repetition_plan.repeat_mode = proposed.repeat_mode
            repetition_plan.start_time = proposed.start_time
            repetition_plan.repeat_interval_minutes = proposed.repeat_interval_minutes
            repetition_plan.manual_count = proposed.manual_count
            repetition_plan.end_time = proposed.end_time
            repetition_plan.default_instance_critical = proposed.default_instance_critical
            plan.updated_at = now
            txn.flush()
            return ok(repetition_plan_dto_from_rows(plan, repetition_plan))

    def generate_instances(
        self,
        repetition_plan_id: PlanID,
        run_started_at: datetime,
    ) -> ServiceResult[RepetitionPlanDTO]:
        validation_error = validate_run_started_at(run_started_at)
        if validation_error is not None:
            return fail(validation_error)

        with transaction(self._session) as txn:
            loaded = load_plan_with_subtype(
                txn, repetition_plan_id, expected_kind=PlanKind.REPETITION
            )
            if isinstance(loaded, ServiceMessage):
                return fail(loaded)
            plan, repetition_plan = loaded

            if repetition_plan.generated_at is not None:
                return fail(
                    ServiceMessage(
                        code=MessageCode.REPETITION_ALREADY_GENERATED,
                        message="Repetition instances were already generated",
                        details={"repetition_plan_id": str(repetition_plan_id)},
                    )
                )

            template_root = txn.get(Plan, repetition_plan.template_root_id)
            if template_root is None:
                return fail(
                    ServiceMessage(
                        code=MessageCode.PLAN_NOT_FOUND,
                        message="Repetition template root not found",
                        details={
                            "repetition_plan_id": str(repetition_plan_id),
                            "template_root_id": str(repetition_plan.template_root_id),
                        },
                    )
                )

            needs_horizon = (
                repetition_plan.repeat_mode == RepeatMode.DATE_RANGE
                and repetition_plan.end_time is None
            )
            master_horizon_end = get_master_horizon_end(txn) if needs_horizon else None

            indices_result = compute_instance_indices(
                repeat_mode=repetition_plan.repeat_mode,
                start_time=repetition_plan.start_time,
                repeat_interval_minutes=repetition_plan.repeat_interval_minutes,
                manual_count=repetition_plan.manual_count,
                end_time=repetition_plan.end_time,
                master_horizon_end=master_horizon_end,
            )
            if isinstance(indices_result, ServiceMessage):
                return fail(indices_result)

            is_critical = repetition_plan.default_instance_critical
            for sort_order, instance_index in enumerate(indices_result):
                root_clone_id = _clone_template_subtree(
                    txn,
                    template_root_id=PlanID(template_root.plan_id),
                    repetition_plan_id=repetition_plan_id,
                    now=run_started_at,
                )
                instance_start = instance_start_time(
                    repetition_plan.start_time,
                    repeat_interval_minutes=repetition_plan.repeat_interval_minutes,
                    instance_index=instance_index,
                )
                txn.add(
                    RepetitionInstance(
                        repetition_instance_id=new_id(RepetitionInstanceID),
                        repetition_plan_id=repetition_plan.plan_id,
                        instance_index=instance_index,
                        root_clone_id=root_clone_id,
                        instance_start_time=instance_start,
                        is_critical=is_critical,
                        sort_order=sort_order,
                    )
                )
                _upsert_repetition_instance_window(
                    txn,
                    instance_root_plan_id=PlanID(root_clone_id),
                    window_start=instance_start,
                    window_end=instance_start
                    + timedelta(minutes=repetition_plan.repeat_interval_minutes),
                )

            repetition_plan.generated_at = run_started_at
            plan.updated_at = run_started_at
            txn.flush()
            return ok(repetition_plan_dto_from_rows(plan, repetition_plan))


def _upsert_repetition_instance_window(
    session: Session,
    *,
    instance_root_plan_id: PlanID,
    window_start: datetime,
    window_end: datetime,
) -> None:
    group = session.scalar(
        select(TimeConstraintGroup)
        .where(TimeConstraintGroup.plan_id == instance_root_plan_id)
        .where(TimeConstraintGroup.constraint_kind == ConstraintKind.SYSTEM_REPETITION_WINDOW)
    )
    if group is None:
        group = TimeConstraintGroup(
            time_constraint_group_id=new_id(TimeConstraintGroupID),
            plan_id=instance_root_plan_id,
            constraint_kind=ConstraintKind.SYSTEM_REPETITION_WINDOW,
        )
        session.add(group)

    session.execute(delete(TimeWindow).where(TimeWindow.group_id == group.time_constraint_group_id))

    session.add(
        TimeWindow(
            time_window_id=new_id(TimeWindowID),
            group_id=group.time_constraint_group_id,
            start_time=window_start,
            end_time=window_end,
        )
    )


def _clone_template_subtree(
    txn: Session,
    *,
    template_root_id: PlanID,
    repetition_plan_id: PlanID,
    now: datetime,
) -> uuid.UUID:
    template_plans = _collect_template_subtree(txn, template_root_id)
    clone_by_template_id: dict[uuid.UUID, uuid.UUID] = {}

    for template_plan in template_plans:
        parent_id = (
            repetition_plan_id
            if template_plan.plan_id == template_root_id
            else clone_by_template_id[template_plan.parent_id]  # pyright: ignore[reportArgumentType]  # type checker: parent cloned earlier in BFS order
        )
        clone_plan_id = new_id(PlanID)
        template_plan_id = PlanID(template_plan.plan_id)
        clone_by_template_id[template_plan.plan_id] = clone_plan_id
        txn.add(
            Plan(
                plan_id=clone_plan_id,
                plan_kind=template_plan.plan_kind,
                name=template_plan.name,
                parent_id=parent_id,
                is_master=False,
                cloned_from_id=template_plan.plan_id,
                clone_status=CloneStatus.LINKED,
                created_at=now,
                updated_at=now,
            )
        )

        goal_plan = txn.get(GoalPlan, template_plan_id)
        if goal_plan is not None:
            txn.add(GoalPlan(plan_id=clone_plan_id))
            continue

        task_plan = txn.get(TaskPlan, template_plan_id)
        if task_plan is not None:
            txn.add(
                TaskPlan(
                    plan_id=clone_plan_id,
                    duration_minutes=task_plan.duration_minutes,
                    divisible=task_plan.divisible,
                    minimum_chunk_size_minutes=task_plan.minimum_chunk_size_minutes,
                    user_completed=False,
                    completed_at=None,
                )
            )
            continue

        repetition_plan = txn.get(RepetitionPlan, template_plan_id)
        if repetition_plan is not None:
            txn.add(
                RepetitionPlan(
                    plan_id=clone_plan_id,
                    repeat_mode=repetition_plan.repeat_mode,
                    start_time=repetition_plan.start_time,
                    repeat_interval_minutes=repetition_plan.repeat_interval_minutes,
                    manual_count=repetition_plan.manual_count,
                    end_time=repetition_plan.end_time,
                    template_root_id=repetition_plan.template_root_id,
                    default_instance_critical=repetition_plan.default_instance_critical,
                    generated_at=None,
                )
            )

    for template_plan in template_plans:
        clone_plan_id = clone_by_template_id[template_plan.plan_id]
        template_plan_id = PlanID(template_plan.plan_id)

        clone_repetition = txn.get(RepetitionPlan, clone_plan_id)
        if clone_repetition is not None:
            source_repetition = txn.get(RepetitionPlan, template_plan_id)
            assert source_repetition is not None  # type checker: clone has repetition row
            cloned_template_root_id = clone_by_template_id.get(source_repetition.template_root_id)
            if cloned_template_root_id is not None:
                clone_repetition.template_root_id = cloned_template_root_id

        if txn.get(GoalPlan, template_plan_id) is None:
            continue

        clone_goal_id = PlanID(clone_plan_id)
        chains = txn.scalars(
            select(GoalChildChain)
            .where(GoalChildChain.parent_goal_id == template_plan_id)
            .order_by(GoalChildChain.sort_order)
        ).all()
        for chain in chains:
            clone_chain_id = new_id(GoalChildChainID)
            txn.add(
                GoalChildChain(
                    goal_child_chain_id=clone_chain_id,
                    parent_goal_id=clone_goal_id,
                    is_critical=chain.is_critical,
                    sort_order=chain.sort_order,
                    created_at=now,
                    updated_at=now,
                )
            )
            items = txn.scalars(
                select(GoalChildChainItem)
                .where(GoalChildChainItem.chain_id == chain.goal_child_chain_id)
                .order_by(GoalChildChainItem.position)
            ).all()
            for item in items:
                clone_child_id = clone_by_template_id.get(item.child_plan_id)
                if clone_child_id is None:
                    continue
                txn.add(
                    GoalChildChainItem(
                        goal_child_chain_item_id=new_id(GoalChildChainItemID),
                        chain_id=clone_chain_id,
                        child_plan_id=clone_child_id,
                        position=item.position,
                    )
                )

    return clone_by_template_id[template_root_id]


def _collect_template_subtree(txn: Session, template_root_id: PlanID) -> tuple[Plan, ...]:
    root = txn.get(Plan, template_root_id)
    if root is None:
        return ()

    ordered: list[Plan] = []
    seen: set[uuid.UUID] = set()
    queue: deque[Plan] = deque([root])
    while queue:
        plan = queue.popleft()
        if plan.plan_id in seen:
            continue
        seen.add(plan.plan_id)
        ordered.append(plan)
        for child_id in txn.scalars(
            select(Plan.plan_id).where(Plan.parent_id == plan.plan_id)
        ).all():
            if child_id in seen:
                continue
            child = txn.get(Plan, child_id)
            if child is not None:
                queue.append(child)
    return tuple(ordered)

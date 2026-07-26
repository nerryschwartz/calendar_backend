"""Goal-parent plan creation and direct child ordering service."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, overload

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from calendar_backend.db.session import transaction
from calendar_backend.domain.dtos import (
    GoalPlanDTO,
    RepetitionPlanDTO,
    TaskPlanDTO,
    goal_plan_dto_from_plan,
    repetition_plan_dto_from_rows,
    task_plan_dto_from_rows,
)
from calendar_backend.domain.enums import CloneStatus, PlanKind
from calendar_backend.domain.errors import MessageCode, ServiceMessage
from calendar_backend.domain.ids import PlanID
from calendar_backend.domain.plan_create import (
    CreatePayload,
    GoalCreatePayload,
    RepetitionCreatePayload,
    TaskCreatePayload,
    validate_create_payload,
)
from calendar_backend.domain.results import ServiceResult, fail, ok
from calendar_backend.domain.time import Clock, SystemClock
from calendar_backend.models.plans import GoalPlan, Plan
from calendar_backend.services.plan_tree import PlanTreeService, detach_linked_self_and_descendants

_APPEND_POSITION = -1


class GoalService:
    def __init__(self, session: Session, clock: Clock | None = None) -> None:
        self._session = session
        self._clock = clock or SystemClock()
        self._plan_tree = PlanTreeService(session, clock)

    # Type checker: correlate kind, payload, and return DTO.
    @overload
    def create_child(
        self,
        parent_id: PlanID,
        kind: Literal[PlanKind.GOAL],
        payload: GoalCreatePayload,
        is_critical: bool,
    ) -> ServiceResult[GoalPlanDTO]: ...

    @overload
    def create_child(
        self,
        parent_id: PlanID,
        kind: Literal[PlanKind.TASK],
        payload: TaskCreatePayload,
        is_critical: bool,
    ) -> ServiceResult[TaskPlanDTO]: ...

    @overload
    def create_child(
        self,
        parent_id: PlanID,
        kind: Literal[PlanKind.REPETITION],
        payload: RepetitionCreatePayload,
        is_critical: bool,
    ) -> ServiceResult[RepetitionPlanDTO]: ...

    def create_child(  # pyright: ignore[reportInconsistentOverload]  # type checker: wider implementation
        self,
        parent_id: PlanID,
        kind: PlanKind,
        payload: CreatePayload,
        is_critical: bool,
    ) -> ServiceResult[GoalPlanDTO | TaskPlanDTO | RepetitionPlanDTO]:
        validation_error = validate_create_payload(kind, payload)
        if validation_error is not None:
            return fail(validation_error)

        with transaction(self._session) as txn:
            parent_error = _load_parent_goal(txn, parent_id, is_critical)
            if parent_error is not None:
                return fail(parent_error)

            return _persist_create_child(
                self._plan_tree,
                txn,
                parent_id=parent_id,
                kind=kind,
                payload=payload,
                is_critical=is_critical,
                now=self._clock.now_utc(),
            )

    # Type checker: within-bucket reorder.
    @overload
    def move_plan(self, plan_id: PlanID, position: int) -> ServiceResult[None]: ...

    # Type checker: cross-bucket move under the same parent goal.
    @overload
    def move_plan(
        self,
        plan_id: PlanID,
        is_critical: bool,
        position: int,
    ) -> ServiceResult[None]: ...

    def move_plan(  # pyright: ignore[reportInconsistentOverload]  # type checker: wider implementation
        self,
        plan_id: PlanID,
        is_critical_or_position: bool | int,
        position: int | None = None,
    ) -> ServiceResult[None]:
        with transaction(self._session) as txn:
            if position is None:
                assert isinstance(is_critical_or_position, int)
                return _move_within_bucket(
                    txn,
                    plan_id=plan_id,
                    position=is_critical_or_position,
                    now=self._clock.now_utc(),
                )
            assert isinstance(is_critical_or_position, bool)
            return _move_across_buckets(
                txn,
                plan_id=plan_id,
                is_critical=is_critical_or_position,
                position=position,
                now=self._clock.now_utc(),
            )


def _persist_create_child(
    plan_tree: PlanTreeService,
    txn: Session,
    *,
    parent_id: PlanID,
    kind: PlanKind,
    payload: CreatePayload,
    is_critical: bool,
    now: datetime,
) -> ServiceResult[GoalPlanDTO | TaskPlanDTO | RepetitionPlanDTO]:
    created = plan_tree.make_from_create_payload(
        txn,
        kind=kind,
        payload=payload,
        clone_status=CloneStatus.NOT_CLONED,
        now=now,
    )
    _attach_goal_child_ordering(
        plan_tree,
        txn,
        parent_goal_id=parent_id,
        child_plan_id=PlanID(created.plan.plan_id),
        is_critical=is_critical,
        now=now,
    )
    txn.flush()
    if kind == PlanKind.GOAL:
        return ok(goal_plan_dto_from_plan(created.plan))
    if kind == PlanKind.TASK:
        assert created.task_plan is not None  # type checker: kind TASK implies task row
        return ok(task_plan_dto_from_rows(created.plan, created.task_plan))
    assert created.repetition_plan is not None  # type checker: kind REPETITION implies detail row
    return ok(repetition_plan_dto_from_rows(created.plan, created.repetition_plan))


def _load_parent_goal(
    txn: Session,
    parent_id: PlanID,
    is_critical: bool,
) -> ServiceMessage | None:
    parent_plan = txn.get(Plan, parent_id)
    if parent_plan is None:
        return ServiceMessage(
            code=MessageCode.PLAN_NOT_FOUND,
            message="Plan not found",
            details={"plan_id": str(parent_id)},
        )

    if txn.get(GoalPlan, parent_id) is None:
        return ServiceMessage(
            code=MessageCode.INVALID_PARENT,
            message="Parent must be a goal plan",
            details={"parent_id": str(parent_id)},
        )

    if parent_plan.is_master and is_critical:
        return ServiceMessage(
            code=MessageCode.MASTER_CHILD_MUST_BE_NON_CRITICAL,
            message="Children of master must be non-critical",
            details={"parent_id": str(parent_id)},
        )

    return None


def _attach_goal_child_ordering(
    plan_tree: PlanTreeService,
    txn: Session,
    *,
    parent_goal_id: PlanID,
    child_plan_id: PlanID,
    is_critical: bool,
    now: datetime,
) -> None:
    sort_order = _next_sort_order_in_bucket(
        txn,
        parent_goal_id=parent_goal_id,
        is_critical=is_critical,
    )
    plan_tree.attach_under_parent(
        txn,
        child_plan_id=child_plan_id,
        parent_id=parent_goal_id,
        now=now,
    )
    child = txn.get(Plan, child_plan_id)
    assert child is not None
    child.goal_is_critical = is_critical
    child.goal_sort_order = sort_order
    child.updated_at = now


def _next_sort_order_in_bucket(
    txn: Session,
    *,
    parent_goal_id: PlanID,
    is_critical: bool,
) -> int:
    max_sort_order = txn.scalar(
        select(func.max(Plan.goal_sort_order)).where(
            Plan.parent_id == parent_goal_id,
            Plan.goal_is_critical == is_critical,
        )
    )
    return 0 if max_sort_order is None else max_sort_order + 1


def _sorted_bucket_children(
    txn: Session,
    *,
    parent_goal_id: PlanID,
    is_critical: bool,
) -> list[Plan]:
    return list(
        txn.scalars(
            select(Plan)
            .where(
                Plan.parent_id == parent_goal_id,
                Plan.goal_is_critical == is_critical,
                Plan.goal_sort_order.is_not(None),
            )
            .order_by(Plan.goal_sort_order, Plan.plan_id)
        ).all()
    )


def _assign_dense_goal_sort_orders(plans: list[Plan], *, now: datetime) -> None:
    for index, plan in enumerate(plans):
        plan.goal_sort_order = index
        plan.updated_at = now


def _move_within_bucket(
    txn: Session,
    *,
    plan_id: PlanID,
    position: int,
    now: datetime,
    loaded: tuple[Plan, PlanID] | None = None,
    siblings: list[Plan] | None = None,
) -> ServiceResult[None]:
    if loaded is None:
        loaded_result = _load_movable_goal_child(txn, plan_id)
        if isinstance(loaded_result, ServiceMessage):
            return fail(loaded_result)
        loaded = loaded_result
    plan, parent_goal_id = loaded
    assert plan.goal_is_critical is not None

    if siblings is None:
        siblings = _sorted_bucket_children(
            txn,
            parent_goal_id=parent_goal_id,
            is_critical=plan.goal_is_critical,
        )

    if position == _APPEND_POSITION:
        position = len(siblings) - 1

    current_index = next(
        index for index, sibling in enumerate(siblings) if sibling.plan_id == plan_id
    )
    if position < 0 or position >= len(siblings):
        return fail(
            ServiceMessage(
                code=MessageCode.INVALID_MOVE,
                message="Position out of range for within-bucket move",
                details={
                    "plan_id": str(plan_id),
                    "position": str(position),
                    "item_count": str(len(siblings)),
                },
            )
        )
    if position == current_index:
        return ok(None)

    siblings.pop(current_index)
    siblings.insert(position, plan)
    _assign_dense_goal_sort_orders(siblings, now=now)
    detach_linked_self_and_descendants(txn, plan, now)
    txn.flush()
    return ok(None)


def _move_across_buckets(
    txn: Session,
    *,
    plan_id: PlanID,
    is_critical: bool,
    position: int,
    now: datetime,
) -> ServiceResult[None]:
    loaded = _load_movable_goal_child(txn, plan_id)
    if isinstance(loaded, ServiceMessage):
        return fail(loaded)
    plan, parent_goal_id = loaded
    assert plan.goal_is_critical is not None

    if is_critical == plan.goal_is_critical:
        return _move_within_bucket(
            txn,
            plan_id=plan_id,
            position=position,
            now=now,
            loaded=loaded,
        )

    parent_plan = txn.get(Plan, parent_goal_id)
    assert parent_plan is not None
    if parent_plan.is_master and is_critical:
        return fail(
            ServiceMessage(
                code=MessageCode.MASTER_CHILD_MUST_BE_NON_CRITICAL,
                message="Children of master must be non-critical",
                details={"parent_id": str(parent_goal_id)},
            )
        )

    source_siblings = _sorted_bucket_children(
        txn,
        parent_goal_id=parent_goal_id,
        is_critical=plan.goal_is_critical,
    )
    source_siblings = [row for row in source_siblings if row.plan_id != plan_id]
    _assign_dense_goal_sort_orders(source_siblings, now=now)

    target_siblings = _sorted_bucket_children(
        txn,
        parent_goal_id=parent_goal_id,
        is_critical=is_critical,
    )
    insert_at = len(target_siblings) if position == _APPEND_POSITION else position
    if insert_at < 0 or insert_at > len(target_siblings):
        return fail(
            ServiceMessage(
                code=MessageCode.INVALID_MOVE,
                message="Position out of range for cross-bucket move",
                details={
                    "plan_id": str(plan_id),
                    "position": str(position),
                    "target_item_count": str(len(target_siblings)),
                },
            )
        )

    plan.goal_is_critical = is_critical
    target_siblings.insert(insert_at, plan)
    _assign_dense_goal_sort_orders(target_siblings, now=now)
    detach_linked_self_and_descendants(txn, plan, now)
    txn.flush()
    return ok(None)


def _load_movable_goal_child(
    txn: Session,
    plan_id: PlanID,
) -> tuple[Plan, PlanID] | ServiceMessage:
    plan = txn.get(Plan, plan_id)
    if plan is None:
        return ServiceMessage(
            code=MessageCode.PLAN_NOT_FOUND,
            message="Plan not found",
            details={"plan_id": str(plan_id)},
        )
    if plan.is_master:
        return ServiceMessage(
            code=MessageCode.MASTER_MUTATION_FORBIDDEN,
            message="Master plan cannot be moved",
            details={"plan_id": str(plan_id)},
        )
    if plan.parent_id is None:
        return ServiceMessage(
            code=MessageCode.PLAN_NOT_IN_CHAIN,
            message="Plan is not an ordered goal child",
            details={"plan_id": str(plan_id)},
        )
    if txn.get(GoalPlan, plan.parent_id) is None:
        return ServiceMessage(
            code=MessageCode.PLAN_NOT_IN_CHAIN,
            message="Plan is not an ordered goal child",
            details={"plan_id": str(plan_id)},
        )
    if plan.goal_is_critical is None or plan.goal_sort_order is None:
        return ServiceMessage(
            code=MessageCode.PLAN_NOT_IN_CHAIN,
            message="Plan is not an ordered goal child",
            details={"plan_id": str(plan_id)},
        )

    return plan, PlanID(plan.parent_id)

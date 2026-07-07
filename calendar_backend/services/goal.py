"""Goal-parent plan creation and child-chain layout service."""

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
from calendar_backend.domain.ids import GoalChildChainID, GoalChildChainItemID, PlanID, new_id
from calendar_backend.domain.plan_create import (
    CreatePayload,
    GoalCreatePayload,
    RepetitionCreatePayload,
    TaskCreatePayload,
    validate_create_payload,
)
from calendar_backend.domain.results import ServiceResult, fail, ok
from calendar_backend.domain.time import Clock, SystemClock
from calendar_backend.models.chains import GoalChildChain, GoalChildChainItem
from calendar_backend.models.plans import GoalPlan, Plan
from calendar_backend.services.plan_tree import PlanTreeService, detach_linked_self_and_descendants

_APPEND_POSITION = -1
_NEW_CHAIN_INDEX = -1


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

    # Type checker: single-index within-chain move.
    @overload
    def move_plan(self, plan_id: PlanID, position: int) -> ServiceResult[None]: ...

    # Type checker: cross-chain move with chain_index and position.
    @overload
    def move_plan(
        self,
        plan_id: PlanID,
        chain_index: int,
        position: int,
    ) -> ServiceResult[None]: ...

    def move_plan(  # pyright: ignore[reportInconsistentOverload]  # type checker: wider implementation
        self,
        plan_id: PlanID,
        chain_index_or_position: int,
        position: int | None = None,
    ) -> ServiceResult[None]:
        with transaction(self._session) as txn:
            if position is None:
                return _move_within_chain(
                    txn,
                    plan_id=plan_id,
                    position=chain_index_or_position,
                    now=self._clock.now_utc(),
                )
            return _move_across_chains(
                txn,
                plan_id=plan_id,
                chain_index=chain_index_or_position,
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
    _attach_to_goal_chain(
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
            message="Children of master must be in non-critical chains",
            details={"parent_id": str(parent_id)},
        )

    return None


def _attach_to_goal_chain(
    plan_tree: PlanTreeService,
    txn: Session,
    *,
    parent_goal_id: PlanID,
    child_plan_id: PlanID,
    is_critical: bool,
    now: datetime,
) -> None:
    plan_tree.attach_under_parent(
        txn,
        child_plan_id=child_plan_id,
        parent_id=parent_goal_id,
        now=now,
    )
    chain = _create_chain_at_bucket_end(
        txn,
        parent_goal_id=parent_goal_id,
        is_critical=is_critical,
        now=now,
    )
    txn.add(
        GoalChildChainItem(
            goal_child_chain_item_id=new_id(GoalChildChainItemID),
            chain_id=chain.goal_child_chain_id,
            child_plan_id=child_plan_id,
            position=0,
        )
    )


def _move_within_chain(
    txn: Session,
    *,
    plan_id: PlanID,
    position: int,
    now: datetime,
    loaded: tuple[Plan, GoalChildChainItem, GoalChildChain] | None = None,
    items: list[GoalChildChainItem] | None = None,
) -> ServiceResult[None]:
    if loaded is None:
        loaded_result = _load_movable_chain_item(txn, plan_id)
        if isinstance(loaded_result, ServiceMessage):
            return fail(loaded_result)
        loaded = loaded_result
    plan, item, chain = loaded

    if items is None:
        items = _sorted_chain_items(txn, GoalChildChainID(chain.goal_child_chain_id))

    if position == _APPEND_POSITION:
        position = len(items) - 1

    current_index = item.position
    if position < 0 or position >= len(items):
        return fail(
            ServiceMessage(
                code=MessageCode.INVALID_MOVE,
                message="Position out of range for within-chain move",
                details={
                    "plan_id": str(plan_id),
                    "position": str(position),
                    "item_count": str(len(items)),
                },
            )
        )
    if position == current_index:
        return ok(None)

    items.pop(current_index)
    items.insert(position, item)
    _assign_dense_positions(items)
    chain.updated_at = now
    detach_linked_self_and_descendants(txn, plan, now)
    txn.flush()
    return ok(None)


def _move_across_chains(
    txn: Session,
    *,
    plan_id: PlanID,
    chain_index: int,
    position: int,
    now: datetime,
) -> ServiceResult[None]:
    loaded = _load_movable_chain_item(txn, plan_id)
    if isinstance(loaded, ServiceMessage):
        return fail(loaded)
    plan, item, source_chain = loaded

    parent_goal_id = PlanID(source_chain.parent_goal_id)
    chains = _ordered_chains_for_goal(txn, parent_goal_id)

    if chain_index == _NEW_CHAIN_INDEX:
        target_chain = _create_chain_at_bucket_end(
            txn,
            parent_goal_id=parent_goal_id,
            is_critical=source_chain.is_critical,
            now=now,
        )
    elif chain_index < 0 or chain_index >= len(chains):
        return fail(
            ServiceMessage(
                code=MessageCode.INVALID_MOVE,
                message="chain_index out of range",
                details={
                    "plan_id": str(plan_id),
                    "chain_index": str(chain_index),
                    "chain_count": str(len(chains)),
                },
            )
        )
    else:
        target_chain = chains[chain_index]

    if target_chain.goal_child_chain_id == source_chain.goal_child_chain_id:
        source_items = _sorted_chain_items(txn, GoalChildChainID(source_chain.goal_child_chain_id))
        return _move_within_chain(
            txn,
            plan_id=plan_id,
            position=position,
            now=now,
            loaded=(plan, item, source_chain),
            items=source_items,
        )

    source_items = _sorted_chain_items(txn, GoalChildChainID(source_chain.goal_child_chain_id))
    source_items = [row for row in source_items if row.child_plan_id != plan_id]
    _assign_dense_positions(source_items)
    source_chain.updated_at = now

    target_items = _sorted_chain_items(txn, GoalChildChainID(target_chain.goal_child_chain_id))
    insert_at = len(target_items) if position == _APPEND_POSITION else position
    if insert_at < 0 or insert_at > len(target_items):
        return fail(
            ServiceMessage(
                code=MessageCode.INVALID_MOVE,
                message="Position out of range for cross-chain move",
                details={
                    "plan_id": str(plan_id),
                    "position": str(position),
                    "target_item_count": str(len(target_items)),
                },
            )
        )

    item.chain_id = target_chain.goal_child_chain_id
    target_items.insert(insert_at, item)
    _assign_dense_positions(target_items)
    target_chain.updated_at = now

    if not source_items:
        txn.flush()
        txn.delete(source_chain)

    detach_linked_self_and_descendants(txn, plan, now)
    txn.flush()
    return ok(None)


def _load_movable_chain_item(
    txn: Session,
    plan_id: PlanID,
) -> tuple[Plan, GoalChildChainItem, GoalChildChain] | ServiceMessage:
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

    item = txn.scalar(select(GoalChildChainItem).where(GoalChildChainItem.child_plan_id == plan_id))
    if item is None:
        return ServiceMessage(
            code=MessageCode.PLAN_NOT_IN_CHAIN,
            message="Plan is not in a goal child chain",
            details={"plan_id": str(plan_id)},
        )

    chain = txn.get(GoalChildChain, item.chain_id)
    assert chain is not None  # FK: goal_child_chain_item.chain_id -> goal_child_chain
    return plan, item, chain


def _ordered_chains_for_goal(
    txn: Session,
    parent_goal_id: PlanID,
) -> list[GoalChildChain]:
    return list(
        txn.scalars(
            select(GoalChildChain)
            .where(GoalChildChain.parent_goal_id == parent_goal_id)
            .order_by(
                GoalChildChain.is_critical.desc(),
                GoalChildChain.sort_order,
                GoalChildChain.goal_child_chain_id,
            )
        ).all()
    )


def _sorted_chain_items(txn: Session, chain_id: GoalChildChainID) -> list[GoalChildChainItem]:
    return list(
        txn.scalars(
            select(GoalChildChainItem)
            .where(GoalChildChainItem.chain_id == chain_id)
            .order_by(GoalChildChainItem.position, GoalChildChainItem.goal_child_chain_item_id)
        ).all()
    )


def _assign_dense_positions(items: list[GoalChildChainItem]) -> None:
    for index, item in enumerate(items):
        item.position = index


def _create_chain_at_bucket_end(
    txn: Session,
    *,
    parent_goal_id: PlanID,
    is_critical: bool,
    now: datetime,
) -> GoalChildChain:
    max_sort_order = txn.scalar(
        select(func.max(GoalChildChain.sort_order)).where(
            GoalChildChain.parent_goal_id == parent_goal_id,
            GoalChildChain.is_critical == is_critical,
        )
    )
    sort_order = 0 if max_sort_order is None else max_sort_order + 1
    chain_id = new_id(GoalChildChainID)
    chain = GoalChildChain(
        goal_child_chain_id=chain_id,
        parent_goal_id=parent_goal_id,
        is_critical=is_critical,
        sort_order=sort_order,
        created_at=now,
        updated_at=now,
    )
    txn.add(chain)
    txn.flush()
    return chain

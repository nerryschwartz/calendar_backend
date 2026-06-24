"""Goal-parent plan creation service."""

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
from calendar_backend.services.plan_tree import PlanTreeService


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
    if kind == PlanKind.GOAL:
        assert isinstance(
            payload, GoalCreatePayload
        )  # type checker: validate_create_payload already enforced match
        plan = plan_tree.make_goal(txn, name=payload.name, now=now)
        _attach_to_goal_chain(
            plan_tree,
            txn,
            parent_goal_id=parent_id,
            child_plan_id=PlanID(plan.plan_id),
            is_critical=is_critical,
            now=now,
        )
        txn.flush()
        return ok(goal_plan_dto_from_plan(plan))

    if kind == PlanKind.TASK:
        assert isinstance(
            payload, TaskCreatePayload
        )  # type checker: validate_create_payload already enforced match
        plan, task_plan = plan_tree.make_task(
            txn,
            name=payload.name,
            duration_minutes=payload.duration_minutes,
            divisible=payload.divisible,
            minimum_chunk_size_minutes=payload.minimum_chunk_size_minutes,
            now=now,
        )
        _attach_to_goal_chain(
            plan_tree,
            txn,
            parent_goal_id=parent_id,
            child_plan_id=PlanID(plan.plan_id),
            is_critical=is_critical,
            now=now,
        )
        txn.flush()
        return ok(task_plan_dto_from_rows(plan, task_plan))

    assert isinstance(
        payload, RepetitionCreatePayload
    )  # type checker: validate_create_payload already enforced match
    assert isinstance(
        payload.template_payload, GoalCreatePayload
    )  # type checker: repetition create validates goal template
    template_plan = plan_tree.make_goal(
        txn,
        name=payload.template_payload.name,
        clone_status=CloneStatus.TEMPLATE,
        now=now,
    )
    repetition_plan_row, repetition_detail = plan_tree.make_repetition(
        txn,
        name=payload.name,
        repeat_mode=payload.repeat_mode,
        start_time=payload.start_time,
        repeat_interval_minutes=payload.repeat_interval_minutes,
        manual_count=payload.manual_count,
        end_time=payload.end_time,
        template_root_id=PlanID(template_plan.plan_id),
        default_instance_critical=payload.default_instance_critical,
        now=now,
    )
    plan_tree.attach_under_parent(
        txn,
        child_plan_id=PlanID(template_plan.plan_id),
        parent_id=PlanID(repetition_plan_row.plan_id),
        now=now,
    )
    _attach_to_goal_chain(
        plan_tree,
        txn,
        parent_goal_id=parent_id,
        child_plan_id=PlanID(repetition_plan_row.plan_id),
        is_critical=is_critical,
        now=now,
    )
    txn.flush()
    return ok(repetition_plan_dto_from_rows(repetition_plan_row, repetition_detail))


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
    max_sort_order = txn.scalar(
        select(func.max(GoalChildChain.sort_order)).where(
            GoalChildChain.parent_goal_id == parent_goal_id,
            GoalChildChain.is_critical == is_critical,
        )
    )
    sort_order = 0 if max_sort_order is None else max_sort_order + 1

    chain_id = new_id(GoalChildChainID)
    txn.add(
        GoalChildChain(
            goal_child_chain_id=chain_id,
            parent_goal_id=parent_goal_id,
            is_critical=is_critical,
            sort_order=sort_order,
            created_at=now,
            updated_at=now,
        )
    )
    txn.add(
        GoalChildChainItem(
            goal_child_chain_item_id=new_id(GoalChildChainItemID),
            chain_id=chain_id,
            child_plan_id=child_plan_id,
            position=0,
        )
    )

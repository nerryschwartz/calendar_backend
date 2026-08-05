"""Block plan subtype self-edit service."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from calendar_backend.db.session import transaction
from calendar_backend.domain.blocks import validate_block_family, validate_block_scheduling_fields
from calendar_backend.domain.dtos import BlockPlanDTO, block_plan_dto_from_rows
from calendar_backend.domain.enums import PlanKind
from calendar_backend.domain.errors import MessageCode, ServiceMessage
from calendar_backend.domain.ids import PlanID
from calendar_backend.domain.prerequisites import validate_immediate_prerequisite_link
from calendar_backend.domain.results import ServiceResult, fail, ok
from calendar_backend.domain.time import Clock, SystemClock
from calendar_backend.models.plans import Plan
from calendar_backend.services.plan_tree import (
    detach_linked_self_and_descendants,
    load_plan_with_subtype,
)


class BlockService:
    def __init__(self, session: Session, clock: Clock | None = None) -> None:
        self._session = session
        self._clock = clock or SystemClock()

    def update_scheduling_fields(
        self,
        plan_id: PlanID,
        duration_minutes: int,
        divisible: bool,
        minimum_chunk_size_minutes: int | None,
        block_family: str,
    ) -> ServiceResult[BlockPlanDTO]:
        family_error = validate_block_family(block_family)
        if family_error is not None:
            return fail(family_error)

        validation_error = validate_block_scheduling_fields(
            duration_minutes,
            divisible,
            minimum_chunk_size_minutes,
        )
        if validation_error is not None:
            return fail(validation_error)

        with transaction(self._session) as txn:
            loaded = load_plan_with_subtype(txn, plan_id, expected_kind=PlanKind.BLOCK)
            if isinstance(loaded, ServiceMessage):
                return fail(loaded)
            plan, block_plan = loaded

            now = self._clock.now_utc()
            block_plan.duration_minutes = duration_minutes
            block_plan.divisible = divisible
            block_plan.minimum_chunk_size_minutes = minimum_chunk_size_minutes
            block_plan.block_family = block_family
            plan.updated_at = now
            detach_linked_self_and_descendants(txn, plan, now)
            txn.flush()
            return ok(block_plan_dto_from_rows(plan, block_plan))

    def mark_complete(self, plan_id: PlanID) -> ServiceResult[BlockPlanDTO]:
        with transaction(self._session) as txn:
            loaded = load_plan_with_subtype(txn, plan_id, expected_kind=PlanKind.BLOCK)
            if isinstance(loaded, ServiceMessage):
                return fail(loaded)
            plan, block_plan = loaded

            if block_plan.user_completed:
                return fail(
                    ServiceMessage(
                        code=MessageCode.TASK_ALREADY_COMPLETED,
                        message="Block is already completed",
                        details={"plan_id": str(plan_id)},
                    )
                )

            now = self._clock.now_utc()
            block_plan.user_completed = True
            block_plan.completed_at = now
            plan.updated_at = now
            detach_linked_self_and_descendants(txn, plan, now)
            txn.flush()
            return ok(block_plan_dto_from_rows(plan, block_plan))

    def reopen(self, plan_id: PlanID) -> ServiceResult[BlockPlanDTO]:
        with transaction(self._session) as txn:
            loaded = load_plan_with_subtype(txn, plan_id, expected_kind=PlanKind.BLOCK)
            if isinstance(loaded, ServiceMessage):
                return fail(loaded)
            plan, block_plan = loaded

            now = self._clock.now_utc()
            block_plan.user_completed = False
            block_plan.completed_at = None
            plan.updated_at = now
            txn.flush()
            return ok(block_plan_dto_from_rows(plan, block_plan))

    def set_immediate_prerequisite(
        self,
        block_id: PlanID,
        predecessor_id: PlanID,
    ) -> ServiceResult[BlockPlanDTO]:
        with transaction(self._session) as txn:
            loaded = load_plan_with_subtype(txn, block_id, expected_kind=PlanKind.BLOCK)
            if isinstance(loaded, ServiceMessage):
                return fail(loaded)
            plan, block_plan = loaded

            validation_error = validate_immediate_prerequisite_link(
                successor_id=block_id,
                predecessor_id=predecessor_id,
                plans_by_id=_load_plans_by_id(txn),
            )
            if validation_error is not None:
                return fail(validation_error)

            now = self._clock.now_utc()
            block_plan.immediate_prerequisite_plan_id = predecessor_id
            plan.updated_at = now
            detach_linked_self_and_descendants(txn, plan, now)
            txn.flush()
            return ok(block_plan_dto_from_rows(plan, block_plan))

    def clear_immediate_prerequisite(self, block_id: PlanID) -> ServiceResult[BlockPlanDTO]:
        with transaction(self._session) as txn:
            loaded = load_plan_with_subtype(txn, block_id, expected_kind=PlanKind.BLOCK)
            if isinstance(loaded, ServiceMessage):
                return fail(loaded)
            plan, block_plan = loaded

            if block_plan.immediate_prerequisite_plan_id is None:
                return ok(block_plan_dto_from_rows(plan, block_plan))

            now = self._clock.now_utc()
            block_plan.immediate_prerequisite_plan_id = None
            plan.updated_at = now
            detach_linked_self_and_descendants(txn, plan, now)
            txn.flush()
            return ok(block_plan_dto_from_rows(plan, block_plan))


def _load_plans_by_id(txn: Session) -> dict[uuid.UUID, Plan]:
    plans = txn.scalars(
        select(Plan).options(
            selectinload(Plan.task_plan),
            selectinload(Plan.block_plan),
            selectinload(Plan.repetition_plan),
        )
    ).all()
    return {plan.plan_id: plan for plan in plans}

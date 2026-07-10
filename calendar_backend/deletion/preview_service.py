"""Deletion preview service: graph load and impact analysis for candidate deletes."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from calendar_backend.db.session import transaction
from calendar_backend.domain.deletion import (
    DeletionOperation,
    DeletionPreview,
    build_deletion_preview,
)
from calendar_backend.domain.errors import MessageCode, ServiceMessage
from calendar_backend.domain.ids import PlanID
from calendar_backend.domain.results import ServiceResult, fail, ok
from calendar_backend.domain.time import Clock, SystemClock
from calendar_backend.models.calendar import CalendarEntry
from calendar_backend.models.chains import GoalChildChain
from calendar_backend.models.constraints import TimeConstraintGroup
from calendar_backend.models.plans import GoalPlan, Plan, RepetitionPlan


class DeletionPreviewService:
    """Compute exactly what would be deleted for a candidate deletion operation."""

    def __init__(self, session: Session, clock: Clock | None = None) -> None:
        self._session = session
        self._clock = clock or SystemClock()

    def preview_delete_plan(self, plan_id: PlanID) -> ServiceResult[DeletionPreview]:
        return self.preview_delete(DeletionOperation(root_plan_id=plan_id))

    def preview_delete(self, operation: DeletionOperation) -> ServiceResult[DeletionPreview]:
        with transaction(self._session) as txn:
            root_plan = txn.get(Plan, operation.root_plan_id)
            if root_plan is None:
                return fail(
                    ServiceMessage(
                        code=MessageCode.PLAN_NOT_FOUND,
                        message="Plan not found",
                        details={"plan_id": str(operation.root_plan_id)},
                    )
                )
            if root_plan.is_master:
                return fail(
                    ServiceMessage(
                        code=MessageCode.MASTER_DELETE_FORBIDDEN,
                        message="Master plan cannot be deleted",
                        details={"plan_id": str(operation.root_plan_id)},
                    )
                )

            plans, calendar_entries = _load_deletion_graph(txn)
            preview = build_deletion_preview(operation, plans, calendar_entries)
            return ok(preview)


def _load_deletion_graph(
    txn: Session,
) -> tuple[tuple[Plan, ...], tuple[CalendarEntry, ...]]:
    plans = tuple(
        txn.scalars(
            select(Plan).options(
                selectinload(Plan.goal_plan)
                .selectinload(GoalPlan.chains)
                .selectinload(GoalChildChain.items),
                selectinload(Plan.task_plan),
                selectinload(Plan.repetition_plan).selectinload(RepetitionPlan.instances),
                selectinload(Plan.constraint_groups).selectinload(TimeConstraintGroup.windows),
            )
        ).all()
    )
    plan_ids = [plan.plan_id for plan in plans]
    if not plan_ids:
        return plans, ()

    calendar_entries = tuple(
        txn.scalars(select(CalendarEntry).where(CalendarEntry.source_plan_id.in_(plan_ids))).all()
    )
    return plans, calendar_entries

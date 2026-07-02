"""Task plan subtype self-edit service."""

from __future__ import annotations

from sqlalchemy.orm import Session

from calendar_backend.db.session import transaction
from calendar_backend.domain.dtos import TaskPlanDTO, task_plan_dto_from_rows
from calendar_backend.domain.enums import PlanKind
from calendar_backend.domain.errors import MessageCode, ServiceMessage
from calendar_backend.domain.ids import PlanID
from calendar_backend.domain.results import ServiceResult, fail, ok
from calendar_backend.domain.tasks import validate_task_scheduling_fields
from calendar_backend.domain.time import Clock, SystemClock
from calendar_backend.models.plans import Plan, TaskPlan
from calendar_backend.services.plan_tree import detach_linked_self_and_descendants


class TaskService:
    def __init__(self, session: Session, clock: Clock | None = None) -> None:
        self._session = session
        self._clock = clock or SystemClock()

    def update_scheduling_fields(
        self,
        plan_id: PlanID,
        duration_minutes: int,
        divisible: bool,
        minimum_chunk_size_minutes: int | None,
    ) -> ServiceResult[TaskPlanDTO]:
        validation_error = validate_task_scheduling_fields(
            duration_minutes,
            divisible,
            minimum_chunk_size_minutes,
        )
        if validation_error is not None:
            return fail(validation_error)

        with transaction(self._session) as txn:
            loaded = _load_task_plan(txn, plan_id)
            if isinstance(loaded, ServiceMessage):
                return fail(loaded)
            plan, task_plan = loaded

            now = self._clock.now_utc()
            task_plan.duration_minutes = duration_minutes
            task_plan.divisible = divisible
            task_plan.minimum_chunk_size_minutes = minimum_chunk_size_minutes
            plan.updated_at = now
            detach_linked_self_and_descendants(txn, plan, now)
            txn.flush()
            return ok(task_plan_dto_from_rows(plan, task_plan))

    def mark_complete(self, plan_id: PlanID) -> ServiceResult[TaskPlanDTO]:
        with transaction(self._session) as txn:
            loaded = _load_task_plan(txn, plan_id)
            if isinstance(loaded, ServiceMessage):
                return fail(loaded)
            plan, task_plan = loaded

            if task_plan.user_completed:
                return fail(
                    ServiceMessage(
                        code=MessageCode.TASK_ALREADY_COMPLETED,
                        message="Task is already completed",
                        details={"plan_id": str(plan_id)},
                    )
                )

            now = self._clock.now_utc()
            task_plan.user_completed = True
            task_plan.completed_at = now
            plan.updated_at = now
            detach_linked_self_and_descendants(txn, plan, now)
            txn.flush()
            return ok(task_plan_dto_from_rows(plan, task_plan))

    def reopen(self, plan_id: PlanID) -> ServiceResult[TaskPlanDTO]:
        with transaction(self._session) as txn:
            loaded = _load_task_plan(txn, plan_id)
            if isinstance(loaded, ServiceMessage):
                return fail(loaded)
            plan, task_plan = loaded

            now = self._clock.now_utc()
            task_plan.user_completed = False
            task_plan.completed_at = None
            plan.updated_at = now
            txn.flush()
            return ok(task_plan_dto_from_rows(plan, task_plan))


def _load_task_plan(txn: Session, plan_id: PlanID) -> tuple[Plan, TaskPlan] | ServiceMessage:
    plan = txn.get(Plan, plan_id)
    if plan is None:
        return ServiceMessage(
            code=MessageCode.PLAN_NOT_FOUND,
            message="Plan not found",
            details={"plan_id": str(plan_id)},
        )
    if plan.plan_kind != PlanKind.TASK:
        return ServiceMessage(
            code=MessageCode.PLAN_SUBTYPE_MISMATCH,
            message="Plan is not a task",
            details={
                "plan_id": str(plan_id),
                "plan_kind": plan.plan_kind.value,
            },
        )
    task_plan = plan.task_plan
    if task_plan is None:
        return ServiceMessage(
            code=MessageCode.PLAN_SUBTYPE_MISMATCH,
            message="Task plan is missing task_plan detail row",
            details={"plan_id": str(plan_id)},
        )
    return plan, task_plan

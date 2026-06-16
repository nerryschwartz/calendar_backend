"""Master plan bootstrap and retrieval."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from calendar_backend.db.session import transaction
from calendar_backend.domain.dtos import GoalPlanDTO, goal_plan_dto_from_plan
from calendar_backend.domain.enums import CloneStatus, PlanKind
from calendar_backend.domain.ids import PlanID, new_id
from calendar_backend.domain.results import ServiceResult, ok
from calendar_backend.domain.time import Clock, SystemClock
from calendar_backend.models.plans import GoalPlan, Plan

MASTER_PLAN_NAME = "master"


class MasterPlanService:
    def __init__(self, session: Session, clock: Clock | None = None) -> None:
        self._session = session
        self._clock = clock or SystemClock()

    def ensure_master_exists(self) -> ServiceResult[GoalPlanDTO]:
        with transaction(self._session) as txn:
            existing = txn.scalar(select(Plan).where(Plan.is_master.is_(True)))
            if existing is not None:
                return ok(goal_plan_dto_from_plan(existing))

            now = self._clock.now_utc()
            plan_id = new_id(PlanID)
            plan = Plan(
                plan_id=plan_id,
                plan_kind=PlanKind.GOAL,
                name=MASTER_PLAN_NAME,
                parent_id=None,
                is_master=True,
                cloned_from_id=None,
                clone_status=CloneStatus.NOT_CLONED,
                created_at=now,
                updated_at=now,
            )
            txn.add(plan)
            txn.add(GoalPlan(plan_id=plan_id))
            txn.flush()
            return ok(goal_plan_dto_from_plan(plan))

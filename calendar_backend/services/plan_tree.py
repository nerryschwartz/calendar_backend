"""Plan tree insert/attach primitives and move/rename/delete service."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from calendar_backend.domain.enums import CloneStatus, PlanKind, RepeatMode
from calendar_backend.domain.ids import PlanID, new_id
from calendar_backend.domain.time import Clock, SystemClock
from calendar_backend.models.plans import GoalPlan, Plan, RepetitionPlan, TaskPlan


class PlanTreeService:
    """Tree-wide mutations and repo-internal insert/attach primitives.

    Sibling services (for example ``GoalService``) may call ``make_*`` and
    ``attach_under_parent``; those methods are not part of the external API.
    """

    def __init__(self, session: Session, clock: Clock | None = None) -> None:
        self._session = session
        self._clock = clock or SystemClock()

    def make_goal(
        self,
        txn: Session,
        *,
        name: str,
        clone_status: CloneStatus = CloneStatus.NOT_CLONED,
        now: datetime,
    ) -> Plan:
        plan_id = new_id(PlanID)
        plan = Plan(
            plan_id=plan_id,
            plan_kind=PlanKind.GOAL,
            name=name,
            parent_id=None,
            is_master=False,
            cloned_from_id=None,
            clone_status=clone_status,
            created_at=now,
            updated_at=now,
        )
        txn.add(plan)
        txn.add(GoalPlan(plan_id=plan_id))
        return plan

    def make_task(
        self,
        txn: Session,
        *,
        name: str,
        duration_minutes: int,
        divisible: bool,
        minimum_chunk_size_minutes: int | None,
        now: datetime,
    ) -> tuple[Plan, TaskPlan]:
        plan_id = new_id(PlanID)
        plan = Plan(
            plan_id=plan_id,
            plan_kind=PlanKind.TASK,
            name=name,
            parent_id=None,
            is_master=False,
            cloned_from_id=None,
            clone_status=CloneStatus.NOT_CLONED,
            created_at=now,
            updated_at=now,
        )
        txn.add(plan)
        task_plan = TaskPlan(
            plan_id=plan_id,
            duration_minutes=duration_minutes,
            divisible=divisible,
            minimum_chunk_size_minutes=minimum_chunk_size_minutes,
            user_completed=False,
            completed_at=None,
        )
        txn.add(task_plan)
        return plan, task_plan

    def make_repetition(
        self,
        txn: Session,
        *,
        name: str,
        repeat_mode: RepeatMode,
        start_time: datetime,
        repeat_interval_minutes: int,
        manual_count: int | None,
        end_time: datetime | None,
        template_root_id: PlanID,
        default_instance_critical: bool,
        now: datetime,
    ) -> tuple[Plan, RepetitionPlan]:
        plan_id = new_id(PlanID)
        plan = Plan(
            plan_id=plan_id,
            plan_kind=PlanKind.REPETITION,
            name=name,
            parent_id=None,
            is_master=False,
            cloned_from_id=None,
            clone_status=CloneStatus.NOT_CLONED,
            created_at=now,
            updated_at=now,
        )
        txn.add(plan)
        repetition_detail = RepetitionPlan(
            plan_id=plan_id,
            repeat_mode=repeat_mode,
            start_time=start_time,
            repeat_interval_minutes=repeat_interval_minutes,
            manual_count=manual_count,
            end_time=end_time,
            template_root_id=template_root_id,
            default_instance_critical=default_instance_critical,
            generated_at=None,
        )
        txn.add(repetition_detail)
        return plan, repetition_detail

    def attach_under_parent(
        self,
        txn: Session,
        *,
        child_plan_id: PlanID,
        parent_id: PlanID,
        now: datetime,
    ) -> None:
        child_plan = txn.get(Plan, child_plan_id)
        child_plan.parent_id = parent_id  # pyright: ignore[reportOptionalMemberAccess]  # type checker: trusted internal caller
        child_plan.updated_at = now  # pyright: ignore[reportOptionalMemberAccess]

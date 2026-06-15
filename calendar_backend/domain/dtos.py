"""Frozen DTOs returned by public service methods."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from calendar_backend.domain.ids import PlanID
from calendar_backend.models.plans import Plan


@dataclass(frozen=True)
class GoalPlanDTO:
    plan_id: PlanID
    name: str
    is_master: bool
    parent_id: PlanID | None
    created_at: datetime
    updated_at: datetime


def goal_plan_dto_from_plan(plan: Plan) -> GoalPlanDTO:
    return GoalPlanDTO(
        plan_id=PlanID(plan.plan_id),
        name=plan.name,
        is_master=plan.is_master,
        parent_id=PlanID(plan.parent_id) if plan.parent_id is not None else None,
        created_at=plan.created_at,
        updated_at=plan.updated_at,
    )

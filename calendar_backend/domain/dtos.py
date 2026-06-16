"""Frozen DTOs returned by public service methods."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from calendar_backend.domain.enums import FreeTimeWeekStartDay
from calendar_backend.domain.ids import PlanID, TimeConstraintGroupID, TimeWindowID
from calendar_backend.models.plans import Plan
from calendar_backend.models.settings import AppSettings


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


@dataclass(frozen=True)
class AppSettingsDTO:
    local_timezone: str
    master_horizon_duration_minutes: int
    exact_solver_time_limit_seconds: int
    exact_solver_model_size_limit: int
    heuristic_enabled: bool
    free_time_week_start_day: FreeTimeWeekStartDay
    updated_at: datetime


def app_settings_dto_from_row(row: AppSettings) -> AppSettingsDTO:
    return AppSettingsDTO(
        local_timezone=row.local_timezone,
        master_horizon_duration_minutes=row.master_horizon_duration_minutes,
        exact_solver_time_limit_seconds=row.exact_solver_time_limit_seconds,
        exact_solver_model_size_limit=row.exact_solver_model_size_limit,
        heuristic_enabled=row.heuristic_enabled,
        free_time_week_start_day=row.free_time_week_start_day,
        updated_at=row.updated_at,
    )


@dataclass(frozen=True)
class MasterHorizonDTO:
    horizon_start: datetime
    horizon_end: datetime
    constraint_group_id: TimeConstraintGroupID
    time_window_id: TimeWindowID

"""Frozen DTOs returned by public service methods."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from calendar_backend.domain.enums import ConstraintKind, FreeTimeWeekStartDay
from calendar_backend.domain.ids import PlanID, TimeConstraintGroupID, TimeWindowID
from calendar_backend.models.constraints import TimeConstraintGroup, TimeWindow
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


@dataclass(frozen=True)
class _TimeWindowDTO:
    time_window_id: TimeWindowID
    start_time: datetime
    end_time: datetime


@dataclass(frozen=True)
class TimeConstraintGroupDTO:
    constraint_group_id: TimeConstraintGroupID
    plan_id: PlanID
    constraint_kind: ConstraintKind
    windows: tuple[_TimeWindowDTO, ...]


def time_constraint_group_dto_from_rows(
    group: TimeConstraintGroup,
    windows: tuple[TimeWindow, ...],
) -> TimeConstraintGroupDTO:
    return TimeConstraintGroupDTO(
        constraint_group_id=TimeConstraintGroupID(group.time_constraint_group_id),
        plan_id=PlanID(group.plan_id),
        constraint_kind=group.constraint_kind,
        windows=tuple(
            _TimeWindowDTO(
                time_window_id=TimeWindowID(window.time_window_id),
                start_time=window.start_time,
                end_time=window.end_time,
            )
            for window in windows
        ),
    )

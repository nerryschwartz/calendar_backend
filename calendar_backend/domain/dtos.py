"""Frozen DTOs returned by public service methods."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from calendar_backend.domain.enums import ConstraintKind, FreeTimeWeekStartDay, RepeatMode
from calendar_backend.domain.ids import PlanID, TimeConstraintGroupID, TimeWindowID
from calendar_backend.models.constraints import TimeConstraintGroup, TimeWindow
from calendar_backend.models.plans import Plan, RepetitionPlan, TaskPlan
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
class TaskPlanDTO:
    plan_id: PlanID
    name: str
    is_master: bool
    parent_id: PlanID | None
    duration_minutes: int
    divisible: bool
    minimum_chunk_size_minutes: int | None
    user_completed: bool
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


def task_plan_dto_from_rows(plan: Plan, task_plan: TaskPlan) -> TaskPlanDTO:
    return TaskPlanDTO(
        plan_id=PlanID(plan.plan_id),
        name=plan.name,
        is_master=plan.is_master,
        parent_id=PlanID(plan.parent_id) if plan.parent_id is not None else None,
        duration_minutes=task_plan.duration_minutes,
        divisible=task_plan.divisible,
        minimum_chunk_size_minutes=task_plan.minimum_chunk_size_minutes,
        user_completed=task_plan.user_completed,
        completed_at=task_plan.completed_at,
        created_at=plan.created_at,
        updated_at=plan.updated_at,
    )


@dataclass(frozen=True)
class RepetitionPlanDTO:
    plan_id: PlanID
    name: str
    is_master: bool
    parent_id: PlanID | None
    repeat_mode: RepeatMode
    start_time: datetime
    repeat_interval_minutes: int
    manual_count: int | None
    end_time: datetime | None
    template_root_id: PlanID
    default_instance_critical: bool
    generated_at: datetime | None
    created_at: datetime
    updated_at: datetime


def repetition_plan_dto_from_rows(plan: Plan, repetition_plan: RepetitionPlan) -> RepetitionPlanDTO:
    return RepetitionPlanDTO(
        plan_id=PlanID(plan.plan_id),
        name=plan.name,
        is_master=plan.is_master,
        parent_id=PlanID(plan.parent_id) if plan.parent_id is not None else None,
        repeat_mode=repetition_plan.repeat_mode,
        start_time=repetition_plan.start_time,
        repeat_interval_minutes=repetition_plan.repeat_interval_minutes,
        manual_count=repetition_plan.manual_count,
        end_time=repetition_plan.end_time,
        template_root_id=PlanID(repetition_plan.template_root_id),
        default_instance_critical=repetition_plan.default_instance_critical,
        generated_at=repetition_plan.generated_at,
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

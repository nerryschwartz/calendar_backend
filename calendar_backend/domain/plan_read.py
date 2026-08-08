"""Frozen DTOs for plan tree read operations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from calendar_backend.domain.dtos import (
    BlockPlanDTO,
    GoalPlanDTO,
    RepetitionPlanDTO,
    TaskPlanDTO,
    TimeConstraintGroupDTO,
)
from calendar_backend.domain.enums import PlanKind
from calendar_backend.domain.ids import PlanID


@dataclass(frozen=True)
class PlanChildSummaryDTO:
    plan_id: PlanID
    name: str
    plan_kind: PlanKind
    goal_is_critical: bool | None
    goal_sort_order: int | None


@dataclass(frozen=True)
class PlanAncestryItemDTO:
    plan_id: PlanID
    name: str
    plan_kind: PlanKind


@dataclass(frozen=True)
class PlanPrerequisiteSummaryDTO:
    prerequisite_plan_id: PlanID
    name: str
    plan_kind: PlanKind


@dataclass(frozen=True)
class PlanDetailDTO:
    plan_id: PlanID
    name: str
    plan_kind: PlanKind
    is_master: bool
    parent_id: PlanID | None
    goal_is_critical: bool | None
    goal_sort_order: int | None
    created_at: datetime
    updated_at: datetime
    ancestry: tuple[PlanAncestryItemDTO, ...]
    children: tuple[PlanChildSummaryDTO, ...]
    prerequisite_plan_ids: tuple[PlanID, ...]
    prerequisites: tuple[PlanPrerequisiteSummaryDTO, ...]
    time_constraint_groups: tuple[TimeConstraintGroupDTO, ...]
    goal_detail: GoalPlanDTO | None
    task_detail: TaskPlanDTO | None
    block_detail: BlockPlanDTO | None
    repetition_detail: RepetitionPlanDTO | None


@dataclass(frozen=True)
class PlanSearchResultDTO:
    plan_id: PlanID
    name: str
    plan_kind: PlanKind
    parent_id: PlanID | None

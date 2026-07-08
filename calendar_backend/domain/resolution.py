"""Frozen DTOs for task resolution output per design §8.2."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from calendar_backend.domain.enums import ConstraintKind
from calendar_backend.domain.errors import ServiceMessage
from calendar_backend.domain.ids import GoalChildChainID, PlanID, TimeConstraintGroupID
from calendar_backend.domain.time import TimeWindow

ChainPathStep = tuple[GoalChildChainID, int]


@dataclass(frozen=True)
class ConstraintSource:
    plan_id: PlanID
    constraint_kind: ConstraintKind
    constraint_group_id: TimeConstraintGroupID


@dataclass(frozen=True)
class ResolvedTask:
    plan_id: PlanID
    name: str
    duration_minutes: int
    divisible: bool
    minimum_chunk_size_minutes: int | None
    user_completed: bool
    completed_at: datetime | None
    effective_time_windows: tuple[TimeWindow, ...]
    constraint_sources: tuple[ConstraintSource, ...]
    priority_path: tuple[int, ...]
    criticality_path: tuple[bool, ...]
    parent_path: tuple[PlanID, ...]
    chain_path: tuple[ChainPathStep, ...]
    validation_errors: tuple[ServiceMessage, ...]


@dataclass(frozen=True)
class ResolvedPrecedenceConstraint:
    predecessor_task_id: PlanID
    successor_task_id: PlanID
    source_chain_id: GoalChildChainID
    reason: str


@dataclass(frozen=True)
class ResolveTasksResult:
    run_started_at: datetime
    valid_incomplete: tuple[ResolvedTask, ...]
    valid_completed: tuple[ResolvedTask, ...]
    invalid_incomplete: tuple[ResolvedTask, ...]
    invalid_completed: tuple[ResolvedTask, ...]
    precedence_constraints: tuple[ResolvedPrecedenceConstraint, ...]
    warnings: tuple[ServiceMessage, ...]

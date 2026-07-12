from __future__ import annotations

import uuid
from datetime import UTC, datetime

from calendar_backend.domain.ids import PlanID
from calendar_backend.domain.time import TimeWindow
from calendar_backend.scheduling.decomposition import AssignmentComponent
from calendar_backend.scheduling.input import (
    AssignmentInput,
    OccupiedInterval,
    PrecedenceEdge,
    SchedulableTask,
    SolverLimits,
)

RUN_AT = datetime(2026, 6, 7, 10, 0, tzinfo=UTC)


def utc(y: int, m: int, d: int, h: int, mi: int) -> datetime:
    return datetime(y, m, d, h, mi, 0, tzinfo=UTC)


def window(start: datetime, end: datetime) -> TimeWindow:
    return TimeWindow(start_time=start, end_time=end)


def plan_id(value: uuid.UUID | None = None) -> PlanID:
    return PlanID(value or uuid.uuid4())


def schedulable_task(
    *,
    task_id: PlanID | None = None,
    duration_minutes: int,
    effective_time_windows: tuple[TimeWindow, ...],
    divisible: bool = False,
    minimum_chunk_size_minutes: int | None = None,
    priority_path: tuple[int, ...] = (0,),
) -> SchedulableTask:
    return SchedulableTask(
        plan_id=task_id or plan_id(),
        duration_minutes=duration_minutes,
        divisible=divisible,
        minimum_chunk_size_minutes=minimum_chunk_size_minutes,
        effective_time_windows=effective_time_windows,
        priority_path=priority_path,
    )


def assignment_input(
    *,
    tasks: tuple[SchedulableTask, ...],
    precedence_edges: tuple[PrecedenceEdge, ...] = (),
    occupied_intervals: tuple[OccupiedInterval, ...] = (),
    previous_placements_by_task_id: tuple[tuple[PlanID, tuple[TimeWindow, ...]], ...] = (),
    run_started_at: datetime = RUN_AT,
    solver_limits: SolverLimits | None = None,
) -> AssignmentInput:
    return AssignmentInput(
        run_started_at=run_started_at,
        tasks=tasks,
        precedence_edges=precedence_edges,
        occupied_intervals=occupied_intervals,
        previous_placements_by_task_id=previous_placements_by_task_id,
        solver_limits=solver_limits,
    )


def _stable_plan_id(label: str) -> PlanID:
    return PlanID(uuid.uuid5(uuid.NAMESPACE_DNS, label))


def two_disconnected_chain_input() -> AssignmentInput:
    """Two precedence chains (two components) with narrow morning windows."""
    chain_a_first = _stable_plan_id("slice7-chain-a-first")
    chain_a_second = _stable_plan_id("slice7-chain-a-second")
    chain_b_first = _stable_plan_id("slice7-chain-b-first")
    chain_b_second = _stable_plan_id("slice7-chain-b-second")
    morning = window(utc(2026, 6, 7, 9, 0), utc(2026, 6, 7, 12, 0))

    return assignment_input(
        tasks=(
            schedulable_task(
                task_id=chain_a_first,
                duration_minutes=30,
                effective_time_windows=(morning,),
            ),
            schedulable_task(
                task_id=chain_a_second,
                duration_minutes=30,
                effective_time_windows=(morning,),
            ),
            schedulable_task(
                task_id=chain_b_first,
                duration_minutes=30,
                effective_time_windows=(morning,),
            ),
            schedulable_task(
                task_id=chain_b_second,
                duration_minutes=30,
                effective_time_windows=(morning,),
            ),
        ),
        precedence_edges=(
            PrecedenceEdge(
                predecessor_plan_id=chain_a_first,
                successor_plan_id=chain_a_second,
            ),
            PrecedenceEdge(
                predecessor_plan_id=chain_b_first,
                successor_plan_id=chain_b_second,
            ),
        ),
    )


def solver_limits(
    *,
    time_limit_seconds: int = 30,
    model_size_limit: int = 10_000,
) -> SolverLimits:
    return SolverLimits(
        time_limit_seconds=time_limit_seconds,
        model_size_limit=model_size_limit,
    )


def assignment_component(
    *,
    tasks: tuple[SchedulableTask, ...],
    precedence_edges: tuple[PrecedenceEdge, ...] = (),
    occupied_intervals: tuple[OccupiedInterval, ...] = (),
    previous_placements_by_task_id: tuple[tuple[PlanID, tuple[TimeWindow, ...]], ...] = (),
    run_started_at: datetime = RUN_AT,
    solver_limits_value: SolverLimits | None = None,
) -> AssignmentComponent:
    return AssignmentComponent(
        run_started_at=run_started_at,
        tasks=tasks,
        precedence_edges=precedence_edges,
        occupied_intervals=occupied_intervals,
        previous_placements_by_task_id=previous_placements_by_task_id,
        solver_limits=solver_limits_value or solver_limits(),
    )


def occupied(
    start: datetime, end: datetime, source_plan_id: PlanID | None = None
) -> OccupiedInterval:
    return OccupiedInterval(start_time=start, end_time=end, source_plan_id=source_plan_id)

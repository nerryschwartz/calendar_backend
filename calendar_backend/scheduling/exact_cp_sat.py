"""OR-Tools CP-SAT exact assignment solver.

All ortools imports for the scheduling package must live in this module only.
"""

# pyright: reportAttributeAccessIssue=false

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from ortools.sat.python import cp_model

from calendar_backend.domain.ids import PlanID
from calendar_backend.domain.time import TimeWindow
from calendar_backend.scheduling import decomposition
from calendar_backend.scheduling.decomposition import AssignmentComponent
from calendar_backend.scheduling.feasibility import validate_full_assignment
from calendar_backend.scheduling.input import (
    AssignmentInput,
    OccupiedInterval,
    SchedulableTask,
    SolverLimits,
)
from calendar_backend.scheduling.types import (
    AssignmentSolverResult,
    TaskAssignment,
    feasible_result,
)

_DEFAULT_TIME_LIMIT_SECONDS = 30

estimate_model_variable_count = decomposition.estimate_model_variable_count
model_size_guard_exceeded = decomposition.model_size_guard_exceeded


@dataclass(frozen=True)
class _SegmentVariables:
    presence: cp_model.IntVar
    start: cp_model.IntVar
    duration: cp_model.IntVar
    end: cp_model.IntVar
    interval: cp_model.IntervalVar


@dataclass(frozen=True)
class _TaskVariables:
    plan_id: PlanID
    task: SchedulableTask
    segments: tuple[_SegmentVariables, ...]


class ExactAssignmentSolver:
    """CP-SAT exact assignment solver; full pipeline deferred to later slices."""

    def solve(self, assignment_input: AssignmentInput) -> AssignmentSolverResult:
        if not assignment_input.tasks:
            return feasible_result(())

        raise NotImplementedError(
            "ExactAssignmentSolver.solve full pipeline is not implemented until slice 5"
        )


def _solve_single_component(  # pyright: ignore[reportUnusedFunction]
    component: AssignmentComponent,
) -> tuple[TaskAssignment, ...] | None:
    if not component.tasks:
        return ()

    if _component_hard_unusable(component):
        return None

    timeline_anchor = _timeline_anchor(component)
    horizon_minutes = _component_horizon_minutes(component)
    model, task_variables, _fixed_intervals = _build_hard_constraint_model(
        component,
        timeline_anchor=timeline_anchor,
        horizon_minutes=horizon_minutes,
    )
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = _time_limit_seconds(component.solver_limits)
    status = solver.Solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None

    assignments = _extract_assignments(
        timeline_anchor,
        task_variables=task_variables,
        solver=solver,
    )
    if assignments is None:
        return None

    validation_failure = validate_full_assignment(
        _assignment_input_from_component(component),
        assignments,
    )
    if validation_failure is not None:
        return None

    return assignments


def _component_hard_unusable(component: AssignmentComponent) -> bool:
    if any(not task.effective_time_windows for task in component.tasks):
        return True
    if model_size_guard_exceeded(component, component.solver_limits):
        return True
    return _component_horizon_minutes(component) <= 0


def _build_hard_constraint_model(
    component: AssignmentComponent,
    *,
    timeline_anchor: datetime,
    horizon_minutes: int,
) -> tuple[cp_model.CpModel, tuple[_TaskVariables, ...], tuple[cp_model.IntervalVar, ...]]:
    model = cp_model.CpModel()

    fixed_intervals = tuple(
        _fixed_interval(
            model,
            occupied=occupied,
            timeline_anchor=timeline_anchor,
            horizon_minutes=horizon_minutes,
            name=f"occupied_{index}",
        )
        for index, occupied in enumerate(component.occupied_intervals)
    )

    task_variables: list[_TaskVariables] = []
    all_task_intervals: list[cp_model.IntervalVar] = []

    for task_index, task in enumerate(component.tasks):
        segment_vars = _task_segment_variables(
            model,
            task=task,
            horizon_minutes=horizon_minutes,
            timeline_anchor=timeline_anchor,
            name_prefix=f"task_{task_index}",
        )
        task_variables.append(
            _TaskVariables(plan_id=task.plan_id, task=task, segments=segment_vars)
        )
        all_task_intervals.extend(segment.interval for segment in segment_vars)

    if all_task_intervals or fixed_intervals:
        model.AddNoOverlap([*all_task_intervals, *fixed_intervals])

    task_variables_tuple = tuple(task_variables)
    for edge in component.precedence_edges:
        predecessor = _task_vars_by_plan_id(task_variables_tuple).get(edge.predecessor_plan_id)
        successor = _task_vars_by_plan_id(task_variables_tuple).get(edge.successor_plan_id)
        if predecessor is None or successor is None:
            continue
        predecessor_ends = _active_segment_values(
            model,
            segments=predecessor.segments,
            horizon_minutes=horizon_minutes,
            inactive_value=0,
            active_field="end",
            name_prefix=f"pred_end_{edge.predecessor_plan_id}_{edge.successor_plan_id}",
        )
        successor_starts = _active_segment_values(
            model,
            segments=successor.segments,
            horizon_minutes=horizon_minutes,
            inactive_value=horizon_minutes,
            active_field="start",
            name_prefix=f"succ_start_{edge.predecessor_plan_id}_{edge.successor_plan_id}",
        )
        latest_predecessor_end = model.NewIntVar(
            0,
            horizon_minutes,
            f"latest_pred_end_{edge.predecessor_plan_id}_{edge.successor_plan_id}",
        )
        earliest_successor_start = model.NewIntVar(
            0,
            horizon_minutes,
            f"earliest_succ_start_{edge.predecessor_plan_id}_{edge.successor_plan_id}",
        )
        model.AddMaxEquality(latest_predecessor_end, predecessor_ends)
        model.AddMinEquality(earliest_successor_start, successor_starts)
        model.Add(latest_predecessor_end <= earliest_successor_start)

    model.Minimize(0)
    return model, task_variables_tuple, fixed_intervals


def _task_segment_variables(
    model: cp_model.CpModel,
    *,
    task: SchedulableTask,
    horizon_minutes: int,
    timeline_anchor: datetime,
    name_prefix: str,
) -> tuple[_SegmentVariables, ...]:
    max_segments = _max_segments_for_task(task)
    segment_vars: list[_SegmentVariables] = []
    duration_vars: list[cp_model.IntVar] = []

    for segment_index in range(max_segments):
        presence = model.NewBoolVar(f"{name_prefix}_seg_{segment_index}_presence")
        start = model.NewIntVar(0, horizon_minutes, f"{name_prefix}_seg_{segment_index}_start")
        duration = model.NewIntVar(
            0,
            task.duration_minutes,
            f"{name_prefix}_seg_{segment_index}_duration",
        )
        end = model.NewIntVar(0, horizon_minutes, f"{name_prefix}_seg_{segment_index}_end")
        interval = model.NewOptionalIntervalVar(
            start,
            duration,
            end,
            presence,
            f"{name_prefix}_seg_{segment_index}_interval",
        )
        model.Add(end == start + duration)
        model.Add(duration == 0).OnlyEnforceIf(presence.Not())
        model.Add(start == 0).OnlyEnforceIf(presence.Not())
        _add_window_membership_constraints(
            model,
            presence=presence,
            start=start,
            duration=duration,
            windows=task.effective_time_windows,
            timeline_anchor=timeline_anchor,
            name_prefix=f"{name_prefix}_seg_{segment_index}",
        )
        segment_vars.append(
            _SegmentVariables(
                presence=presence,
                start=start,
                duration=duration,
                end=end,
                interval=interval,
            )
        )
        duration_vars.append(duration)

        if not task.divisible:
            model.Add(presence == 1)
            model.Add(duration == task.duration_minutes)
        else:
            minimum_chunk = task.minimum_chunk_size_minutes
            if minimum_chunk is not None and minimum_chunk > 0:
                model.Add(duration >= minimum_chunk).OnlyEnforceIf(presence)

    if task.divisible:
        model.Add(sum(duration_vars) == task.duration_minutes)

    return tuple(segment_vars)


def _add_window_membership_constraints(
    model: cp_model.CpModel,
    *,
    presence: cp_model.IntVar,
    start: cp_model.IntVar,
    duration: cp_model.IntVar,
    windows: tuple[TimeWindow, ...],
    timeline_anchor: datetime,
    name_prefix: str,
) -> None:
    window_choices: list[cp_model.IntVar] = []
    for window_index, effective_window in enumerate(windows):
        in_window = model.NewBoolVar(f"{name_prefix}_in_window_{window_index}")
        window_choices.append(in_window)
        window_start = _minute_offset(effective_window.start_time, timeline_anchor)
        window_end = _minute_offset(effective_window.end_time, timeline_anchor)
        model.Add(start >= window_start).OnlyEnforceIf(in_window)
        model.Add(start + duration <= window_end).OnlyEnforceIf(in_window)

    model.Add(sum(window_choices) == presence)


def _fixed_interval(
    model: cp_model.CpModel,
    *,
    occupied: OccupiedInterval,
    timeline_anchor: datetime,
    horizon_minutes: int,
    name: str,
) -> cp_model.IntervalVar:
    start_minutes = _minute_offset(occupied.start_time, timeline_anchor)
    end_minutes = _minute_offset(occupied.end_time, timeline_anchor)
    start_minutes = max(0, min(start_minutes, horizon_minutes))
    end_minutes = max(start_minutes, min(end_minutes, horizon_minutes))
    size = end_minutes - start_minutes
    return model.NewIntervalVar(start_minutes, size, end_minutes, name)


def _extract_assignments(
    timeline_anchor: datetime,
    *,
    task_variables: tuple[_TaskVariables, ...],
    solver: cp_model.CpSolver,
) -> tuple[TaskAssignment, ...] | None:
    assignments: list[TaskAssignment] = []
    for task_vars in task_variables:
        segments: list[TimeWindow] = []
        for segment in task_vars.segments:
            if solver.Value(segment.presence) == 0:
                continue
            duration_minutes = solver.Value(segment.duration)
            if duration_minutes <= 0:
                continue
            start_minutes = solver.Value(segment.start)
            segment_start = timeline_anchor + timedelta(minutes=start_minutes)
            segment_end = segment_start + timedelta(minutes=duration_minutes)
            segments.append(TimeWindow(start_time=segment_start, end_time=segment_end))

        if not segments:
            return None

        segments.sort(key=lambda item: item.start_time)
        assignments.append(TaskAssignment(plan_id=task_vars.plan_id, segments=tuple(segments)))

    return tuple(assignments)


def _assignment_input_from_component(component: AssignmentComponent) -> AssignmentInput:
    return AssignmentInput(
        run_started_at=component.run_started_at,
        tasks=component.tasks,
        precedence_edges=component.precedence_edges,
        occupied_intervals=component.occupied_intervals,
        previous_placements_by_task_id=component.previous_placements_by_task_id,
        solver_limits=component.solver_limits,
    )


def _task_vars_by_plan_id(
    task_variables: tuple[_TaskVariables, ...],
) -> dict[PlanID, _TaskVariables]:
    return {task_vars.plan_id: task_vars for task_vars in task_variables}


def _timeline_anchor(component: AssignmentComponent) -> datetime:
    anchor = component.run_started_at
    for task in component.tasks:
        for effective_window in task.effective_time_windows:
            anchor = min(anchor, effective_window.start_time)
    for occupied in component.occupied_intervals:
        anchor = min(anchor, occupied.start_time)
    for _, segments in component.previous_placements_by_task_id:
        for segment in segments:
            anchor = min(anchor, segment.start_time)
    return anchor


def _component_horizon_minutes(component: AssignmentComponent) -> int:
    timeline_anchor = _timeline_anchor(component)
    latest_end = component.run_started_at
    for task in component.tasks:
        for effective_window in task.effective_time_windows:
            latest_end = max(latest_end, effective_window.end_time)
    for occupied in component.occupied_intervals:
        latest_end = max(latest_end, occupied.end_time)
    for _, segments in component.previous_placements_by_task_id:
        for segment in segments:
            latest_end = max(latest_end, segment.end_time)

    delta = latest_end - timeline_anchor
    return max(0, int(delta.total_seconds() // 60))


def _max_segments_for_task(task: SchedulableTask) -> int:
    if not task.divisible:
        return 1

    minimum_chunk = task.minimum_chunk_size_minutes
    if minimum_chunk is None or minimum_chunk <= 0:
        return 1

    return max(1, (task.duration_minutes + minimum_chunk - 1) // minimum_chunk)


def _minute_offset(timestamp: datetime, timeline_anchor: datetime) -> int:
    delta = timestamp - timeline_anchor
    return int(delta.total_seconds() // 60)


def _active_segment_values(
    model: cp_model.CpModel,
    *,
    segments: tuple[_SegmentVariables, ...],
    horizon_minutes: int,
    inactive_value: int,
    active_field: str,
    name_prefix: str,
) -> list[cp_model.IntVar]:
    values: list[cp_model.IntVar] = []
    for segment_index, segment in enumerate(segments):
        active_source = segment.end if active_field == "end" else segment.start
        value = model.NewIntVar(0, horizon_minutes, f"{name_prefix}_{segment_index}")
        model.Add(value == active_source).OnlyEnforceIf(segment.presence)
        model.Add(value == inactive_value).OnlyEnforceIf(segment.presence.Not())
        values.append(value)
    return values


def _time_limit_seconds(limits: SolverLimits | None) -> float:
    if limits is None:
        return float(_DEFAULT_TIME_LIMIT_SECONDS)
    return float(limits.time_limit_seconds)

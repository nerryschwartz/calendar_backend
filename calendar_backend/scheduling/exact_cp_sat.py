"""OR-Tools CP-SAT exact assignment solver.

All ortools imports for the scheduling package must live in this module only.
"""

# pyright: reportAttributeAccessIssue=false

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from ortools.sat.python import cp_model

from calendar_backend.domain.enums import SolverStatus
from calendar_backend.domain.errors import MessageCode, ServiceMessage
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
    exact_feasible_result,
    exact_optimal_result,
    infeasible_result,
    weakest_solver_status,
)

_DEFAULT_TIME_LIMIT_SECONDS = 30
_SEGMENT_COUNT_MISMATCH_PENALTY_MULTIPLIER = 4

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


@dataclass(frozen=True)
class _ComponentContext:
    component: AssignmentComponent
    timeline_anchor: datetime
    horizon_minutes: int
    model: cp_model.CpModel
    task_variables: tuple[_TaskVariables, ...]
    hints_by_plan_id: dict[PlanID, tuple[TimeWindow, ...]]


@dataclass(frozen=True)
class _ComponentSolveResult:
    assignments: tuple[TaskAssignment, ...]
    status: SolverStatus
    limit_reached: bool


class ExactAssignmentSolver:
    """CP-SAT exact assignment solver with per-component lexicographic objectives."""

    def solve(self, assignment_input: AssignmentInput) -> AssignmentSolverResult:
        if not assignment_input.tasks:
            return exact_optimal_result(())

        base_components = decomposition.decompose_assignment_input(assignment_input)
        prior_solved_assignments: tuple[TaskAssignment, ...] = ()
        component_statuses: list[SolverStatus] = []
        any_limit_reached = False

        for component_index in range(len(base_components)):
            component = decomposition.iter_component_sub_inputs(
                assignment_input,
                prior_solved_assignments=prior_solved_assignments,
            )[component_index]
            component_result = _solve_component_with_status(component)
            if component_result is None:
                if model_size_guard_exceeded(component, component.solver_limits):
                    return _exact_guard_not_usable_result()
                return _exact_not_usable_result()

            prior_solved_assignments = (
                *prior_solved_assignments,
                *component_result.assignments,
            )
            component_statuses.append(component_result.status)
            any_limit_reached = any_limit_reached or component_result.limit_reached

        merged_assignments = prior_solved_assignments
        validation_failure = validate_full_assignment(assignment_input, merged_assignments)
        if validation_failure is not None:
            return infeasible_result(validation_failure)

        aggregate_status = weakest_solver_status(*component_statuses)
        if aggregate_status == SolverStatus.OPTIMAL:
            return exact_optimal_result(merged_assignments)
        return exact_feasible_result(merged_assignments, limit_reached=any_limit_reached)


def _solve_single_component(  # pyright: ignore[reportUnusedFunction]
    component: AssignmentComponent,
) -> tuple[TaskAssignment, ...] | None:
    component_result = _solve_component_with_status(component)
    if component_result is None:
        return None
    return component_result.assignments


def _solve_component_with_status(
    component: AssignmentComponent,
) -> _ComponentSolveResult | None:
    if not component.tasks:
        return _ComponentSolveResult((), SolverStatus.OPTIMAL, False)

    if _component_hard_unusable(component):
        return None

    context = _build_component_context(component)
    if context is None:
        return None

    lex_result = _run_lex_chain(context)
    if lex_result is None:
        return None

    assignments, status, limit_reached = lex_result
    validation_failure = validate_full_assignment(
        _assignment_input_from_component(component),
        assignments,
    )
    if validation_failure is not None:
        return None

    return _ComponentSolveResult(assignments, status, limit_reached)


def _run_lex_chain(
    context: _ComponentContext,
) -> tuple[tuple[TaskAssignment, ...], SolverStatus, bool] | None:
    time_limit_seconds = _time_limit_seconds(context.component.solver_limits)
    solve_status = SolverStatus.OPTIMAL
    limit_reached = False

    def absorb_solve(ortools_status: int, solver: cp_model.CpSolver) -> None:
        nonlocal solve_status, limit_reached
        solve_status = weakest_solver_status(
            solve_status,
            _solver_status_from_ortools(ortools_status),
        )
        if _solve_hit_time_limit(solver, time_limit_seconds):
            limit_reached = True

    context.model.Minimize(0)
    ortools_status, solver = _solve_context(context)
    if ortools_status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None
    absorb_solve(ortools_status, solver)

    last_assignments = _extract_assignments(
        context.timeline_anchor,
        task_variables=context.task_variables,
        solver=solver,
    )
    if last_assignments is None:
        return None

    lex_steps: list[tuple[Callable[[_ComponentContext], cp_model.IntVar], bool]] = [
        (_objective_maximize_exact_hint_matches, True),
        (_objective_minimize_moved_minutes, False),
        (_objective_minimize_changed_assignments, False),
    ]

    for objective_builder, maximize in lex_steps:
        assignments, ortools_status, solver = _run_lex_pass(
            context,
            objective_builder,
            maximize=maximize,
        )
        if assignments is None:
            return last_assignments, solve_status, limit_reached
        absorb_solve(ortools_status, solver)
        last_assignments = assignments

    for task_variables in _tasks_in_priority_order(context.task_variables):
        assignments, ortools_status, solver = _run_lex_pass(
            context,
            lambda ctx, task_vars=task_variables: _objective_earliest_start_for_task(
                ctx, task_vars
            ),
            maximize=False,
        )
        if assignments is None:
            return last_assignments, solve_status, limit_reached
        absorb_solve(ortools_status, solver)
        last_assignments = assignments

    for objective_builder, maximize in (
        (_objective_consolidate_global_gaps, False),
        (_objective_minimize_sum_of_starts, False),
        (_objective_minimize_active_segment_count, False),
        (_objective_minimize_intra_task_gaps, False),
    ):
        assignments, ortools_status, solver = _run_lex_pass(
            context,
            objective_builder,
            maximize=maximize,
        )
        if assignments is None:
            return last_assignments, solve_status, limit_reached
        absorb_solve(ortools_status, solver)
        last_assignments = assignments

    return last_assignments, solve_status, limit_reached


def _solve_context(context: _ComponentContext) -> tuple[int, cp_model.CpSolver]:
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = _time_limit_seconds(context.component.solver_limits)
    status = solver.Solve(context.model)
    return status, solver


def _run_lex_pass(
    context: _ComponentContext,
    objective_builder: Callable[[_ComponentContext], cp_model.IntVar],
    *,
    maximize: bool,
) -> tuple[tuple[TaskAssignment, ...] | None, int, cp_model.CpSolver]:
    objective = objective_builder(context)
    if maximize:
        context.model.Maximize(objective)
    else:
        context.model.Minimize(objective)

    ortools_status, solver = _solve_context(context)
    if ortools_status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None, ortools_status, solver

    optimal_value = round(solver.ObjectiveValue())
    context.model.Add(objective == optimal_value)

    assignments = _extract_assignments(
        context.timeline_anchor,
        task_variables=context.task_variables,
        solver=solver,
    )
    return assignments, ortools_status, solver


def _build_component_context(
    component: AssignmentComponent,
) -> _ComponentContext | None:
    timeline_anchor = _timeline_anchor(component)
    horizon_minutes = _component_horizon_minutes(component)
    model, task_variables, _fixed_intervals = _build_hard_constraint_model(
        component,
        timeline_anchor=timeline_anchor,
        horizon_minutes=horizon_minutes,
    )
    hints_by_plan_id = {
        plan_id: tuple(sorted(segments, key=lambda segment: segment.start_time))
        for plan_id, segments in component.previous_placements_by_task_id
    }
    return _ComponentContext(
        component=component,
        timeline_anchor=timeline_anchor,
        horizon_minutes=horizon_minutes,
        model=model,
        task_variables=task_variables,
        hints_by_plan_id=hints_by_plan_id,
    )


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
        _add_divisible_segment_ordering_constraints(model, segment_vars)

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

    return model, task_variables_tuple, fixed_intervals


def _add_divisible_segment_ordering_constraints(
    model: cp_model.CpModel,
    segments: tuple[_SegmentVariables, ...],
) -> None:
    for left_index in range(len(segments) - 1):
        left = segments[left_index]
        right = segments[left_index + 1]
        both_active = model.NewBoolVar(f"ordered_segments_{left_index}")
        model.AddMultiplicationEquality(both_active, [left.presence, right.presence])
        model.Add(left.end <= right.start).OnlyEnforceIf(both_active)


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


def _objective_maximize_exact_hint_matches(context: _ComponentContext) -> cp_model.IntVar:
    match_vars: list[cp_model.IntVar] = []
    for task_variables in context.task_variables:
        hint_segments = context.hints_by_plan_id.get(task_variables.plan_id)
        if hint_segments is None:
            continue
        match_var = _hint_exact_match_var(
            context.model,
            task_variables=task_variables,
            hint_segments=hint_segments,
            timeline_anchor=context.timeline_anchor,
        )
        if match_var is not None:
            match_vars.append(match_var)

    if not match_vars:
        return _zero_objective_var(context.model, "hint_matches")

    total_matches = context.model.NewIntVar(0, len(match_vars), "total_hint_matches")
    context.model.Add(total_matches == sum(match_vars))
    return total_matches


def _objective_minimize_moved_minutes(context: _ComponentContext) -> cp_model.IntVar:
    moved_terms: list[cp_model.IntVar] = []
    penalty = context.horizon_minutes * _SEGMENT_COUNT_MISMATCH_PENALTY_MULTIPLIER

    for task_variables in context.task_variables:
        hint_segments = context.hints_by_plan_id.get(task_variables.plan_id)
        if hint_segments is None:
            continue
        if len(hint_segments) > len(task_variables.segments):
            mismatch = context.model.NewIntVar(penalty, penalty, "moved_mismatch")
            moved_terms.append(mismatch)
            continue

        for index, hint_segment in enumerate(hint_segments):
            segment = task_variables.segments[index]
            moved_terms.append(
                _segment_moved_minutes_var(
                    context.model,
                    segment=segment,
                    hint_segment=hint_segment,
                    timeline_anchor=context.timeline_anchor,
                    horizon_minutes=context.horizon_minutes,
                    name_prefix=f"moved_{task_variables.plan_id}_{index}",
                )
            )

        if len(hint_segments) < len(task_variables.segments):
            extra_slots = len(task_variables.segments) - len(hint_segments)
            mismatch = context.model.NewIntVar(
                penalty * extra_slots,
                penalty * extra_slots,
                f"moved_extra_{task_variables.plan_id}",
            )
            moved_terms.append(mismatch)

    if not moved_terms:
        return _zero_objective_var(context.model, "moved_minutes")

    total_moved = context.model.NewIntVar(0, penalty * len(moved_terms), "total_moved_minutes")
    context.model.Add(total_moved == sum(moved_terms))
    return total_moved


def _objective_minimize_changed_assignments(context: _ComponentContext) -> cp_model.IntVar:
    changed_vars: list[cp_model.IntVar] = []
    for task_variables in context.task_variables:
        hint_segments = context.hints_by_plan_id.get(task_variables.plan_id)
        if hint_segments is None:
            continue
        match_var = _hint_exact_match_var(
            context.model,
            task_variables=task_variables,
            hint_segments=hint_segments,
            timeline_anchor=context.timeline_anchor,
        )
        if match_var is None:
            changed = context.model.NewBoolVar(f"changed_{task_variables.plan_id}")
            context.model.Add(changed == 1)
            changed_vars.append(changed)
            continue
        changed = context.model.NewBoolVar(f"changed_{task_variables.plan_id}")
        context.model.Add(match_var + changed == 1)
        changed_vars.append(changed)

    if not changed_vars:
        return _zero_objective_var(context.model, "changed_assignments")

    total_changed = context.model.NewIntVar(0, len(changed_vars), "total_changed_assignments")
    context.model.Add(total_changed == sum(changed_vars))
    return total_changed


def _objective_earliest_start_for_task(
    context: _ComponentContext,
    task_variables: _TaskVariables,
) -> cp_model.IntVar:
    return _earliest_active_start(
        context.model,
        segments=task_variables.segments,
        horizon_minutes=context.horizon_minutes,
        name_prefix=f"priority_start_{task_variables.plan_id}",
    )


def _objective_consolidate_global_gaps(context: _ComponentContext) -> cp_model.IntVar:
    model = context.model
    horizon_minutes = context.horizon_minutes
    active_ends: list[cp_model.IntVar] = []
    active_starts: list[cp_model.IntVar] = []
    duration_terms: list[cp_model.IntVar] = []

    for task_variables in context.task_variables:
        for segment_index, segment in enumerate(task_variables.segments):
            prefix = f"global_idle_{task_variables.plan_id}_{segment_index}"
            active_ends.append(
                _active_segment_scalar(
                    model,
                    segment=segment,
                    horizon_minutes=horizon_minutes,
                    active_field="end",
                    inactive_value=0,
                    name_prefix=f"{prefix}_end",
                )
            )
            active_starts.append(
                _active_segment_scalar(
                    model,
                    segment=segment,
                    horizon_minutes=horizon_minutes,
                    active_field="start",
                    inactive_value=horizon_minutes,
                    name_prefix=f"{prefix}_start",
                )
            )
            duration = model.NewIntVar(0, horizon_minutes, f"{prefix}_duration")
            model.Add(duration == segment.duration).OnlyEnforceIf(segment.presence)
            model.Add(duration == 0).OnlyEnforceIf(segment.presence.Not())
            duration_terms.append(duration)

    if not duration_terms:
        return _zero_objective_var(model, "global_gaps")

    latest_end = model.NewIntVar(0, horizon_minutes, "global_idle_latest_end")
    earliest_start = model.NewIntVar(0, horizon_minutes, "global_idle_earliest_start")
    model.AddMaxEquality(latest_end, active_ends)
    model.AddMinEquality(earliest_start, active_starts)

    total_duration = model.NewIntVar(0, horizon_minutes, "global_idle_total_duration")
    model.Add(total_duration == sum(duration_terms))

    idle_span = model.NewIntVar(0, horizon_minutes, "global_idle_span")
    model.Add(idle_span == latest_end - earliest_start - total_duration)
    return idle_span


def _objective_minimize_sum_of_starts(context: _ComponentContext) -> cp_model.IntVar:
    start_terms: list[cp_model.IntVar] = []
    for task_variables in context.task_variables:
        for segment_index, segment in enumerate(task_variables.segments):
            start_terms.append(
                _active_segment_start_for_sum(
                    context.model,
                    segment=segment,
                    horizon_minutes=context.horizon_minutes,
                    name_prefix=f"sum_start_{task_variables.plan_id}_{segment_index}",
                )
            )

    total_start = context.model.NewIntVar(
        0,
        context.horizon_minutes * max(1, len(start_terms)),
        "total_start_minutes",
    )
    context.model.Add(total_start == sum(start_terms))
    return total_start


def _objective_minimize_active_segment_count(context: _ComponentContext) -> cp_model.IntVar:
    presence_vars = [
        segment.presence
        for task_variables in context.task_variables
        for segment in task_variables.segments
    ]
    total_segments = context.model.NewIntVar(0, len(presence_vars), "total_active_segments")
    context.model.Add(total_segments == sum(presence_vars))
    return total_segments


def _objective_minimize_intra_task_gaps(context: _ComponentContext) -> cp_model.IntVar:
    gap_terms: list[cp_model.IntVar] = []
    for task_variables in context.task_variables:
        if len(task_variables.segments) <= 1:
            continue
        for left_index in range(len(task_variables.segments) - 1):
            left = task_variables.segments[left_index]
            right = task_variables.segments[left_index + 1]
            both_active = context.model.NewBoolVar(
                f"intra_gap_active_{task_variables.plan_id}_{left_index}"
            )
            model = context.model
            model.AddMultiplicationEquality(both_active, [left.presence, right.presence])
            gap = model.NewIntVar(
                0,
                context.horizon_minutes,
                f"intra_gap_{task_variables.plan_id}_{left_index}",
            )
            model.Add(gap == right.start - left.end).OnlyEnforceIf(both_active)
            model.Add(gap == 0).OnlyEnforceIf(both_active.Not())
            gap_terms.append(gap)

    if not gap_terms:
        return _zero_objective_var(context.model, "intra_task_gaps")

    total_gap = context.model.NewIntVar(
        0,
        context.horizon_minutes * len(gap_terms),
        "total_intra_task_gaps",
    )
    context.model.Add(total_gap == sum(gap_terms))
    return total_gap


def _hint_exact_match_var(
    model: cp_model.CpModel,
    *,
    task_variables: _TaskVariables,
    hint_segments: tuple[TimeWindow, ...],
    timeline_anchor: datetime,
) -> cp_model.IntVar | None:
    if not hint_segments:
        return None
    if len(hint_segments) > len(task_variables.segments):
        return None

    match = model.NewBoolVar(f"hint_match_{task_variables.plan_id}")
    for index, hint_segment in enumerate(hint_segments):
        segment = task_variables.segments[index]
        hint_start = _minute_offset(hint_segment.start_time, timeline_anchor)
        hint_duration = _window_duration_minutes(hint_segment)
        model.Add(segment.presence == 1).OnlyEnforceIf(match)
        model.Add(segment.start == hint_start).OnlyEnforceIf(match)
        model.Add(segment.duration == hint_duration).OnlyEnforceIf(match)

    for index in range(len(hint_segments), len(task_variables.segments)):
        model.Add(task_variables.segments[index].presence == 0).OnlyEnforceIf(match)

    return match


def _segment_moved_minutes_var(
    model: cp_model.CpModel,
    *,
    segment: _SegmentVariables,
    hint_segment: TimeWindow,
    timeline_anchor: datetime,
    horizon_minutes: int,
    name_prefix: str,
) -> cp_model.IntVar:
    hint_start = _minute_offset(hint_segment.start_time, timeline_anchor)
    hint_end = _minute_offset(hint_segment.end_time, timeline_anchor)
    max_moved = horizon_minutes * 2
    moved = model.NewIntVar(0, max_moved, name_prefix)

    active_moved = model.NewIntVar(0, max_moved, f"{name_prefix}_active")
    start_delta = model.NewIntVar(-horizon_minutes, horizon_minutes, f"{name_prefix}_start_delta")
    end_delta = model.NewIntVar(-horizon_minutes, horizon_minutes, f"{name_prefix}_end_delta")
    abs_start = model.NewIntVar(0, horizon_minutes, f"{name_prefix}_abs_start")
    abs_end = model.NewIntVar(0, horizon_minutes, f"{name_prefix}_abs_end")
    model.Add(start_delta == segment.start - hint_start).OnlyEnforceIf(segment.presence)
    model.Add(start_delta == 0).OnlyEnforceIf(segment.presence.Not())
    model.Add(end_delta == segment.end - hint_end).OnlyEnforceIf(segment.presence)
    model.Add(end_delta == 0).OnlyEnforceIf(segment.presence.Not())
    model.AddAbsEquality(abs_start, start_delta)
    model.AddAbsEquality(abs_end, end_delta)
    model.Add(active_moved == abs_start + abs_end).OnlyEnforceIf(segment.presence)
    model.Add(active_moved == max_moved).OnlyEnforceIf(segment.presence.Not())
    model.Add(moved == active_moved)
    return moved


def _active_segment_scalar(
    model: cp_model.CpModel,
    *,
    segment: _SegmentVariables,
    horizon_minutes: int,
    active_field: str,
    inactive_value: int,
    name_prefix: str,
) -> cp_model.IntVar:
    active_source = segment.end if active_field == "end" else segment.start
    value = model.NewIntVar(0, horizon_minutes, name_prefix)
    model.Add(value == active_source).OnlyEnforceIf(segment.presence)
    model.Add(value == inactive_value).OnlyEnforceIf(segment.presence.Not())
    return value


def _active_segment_start_for_sum(
    model: cp_model.CpModel,
    *,
    segment: _SegmentVariables,
    horizon_minutes: int,
    name_prefix: str,
) -> cp_model.IntVar:
    value = model.NewIntVar(0, horizon_minutes, name_prefix)
    model.Add(value == segment.start).OnlyEnforceIf(segment.presence)
    model.Add(value == 0).OnlyEnforceIf(segment.presence.Not())
    return value


def _earliest_active_start(
    model: cp_model.CpModel,
    *,
    segments: tuple[_SegmentVariables, ...],
    horizon_minutes: int,
    name_prefix: str,
) -> cp_model.IntVar:
    starts = _active_segment_values(
        model,
        segments=segments,
        horizon_minutes=horizon_minutes,
        inactive_value=horizon_minutes,
        active_field="start",
        name_prefix=name_prefix,
    )
    earliest = model.NewIntVar(0, horizon_minutes, f"{name_prefix}_earliest")
    model.AddMinEquality(earliest, starts)
    return earliest


def _zero_objective_var(model: cp_model.CpModel, name: str) -> cp_model.IntVar:
    value = model.NewIntVar(0, 0, name)
    model.Add(value == 0)
    return value


def _tasks_in_priority_order(
    task_variables: tuple[_TaskVariables, ...],
) -> tuple[_TaskVariables, ...]:
    return tuple(
        sorted(
            task_variables,
            key=lambda task_vars: (task_vars.task.priority_path, str(task_vars.plan_id)),
        )
    )


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


def _window_duration_minutes(window_value: TimeWindow) -> int:
    return int((window_value.end_time - window_value.start_time).total_seconds() // 60)


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


def _solver_status_from_ortools(ortools_status: int) -> SolverStatus:
    if ortools_status == cp_model.OPTIMAL:
        return SolverStatus.OPTIMAL
    return SolverStatus.FEASIBLE


def _solve_hit_time_limit(solver: cp_model.CpSolver, time_limit_seconds: float) -> bool:
    if time_limit_seconds <= 0:
        return False
    return solver.WallTime() >= time_limit_seconds - 0.001


def _exact_guard_not_usable_result() -> AssignmentSolverResult:
    return AssignmentSolverResult(
        status=SolverStatus.INFEASIBLE,
        assignments=(),
        warnings=(),
        failure=None,
    )


def _exact_not_usable_result() -> AssignmentSolverResult:
    return AssignmentSolverResult(
        status=SolverStatus.INFEASIBLE,
        assignments=(),
        warnings=(),
        failure=ServiceMessage(
            code=MessageCode.SOLVER_FAILED_TO_FIND_FEASIBLE_ASSIGNMENT,
            message="Exact solver could not produce a usable assignment",
            details={},
        ),
    )

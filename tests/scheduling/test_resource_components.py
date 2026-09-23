"""Competing windows must be solved together, independently of UUID and horizon."""

from dataclasses import replace
from datetime import timedelta
from uuid import UUID

import pytest
from calendar_backend.scheduling import exact_cp_sat as exact
from calendar_backend.scheduling.decomposition import (
    decompose_assignment_input,
    estimate_model_variable_count,
)
from calendar_backend.scheduling.feasibility import validate_full_assignment
from calendar_backend.scheduling.input import PrecedenceEdge, SolverLimits
from calendar_backend.scheduling.types import is_usable_solver_result
from calendar_backend.services.task_assignment import _solve_assignment

from .conftest import assignment_input, schedulable_task, utc, window


@pytest.mark.parametrize("swap_ids", [False, True])
@pytest.mark.parametrize("day_offset", [0, 365])
def test_two_things_share_capacity_and_split_around_indivisible_task(swap_ids, day_offset):
    ids = [
        UUID("97f5fdfe-b22a-46ce-968c-89a270a2347b"),
        UUID("5d620735-1835-4fac-ac2e-47b525cae413"),
    ]
    if swap_ids:
        ids.reverse()
    start = utc(2026, 9, 17, 0, 0) + timedelta(days=day_offset)
    first = schedulable_task(
        task_id=ids[0],
        duration_minutes=120,
        divisible=True,
        minimum_chunk_size_minutes=60,
        effective_time_windows=(window(start, start + timedelta(hours=3)),),
        priority_path=(0,),
    )
    second = schedulable_task(
        task_id=ids[1],
        duration_minutes=60,
        effective_time_windows=(
            window(start + timedelta(minutes=30), start + timedelta(minutes=150)),
        ),
        priority_path=(1,),
    )
    value = assignment_input(
        tasks=(second, first),
        run_started_at=utc(2026, 9, 16, 0, 33),
        solver_limits=SolverLimits(time_limit_seconds=2, model_size_limit=1000),
    )
    components = decompose_assignment_input(value)
    assert len(components) == 1
    assert estimate_model_variable_count(components[0]) < 1000
    result, _ = _solve_assignment(value, heuristic_enabled=True)
    assert is_usable_solver_result(result), result
    assert validate_full_assignment(value, result.assignments) is None
    assigned = {item.plan_id: item.segments for item in result.assignments}
    assert assigned[ids[0]] == (
        window(start, start + timedelta(hours=1)),
        window(start + timedelta(hours=2), start + timedelta(hours=3)),
    )
    assert assigned[ids[1]] == (window(start + timedelta(hours=1), start + timedelta(hours=2)),)


def test_touching_windows_stay_independent_but_bridging_task_couples_both():
    start = utc(2026, 9, 17, 0, 0)
    tasks = tuple(
        schedulable_task(
            duration_minutes=30,
            effective_time_windows=(
                window(start + timedelta(hours=i), start + timedelta(hours=i + 1)),
            ),
        )
        for i in range(3)
    )
    assert len(decompose_assignment_input(assignment_input(tasks=tasks))) == 3
    bridge = schedulable_task(
        duration_minutes=30,
        effective_time_windows=(
            window(start + timedelta(minutes=30), start + timedelta(minutes=150)),
        ),
    )
    assert len(decompose_assignment_input(assignment_input(tasks=(*tasks, bridge)))) == 1


def test_structural_estimate_bounds_full_lex_model_with_hints_and_precedence(monkeypatch):
    start = utc(2026, 9, 17, 0, 0)
    tasks = tuple(
        schedulable_task(
            duration_minutes=60,
            divisible=True,
            minimum_chunk_size_minutes=30,
            effective_time_windows=(window(start, start + timedelta(hours=6)),),
        )
        for _ in range(2)
    )
    value = assignment_input(
        tasks=tasks,
        precedence_edges=(PrecedenceEdge(tasks[0].plan_id, tasks[1].plan_id),),
        previous_placements_by_task_id=(
            (tasks[0].plan_id, (window(start, start + timedelta(hours=1)),)),
        ),
    )
    component = decompose_assignment_input(value)[0]
    estimate = estimate_model_variable_count(component)
    counts = []
    solve = exact._solve_context

    def inspect_model(context):
        counts.append(len(context.model.Proto().variables))
        return solve(context)

    monkeypatch.setattr(exact, "_solve_context", inspect_model)
    assert is_usable_solver_result(exact.solve_exact_component(component))
    assert len(counts) > 5
    assert max(counts) <= estimate
    moved = replace(component, run_started_at=utc(2025, 1, 1, 0, 0))
    assert estimate_model_variable_count(moved) == estimate

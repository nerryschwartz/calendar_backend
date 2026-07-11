from __future__ import annotations

import uuid

from calendar_backend.domain.ids import PlanID
from calendar_backend.scheduling.decomposition import (
    decompose_assignment_input,
    estimate_model_variable_count,
    iter_component_sub_inputs,
    model_size_guard_exceeded,
)
from calendar_backend.scheduling.input import PrecedenceEdge, SolverLimits
from calendar_backend.scheduling.types import TaskAssignment

from .conftest import assignment_input, occupied, schedulable_task, utc, window


def _stable_plan_id(label: str) -> PlanID:
    return PlanID(uuid.uuid5(uuid.NAMESPACE_DNS, label))


def test_decompose_empty_tasks_returns_empty_tuple() -> None:
    assert decompose_assignment_input(assignment_input(tasks=())) == ()


def test_decompose_single_precedence_edge_yields_one_component() -> None:
    first_id = _stable_plan_id("first")
    second_id = _stable_plan_id("second")
    morning = window(utc(2026, 6, 7, 9, 0), utc(2026, 6, 7, 12, 0))
    first = schedulable_task(
        task_id=first_id,
        duration_minutes=30,
        effective_time_windows=(morning,),
    )
    second = schedulable_task(
        task_id=second_id,
        duration_minutes=30,
        effective_time_windows=(morning,),
    )

    components = decompose_assignment_input(
        assignment_input(
            tasks=(first, second),
            precedence_edges=(
                PrecedenceEdge(predecessor_plan_id=first_id, successor_plan_id=second_id),
            ),
        )
    )

    assert len(components) == 1
    assert {task.plan_id for task in components[0].tasks} == {first_id, second_id}
    assert len(components[0].precedence_edges) == 1


def test_decompose_two_disconnected_chains_yields_two_components() -> None:
    chain_a_first = _stable_plan_id("chain-a-first")
    chain_a_second = _stable_plan_id("chain-a-second")
    chain_b_first = _stable_plan_id("chain-b-first")
    chain_b_second = _stable_plan_id("chain-b-second")
    morning = window(utc(2026, 6, 7, 9, 0), utc(2026, 6, 7, 12, 0))

    components = decompose_assignment_input(
        assignment_input(
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
    )

    assert len(components) == 2
    component_task_sets = [{task.plan_id for task in component.tasks} for component in components]
    assert {frozenset(task_set) for task_set in component_task_sets} == {
        frozenset({chain_a_first, chain_a_second}),
        frozenset({chain_b_first, chain_b_second}),
    }
    assert str(min(components[0].tasks, key=lambda task: str(task.plan_id)).plan_id) < str(
        min(components[1].tasks, key=lambda task: str(task.plan_id)).plan_id
    )


def test_decompose_ignores_precedence_edge_with_missing_task_endpoint() -> None:
    present_id = _stable_plan_id("present")
    missing_id = _stable_plan_id("missing")
    morning = window(utc(2026, 6, 7, 9, 0), utc(2026, 6, 7, 12, 0))
    present = schedulable_task(
        task_id=present_id,
        duration_minutes=30,
        effective_time_windows=(morning,),
    )

    components = decompose_assignment_input(
        assignment_input(
            tasks=(present,),
            precedence_edges=(
                PrecedenceEdge(predecessor_plan_id=present_id, successor_plan_id=missing_id),
            ),
        )
    )

    assert len(components) == 1
    assert components[0].precedence_edges == ()


def test_iter_component_sub_inputs_accumulates_prior_component_placements() -> None:
    first_id = _stable_plan_id("accum-first")
    second_id = _stable_plan_id("accum-second")
    morning = window(utc(2026, 6, 7, 9, 0), utc(2026, 6, 7, 12, 0))
    base_occupied = occupied(utc(2026, 6, 7, 8, 0), utc(2026, 6, 7, 8, 30))
    first = schedulable_task(
        task_id=first_id,
        duration_minutes=30,
        effective_time_windows=(morning,),
    )
    second = schedulable_task(
        task_id=second_id,
        duration_minutes=30,
        effective_time_windows=(morning,),
    )
    assignment_input_value = assignment_input(
        tasks=(first, second),
        precedence_edges=(),
        occupied_intervals=(base_occupied,),
    )

    first_placement = TaskAssignment(
        plan_id=first_id,
        segments=(window(utc(2026, 6, 7, 9, 0), utc(2026, 6, 7, 9, 30)),),
    )
    components = iter_component_sub_inputs(
        assignment_input_value,
        prior_solved_assignments=(first_placement,),
    )

    assert len(components) == 2
    assert len(components[0].occupied_intervals) == 1
    assert components[0].occupied_intervals[0] == base_occupied
    assert len(components[1].occupied_intervals) == 2
    assert components[1].occupied_intervals[0] == base_occupied
    assert components[1].occupied_intervals[1].start_time == first_placement.segments[0].start_time


def test_model_size_guard_exceeded_trips_on_artificially_low_limit() -> None:
    task_id = _stable_plan_id("guard-task")
    morning = window(utc(2026, 6, 7, 9, 0), utc(2026, 6, 7, 18, 0))
    component = decompose_assignment_input(
        assignment_input(
            tasks=(
                schedulable_task(
                    task_id=task_id,
                    duration_minutes=120,
                    effective_time_windows=(morning,),
                ),
            ),
        )
    )[0]
    limits = SolverLimits(time_limit_seconds=30, model_size_limit=1)

    assert estimate_model_variable_count(component) > limits.model_size_limit
    assert model_size_guard_exceeded(component, limits) is True


def test_model_size_guard_exceeded_disabled_when_limits_none() -> None:
    task_id = _stable_plan_id("guard-none")
    morning = window(utc(2026, 6, 7, 9, 0), utc(2026, 6, 7, 18, 0))
    component = decompose_assignment_input(
        assignment_input(
            tasks=(
                schedulable_task(
                    task_id=task_id,
                    duration_minutes=120,
                    effective_time_windows=(morning,),
                ),
            ),
        )
    )[0]

    assert model_size_guard_exceeded(component, None) is False

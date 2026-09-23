"""Attach persisted names and constraint evidence to assignment failures."""

from dataclasses import replace

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from calendar_backend.domain.deletion import (
    AssignmentConflict,
    AssignmentDiagnostics,
    DiagnosticConstraintSource,
    DiagnosticPlan,
    DiagnosticSolver,
    DiagnosticTask,
    DiagnosticWindows,
)
from calendar_backend.domain.enums import SolverStatus
from calendar_backend.domain.errors import MessageCode
from calendar_backend.domain.ids import PlanID
from calendar_backend.domain.resolution import ResolveTasksResult
from calendar_backend.domain.time import TimeWindow, sqlite_utc
from calendar_backend.models.constraints import TimeConstraintGroup
from calendar_backend.models.plans import Plan
from calendar_backend.scheduling.input import AssignmentInput
from calendar_backend.scheduling.types import AssignmentSolverResult


def enrich_assignment_conflicts(
    session: Session,
    conflicts: tuple[AssignmentConflict, ...],
    resolved: ResolveTasksResult,
    assignment_input: AssignmentInput,
    solver_result: AssignmentSolverResult,
) -> tuple[AssignmentConflict, ...]:
    tasks = {
        task.plan_id: task for task in (*resolved.valid_incomplete, *resolved.invalid_incomplete)
    }
    all_source_ids = {
        source.constraint_group_id for task in tasks.values() for source in task.constraint_sources
    }
    name_ids = (
        set(tasks)
        | {source.plan_id for task in tasks.values() for source in task.constraint_sources}
        | {
            item.source_plan_id
            for item in assignment_input.occupied_intervals
            if item.source_plan_id is not None
        }
    )
    names = dict(
        session.execute(select(Plan.plan_id, Plan.name).where(Plan.plan_id.in_(name_ids))).all()
    )
    groups = {
        group.time_constraint_group_id: group
        for group in session.scalars(
            select(TimeConstraintGroup)
            .where(TimeConstraintGroup.time_constraint_group_id.in_(all_source_ids))
            .options(selectinload(TimeConstraintGroup.windows))
        )
    }
    failure_details = solver_result.failure.details if solver_result.failure else {}
    for warning in solver_result.warnings:
        if warning.code == MessageCode.SOLVER_LIMIT_REACHED:
            failure_details = {**failure_details, **warning.details}
    proven = solver_result.status == SolverStatus.INFEASIBLE
    solver = DiagnosticSolver(
        stage=failure_details.get("stage", "assignment"),
        estimate=float(failure_details["estimate"]) if "estimate" in failure_details else None,
        limit=float(failure_details["limit"]) if "limit" in failure_details else None,
        proof_status="proven_infeasible" if proven else "not_proven",
    )
    enriched = []
    for conflict in conflicts:
        selected = [tasks[plan_id] for plan_id in conflict.task_ids if plan_id in tasks]
        source_ids = {
            source.constraint_group_id for task in selected for source in task.constraint_sources
        }
        sources = tuple(
            DiagnosticConstraintSource(
                plan_id=PlanID(group.plan_id),
                name=names.get(group.plan_id, "Unknown plan"),
                constraint_kind=group.constraint_kind,
                constraint_group_id=group.time_constraint_group_id,
                windows=tuple(
                    TimeWindow(sqlite_utc(window.start_time), sqlite_utc(window.end_time))
                    for window in group.windows
                ),
            )
            for source_id in sorted(source_ids, key=str)
            if (group := groups.get(source_id)) is not None
        )
        selected_ids = {task.plan_id for task in selected}
        blockers = {
            interval.source_plan_id
            for interval in assignment_input.occupied_intervals
            if interval.source_plan_id is not None
            and any(
                window.start_time < interval.end_time and interval.start_time < window.end_time
                for task in selected
                for window in task.effective_time_windows
            )
        }
        blockers.update(
            task.plan_id
            for task in tasks.values()
            if task.plan_id not in selected_ids
            and any(
                left.start_time < right.end_time and right.start_time < left.end_time
                for affected in selected
                for left in affected.effective_time_windows
                for right in task.effective_time_windows
            )
        )
        for edge in assignment_input.precedence_edges:
            if edge.successor_plan_id in selected_ids:
                blockers.add(edge.predecessor_plan_id)
        explanation = conflict.explanation
        if selected:
            requirements = "; ".join(
                f'"{task.name}" requires {task.duration_minutes} minutes'
                + (
                    f" in chunks of at least {task.minimum_chunk_size_minutes} minutes"
                    if task.divisible
                    else " in one continuous window"
                )
                for task in selected
            )
            explanation += f". {requirements}."
            if any(not task.effective_time_windows for task in selected):
                explanation += " No effective time remains after intersecting inherited constraints and allowed block families; inspect the listed constraint sources."
        if not proven:
            explanation += " Search did not prove infeasibility; changing or deleting these tasks is not necessarily required."
        enriched.append(
            replace(
                conflict,
                explanation=explanation,
                diagnostics=AssignmentDiagnostics(
                    tasks=tuple(
                        DiagnosticTask(
                            task.plan_id,
                            task.name,
                            task.duration_minutes,
                            task.divisible,
                            task.minimum_chunk_size_minutes,
                            task.allowed_block_families,
                        )
                        for task in selected
                    ),
                    constraint_sources=sources,
                    effective_windows=tuple(
                        DiagnosticWindows(task.plan_id, task.effective_time_windows)
                        for task in selected
                    ),
                    blocking_plans=tuple(
                        DiagnosticPlan(plan_id, names[plan_id])
                        for plan_id in sorted(blockers, key=str)
                        if plan_id in names
                    ),
                    solver=solver,
                ),
            )
        )
    return tuple(enriched)

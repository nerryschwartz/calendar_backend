"""Task resolution service: refresh horizon/repetitions and resolve the master tree.

Resolution does not write calendar entries. Downstream assignment (Prompt 14)
``assign_tasks`` must refuse when ``ResolveTasksResult.invalid_incomplete`` is
non-empty (``MessageCode.INVALID_INCOMPLETE_TASKS_BLOCK_ASSIGNMENT``). Invalid
completed tasks do not block assignment.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from calendar_backend.db.session import transaction
from calendar_backend.domain.assignment import sqlite_utc
from calendar_backend.domain.errors import ServiceTransactionAborted
from calendar_backend.domain.resolution import ResolveTasksResult, resolve_tasks_from_graph
from calendar_backend.domain.results import ServiceResult, fail, ok
from calendar_backend.domain.time import Clock, SystemClock
from calendar_backend.models.chains import GoalChildChain
from calendar_backend.models.constraints import TimeConstraintGroup
from calendar_backend.models.plans import GoalPlan, Plan, RepetitionPlan
from calendar_backend.services.master_horizon import (
    MasterHorizonService,
    validate_run_started_at,
)
from calendar_backend.services.plan_tree_invariant import PlanTreeInvariantService
from calendar_backend.services.repetition import RepetitionService


class TaskResolutionService:
    """Resolve the master plan tree into task buckets for scheduling."""

    def __init__(self, session: Session, clock: Clock | None = None) -> None:
        self._session = session
        self._clock = clock or SystemClock()

    def resolve_tasks(self, run_started_at: datetime) -> ServiceResult[ResolveTasksResult]:
        """Refresh horizon/repetitions, validate the tree, and return resolved tasks.

        ``ResolveTasksResult.run_started_at`` echoes the validated input.
        """
        validation_error = validate_run_started_at(run_started_at)
        if validation_error is not None:
            return fail(validation_error)

        try:
            with transaction(self._session) as txn:
                horizon_result = MasterHorizonService(txn, self._clock).refresh_master_horizon(
                    run_started_at
                )
                if not horizon_result.success:
                    raise ServiceTransactionAborted(horizon_result.errors)

                repetition_result = RepetitionService(txn, self._clock).refresh_all_repetitions(
                    run_started_at
                )
                if not repetition_result.success:
                    raise ServiceTransactionAborted(repetition_result.errors)

                invariant_result = PlanTreeInvariantService(txn).validate_master_tree()
                if not invariant_result.success:
                    raise ServiceTransactionAborted(invariant_result.errors)

                plans = load_plan_graph(txn)
                result = _resolve_from_current_tree(run_started_at, plans=plans)
                return ok(result)
        except ServiceTransactionAborted as exc:
            return fail(*exc.errors)

    def load_plan_graph(self, txn: Session) -> tuple[Plan, ...]:
        """Load the full master plan tree graph for resolution-shaped consumers."""
        return load_plan_graph(txn)


def _resolve_from_current_tree(
    run_started_at: datetime,
    *,
    plans: tuple[Plan, ...],
) -> ResolveTasksResult:
    """Read-only resolution test seam: graph load without refresh side effects."""
    return resolve_tasks_from_graph(run_started_at, plans)


def load_plan_graph(session: Session) -> tuple[Plan, ...]:
    plans = tuple(
        session.scalars(
            select(Plan).options(
                selectinload(Plan.goal_plan)
                .selectinload(GoalPlan.chains)
                .selectinload(GoalChildChain.items),
                selectinload(Plan.task_plan),
                selectinload(Plan.repetition_plan).selectinload(RepetitionPlan.instances),
                selectinload(Plan.constraint_groups).selectinload(TimeConstraintGroup.windows),
            )
        ).all()
    )
    _normalize_sqlite_constraint_window_timezones(plans)
    return plans


def _normalize_sqlite_constraint_window_timezones(plans: tuple[Plan, ...]) -> None:
    """SQLite stores UTC datetimes as naive; resolution requires timezone-aware windows."""
    for plan in plans:
        for group in plan.constraint_groups:
            for window in group.windows:
                window.start_time = sqlite_utc(window.start_time)
                window.end_time = sqlite_utc(window.end_time)
